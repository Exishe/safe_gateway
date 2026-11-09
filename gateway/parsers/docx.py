"""Word (.docx): paragraphs of every story part in; a redacted copy out (ADR 0017, 0018).

Text is read at the level of `w:t`, `w:delText` and `w:instrText`, so a value split
across runs, a tracked deletion and a field code are all masked. Drawings, pictures
and embedded objects are not read and are removed from the copy, with every
relationship that is not on an allowlist.
"""

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import docx
from docx.opc.oxml import serialize_part_xml
from docx.opc.part import Part, XmlPart
from docx.oxml import parse_xml
from docx.oxml.ns import qn

from gateway.parsers.document import Document, DocumentBuilder, DocumentError, ParagraphPosition
from gateway.parsers.output import atomic_output
from gateway.parsers.redact import UnitMask, distribute

REDACTED_AUTHOR = "Redacted"
_WML = "application/vnd.openxmlformats-officedocument.wordprocessingml."
_MAIN = _WML + "document.main+xml"
# Story parts: the ones whose paragraphs a reader sees.
_STORIES = frozenset(
    _WML + kind
    for kind in (
        "document.main+xml",
        "header+xml",
        "footer+xml",
        "footnotes+xml",
        "endnotes+xml",
        "comments+xml",
    )
)
_SETTINGS = _WML + "settings+xml"

_PARAGRAPH = qn("w:p")
_RUN = qn("w:r")
_WRITABLE = frozenset({qn("w:t"), qn("w:delText"), qn("w:instrText")})
_FIXED = {qn("w:tab"): "\t", qn("w:br"): "\n", qn("w:cr"): "\n", qn("w:noBreakHyphen"): "-"}
_MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
# Content we cannot read: removed from the copy, and never read as text.
_REMOVED = frozenset(
    {qn("w:drawing"), qn("w:pict"), qn("w:object"), qn("w:altChunk"), qn("w:subDoc")}
    | {f"{{{_MC}}}AlternateContent"}
)
_UNWRAPPED = frozenset({qn("w:hyperlink"), qn("w:fldSimple")})
_SETTINGS_REMOVED = frozenset({qn("w:attachedTemplate"), qn("w:docVars")})
_PEOPLE_ATTRIBUTES = (qn("w:author"), qn("w:initials"))
# Relationship types kept in the copy (the last part of the type URI).
_KEPT_RELATIONSHIPS = frozenset(
    {
        "officeDocument", "core-properties", "styles", "stylesWithEffects", "numbering",
        "settings", "webSettings", "fontTable", "font", "theme", "header", "footer",
        "footnotes", "endnotes", "comments", "commentsExtended", "commentsIds",
        "commentsExtensible",
    }
)  # fmt: skip
_REFERENCE = re.compile(rb'\br:(?:id|embed|link|pict)="([^"]+)"')
_CORE_TEXT = (
    "author", "category", "comments", "content_status", "identifier", "keywords",
    "language", "last_modified_by", "subject", "title", "version",
)  # fmt: skip


@dataclass
class _Story:
    part: Part
    root: Any  # lxml element of the part


@dataclass(frozen=True)
class _Unit:
    source: ParagraphPosition
    pieces: list[tuple[Any, str, bool]]  # (element, text, writable)

    @property
    def text(self) -> str:
        return "".join(text for _, text, _ in self.pieces)


def _open(path: Path) -> Any:  # noqa: ANN401  (python-docx's Document is a factory)
    try:
        return docx.Document(str(path))
    except (OSError, KeyError, ValueError) as error:
        msg = f"{path.name} is not a readable .docx document ({error})"
        raise DocumentError(msg) from error


def _stories(document: Any) -> list[_Story]:  # noqa: ANN401
    parts = [p for p in document.part.package.iter_parts() if p.content_type in _STORIES]
    parts.sort(key=lambda p: (p.content_type != _MAIN, str(p.partname)))
    stories = []
    for part in parts:
        root = part.element if isinstance(part, XmlPart) else parse_xml(part.blob)
        stories.append(_Story(part, root))
    return stories


def _skipped(element: Any) -> bool:  # noqa: ANN401
    return any(ancestor.tag in _REMOVED for ancestor in element.iterancestors())


def _paragraph_of(element: Any) -> Any:  # noqa: ANN401
    return next((a for a in element.iterancestors() if a.tag == _PARAGRAPH), None)


