import datetime as dt
import zipfile
from pathlib import Path

import openpyxl
from openpyxl.comments import Comment
from openpyxl.drawing.image import Image as SheetImage
from PIL import Image

from gateway.engine.detect import EngineConfig, detect
from gateway.engine.rehydrate import rehydrate
from gateway.parsers import xlsx
from gateway.parsers.document import CellPosition
from gateway.parsers.redact import mask_units, masked_text

EMAIL = "anna.petrova@example.org"
OTHER = "boris.ivanov@example.com"
CARD = "4111 1111 1111 1111"  # pragma: allowlist secret
CARD_NUMBER = 4111111111111111  # pragma: allowlist secret
IBAN = "DE89370400440532013000"  # pragma: allowlist secret
AUTHOR = "Anna Petrova"
STRUCTURED = EngineConfig(ner="off")


def _workbook(tmp_path: Path, *, title_with_value: bool = False) -> Path:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Clients"
    sheet.append(["Email", "Card", "Amount", "Since"])
    sheet.append([EMAIL, CARD, 12.5, dt.date(2024, 3, 1)])
    sheet.append([OTHER, CARD_NUMBER, 7.0, None])
    sheet["A2"].hyperlink = f"mailto:{EMAIL}"
    sheet["C2"].comment = Comment(f"ask {OTHER}", AUTHOR)
    sheet["D3"] = "=C2*2"
    picture = tmp_path / "logo.png"
    Image.new("RGB", (4, 4)).save(picture)
    sheet.add_image(SheetImage(str(picture)), "F1")
    second = workbook.create_sheet(OTHER if title_with_value else "Bank")
    second["B2"] = IBAN
    workbook.properties.creator = AUTHOR
    workbook.properties.lastModifiedBy = AUTHOR
    path = tmp_path / "clients.xlsx"
    workbook.save(path)
    return path


def _redact(tmp_path: Path, **kwargs: bool):  # noqa: ANN202  (test helper)
    source = _workbook(tmp_path, **kwargs)
    document = xlsx.parse(source)
    masks, mapping = mask_units(document, detect(document.text, config=STRUCTURED))
    out = tmp_path / "out.xlsx"
    notes = xlsx.write_redacted(source, document, masks, out)
    return document, masks, mapping, out, notes


def test_cells_comments_and_titles_are_units(tmp_path: Path) -> None:
    document = xlsx.parse(_workbook(tmp_path))

    assert document.format == "xlsx"
    assert document.text.startswith("Clients\nEmail\tCard\tAmount\tSince\n")
    assert f"{EMAIL}\t{CARD}\t12.5\t2024-03-01\n" in document.text
    assert f"{OTHER}\t{CARD_NUMBER}\t7" in document.text
    assert f"ask {OTHER}" in document.text
    assert document.source_at(document.text.index(CARD)) == CellPosition("Clients", "B2")
    assert document.source_at(document.text.index("ask")) == CellPosition(
        "Clients", "C2", "comment"
    )
    assert document.source_at(document.text.index("Bank")) == CellPosition("Bank", "", "title")
    assert document.source_at(document.text.index(IBAN)) == CellPosition("Bank", "B2")
    assert "=C2" not in document.text  # a formula is read as its cached value (none here)


def test_redacted_copy_holds_placeholders_and_keeps_unchanged_cells(tmp_path: Path) -> None:
    *_, out, notes = _redact(tmp_path)
    workbook = openpyxl.load_workbook(out)
    sheet = workbook["Clients"]

    assert sheet["A2"].value == "[EMAIL_1]"
    assert sheet["B2"].value == "[CREDIT_CARD_1]"
    assert sheet["B3"].value == "[CREDIT_CARD_2]"
    assert sheet["C2"].value == 12.5  # unchanged cell keeps its type
    assert sheet["A2"].hyperlink is None
    assert sheet["C2"].comment is not None
    assert sheet["C2"].comment.text == "ask [EMAIL_2]"
    assert sheet["C2"].comment.author == "Redacted"
    assert workbook["Bank"]["B2"].value == "[IBAN_1]"
    assert workbook.properties.creator in {None, ""}
    assert workbook.properties.lastModifiedBy in {None, ""}
    assert "removed 1 image or chart" in notes


def test_no_value_survives_in_any_zip_member(tmp_path: Path) -> None:
    *_, out, _ = _redact(tmp_path)
    with zipfile.ZipFile(out) as archive:
        names = archive.namelist()
        everything = b"".join(archive.read(name) for name in names).decode("utf-8", "replace")
    assert not any(name.startswith("xl/media/") for name in names)
    for value in (EMAIL, OTHER, CARD, str(CARD_NUMBER), IBAN, AUTHOR):
        assert value not in everything


def test_redacted_copy_reparses_to_the_masked_text_and_restores(tmp_path: Path) -> None:
    document, masks, mapping, out, _ = _redact(tmp_path)
    again = xlsx.parse(out)
    assert again.text == masked_text(document, masks)
    assert rehydrate(again.text, mapping) == (document.text, [])


def test_a_masked_sheet_title_loses_its_brackets(tmp_path: Path) -> None:
    *_, out, _ = _redact(tmp_path, title_with_value=True)
    workbook = openpyxl.load_workbook(out)
    assert workbook.sheetnames == ["Clients", "EMAIL_2"]
