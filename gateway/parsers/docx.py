"""Word (.docx): paragraphs of every story part in; a redacted copy out (ADR 0017, 0018).

Text is read at the level of `w:t`, `w:delText`, `w:instrText` and `w:delInstrText`, so
a value split across runs, a tracked deletion and a field code are all masked. The copy
keeps only what `docx_allow` lists for each part: every other element and attribute is
removed, so a construct this module does not know is dropped, never copied. Content
that cannot be read as text (drawings, embedded objects, equations) is not read and is
removed and counted. Text inside an unknown element is not read either, so the masked
text a model sees is exactly the text the copy holds.
"""

import re
import zipfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

import docx
from docx.opc.oxml import serialize_part_xml
from docx.opc.packuri import PackURI
from docx.opc.part import Part, XmlPart
from docx.oxml import parse_xml
from docx.oxml.coreprops import CT_CoreProperties
from docx.oxml.ns import qn
from lxml import etree  # ty: ignore[import-not-found]  (lxml ships no type stubs)

from gateway.parsers import docx_allow as allow
from gateway.parsers.document import Document, DocumentBuilder, DocumentError, ParagraphPosition
from gateway.parsers.output import atomic_output
from gateway.parsers.redact import UnitMask, distribute
from gateway.parsers.zipcheck import check_archive, parse_errors

REDACTED_AUTHOR = "Redacted"
_WML = "application/vnd.openxmlformats-officedocument.wordprocessingml."
_MAIN = _WML + "document.main+xml"
_CORE = "application/vnd.openxmlformats-package.core-properties+xml"
_THEME = "application/vnd.openxmlformats-officedocument.theme+xml"
# Story parts: the ones whose paragraphs a reader sees, with their allowlist.
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
# Other parts the copy keeps, with the allowlist each one is cut to.
_SUPPORT = {
    _WML + "styles+xml": allow.STYLES,
    _WML + "numbering+xml": allow.NUMBERING,
    _WML + "settings+xml": allow.SETTINGS,
    _WML + "fontTable+xml": allow.FONTS,
}
# Every kept part gets a fixed name: a part name from the source is free text.
_NAMES = {
    _MAIN: "/word/document.xml",
    _WML + "footnotes+xml": "/word/footnotes.xml",
    _WML + "endnotes+xml": "/word/endnotes.xml",
    _WML + "comments+xml": "/word/comments.xml",
    _WML + "styles+xml": "/word/styles.xml",
    _WML + "numbering+xml": "/word/numbering.xml",
    _WML + "settings+xml": "/word/settings.xml",
    _WML + "fontTable+xml": "/word/fontTable.xml",
    _THEME: "/word/theme/theme1.xml",
    _CORE: "/docProps/core.xml",
}
_NUMBERED_NAMES = {
    _WML + "header+xml": "/word/header{}.xml",
    _WML + "footer+xml": "/word/footer{}.xml",
}
_KEPT_MEMBERS = re.compile(
    r"\[Content_Types\]\.xml|_rels/\.rels|docProps/core\.xml"
    r"|word/(?:document|styles|numbering|settings|fontTable|footnotes|endnotes|comments)\.xml"
    r"|word/(?:header|footer)\d+\.xml|word/theme/theme1\.xml"
    r"|word/_rels/(?:document|header\d+|footer\d+|footnotes|endnotes|comments)\.xml\.rels"
)

_PARAGRAPH = qn("w:p")
_RUN = qn("w:r")
_WRITABLE = allow.TEXT
_FIXED = {qn("w:tab"): "\t", qn("w:br"): "\n", qn("w:cr"): "\n", qn("w:noBreakHyphen"): "-"}
# Elements a paragraph's text is read through; text under anything else is not read,
# because the copy removes it.
_READ = allow.STORY | allow.UNWRAPPED
_LEVEL_REFERENCE = re.compile(r"%[1-9]")
_LABEL_FILLER = 2  # characters allowed beside the level references
_AUTHOR = qn("w:author")
_INITIALS = qn("w:initials")
_EXTRA_ATTRIBUTES = {
    qn("w:compatSetting"): frozenset({qn("w:name"), qn("w:uri")}),
    qn("w:font"): frozenset({qn("w:name")}),  # a font name (ADR 0018: kept)
}
# Relationship types kept in the copy (the last part of the type URI).
_KEPT_RELATIONSHIPS = frozenset(
    {
        "officeDocument", "core-properties", "styles", "numbering", "settings", "fontTable",
        "theme", "header", "footer", "footnotes", "endnotes", "comments",
    }
)  # fmt: skip
_REFERENCE = re.compile(rb'\br:(?:id|embed|link|pict)="([^"]+)"')
_R_NAMESPACE = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
# lxml's own text property: python-docx's element classes redefine `text` on some tags.
_RAW_TEXT = etree._Element.text  # noqa: SLF001


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


