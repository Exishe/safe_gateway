import zipfile
from pathlib import Path

import docx
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls
from PIL import Image

from gateway.engine.detect import EngineConfig, detect
from gateway.engine.rehydrate import rehydrate
from gateway.parsers import docx as docx_parser
from gateway.parsers.document import ParagraphPosition
from gateway.parsers.redact import mask_units, masked_text

EMAIL = "anna.petrova@example.org"
LINKED = "boris.ivanov@example.com"
DELETED = "deleted.person@example.net"
FIELD = "field.code@example.net"
HEADER = "header.mail@example.org"
FOOTER = "footer.mail@example.org"
NOTE = "comment.mail@example.org"
CARD = "4111 1111 1111 1111"  # pragma: allowlist secret
IBAN = "DE89370400440532013000"  # pragma: allowlist secret
AUTHOR = "Anna Petrova"
STRUCTURED = EngineConfig(ner="off")
VALUES = (EMAIL, LINKED, DELETED, FIELD, HEADER, FOOTER, NOTE, CARD, IBAN, AUTHOR)


def _contract(tmp_path: Path) -> Path:
    document = docx.Document()
    document.core_properties.author = AUTHOR
    document.core_properties.title = f"Contract for {EMAIL}"
    section = document.sections[0]
    section.header.paragraphs[0].text = f"Contact {HEADER}"
    section.footer.paragraphs[0].text = f"Footer {FOOTER}"

    split = document.add_paragraph("Mail: ")
    split.add_run(EMAIL[:8]).bold = True  # the value spans two runs
    split.add_run(EMAIL[8:] + " today")

    linked = document.add_paragraph("Write to ")
    r_id = document.part.relate_to(f"mailto:{LINKED}", RT.HYPERLINK, is_external=True)
    linked._p.append(  # noqa: SLF001
        parse_xml(
            f'<w:hyperlink {nsdecls("w", "r")} r:id="{r_id}"><w:r><w:t>{LINKED}</w:t></w:r>'
            "</w:hyperlink>"
        )
    )

    tracked = document.add_paragraph("Changed: ")
    tracked._p.append(  # noqa: SLF001
        parse_xml(
            f'<w:del {nsdecls("w")} w:id="1" w:author="{AUTHOR}" w:date="2024-01-01T00:00:00Z">'
            f"<w:r><w:delText>{DELETED}</w:delText></w:r></w:del>"
        )
    )
    field = document.add_paragraph("Field ")
    field._p.append(  # noqa: SLF001
        parse_xml(
            f'<w:r {nsdecls("w")}><w:instrText xml:space="preserve"> HYPERLINK '
            f'"mailto:{FIELD}" </w:instrText></w:r>'
        )
    )

    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = f"Card {CARD}"
    inner = table.cell(0, 1).add_table(rows=1, cols=1)
    inner.cell(0, 0).text = f"IBAN {IBAN}"

    commented = document.add_paragraph("Reviewed")
    document.add_comment(commented.runs, text=f"ask {NOTE}", author=AUTHOR, initials="AP")

    picture = tmp_path / "scan.png"
    Image.new("RGB", (8, 8), "white").save(picture)
    document.add_picture(str(picture))

    path = tmp_path / "contract.docx"
    document.save(str(path))
    return path


def _redact(tmp_path: Path):  # noqa: ANN202  (test helper)
    source = _contract(tmp_path)
    document = docx_parser.parse(source)
    masks, mapping = mask_units(document, detect(document.text, config=STRUCTURED))
    out = tmp_path / "out.docx"
    notes = docx_parser.write_redacted(source, document, masks, out)
    return document, masks, mapping, out, notes


def test_paragraphs_of_every_story_part_are_units(tmp_path: Path) -> None:
    document = docx_parser.parse(_contract(tmp_path))
    text = document.text

    assert document.format == "docx"
    for value in (EMAIL, LINKED, DELETED, FIELD, HEADER, FOOTER, NOTE, CARD, IBAN):
        assert value in text, value
    assert f"Mail: {EMAIL} today" in text
    source = document.source_at(text.index(EMAIL))
    assert isinstance(source, ParagraphPosition)
    assert source.part == "/word/document.xml"
    header = document.source_at(text.index(HEADER))
    assert isinstance(header, ParagraphPosition)
    assert "header" in header.part
    assert AUTHOR not in text  # metadata is not content


def test_redacted_copy_has_placeholders_and_keeps_run_formatting(tmp_path: Path) -> None:
    *_, out, notes = _redact(tmp_path)
    copy = docx.Document(str(out))
    body = "\n".join(p.text for p in copy.paragraphs)

    split = next(p for p in copy.paragraphs if p.text.startswith("Mail: "))
    assert split.text == "Mail: [EMAIL_1] today"
    assert split.runs[1].bold  # the placeholder took its region's first run
    assert split.runs[1].text == "[EMAIL_1]"
    assert "Write to [EMAIL_2]" in body
    assert copy.sections[0].header.paragraphs[0].text.startswith("Contact [EMAIL_")
    assert "[CREDIT_CARD_1]" in copy.tables[0].cell(0, 0).text
    assert not copy.inline_shapes
    assert copy.core_properties.author == ""
    assert copy.core_properties.title == ""
    assert "removed 1 drawing or embedded object" in notes


def test_no_value_survives_in_any_zip_member(tmp_path: Path) -> None:
    *_, out, _ = _redact(tmp_path)
    with zipfile.ZipFile(out) as archive:
        names = archive.namelist()
        everything = b"".join(archive.read(name) for name in names).decode("utf-8", "replace")
    assert not any(name.startswith("word/media/") for name in names)
    assert "docProps/app.xml" not in names
    for value in VALUES:
        assert value not in everything, value


def test_redacted_copy_reparses_to_the_masked_text_and_restores(tmp_path: Path) -> None:
    document, masks, mapping, out, _ = _redact(tmp_path)
    again = docx_parser.parse(out)
    assert again.text == masked_text(document, masks)
    assert rehydrate(again.text, mapping) == (document.text, [])


FOOTNOTE = "footnote.mail@example.org"


def test_footnotes_held_as_raw_bytes_are_masked_too(tmp_path: Path) -> None:
    from docx.opc.packuri import PackURI  # noqa: PLC0415
    from docx.opc.part import Part  # noqa: PLC0415

    document = docx.Document()
    document.add_paragraph("Body")
    xml = (
        f'<w:footnotes {nsdecls("w")}><w:footnote w:id="1"><w:p><w:r>'
        f"<w:t>See {FOOTNOTE}</w:t></w:r></w:p></w:footnote></w:footnotes>"
    ).encode()
    content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"
    part = Part(PackURI("/word/footnotes.xml"), content_type, xml, document.part.package)
    document.part.relate_to(part, RT.FOOTNOTES)
    source = tmp_path / "notes.docx"
    document.save(str(source))

    parsed = docx_parser.parse(source)
    assert f"See {FOOTNOTE}" in parsed.text
    masks, _ = mask_units(parsed, detect(parsed.text, config=STRUCTURED))
    out = tmp_path / "out.docx"
    docx_parser.write_redacted(source, parsed, masks, out)
    with zipfile.ZipFile(out) as archive:
        assert b"See [EMAIL_1]" in archive.read("word/footnotes.xml")
        assert FOOTNOTE.encode() not in b"".join(archive.read(n) for n in archive.namelist())
