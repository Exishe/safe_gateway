# Phase 3: Document parsers and redacted copies

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `safeguard mask contract.docx -o out.docx` (and `.pdf`, `.xlsx`, `.eml`, `.txt`) writes a copy in the same format where every detected value is its placeholder from one session mapping, and none of our parsers can find the original value in the copy.

**Architecture:** `gateway/parsers/document.py` defines `Document(format, text, segments, metadata)`, where each segment is one source unit (line, paragraph, cell, header, PDF line) with a typed source position. Each format module has one unit walker used by both `parse()` and `write_redacted()`, so the order is identical by construction (ADR 0017). `gateway/parsers/redact.py` clips spans to units and masks each unit with one `Mapping` through the engine's new public `replacements()`. Writers follow ADR 0018: masked text in place, unreadable content dropped, PDF rebuilt rather than edited. `gateway/parsers/formats.py` maps suffixes to handlers and runs parse, detect, mask and write; `cli.py` only dispatches to it.

**Tech Stack:** pypdf (BSD-3-Clause), pdfminer.six (MIT), python-docx (MIT), openpyxl (MIT), pytesseract (Apache-2.0), Pillow (MIT-CMU), stdlib `email` and `html.parser`. Faker for generated fixtures. pdfplumber is **not** used: its required pypdfium2 is `review` under the license policy (ADR 0017).

**Spec:** `docs/adr/0017`, `0018`; `0001`, `0014` for placeholders; `0006`, `0010` for the egress rules.

## Global Constraints

- `gateway/parsers/` gets the engine's egress rules: no network, no `subprocess`, no dynamic code. Only `gateway/parsers/ocr.py` may import `pytesseract` (ADR 0017 point 6).
- No real documents are committed. Every fixture is generated in the test with Faker and the format library; literal fake identifiers carry `# pragma: allowlist secret`.
- Tests not marked `models` run with no NER model, so the values asserted on are structured identifiers (email, card, SSN, IBAN) or gazetteer entries.
- `Document.metadata` never holds document content.
- A writer never leaves a partial output: temporary file in the target directory, renamed on success.
- Fail closed: an encrypted PDF, an unreadable page, Tesseract missing for a scanned page, or a unit-count mismatch raises `DocumentError`, which the CLI turns into exit code 2.

## Review Focus

1. No original value survives in any output, checked through our own parsers and through raw bytes: zip members of DOCX and XLSX, decoded MIME parts of EML, decompressed streams of PDF. Tests in Tasks 5 to 10.
2. DOCX: a value split across runs, inside a hyperlink, in a tracked deletion, in a field code, in a header, footer or comment is masked. Drawings and images are gone from the package. Task 7.
3. PDF: the rebuilt copy has no image, annotation or metadata object, and keeps text outside cp1252 extractable. Task 8.
4. The egress checker rejects `subprocess` and `pytesseract` in `gateway/parsers/*.py` other than `ocr.py`. Task 1.
5. The CLI keeps the old behaviour for other suffixes, and `.pdf`, `.docx`, `.xlsx`, `.eml` without `-o` exit 2. Task 10.

---

### Task 0: Dependencies

- [ ] `uv add pypdf pdfminer.six python-docx openpyxl pytesseract pillow`; `uv run python -m tools.check_licenses` passes; commit `chore(deps): document parser libraries`.

### Task 1: Egress rules for parsers and the OCR allowance

**Files:** modify `tools/check_no_egress.py`, `tests/unit/tools/test_check_no_egress.py`.

- [ ] Tests: `import subprocess`, `import pytesseract`, `eval("1")` are rejected in `gateway/parsers/pdf.py`; `import pytesseract` is allowed in `gateway/parsers/ocr.py`, but `import subprocess` and `import httpx` are not; `import pytesseract` is rejected in `gateway/cli.py` and `gateway/engine/detect.py`. The old test "parsers may run subprocesses" is replaced.
- [ ] Run, FAIL; implement `PARSER_RULES` and `OCR_RULES`; PASS; commit `feat(tools): parsers get engine egress rules, OCR in one named module`.

### Task 2: `replacements()` in the engine

**Files:** modify `gateway/engine/mask.py`; test `tests/unit/engine/test_mask.py`.

**Interfaces (produces):** `Replacement(start, end, placeholder)` (frozen); `replacements(text, spans, mapping) -> list[Replacement]`; `apply_replacements(text, replacements) -> str`. `mask()` is rebuilt on them, with the same output.

- [ ] Tests: replacements for an email and a card give the placeholders and offsets; applying them equals `mask()`; a placeholder-shaped token is a LITERAL replacement.
- [ ] Run, FAIL; implement; PASS; commit `feat(engine): replacements() exposes where mask puts each placeholder`.

### Task 3: `Document` and unit masking

**Files:** create `gateway/parsers/document.py`, `gateway/parsers/redact.py`; tests `tests/unit/parsers/test_document.py`, `tests/unit/parsers/test_redact.py`.

**Interfaces (produces):** `DocumentError(ValueError)`; position types `TextPosition`, `ParagraphPosition`, `CellPosition`, `EmailPosition`, `PagePosition`; `Segment(start, end, source)`; `Document(format, text, segments, metadata)` with `segment_text(i)`, `source_at(offset)`; `DocumentBuilder` (`add(text, source)`, `separate(text)`, `build(format, metadata)`). `UnitMask(text, replacements)`; `mask_units(document, spans, mapping=None) -> tuple[list[UnitMask], Mapping]`; `masked_text(document, masks) -> str`; `distribute(pieces, replacements) -> list[str | None]` (new text per mutable piece, None for a fixed piece that falls inside a replacement).