@dataclass
class _Removed:
    content: int = 0  # drawings, embedded objects, equations
    other: int = 0  # unknown elements that held text
    notes: list[str] = field(default_factory=list)


def _open(path: Path) -> Any:  # noqa: ANN401  (python-docx's Document is a factory)
    check_archive(path)
    try:
        return docx.Document(str(path))
    except (OSError, KeyError, ValueError, *parse_errors()) as error:
        msg = f"{path.name} is not a readable .docx document ({error})"
        raise DocumentError(msg) from error


def _root_of(part: Part) -> Any:  # noqa: ANN401
    if isinstance(part, XmlPart):
        return part.element
    try:
        return parse_xml(part.blob)
    except parse_errors() as error:
        msg = f"{part.partname} is not well-formed XML ({error})"
        raise DocumentError(msg) from error


def _stories(document: Any) -> list[_Story]:  # noqa: ANN401
    parts = [p for p in document.part.package.iter_parts() if p.content_type in _STORIES]
    parts.sort(key=lambda p: (p.content_type != _MAIN, str(p.partname)))
    return [_Story(part, _root_of(part)) for part in parts]


def _skipped(element: Any) -> bool:  # noqa: ANN401
    return any(ancestor.tag not in _READ for ancestor in element.iterancestors())


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


# ------------------------------------------------------------------ allowlist scrub


def _unwrap(root: Any) -> None:  # noqa: ANN401
    """Replace each container by its children, innermost first."""
    for element in reversed(list(root.iter(*allow.UNWRAPPED))):
        parent = element.getparent()
        if parent is None:
            continue
        position = parent.index(element)
        for child in list(element):
            parent.insert(position, child)
            position += 1
        parent.remove(element)


def _holds_text(element: Any) -> bool:  # noqa: ANN401
    return any((node.text or "").strip() for node in element.iter() if isinstance(node.tag, str))


def _prune(root: Any, allowed: frozenset[str], removed: _Removed) -> None:  # noqa: ANN401
    """Remove every element not in `allowed`, with its subtree; and every comment."""
    stack = [root]
    while stack:
        element = stack.pop()
        for child in list(element):
            if not isinstance(child.tag, str):  # an XML comment or processing instruction
                element.remove(child)
            elif child.tag in allowed:
                stack.append(child)
            else:
                if child.tag in allow.CONTENT:
                    removed.content += 1
                elif _holds_text(child):
                    removed.other += 1
                element.remove(child)


def _keep_attributes(root: Any) -> None:  # noqa: ANN401
    """Only allowlisted attributes stay; people are replaced; text sits only in w:t."""
    for element in root.iter():
        extra = _EXTRA_ATTRIBUTES.get(element.tag, frozenset())
        for name in list(element.attrib):
            if name not in allow.ATTRIBUTES and name not in extra:
                del element.attrib[name]
                continue
            kept = allow.keep_attribute(
                element.tag, name, element.get(name, ""), extra=name in extra
            )
            if kept is None:
                del element.attrib[name]
            elif kept != element.get(name):
                element.set(name, kept)
        if element.get(_AUTHOR) is not None:
            element.set(_AUTHOR, REDACTED_AUTHOR)
        if element.get(_INITIALS) is not None:
            element.set(_INITIALS, "")
        if element.tag not in _WRITABLE:
            _RAW_TEXT.__set__(element, None)
        element.tail = None


def _scrub(root: Any, allowed: frozenset[str], removed: _Removed) -> None:  # noqa: ANN401
    if root.tag not in allowed:
        msg = f"unexpected root element {root.tag}"
        raise DocumentError(msg)
    _unwrap(root)
    _prune(root, allowed, removed)
    _keep_attributes(root)
    etree.cleanup_namespaces(root)


