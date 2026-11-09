# 0017. Document parsers: one walk per format, text units and source positions

- Status: Accepted
- Date: 2026-11-09
- Refines: ADR 0006 point 1 (rules for `gateway/parsers/`)

## Context

Phase 3 reads PDF (text and scanned), DOCX, XLSX, TXT and EML, and writes a
redacted copy in the same format (ADR 0018). The engine works on one string, so
each parser must turn a file into text, remember where every character came
from, and let the writer put each placeholder back in the right place. The
outline named pypdf for the PDF text layer, pdfplumber for tables and
pytesseract for OCR. The owner delegated these decisions and was not available.

Facts checked before deciding (2026-11-09, scratch environment):

- Every pdfplumber release from 0.10 on requires pypdfium2. Its metadata reads
  `License: BSD-3-Clause, Apache-2.0, dependency licenses` (PDFium ships with
  its own third-party licenses). `tools/check_licenses.py` classifies it as
  `review`. The instruction for this phase was not to add an exception, so
  pdfplumber is not used. pypdfium2 is also the only permissively licensed PDF
  renderer we know of (poppler and Ghostscript are GPL/AGPL, MuPDF is AGPL).
- pdfminer.six (MIT; the engine pdfplumber is built on) reports each text line
  with its text, bounding box, baseline origin and font size. Table cells come
  out as separate lines because the gap between cells is wider than a word gap.
- pypdf (BSD-3-Clause) reads, decrypts-or-refuses, extracts embedded images as
  Pillow images, and writes new PDFs.
- pytesseract (Apache-2.0) runs the `tesseract` binary in a subprocess.

## Decision

1. **`Document(format, text, segments, metadata)`.** `text` is what the engine
   reads. Each `Segment(start, end, source)` covers the text of one source unit,
   and `source` says where that unit lives:

   | Format | Unit | Source position |
   |--------|------|-----------------|
   | txt | one line, without its line break | `TextPosition(line)` |
   | docx | one paragraph (`w:p`) of a story part | `ParagraphPosition(part, index)` |
   | xlsx | one cell value, cell comment or sheet title | `CellPosition(sheet, cell, field)` |
   | eml | one From/To/Cc/Subject header, or one text part | `EmailPosition(field, index)` |
   | pdf | one text line, or one OCR line | `PagePosition(page, line, x, y, size, ocr)` |

   Text between segments is only separators and labels we add (`\n`, `\t`,
   `From: `), never content. `Document.source_at(offset)` maps an offset back to
   its source. `metadata` is structural (page count, OCR pages, items removed)
   and never holds document content.
2. **One walk per format, shared by parse and write.** Each module has a single
   generator that visits the units in a fixed order. `parse` reads the text from
   it; the writer walks the same generator over the re-opened file and writes the
   masked text back. The writer refuses (DocumentError) if the unit count
   differs, so a file changed in between is never half-written.
3. **Spans are clipped to units, and each unit is masked with one shared
   Mapping, in document order.** `mask()` gains a public `replacements()` that
   returns each `(start, end, placeholder)`; `mask()` itself is unchanged in
   behaviour. Masking unit by unit equals masking the whole text, because no
   placeholder-shaped token can cross a `\n` or `\t` separator and a unit edge
   reads like a text edge (ADR 0014 point 4). A span that crosses a unit
   boundary becomes one placeholder per unit: never unmasked, at worst split.
4. **Libraries.**
   - PDF: pypdf opens the file (encrypted PDFs are refused), decides whether a
     page has a text layer (`extract_text()` finds a non-space character), and
     extracts page images for OCR. pdfminer.six gives the lines with positions,
     which are the units; this replaces pdfplumber for tables (cells become their
     own lines). If pypdf finds text but pdfminer finds no line, the page is
     refused rather than silently dropped.
   - DOCX: python-docx opens the package and gives every part's XML. Units are
     the `w:p` elements of the body, headers, footers, footnotes, endnotes and
     comments, read at the level of `w:t`, `w:delText` and `w:instrText`, so
     tracked deletions and field codes are masked too. Text inside drawings,
     pictures and embedded objects is not read; ADR 0018 removes those.
   - XLSX: openpyxl with `data_only=True`, so a formula cell is read as its
     cached value. Every worksheet, row by row: cells joined by `\t`, rows by
     `\n`. Dates are written in ISO form, integral floats without `.0`.
   - EML: stdlib `email` with `policy.default`. Header values are read raw and
     decoded as unstructured text (an address parser rewrites values it cannot
     parse). HTML parts become text with a stdlib `HTMLParser` (block tags break
     lines, `script` and `style` are skipped, `pre` is kept as written).
     Attachments are not read.
   - TXT: UTF-8; a leading BOM is removed and remembered. Anything else is
     refused, as `safeguard mask` already does.
5. **OCR lives in `gateway/parsers/ocr.py`, the one module allowed to import
   pytesseract.** OCR runs only for a page with no text layer, on each embedded
   image of that page, at the image's position on the page (from pdfminer, or the
   whole page when it cannot be matched). Languages are the installed subset of
   `eng`, `rus` and `hye`. When Tesseract is not installed the page is refused
   with that reason: an unread page is never passed off as an empty one.
6. **`tools/check_no_egress.py` gives `gateway/parsers/` the engine rules** (no
   network, no `subprocess`, no dynamic code), and forbids `pytesseract`
   everywhere under `gateway/` except `gateway/parsers/ocr.py`. Phase 0 let
   parsers start processes; that is withdrawn, and the only process left is
   Tesseract, reached through one named file. The checker is not loosened in
   any other way.
7. **The CLI dispatches on suffix:** `.pdf`, `.docx`, `.xlsx`, `.eml`, `.txt`
   go through `gateway/parsers/formats.py`. Any other suffix keeps the plain
   UTF-8 text path. The dispatch in `cli.py` is a few lines.

## Consequences

- A table in a PDF is read as one line per cell. There is no row and column
  structure until a table extractor passes the license check.
- Clipping can split one value into two placeholders when it crosses a cell or
  line boundary. That costs readability, never privacy.
- Scanned PDFs need Tesseract (`apt-get install tesseract-ocr tesseract-ocr-rus`).
  Without it, those PDFs fail with exit code 2 and a reason.
- OCR quality is measured, not asserted (hard rule 4). Text that is drawn as
  vector outlines, or sits in an image on a page that also has a text layer, is
  not read.
- If pypdfium2 passes a license review later, pdfplumber's table extractor and
  a rendered redaction (ADR 0018) become available. That change needs a new ADR.
