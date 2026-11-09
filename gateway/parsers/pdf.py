"""PDF: text lines with positions in; a rebuilt, text-only redacted copy out.

pypdf opens the file, refuses encryption, decides which pages have a text layer and
writes the copy; pdfminer.six gives each line's text, baseline and size (ADR 0017).
The copy is rebuilt, never edited: new pages that carry only the masked lines, so it
cannot hold a value that is not in the masked text (ADR 0018 point 5).
"""

import unicodedata
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pdfminer.high_level import extract_pages
from pdfminer.layout import LAParams, LTChar, LTTextLine
from pdfminer.pdfparser import PDFSyntaxError
from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
    TextStringObject,
)

from gateway.parsers.document import Document, DocumentBuilder, DocumentError, PagePosition
from gateway.parsers.output import atomic_output
from gateway.parsers.redact import UnitMask

# all_texts: text inside Form XObjects is grouped into lines too, not left loose.
_LAYOUT = LAParams(all_texts=True)
_UNICODE_FONT = "SafeGuardUnicode"
_UNICODE_WIDTH = 500  # thousandths of an em, for every character of the Unicode layer


@dataclass(frozen=True)
class Line:
    text: str
    x: float  # baseline origin, PDF points
    y: float
    size: float


@dataclass(frozen=True)
class Page:
    width: float
    height: float
    lines: Sequence[Line]


# ------------------------------------------------------------------ read


def _reader(path: Path) -> PdfReader:
    try:
        reader = PdfReader(path)
        if reader.is_encrypted:
            msg = f"{path.name} is encrypted; decrypt it first"
            raise DocumentError(msg)
        _ = len(reader.pages)
    except (PdfReadError, OSError, ValueError) as error:
        if isinstance(error, DocumentError):
            raise
        msg = f"{path.name} is not a readable PDF ({error})"
        raise DocumentError(msg) from error
    return reader


def _text_lines(item: Any) -> Iterator[LTTextLine]:  # noqa: ANN401  (pdfminer layout tree)
    if isinstance(item, LTTextLine):
        yield item
        return
    for child in getattr(item, "_objs", ()):
        yield from _text_lines(child)


def _line(line: LTTextLine) -> Line | None:
    text = line.get_text().rstrip("\n")
    chars = [c for c in line if isinstance(c, LTChar)]
    if not text.strip() or not chars:
        return None
    first = chars[0]
    return Line(text, first.matrix[4], first.matrix[5], round(first.size, 2))


def _visible(text: str) -> Counter[str]:
    normal = unicodedata.normalize("NFKC", text)
    return Counter(c for c in normal if not c.isspace() and unicodedata.category(c)[0] != "C")


def _page_lines(number: int, layout: Any, layer: str) -> list[Line]:  # noqa: ANN401
    lines = [line for line in map(_line, _text_lines(layout)) if line is not None]
    # Fail closed: text pypdf sees that pdfminer cannot place would be lost from the copy
    # and never masked.
    missing = _visible(layer) - _visible("".join(line.text for line in lines))
    if missing:
        msg = f"page {number}: {sum(missing.values())} characters of text cannot be located"
        raise DocumentError(msg)
    return lines


def _no_text_layer(number: int) -> list[Line]:
    msg = f"page {number} has no text layer"
    raise DocumentError(msg)


def _pages(path: Path) -> Iterator[tuple[int, list[Line], bool]]:
    reader = _reader(path)
    try:
        layouts = list(extract_pages(path, laparams=_LAYOUT))
    except (PDFSyntaxError, ValueError, TypeError, KeyError) as error:
        msg = f"{path.name}: the text layer cannot be read ({error})"
        raise DocumentError(msg) from error
    for number, (page, layout) in enumerate(zip(reader.pages, layouts, strict=True), start=1):
        layer = page.extract_text() or ""
        if layer.strip():
            yield number, _page_lines(number, layout, layer), False
        else:
            yield number, _no_text_layer(number), True


def parse(path: Path) -> Document:
    builder = DocumentBuilder()
    pages = 0
    ocr_pages = 0
    for number, lines, ocr in _pages(path):
        pages += 1
        ocr_pages += int(ocr)
        if number > 1:
            builder.separate("\n\n")
        for index, line in enumerate(lines):
            if index:
                builder.separate("\n")
            builder.add(line.text, PagePosition(number, index, line.x, line.y, line.size, ocr))
    return builder.build("pdf", {"pages": pages, "ocr_pages": ocr_pages})


# ------------------------------------------------------------------ write


def _helvetica() -> DictionaryObject:
    return DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
            NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
        }
    )


def _to_unicode(codes: dict[str, int]) -> DecodedStreamObject:
    entries = [
        f"<{code:04X}> <{char.encode('utf-16-be').hex().upper()}>" for char, code in codes.items()
    ]
    blocks = [entries[i : i + 100] for i in range(0, len(entries), 100)]
    body = "".join(
        f"{len(block)} beginbfchar\n" + "\n".join(block) + "\nendbfchar\n" for block in blocks
    )
    cmap = (
        "/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n"
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
        "/CMapName /SafeGuard-UCS def /CMapType 2 def\n"
        "1 begincodespacerange <0000> <FFFF> endcodespacerange\n"
        f"{body}endcmap CMapName currentdict /CMap defineresource pop end end\n"
    )
    stream = DecodedStreamObject()
    stream.set_data(cmap.encode("ascii"))
    return stream