def _clean_settings(root: Any) -> None:  # noqa: ANN401
    """A compatibility setting stays only with a name and URI Word itself writes."""
    for element in list(root.iter(qn("w:compatSetting"))):
        known = element.get(qn("w:name")) in allow.COMPAT_NAMES
        if not known or element.get(qn("w:uri")) != allow.COMPAT_URI:
            element.getparent().remove(element)


def _label_ok(label: str) -> bool:
    """`%1.`, `(%2)`, a bullet: level references plus at most two non-alphanumeric
    characters. A lone non-digit is kept (a bullet can be the letter `o`)."""
    if len(label) <= 1:
        return not label.isdigit()
    filler = _LEVEL_REFERENCE.sub("", label)
    return len(filler) <= _LABEL_FILLER and not any(char.isalnum() for char in filler)


def _clean_numbering(root: Any) -> None:  # noqa: ANN401
    """A list label that is not level references and punctuation (`Exhibit %1 for ...`,
    a phone number) becomes `%N.`."""
    for element in root.iter(qn("w:lvlText")):
        if not _label_ok(element.get(qn("w:val"), "")):
            level = element.getparent().get(qn("w:ilvl"), "0")
            number = int(level) + 1 if level.isdigit() else 1
            element.set(qn("w:val"), f"%{min(number, 9)}.")


@cache
def _defaults() -> tuple[frozenset[str], bytes]:
    """Built-in style names and the theme of python-docx's own blank document."""
    blank = docx.Document()
    names = {"normal", "default paragraph font", "table normal", "no list"}
    theme = b""
    for part in blank.part.package.iter_parts():
        if part.content_type == _WML + "styles+xml" and isinstance(part, XmlPart):
            for element in part.element.iter(qn("w:lsdException"), qn("w:name")):
                value = element.get(qn("w:name")) or element.get(qn("w:val")) or ""
                names.add(value.lower())
        if part.content_type == _THEME:
            theme = part.blob
    return frozenset(names), theme


def _style_id(name: str) -> str:
    return "".join(word[:1].upper() + word[1:] for word in name.split())


def _rename_styles(styles: Any, roots: Sequence[Any]) -> None:  # noqa: ANN401
    """Custom style names and every style id are free text: give each style a fixed id,
    keep built-in names, rename custom styles, and drop references to unknown styles."""
    builtin, _ = _defaults()
    renamed: dict[str, str] = {}
    custom = 0
    for style in styles.iter(qn("w:style")):
        name = style.find(qn("w:name"))
        value = name.get(qn("w:val"), "") if name is not None else ""
        new = _style_id(value) if value.lower() in builtin else ""
        if not new or new in renamed.values():
            custom += 1
            new = f"Custom{custom}"
            if name is not None:
                name.set(qn("w:val"), f"Custom {custom}")
        renamed[style.get(qn("w:styleId"), "")] = new
        style.set(qn("w:styleId"), new)
    for root in (styles, *roots):
        for element in list(root.iter(*allow.STYLE_REFERENCES)):
            new = renamed.get(element.get(qn("w:val"), ""))
            if new is None:
                element.getparent().remove(element)
            else:
                element.set(qn("w:val"), new)


# ------------------------------------------------------------------ package


def _drop_relationships(package: Any) -> None:  # noqa: ANN401
    for owner in [package, *package.iter_parts()]:
        for r_id, relationship in list(owner.rels.items()):
            kind = relationship.reltype.rsplit("/", 1)[-1]
            if relationship.is_external or kind not in _KEPT_RELATIONSHIPS:
                del owner.rels[r_id]


def _rekey(owner: Any, root: Any) -> None:  # noqa: ANN401
    """Relationship ids become rId1, rId2...: an id from the source is free text."""
    renamed = {}
    relationships = list(owner.rels.items())
    owner.rels.clear()
    owner.rels._target_parts_by_rId.clear()  # noqa: SLF001  (no public API)
    for number, (old, relationship) in enumerate(relationships, 1):
        new = f"rId{number}"
        relationship._rId = new  # noqa: SLF001
        owner.rels[new] = relationship
        if not relationship.is_external:
            owner.rels._target_parts_by_rId[new] = relationship.target_part  # noqa: SLF001
        renamed[old] = new
    if root is None:
        return
    for element in root.iter():
        for name, value in element.attrib.items():
            if name.startswith(_R_NAMESPACE) and value in renamed:
                element.set(name, renamed[value])


