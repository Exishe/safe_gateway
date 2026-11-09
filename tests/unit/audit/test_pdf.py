import re
import shutil
import subprocess
from pathlib import Path

import pytest

from gateway.audit.pdf import text_pdf

PDFTOTEXT = shutil.which("pdftotext")


def xref_offsets(data: bytes) -> list[int]:
    found = re.search(rb"startxref\n(\d+)\n%%EOF", data)
    assert found is not None
    start = int(found.group(1))
    assert data[start:].startswith(b"xref\n")
    header = re.match(rb"xref\n0 (\d+)\n", data[start:])
    assert header is not None
    count = int(header.group(1))
    table = data[start + header.end() : start + header.end() + 20 * count]
    entries = [table[i : i + 20] for i in range(0, len(table), 20)]
    assert entries[0] == b"0000000000 65535 f \n"  # pragma: allowlist secret  (PDF syntax)
    assert all(len(e) == 20 and e.endswith(b" n \n") for e in entries[1:])
    return [int(e[:10]) for e in entries[1:]]


def page_count(data: bytes) -> int:
    return len(re.findall(rb"/Type /Page\b", data))


def test_a_pdf_has_a_header_trailer_and_exact_xref_offsets() -> None:
    data = text_pdf(["hello", "world"], title="Audit summary")
    assert data.startswith(b"%PDF-1.4\n")
    assert data.endswith(b"%%EOF\n")
    for number, offset in enumerate(xref_offsets(data), start=1):
        assert data[offset:].startswith(f"{number} 0 obj".encode())


def test_lines_flow_onto_new_pages() -> None:
    assert page_count(text_pdf([f"line {n}" for n in range(130)], title="t")) == 3
    assert page_count(text_pdf([], title="t")) == 1


def test_special_characters_are_escaped_and_non_ascii_replaced() -> None:
    data = text_pdf(["a (b) c\\d", "café"], title="t")
    assert b"(a \\(b\\) c\\\\d) Tj" in data
    assert b"(caf?) Tj" in data


def test_long_lines_wrap() -> None:
    data = text_pdf(["x" * 200], title="t")
    assert data.count(b"(" + b"x" * 90 + b") Tj") == 2
    assert b"(" + b"x" * 20 + b") Tj" in data


@pytest.mark.skipif(PDFTOTEXT is None, reason="pdftotext (poppler) is not installed")
def test_a_pdf_reader_extracts_every_line(tmp_path: Path) -> None:
    lines = ["SafeGuard Gateway audit export", "Verification: OK, 3 rows", "a (b) c"]
    path = tmp_path / "summary.pdf"
    path.write_bytes(text_pdf(lines, title="Audit summary"))
    assert PDFTOTEXT is not None
    result = subprocess.run(
        [PDFTOTEXT, "-layout", str(path), "-"], capture_output=True, text=True, check=True
    )
    for line in lines:
        assert line in result.stdout
