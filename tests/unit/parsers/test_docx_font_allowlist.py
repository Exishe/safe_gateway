"""Font names in a DOCX copy come from a list of common families (ADR 0018, 2026-11-10).

A font name is free text. A pattern cannot tell `Lena Petrova` from `Liberation Sans`,
so only names on the list pass and any other becomes Calibri.
"""

import zipfile
from pathlib import Path

import docx
import pytest
from docx.oxml.ns import qn

from gateway.engine.detect import EngineConfig
from gateway.parsers.docx_allow import DEFAULT_FONT, keep_attribute
from gateway.parsers.formats import mask_file

_STRUCTURED = EngineConfig(ner="off")
_RFONTS = qn("w:rFonts")
_ASCII = qn("w:ascii")

COMMON = [
    "Calibri",
    "Cambria",
    "Arial",
    "Times New Roman",
    "Aptos",
    "Helvetica",
    "Courier New",
    "Georgia",
    "Verdana",
    "Tahoma",
    "Segoe UI",
    "Liberation Sans",
    "Liberation Serif",
    "Liberation Mono",
    "DejaVu Sans",
    "DejaVu Serif",
    "DejaVu Sans Mono",
    "Noto Sans",
    "Noto Serif",
    "Noto Sans Armenian",
    "Noto Serif Armenian",
    "Noto Sans Cyrillic",
    "Noto Serif Cyrillic",
    "PT Sans",
    "PT Serif",
    "Symbol",
    "Wingdings",
]

NOT_FONTS = [
    "Lena Petrova",  # a person's name has the shape of a font name
    "Ivan",
    "Smith & Sons LLP",
    "lena@example.org",  # pragma: allowlist secret
    "Calibri Lena",  # a listed name with extra words is not a listed name
    "Arial; Lena",
    "",
]


@pytest.mark.parametrize("name", COMMON)
def test_a_common_family_is_kept(name: str) -> None:
    assert keep_attribute(_RFONTS, _ASCII, name) == name


@pytest.mark.parametrize("name", NOT_FONTS)
def test_any_other_name_becomes_calibri(name: str) -> None:
    assert keep_attribute(_RFONTS, _ASCII, name) == DEFAULT_FONT
    assert DEFAULT_FONT == "Calibri"


def test_case_and_spacing_are_normalised_to_the_listed_spelling() -> None:
    assert keep_attribute(_RFONTS, _ASCII, "times new roman") == "Times New Roman"
    assert keep_attribute(_RFONTS, _ASCII, "  Segoe   UI ") == "Segoe UI"


def test_the_font_table_name_follows_the_same_list() -> None:
    table_font = qn("w:font")
    name = qn("w:name")
    assert keep_attribute(table_font, name, "Georgia", extra=True) == "Georgia"
    assert keep_attribute(table_font, name, "Lena Petrova", extra=True) == DEFAULT_FONT


def test_a_font_name_in_a_document_copy_is_listed_or_calibri(tmp_path: Path) -> None:
    document = docx.Document()
    run = document.add_paragraph().add_run("hello")
    properties = run._element.get_or_add_rPr()  # noqa: SLF001
    properties.append(
        properties.makeelement(_RFONTS, {_ASCII: "Liberation Serif", qn("w:hAnsi"): "Lena Petrova"})
    )
    source = tmp_path / "in.docx"
    document.save(str(source))
    out = tmp_path / "out.docx"
    mask_file(source, out, _STRUCTURED)
    with zipfile.ZipFile(out) as archive:
        body = archive.read("word/document.xml")
    assert b"Liberation Serif" in body
    assert b"Lena Petrova" not in body
