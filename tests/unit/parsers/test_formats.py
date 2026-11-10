from pathlib import Path

import pytest

from gateway.engine.detect import EngineConfig
from gateway.engine.rehydrate import rehydrate
from gateway.parsers import formats, txt
from gateway.parsers.document import DocumentError

EMAIL = "anna.petrova@example.org"
STRUCTURED = EngineConfig(ner="off")


@pytest.mark.parametrize("name", ["a.pdf", "b.DOCX", "c.xlsx", "d.eml", "e.txt"])
def test_document_suffixes_are_handled(name: str) -> None:
    assert formats.handles(Path(name))


@pytest.mark.parametrize("name", ["notes.md", "data.csv", "README", "old.doc"])
def test_other_suffixes_are_not(name: str) -> None:
    assert not formats.handles(Path(name))


def test_only_text_can_go_to_stdout() -> None:
    assert formats.is_text(Path("x.txt"))
    assert not formats.is_text(Path("x.docx"))


def test_mask_file_writes_the_copy_and_returns_text_and_mapping(tmp_path: Path) -> None:
    source = tmp_path / "note.txt"
    source.write_text(f"mail {EMAIL}\n", encoding="utf-8", newline="")
    out = tmp_path / "out.txt"

    result = formats.mask_file(source, out, STRUCTURED)

    assert result.text == "mail [EMAIL_1]\n"
    assert out.read_bytes().decode("utf-8") == result.text
    assert rehydrate(result.text, result.mapping) == (f"mail {EMAIL}\n", [])
    assert result.notes == []
    assert txt.parse(out).text == result.text


def test_mask_file_without_output_writes_nothing(tmp_path: Path) -> None:
    source = tmp_path / "note.txt"
    source.write_text(EMAIL, encoding="utf-8")
    assert formats.mask_file(source, None, STRUCTURED).text == "[EMAIL_1]"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["note.txt"]


def test_a_failed_write_leaves_no_output(tmp_path: Path) -> None:
    source = tmp_path / "broken.pdf"
    source.write_bytes(b"%PDF-1.7 not really")
    out = tmp_path / "out.pdf"
    with pytest.raises(DocumentError):
        formats.mask_file(source, out, STRUCTURED)
    assert not out.exists()