def _unicode_font(writer: PdfWriter, codes: dict[str, int]) -> Any:  # noqa: ANN401
    """A Type0 font with no glyphs: text in it is never drawn (render mode 3), but its
    ToUnicode map makes it searchable and extractable (ADR 0018 point 5)."""
    descriptor = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/FontDescriptor"),
            NameObject("/FontName"): NameObject(f"/{_UNICODE_FONT}"),
            NameObject("/Flags"): NumberObject(32),
            NameObject("/FontBBox"): ArrayObject(
                [NumberObject(0), NumberObject(-200), NumberObject(1000), NumberObject(800)]
            ),
            NameObject("/ItalicAngle"): NumberObject(0),
            NameObject("/Ascent"): NumberObject(800),
            NameObject("/Descent"): NumberObject(-200),
            NameObject("/CapHeight"): NumberObject(700),
            NameObject("/StemV"): NumberObject(80),
        }
    )
    descendant = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/CIDFontType2"),
            NameObject("/BaseFont"): NameObject(f"/{_UNICODE_FONT}"),
            NameObject("/CIDSystemInfo"): DictionaryObject(
                {
                    NameObject("/Registry"): TextStringObject("Adobe"),
                    NameObject("/Ordering"): TextStringObject("Identity"),
                    NameObject("/Supplement"): NumberObject(0),
                }
            ),
            NameObject("/FontDescriptor"): writer._add_object(descriptor),  # noqa: SLF001
            NameObject("/DW"): NumberObject(_UNICODE_WIDTH),
            NameObject("/CIDToGIDMap"): NameObject("/Identity"),
        }
    )
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type0"),
            NameObject("/BaseFont"): NameObject(f"/{_UNICODE_FONT}"),
            NameObject("/Encoding"): NameObject("/Identity-H"),
            NameObject("/DescendantFonts"): ArrayObject(
                [writer._add_object(descendant)]  # noqa: SLF001
            ),
            NameObject("/ToUnicode"): writer._add_object(_to_unicode(codes)),  # noqa: SLF001
        }
    )
    return writer._add_object(font)  # noqa: SLF001


def _runs(text: str) -> Iterator[tuple[bool, str]]:
    """(drawn in Helvetica, run) pieces: Windows-1252 characters, or the rest."""
    current: list[str] = []
    drawn = True
    for char in text:
        try:
            char.encode("cp1252")
            fits = True
        except UnicodeEncodeError:
            fits = False
        if current and fits != drawn:
            yield drawn, "".join(current)
            current = []
        drawn = fits
        current.append(char)
    if current:
        yield drawn, "".join(current)


def _line_operators(line: Line, codes: dict[str, int]) -> str:
    size = max(line.size, 1.0)
    out = [f"BT 1 0 0 1 {line.x:.2f} {line.y:.2f} Tm"]
    for drawn, run in _runs(line.text):
        if drawn:
            out.append(f"0 Tr /F1 {size:.2f} Tf <{run.encode('cp1252').hex().upper()}> Tj")
        else:
            hex_codes = "".join(f"{codes.setdefault(c, len(codes) + 1):04X}" for c in run)
            out.append(f"3 Tr /F2 {size:.2f} Tf <{hex_codes}> Tj")
    out.append("ET")
    return " ".join(out)


def write_pdf(pages: Sequence[Page], output: Path) -> None:
    """A new PDF that holds only these lines of text, and nothing else."""
    writer = PdfWriter()
    codes: dict[str, int] = {}
    contents = []
    for page in pages:
        new = writer.add_blank_page(width=page.width, height=page.height)
        stream = DecodedStreamObject()
        stream.set_data(
            "\n".join(_line_operators(line, codes) for line in page.lines).encode("ascii")
        )
        contents.append((new, stream))
    fonts = DictionaryObject(
        {
            NameObject("/F1"): writer._add_object(_helvetica()),  # noqa: SLF001
            NameObject("/F2"): _unicode_font(writer, codes),
        }
    )
    for new, stream in contents:
        new[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): fonts})
        new[NameObject("/Contents")] = writer._add_object(stream)  # noqa: SLF001
    with output.open("wb") as handle:
        writer.write(handle)


def _page_sizes(path: Path) -> list[tuple[float, float]]:
    """Each page's displayed size: the coordinates pdfminer reports positions in."""
    sizes = []
    for page in _reader(path).pages:
        width, height = float(page.mediabox.width), float(page.mediabox.height)
        sizes.append((height, width) if page.rotation % 180 else (width, height))
    return sizes


def write_redacted(
    source: Path, document: Document, masks: Sequence[UnitMask], output: Path
) -> list[str]:
    sizes = _page_sizes(source)
    if len(sizes) != document.metadata.get("pages") or len(masks) != len(document.segments):
        msg = f"{source.name} changed since it was read; mask it again"
        raise DocumentError(msg)
    lines: list[list[Line]] = [[] for _ in sizes]
    for segment, mask in zip(document.segments, masks, strict=True):
        position = segment.source
        if not isinstance(position, PagePosition):  # pragma: no cover  (parse makes these)
            msg = "a PDF document holds a unit without a page position"
            raise DocumentError(msg)
        lines[position.page - 1].append(Line(mask.text, position.x, position.y, position.size))
    pages = [Page(w, h, page_lines) for (w, h), page_lines in zip(sizes, lines, strict=True)]
    with atomic_output(output) as temporary:
        write_pdf(pages, temporary)
    return [f"rebuilt {len(pages)} pages as text only (ADR 0018)"]
