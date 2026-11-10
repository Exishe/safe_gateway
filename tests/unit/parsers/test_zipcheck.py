"""Security review M6: size caps for zip-based formats, and malformed XML as exit code 2."""

import zipfile
from pathlib import Path

import docx
import openpyxl
import pytest
from faker import Faker

from gateway.cli import main
from gateway.engine.detect import EngineConfig
from gateway.parsers import formats, zipcheck
from gateway.parsers.document import DocumentError

STRUCTURED = EngineConfig(ner="off")


def _docx(path: Path, text: str) -> Path:
    document = docx.Document()
    document.add_paragraph(text)
    document.save(str(path))
    return path


def _xlsx(path: Path, sheets: int = 1) -> Path:
    workbook = openpyxl.Workbook()
    for number in range(1, sheets):
        workbook.create_sheet(f"S{number}")
    workbook.save(path)
    return path


def _rewrite_member(path: Path, name: str, change: bytes | None = None) -> Path:
    """A copy of the archive with one member replaced (or padded with a comment)."""
    out = path.with_name(f"changed-{path.name}")
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as copy:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == name:
                data = change if change is not None else data
            copy.writestr(item.filename, data)
    return out


def _pad(path: Path, name: str, megabytes: int) -> Path:
    with zipfile.ZipFile(path) as source:
        data = source.read(name)
    head, _, tail = data.rpartition(b"?>")  # after the XML declaration, if there is one
    comment = b"<!--" + b"0" * (megabytes * 1024 * 1024) + b"-->"
    padded = head + b"?>" + comment + tail if head else comment + tail
    return _rewrite_member(path, name, padded)


def test_the_caps_are_sane() -> None:
    assert zipcheck.MAX_UNCOMPRESSED == 200 * 1024 * 1024
    assert zipcheck.MAX_MEMBERS == 10_000


def test_a_docx_too_large_uncompressed_is_refused_before_it_is_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(zipcheck, "MAX_UNCOMPRESSED", 2 * 1024 * 1024)
    source = _pad(_docx(tmp_path / "a.docx", Faker().email()), "word/document.xml", 3)
    assert source.stat().st_size < 100_000  # small on disk, large once inflated
    with pytest.raises(DocumentError, match="uncompressed"):
        formats.mask_file(source, tmp_path / "out.docx", STRUCTURED)
    assert not (tmp_path / "out.docx").exists()


def test_an_xlsx_with_too_many_members_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(zipcheck, "MAX_MEMBERS", 10)
    source = _xlsx(tmp_path / "many.xlsx", sheets=12)
    with pytest.raises(DocumentError, match="members"):
        formats.mask_file(source, tmp_path / "out.xlsx", STRUCTURED)


def test_an_oversized_document_exits_2_from_the_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(zipcheck, "MAX_UNCOMPRESSED", 2 * 1024 * 1024)
    source = _pad(_xlsx(tmp_path / "big.xlsx"), "xl/worksheets/sheet1.xml", 3)
    assert main(["mask", "--no-ner", str(source), "-o", str(tmp_path / "out.xlsx")]) == 2
    error = capsys.readouterr().err
    assert "uncompressed" in error
    assert "Traceback" not in error


@pytest.mark.parametrize(
    ("kind", "member"),
    [
        ("docx", "word/document.xml"),
        ("docx", "word/styles.xml"),
        ("xlsx", "xl/workbook.xml"),
        ("xlsx", "xl/worksheets/sheet1.xml"),
        ("xlsx", "xl/styles.xml"),
    ],
)
def test_malformed_xml_exits_2_with_a_reason(
    kind: str, member: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    built = _docx(tmp_path / "a.docx", "text") if kind == "docx" else _xlsx(tmp_path / "a.xlsx")
    source = _rewrite_member(built, member, b"<?xml version='1.0'?><broken><unclosed></broken>")
    assert main(["mask", "--no-ner", str(source), "-o", str(tmp_path / f"out.{kind}")]) == 2
    error = capsys.readouterr().err
    assert error.startswith("safeguard: cannot read input:")
    assert "Traceback" not in error


@pytest.mark.parametrize("suffix", [".docx", ".xlsx"])
def test_a_file_that_is_not_a_zip_exits_2(
    suffix: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / f"fake{suffix}"
    source.write_bytes(b"PK\x03\x04 not really a zip archive")
    assert main(["mask", "--no-ner", str(source), "-o", str(tmp_path / f"out{suffix}")]) == 2
    assert "cannot read input" in capsys.readouterr().err