- [ ] Tests: builder offsets; `source_at` for a unit offset and None for a separator; spans clipped to units; a span crossing a separator gives one placeholder per unit; `masked_text` equals `mask(document.text)` when no span crosses; distribute puts the placeholder in the first mutable piece and empties the rest, drops a fixed piece inside a region, and keeps pieces outside regions.
- [ ] Run, FAIL; implement; PASS; commit `feat(parsers): Document, source positions and unit masking`.

### Task 4: TXT

**Files:** create `gateway/parsers/txt.py`; test `tests/unit/parsers/test_txt.py`.

- [ ] Tests: lines are units with `TextPosition(line)`; CRLF and LF kept; BOM removed in text and restored on write; non-UTF-8 raises DocumentError; write then re-parse equals the masked text.
- [ ] Run, FAIL; implement; PASS; commit `feat(parsers): txt`.

### Task 5: EML

**Files:** create `gateway/parsers/html_text.py`, `gateway/parsers/eml.py`; tests `tests/unit/parsers/test_html_text.py`, `tests/unit/parsers/test_eml.py`.

- [ ] Tests: html to text (blocks, `br`, entities, `script`/`style` skipped, `pre` kept); headers From/To/Cc/Subject (RFC 2047 decoded) and plain and html parts are units; attachments not read; the redacted copy has masked headers, no Received/Message-ID/X- headers, no attachment, html replaced by `<pre>`; no original value in any decoded part or header; re-parse equals the masked text.
- [ ] Run, FAIL; implement; PASS; commit `feat(parsers): eml`.

### Task 6: XLSX

**Files:** create `gateway/parsers/xlsx.py`; test `tests/unit/parsers/test_xlsx.py`.

- [ ] Tests: every sheet's cells, comments and titles are units with `CellPosition(sheet, cell, field)`; numbers and dates as text; formulas read as cached values; redacted copy: masked cells, unchanged numeric cell keeps its type, masked comment, bracket-free sheet title, no hyperlink and no image, properties cleared; no original value in any zip member; re-parse equals the masked text.
- [ ] Run, FAIL; implement; PASS; commit `feat(parsers): xlsx`.

### Task 7: DOCX

**Files:** create `gateway/parsers/docx.py`; test `tests/unit/parsers/test_docx.py`.

- [ ] Tests: paragraphs of body, nested tables, headers, footers, comments are units; a value split over two runs is detected and masked with the first run's formatting; hyperlink text masked and target gone; `w:delText` and `w:instrText` masked; a picture and its media part removed; core properties cleared; no original value in any zip member; re-parse equals the masked text.
- [ ] Run, FAIL; implement; PASS; commit `feat(parsers): docx`.

### Task 8: PDF text layer

**Files:** create `gateway/parsers/pdf.py`; test `tests/unit/parsers/test_pdf.py` (fixtures drawn with pypdf and the standard Helvetica font).

- [ ] Tests: lines with `PagePosition`; two pages; an encrypted PDF raises; the rebuilt copy has the same page count and sizes, the placeholders in pypdf's and pdfminer's text, no original value in any decompressed stream, no `/Annots`, `/XObject` or `/Info` author; Cyrillic text stays extractable.
- [ ] Run, FAIL; implement; PASS; commit `feat(parsers): pdf text layer and rebuilt redacted copy`.

### Task 9: OCR for scanned pages

**Files:** create `gateway/parsers/ocr.py`; modify `gateway/parsers/pdf.py`; test `tests/unit/parsers/test_ocr.py`.

- [ ] Tests (skipped with a clear reason when `tesseract` is not installed): a Pillow-drawn scan saved as PDF parses to text with the email and SSN, its lines marked `ocr=True`; the redacted copy holds the placeholders and no image. Without Tesseract (simulated), a scanned page raises `OcrUnavailable` naming the page.
- [ ] Run, FAIL; implement; PASS; commit `feat(parsers): OCR for pages without a text layer`.

### Task 10: Dispatch and CLI

**Files:** create `gateway/parsers/formats.py`; modify `gateway/cli.py`; tests `tests/unit/parsers/test_formats.py`, `tests/unit/test_cli.py`.

**Interfaces (produces):** `SUFFIXES`; `handles(path) -> bool`; `MaskResult(text, mapping, notes)`; `mask_file(source, output, config) -> MaskResult`.

- [ ] Tests: each suffix dispatches; an unknown suffix is not handled; `safeguard mask x.docx -o out.docx` (and pdf, xlsx, eml) writes the copy and `--json-map` restores the masked text; a document format without `-o` exits 2; an encrypted PDF exits 2 with a reason; `.md` still takes the text path.
- [ ] Run, FAIL; implement; PASS; commit `feat: safeguard mask for pdf, docx, xlsx, eml and txt`.

### Task 11: Generated-corpus round trip

**Files:** create `tests/integration/test_document_corpus.py`.

- [ ] For each format, 20 Faker-generated documents (seeded) with emails, cards, SSNs and IBANs: every value is detected, the copy holds no value (parsers and raw bytes), and `rehydrate(reparse(copy).text, mapping) == document.text` where the format allows exact text (txt, eml, xlsx, docx).
- [ ] Run; commit `test(parsers): generated corpus round trip`.

### Task 12: Close-out

- [ ] ADR 0017, 0018 and the index (written first, with this plan).
- [ ] Full gate: ruff format and check, ty, no-egress, licenses, pytest.
- [ ] CLI round trips on generated samples of each format, re-opened with python-docx, openpyxl, pypdf and `email`.
- [ ] Not in this pass: OCR of images inside DOCX and of attachments, masking nested attachments, a font for visible Cyrillic and Armenian in PDF copies, `leak-hunter` round, tag `v0.3.0`.
