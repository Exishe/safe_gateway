from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.annotations import FreeText
from pypdf.generic import StreamObject

from gateway.engine.detect import EngineConfig, detect
from gateway.engine.rehydrate import rehydrate
from gateway.parsers import pdf
from gateway.parsers.document import DocumentError, PagePosition
from gateway.parsers.redact import mask_units, masked_text

EMAIL = "anna.petrova@example.org"
CARD = "4111 1111 1111 1111"  # pragma: allowlist secret
IBAN = "DE89370400440532013000"  # pragma: allowlist secret
ANNOTATED = "note.only@example.org"
ATTACHED = "attached.only@example.org"
AUTHOR = "Anna Petrova"
STRUCTURED = EngineConfig(ner="off")
VALUES = (EMAIL, CARD, IBAN, ANNOTATED, ATTACHED, AUTHOR)


def _source(tmp_path: Path) -> Path:
    pages = [
        pdf.Page(
            595,
            842,
            [
                pdf.Line(f"Client e-mail {EMAIL}", 72, 760, 12),
                pdf.Line(f"Card {CARD}", 72, 740, 12),
                pdf.Line("Клиент: Иванов", 72, 720, 12),
                pdf.Line("Name", 72, 680, 10),
                pdf.Line("IBAN", 300, 680, 10),
            ],
        ),
        pdf.Page(842, 595, [pdf.Line(f"Pay to {IBAN}", 100, 500, 14)]),
    ]
    plain = tmp_path / "plain.pdf"
    pdf.write_pdf(pages, plain)

    writer = PdfWriter(clone_from=str(plain))
    writer.add_metadata({"/Author": AUTHOR, "/Title": f"for {EMAIL}"})
    writer.add_annotation(0, FreeText(text=f"see {ANNOTATED}", rect=(50, 50, 300, 80)))
    writer.add_attachment("notes.txt", f"notes {ATTACHED}".encode())
    rich = tmp_path / "contract.pdf"
    with rich.open("wb") as handle:
        writer.write(handle)
    return rich


def _redact(tmp_path: Path):  # noqa: ANN202  (test helper)
    source = _source(tmp_path)
    document = pdf.parse(source)
    masks, mapping = mask_units(document, detect(document.text, config=STRUCTURED))
    out = tmp_path / "out.pdf"
    notes = pdf.write_redacted(source, document, masks, out)
    return document, masks, mapping, out, notes


def test_lines_are_units_with_page_positions(tmp_path: Path) -> None:
    document = pdf.parse(_source(tmp_path))

    assert document.format == "pdf"
    assert document.metadata["pages"] == 2
    assert f"Client e-mail {EMAIL}\nCard {CARD}\nКлиент: Иванов" in document.text
    position = document.source_at(document.text.index(CARD))
    assert isinstance(position, PagePosition)
    assert (position.page, round(position.x), round(position.y), position.size) == (
        1,
        72,
        740,
        12,
    )
    assert not position.ocr
    second = document.source_at(document.text.index(IBAN))
    assert isinstance(second, PagePosition)
    assert second.page == 2
    for hidden in (ANNOTATED, ATTACHED, AUTHOR):
        assert hidden not in document.text  # annotations, attachments, metadata: not read


def test_table_cells_on_one_row_stay_separate_units(tmp_path: Path) -> None:
    document = pdf.parse(_source(tmp_path))
    texts = [document.segment_text(i) for i in range(len(document.segments))]
    assert "Name" in texts
    assert "IBAN" in texts


def test_rebuilt_copy_has_the_placeholders_and_page_sizes(tmp_path: Path) -> None:
    *_, out, notes = _redact(tmp_path)
    reader = PdfReader(out)

    assert len(reader.pages) == 2
    assert [(float(p.mediabox.width), float(p.mediabox.height)) for p in reader.pages] == [
        (595.0, 842.0),
        (842.0, 595.0),
    ]
    text = "\n".join(page.extract_text() for page in reader.pages)
    assert "[EMAIL_1]" in text
    assert "[CREDIT_CARD_1]" in text
    assert "[IBAN_1]" in text
    assert "Клиент: Иванов" in text  # outside cp1252: invisible but extractable
    assert notes == ["rebuilt 2 pages as text only (ADR 0018)"]


def test_no_value_survives_in_any_object_of_the_copy(tmp_path: Path) -> None:
    *_, out, _ = _redact(tmp_path)
    reader = PdfReader(out)
    numbers = [n for objects in reader.xref.values() for n in objects]
    numbers += list(reader.xref_objStm)
    assert numbers
    blobs = [out.read_bytes()]
    for number in numbers:
        item = reader.get_object(number)
        if isinstance(item, StreamObject):
            blobs.append(item.get_data())
    everything = b"".join(blobs)
    for value in VALUES:
        assert value.encode() not in everything, value
        assert value.encode().hex().upper().encode() not in everything, value
    assert reader.metadata is None or "/Author" not in reader.metadata
    assert "/Annots" not in reader.pages[0]
    assert not reader.attachments
    assert all("/XObject" not in page.get("/Resources", {}) for page in reader.pages)


def test_rebuilt_copy_reparses_to_the_masked_text_and_restores(tmp_path: Path) -> None:
    document, masks, mapping, out, _ = _redact(tmp_path)
    again = pdf.parse(out)
    assert again.text == masked_text(document, masks)
    assert rehydrate(again.text, mapping) == (document.text, [])


def test_an_encrypted_pdf_is_refused(tmp_path: Path) -> None:
    plain = tmp_path / "plain.pdf"
    pdf.write_pdf([pdf.Page(200, 200, [pdf.Line("x", 10, 100, 12)])], plain)
    writer = PdfWriter(clone_from=str(plain))
    writer.encrypt("owner-and-user")
    locked = tmp_path / "locked.pdf"
    with locked.open("wb") as handle:
        writer.write(handle)
    with pytest.raises(DocumentError, match="encrypted"):
        pdf.parse(locked)


def test_a_file_that_is_not_a_pdf_is_refused(tmp_path: Path) -> None:
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"not a pdf at all")
    with pytest.raises(DocumentError):
        pdf.parse(broken)
