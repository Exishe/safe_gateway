"""Generated-corpus round trip for every document format (Phase 3 exit criterion).

Each document is built with Faker (seeded) and the format's own library; no real
document is read or committed. Values are structured identifiers, which must be
masked every time (hard rule 4); names are not asserted here (no model in tests).
"""

import email
import email.policy
import io
import zipfile
from collections.abc import Callable
from email.message import EmailMessage
from pathlib import Path

import docx
import openpyxl
import pytest
from faker import Faker
from pypdf import PdfReader
from pypdf.generic import StreamObject

from gateway.engine.detect import EngineConfig, detect
from gateway.engine.rehydrate import rehydrate
from gateway.parsers import formats, pdf
from gateway.parsers.redact import mask_units, masked_text

DOCUMENTS_PER_FORMAT = 20
STRUCTURED = EngineConfig(ner="off")


# Card types for the generated corpus. Faker's "maestro" is left out: some of its
# prefixes (0604) are not Maestro IINs. 12-digit Maestro numbers with a real IIN are
# covered by the test at the end of this file and the recognizer's unit tests.
CARD_TYPES = ("visa16", "visa13", "visa19", "mastercard", "amex", "discover", "jcb16")


def _values(fake: Faker) -> list[str]:
    card = fake.credit_card_number(card_type=fake.random_element(CARD_TYPES))
    return [fake.email(), card, fake.iban()]


def _lines(fake: Faker, values: list[str]) -> list[str]:
    lines = [fake.sentence(nb_words=6)]
    lines.extend(f"{fake.sentence(nb_words=4)[:-1]} {value} {fake.word()}." for value in values)
    return lines


def _txt(path: Path, lines: list[str]) -> None:
    path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")


def _docx(path: Path, lines: list[str]) -> None:
    document = docx.Document()
    for line in lines[:2]:
        paragraph = document.add_paragraph(line[:10])
        paragraph.add_run(line[10:]).italic = True  # runs split inside values sometimes
    table = document.add_table(rows=len(lines) - 2, cols=1)
    for row, line in enumerate(lines[2:]):
        table.cell(row, 0).text = line
    document.save(str(path))


def _xlsx(path: Path, lines: list[str]) -> None:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    assert sheet is not None
    for line in lines:
        sheet.append(line.split(" ", 2))
    workbook.save(path)


def _eml(path: Path, lines: list[str]) -> None:
    message = EmailMessage()
    message["From"] = lines[1].split()[-2]
    message["Subject"] = lines[0]
    message.set_content("\n".join(lines) + "\n")
    message.add_alternative(
        "<html><body>" + "".join(f"<p>{line}</p>" for line in lines) + "</body></html>",
        subtype="html",
    )
    path.write_bytes(message.as_bytes())


def _pdf(path: Path, lines: list[str]) -> None:
    placed = [pdf.Line(line, 50, 780 - 20 * i, 10) for i, line in enumerate(lines)]
    pdf.write_pdf([pdf.Page(595, 842, placed)], path)


BUILDERS: dict[str, Callable[[Path, list[str]], None]] = {
    "txt": _txt,
    "docx": _docx,
    "xlsx": _xlsx,
    "eml": _eml,
    "pdf": _pdf,
}


def _raw_text(path: Path) -> str:
    """Everything stored in the file, decoded as far as the format allows."""
    data = path.read_bytes()
    pieces = [data.decode("utf-8", "replace")]
    if path.suffix in {".docx", ".xlsx"}:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            pieces += [archive.read(n).decode("utf-8", "replace") for n in archive.namelist()]
    elif path.suffix == ".eml":
        message = email.message_from_bytes(data, policy=email.policy.default)
        pieces += [str(value) for _, value in message.items()]
        pieces += [str(part.get_content()) for part in message.walk() if not part.is_multipart()]
    elif path.suffix == ".pdf":
        reader = PdfReader(path)
        numbers = [n for objects in reader.xref.values() for n in objects]
        for number in numbers:
            item = reader.get_object(number)
            if isinstance(item, StreamObject):
                stream = item.get_data()
                pieces += [
                    stream.decode("latin-1"),
                    bytes.fromhex(_hex_runs(stream)).decode("cp1252", "replace"),
                ]
    return "\n".join(pieces)


def _hex_runs(stream: bytes) -> str:
    """The bytes of every <...> string in a content stream, joined (our text is hex)."""
    runs = []
    text = stream.decode("latin-1")
    start = text.find("<")
    while start != -1:
        end = text.find(">", start)
        candidate = text[start + 1 : end]
        if candidate and all(c in "0123456789ABCDEFabcdef" for c in candidate):
            runs.append(candidate if len(candidate) % 2 == 0 else "")
        start = text.find("<", end)
    return "".join(runs)


@pytest.mark.parametrize("kind", sorted(BUILDERS))
def test_generated_documents_round_trip_without_leaking(kind: str, tmp_path: Path) -> None:
    fake = Faker("en_US")
    Faker.seed(20261109)
    for number in range(DOCUMENTS_PER_FORMAT):
        values = _values(fake)
        source = tmp_path / f"doc{number}.{kind}"
        BUILDERS[kind](source, _lines(fake, values))
        handler = formats.SUFFIXES[source.suffix]

        document = handler.parse(source)
        for value in values:
            assert value in document.text, (kind, number, value)
        masks, mapping = mask_units(document, detect(document.text, config=STRUCTURED))
        out = tmp_path / f"out{number}.{kind}"
        handler.write(source, document, masks, out)

        again = handler.parse(out)
        assert again.text == masked_text(document, masks), (kind, number)
        assert rehydrate(again.text, mapping) == (document.text, []), (kind, number)
        raw = _raw_text(out)
        for value in values:
            assert value not in again.text, (kind, number, value)
            assert value not in raw, (kind, number, value)


def test_twelve_digit_maestro_numbers_are_detected() -> None:
    value = "676343636590"  # Faker maestro, Luhn-valid  # pragma: allowlist secret
    spans = detect(f"card {value} on file", config=STRUCTURED)
    assert any(span.start == 5 and span.end == 5 + len(value) for span in spans)
