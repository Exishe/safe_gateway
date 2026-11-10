"""Red-team findings against the document parsers and redacted write-back (ADR 0017, 0018).

Each test builds a fixture with the format library and Faker-shaped fake values, masks
it through `formats.mask_file`, and asserts the copy's bytes hold no original value.
Every test is a known bypass, marked xfail(strict=True); the engine fix is the main
session's job. Run with `--runxfail` to watch each one fail for its stated reason.
"""

import zipfile
from pathlib import Path

import docx
import pytest
from docx.oxml import parse_xml
from docx.oxml.ns import qn

from gateway.engine.detect import EngineConfig
from gateway.parsers.formats import mask_file

_STRUCTURED = EngineConfig(ner="off")
_WML_NUMBERING = "application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"


def _members_with(path: Path, needle: bytes) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        return [name for name in archive.namelist() if needle in archive.read(name)]


def _numbering_root(document):  # noqa: ANN001, ANN202
    """The lxml root of the document's numbering part (python-docx ships one by default)."""
    for part in document.part.package.iter_parts():
        if part.content_type == _WML_NUMBERING:
            return part.element
    msg = "the template has no numbering part"
    raise AssertionError(msg)


@pytest.mark.xfail(strict=True, reason="red-team finding (parsers)")
def test_docx_attribute_value_smuggling(tmp_path: Path) -> None:
    """A value hidden in a kept attribute (`w:lang w:val`) survives in the copy.

    ADR 0018 keeps attributes "typed by the schema" without validating the value, so
    free text placed in `w:val` is copied verbatim into word/document.xml and detection
    never reads it (the recognizers only see element text).
    """
    email = "lena.sokolova@example.org"  # pragma: allowlist secret
    document = docx.Document()
    run = document.add_paragraph().add_run("hello")
    run_properties = run._element.get_or_add_rPr()  # noqa: SLF001
    run_properties.append(run_properties.makeelement(qn("w:lang"), {qn("w:val"): email}))
    source = tmp_path / "in.docx"
    document.save(source)

    out = tmp_path / "out.docx"
    mask_file(source, out, _STRUCTURED)
    assert _members_with(out, email.encode()) == []


@pytest.mark.xfail(strict=True, reason="red-team finding (parsers)")
def test_docx_numbering_label_digit_value(tmp_path: Path) -> None:
    """A list label (`w:lvlText w:val`) made of digits survives in word/numbering.xml.

    `_clean_numbering` only rewrites a label that holds a two-letter run, so a phone or
    account number used as a custom list prefix is kept and never masked.
    """
    phone = "8-800-555-35-35"  # pragma: allowlist secret
    document = docx.Document()
    document.add_paragraph("body")
    numbering = _numbering_root(document)
    abstract = parse_xml(
        '<w:abstractNum xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
        ' w:abstractNumId="42"><w:lvl w:ilvl="0"><w:start w:val="1"/>'
        f'<w:lvlText w:val="{phone}"/><w:lvlJc w:val="left"/></w:lvl></w:abstractNum>'.encode()
    )
    numbering.insert(0, abstract)
    source = tmp_path / "in.docx"
    document.save(source)

    out = tmp_path / "out.docx"
    mask_file(source, out, _STRUCTURED)
    assert _members_with(out, phone.encode()) == []
