"""A minimal text-only PDF 1.4 writer, standard library only (ADR 0022 point 8).

reportlab passed the license check but runs `~/.reportlab_mods` and settings
modules from `sys.path` when imported, so the summary uses this instead: Courier
(a standard font, nothing embedded), printable ASCII, A4, uncompressed pages.
"""

from collections.abc import Sequence

LINES_PER_PAGE = 64
MAX_LINE = 90
_PAGE_WIDTH, _PAGE_HEIGHT = 595, 842  # A4 in points
_MARGIN = 50
_FONT_SIZE = 9
_LEADING = 11


def _printable(text: str) -> str:
    return "".join(c if " " <= c <= "~" else "?" for c in text)


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _wrapped(lines: Sequence[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        clean = _printable(line)
        out.extend([clean[i : i + MAX_LINE] for i in range(0, len(clean), MAX_LINE)] or [""])
    return out


def _page_stream(lines: Sequence[str]) -> bytes:
    top = _PAGE_HEIGHT - _MARGIN
    parts = [f"BT\n/F1 {_FONT_SIZE} Tf\n{_LEADING} TL\n{_MARGIN} {top} Td"]
    parts.extend(f"({_escape(line)}) Tj T*" for line in lines)
    parts.append("ET\n")
    return "\n".join(parts).encode("ascii")


def text_pdf(lines: Sequence[str], *, title: str) -> bytes:
    """One PDF with `lines` set in Courier, LINES_PER_PAGE to a page."""
    flowed = _wrapped(lines)
    pages = [flowed[i : i + LINES_PER_PAGE] for i in range(0, len(flowed), LINES_PER_PAGE)]
    pages = pages or [[]]
    # 1 catalog, 2 page tree, 3 font, 4 info, then a page and its content per page.
    page_ids = [5 + 2 * i for i in range(len(pages))]
    kids = " ".join(f"{n} 0 R" for n in page_ids)
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode("ascii"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>",
        f"<< /Title ({_escape(_printable(title))}) /Producer (SafeGuard Gateway) >>".encode(
            "ascii"
        ),
    ]
    for page_id, page_lines in zip(page_ids, pages, strict=True):
        stream = _page_stream(page_lines)
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_PAGE_WIDTH} {_PAGE_HEIGHT}] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {page_id + 1} 0 R >>".encode("ascii")
        )
        objects.append(
            f"<< /Length {len(stream)} >>\nstream\n".encode("ascii") + stream + b"endstream"
        )

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")  # the comment marks the file as binary
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii")
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info 4 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n"
    ).encode("ascii")
    return bytes(out)