def _rename_parts(parts: Sequence[Part]) -> None:
    taken: set[str] = set()
    counters = dict.fromkeys(_NUMBERED_NAMES, 0)
    for part in parts:
        if part.content_type in _NUMBERED_NAMES:
            counters[part.content_type] += 1
            name = _NUMBERED_NAMES[part.content_type].format(counters[part.content_type])
        else:
            name = _NAMES.get(part.content_type, "")
        if not name or name in taken:
            msg = f"{part.partname} ({part.content_type}) cannot be redacted"
            raise DocumentError(msg)
        taken.add(name)
        part.partname = PackURI(name)
        part.rels._baseURI = part.partname.baseURI  # noqa: SLF001  (follow the new name)


def _check_references(roots: dict[Part, Any]) -> None:
    for part, root in roots.items():
        references = set(_REFERENCE.findall(etree.tostring(root)))
        dangling = {r.decode() for r in references} - set(part.rels)
        if dangling:
            msg = f"{part.partname} links to content that cannot be redacted"
            raise DocumentError(msg)


def _check_members(path: Path, source: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        unexpected = [n for n in archive.namelist() if not _KEPT_MEMBERS.fullmatch(n)]
    if unexpected:
        msg = f"{source.name}: the copy would hold {unexpected[0]}, which is not redacted"
        raise DocumentError(msg)


# ------------------------------------------------------------------ write


def _write_unit(unit: _Unit, mask: UnitMask) -> None:
    texts = distribute([(text, writable) for _, text, writable in unit.pieces], mask.replacements)
    for (element, _, writable), new in zip(unit.pieces, texts, strict=True):
        if new is None:
            element.getparent().remove(element)
        elif writable:
            element.text = new
            element.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")


def _notes(removed: _Removed) -> list[str]:
    notes = []
    if removed.content:
        notes.append(f"removed {removed.content} drawing, embedded object or equation")
    if removed.other:
        notes.append(f"removed {removed.other} unrecognised element with text")
    return notes


def _scrub_support(parts: Sequence[Part], roots: dict[Part, Any], removed: _Removed) -> None:
    """Cut every kept part that is not a story to its allowlist; adds them to `roots`."""
    styles = None
    for part in parts:
        if part.content_type in _SUPPORT:
            root = roots[part] = _root_of(part)
            _scrub(root, _SUPPORT[part.content_type], removed)
            if part.content_type == _WML + "settings+xml":
                _clean_settings(root)
            elif part.content_type == _WML + "numbering+xml":
                _clean_numbering(root)
            elif part.content_type == _WML + "styles+xml":
                styles = root
        elif part.content_type == _THEME:
            part._blob = _defaults()[1]  # noqa: SLF001  (a plain Part's bytes)
        elif part.content_type == _CORE and isinstance(part, XmlPart):
            part._element = CT_CoreProperties.new()  # noqa: SLF001  (no property survives)
    if styles is not None:
        _rename_styles(styles, [root for root in roots.values() if root is not styles])


def write_redacted(
    source: Path, document: Document, masks: Sequence[UnitMask], output: Path
) -> list[str]:
    word = _open(source)
    package = word.part.package
    removed = _Removed()
    stories = _stories(word)
    for story in stories:
        _scrub(story.root, allow.STORY, removed)
    units = [unit for story in stories for unit in _units(story)]
    if len(units) != len(document.segments) or len(masks) != len(units):
        msg = f"{source.name} changed since it was read; mask it again"
        raise DocumentError(msg)
    for unit, mask in zip(units, masks, strict=True):
        if mask.replacements:
            _write_unit(unit, mask)

    _drop_relationships(package)
    parts = list(package.iter_parts())
    roots: dict[Part, Any] = {story.part: story.root for story in stories}
    _scrub_support(parts, roots, removed)
    _rename_parts(parts)
    _rekey(package, None)
    for part in parts:
        _rekey(part, roots.get(part))
    _check_references(roots)
    for part, root in roots.items():
        if not isinstance(part, XmlPart):
            part._blob = serialize_part_xml(root)  # noqa: SLF001  (a plain Part's bytes)
    with atomic_output(output) as temporary:
        word.save(str(temporary))
        _check_members(temporary, source)
    return _notes(removed)