def _units(story: _Story) -> Iterator[_Unit]:
    paragraphs = [p for p in story.root.iter(_PARAGRAPH) if not _skipped(p)]
    pieces: dict[Any, list[tuple[Any, str, bool]]] = {p: [] for p in paragraphs}
    for element in story.root.iter(*_WRITABLE, *_FIXED):
        if element.tag in _FIXED and element.getparent().tag != _RUN:
            continue  # w:tab inside w:tabs is a tab stop, not text
        paragraph = _paragraph_of(element)
        if paragraph not in pieces or _skipped(element):
            continue
        if element.tag in _WRITABLE:
            pieces[paragraph].append((element, element.text or "", True))
        else:
            pieces[paragraph].append((element, _FIXED[element.tag], False))
    for index, paragraph in enumerate(paragraphs):
        yield _Unit(ParagraphPosition(str(story.part.partname), index), pieces[paragraph])


def parse(path: Path) -> Document:
    document = _open(path)
    builder = DocumentBuilder()
    first = True
    for story in _stories(document):
        for unit in _units(story):
            if not first:
                builder.separate("\n")
            first = False
            builder.add(unit.text, unit.source)
    return builder.build("docx")


# ------------------------------------------------------------------ write


def _remove_unreadable(root: Any) -> int:  # noqa: ANN401
    found = [e for e in root.iter(*_REMOVED) if not _skipped(e)]
    for element in found:
        element.getparent().remove(element)
    for element in list(root.iter(*_UNWRAPPED)):
        parent = element.getparent()
        position = parent.index(element)
        for child in list(element):
            parent.insert(position, child)
            position += 1
        parent.remove(element)
    return len(found)


def _write_unit(unit: _Unit, mask: UnitMask) -> None:
    texts = distribute([(text, writable) for _, text, writable in unit.pieces], mask.replacements)
    for (element, _, writable), new in zip(unit.pieces, texts, strict=True):
        if new is None:
            element.getparent().remove(element)
        elif writable:
            element.text = new
            element.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")


def _clear_people(root: Any) -> None:  # noqa: ANN401
    for element in root.iter():
        for attribute in _PEOPLE_ATTRIBUTES:
            if element.get(attribute) is not None:
                element.set(attribute, REDACTED_AUTHOR if attribute == qn("w:author") else "")


def _drop_relationships(document: Any) -> None:  # noqa: ANN401
    package = document.part.package
    for owner in [package, *package.iter_parts()]:
        for r_id, relationship in list(owner.rels.items()):
            kind = relationship.reltype.rsplit("/", 1)[-1]
            if relationship.is_external or kind not in _KEPT_RELATIONSHIPS:
                del owner.rels[r_id]
    for part in package.iter_parts():
        if isinstance(part, XmlPart):
            references = set(_REFERENCE.findall(serialize_part_xml(part.element)))
            dangling = {r.decode() for r in references} - set(part.rels)
            if dangling:
                msg = f"{part.partname} links to content that cannot be redacted"
                raise DocumentError(msg)


def write_redacted(
    source: Path, document: Document, masks: Sequence[UnitMask], output: Path
) -> list[str]:
    word = _open(source)
    stories = _stories(word)
    removed = sum(_remove_unreadable(story.root) for story in stories)
    units = [unit for story in stories for unit in _units(story)]
    if len(units) != len(document.segments) or len(masks) != len(units):
        msg = f"{source.name} changed since it was read; mask it again"
        raise DocumentError(msg)
    for unit, mask in zip(units, masks, strict=True):
        if mask.replacements:
            _write_unit(unit, mask)
    for story in stories:
        _clear_people(story.root)
        if not isinstance(story.part, XmlPart):
            story.part._blob = serialize_part_xml(story.root)  # noqa: SLF001  (a plain Part's bytes)
    for part in word.part.package.iter_parts():
        if part.content_type == _SETTINGS and isinstance(part, XmlPart):
            for element in list(part.element.iter(*_SETTINGS_REMOVED)):
                element.getparent().remove(element)
    for name in _CORE_TEXT:
        setattr(word.core_properties, name, "")
    _drop_relationships(word)
    with atomic_output(output) as temporary:
        word.save(str(temporary))
    return [f"removed {removed} drawing or embedded object"] if removed else []
