import codecs
from pathlib import Path

import pytest

from gateway.engine.detect import EngineConfig, detect
from gateway.parsers import txt
from gateway.parsers.document import DocumentError, TextPosition
from gateway.parsers.redact import mask_units, masked_text

EMAIL = "anna.petrova@example.org"
CARD = "4111 1111 1111 1111"  # pragma: allowlist secret
STRUCTURED = EngineConfig(ner="off")


def _write(tmp_path: Path, data: bytes) -> Path:
    path = tmp_path / "note.txt"
    path.write_bytes(data)
    return path


def test_each_line_is_a_unit_with_its_line_number(tmp_path: Path) -> None:
    path = _write(tmp_path, f"first\r\nmail {EMAIL}\nlast".encode())
    document = txt.parse(path)

    assert document.format == "txt"
    assert document.text == f"first\r\nmail {EMAIL}\nlast"
    assert [document.segment_text(i) for i in range(3)] == ["first", f"mail {EMAIL}", "last"]
    assert document.source_at(document.text.index(EMAIL)) == TextPosition(2)


def test_a_bom_is_removed_from_the_text_and_restored_on_write(tmp_path: Path) -> None:
    path = _write(tmp_path, codecs.BOM_UTF8 + f"card {CARD}\n".encode())
    document = txt.parse(path)
    assert document.text == f"card {CARD}\n"
    assert document.metadata["bom"] == 1

    masks, _ = mask_units(document, detect(document.text, config=STRUCTURED))
    out = tmp_path / "out.txt"
    txt.write_redacted(path, document, masks, out)
    assert out.read_bytes() == codecs.BOM_UTF8 + b"card [CREDIT_CARD_1]\n"


def test_non_utf8_input_is_refused(tmp_path: Path) -> None:
    path = _write(tmp_path, "café".encode("cp1252"))
    with pytest.raises(DocumentError, match="UTF-8"):
        txt.parse(path)


def test_redacted_copy_reparses_to_the_masked_text(tmp_path: Path) -> None:
    path = _write(tmp_path, f"to {EMAIL}\r\ncard {CARD}\r\n\r\nend".encode())
    document = txt.parse(path)
    masks, mapping = mask_units(document, detect(document.text, config=STRUCTURED))
    out = tmp_path / "out.txt"
    txt.write_redacted(path, document, masks, out)

    again = txt.parse(out)
    assert again.text == masked_text(document, masks)
    assert EMAIL not in again.text
    assert CARD not in again.text
    assert set(mapping.to_dict().values()) == {EMAIL, CARD}
