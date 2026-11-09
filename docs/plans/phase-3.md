# Phase 3: Document parsers (outline)

> Outline only. Brainstorm, then write the task plan here when the phase starts.

**Goal:** PDF (text and scanned), DOCX, XLSX, TXT and EML in; text plus source positions out; redacted copies written back in the original format.

**Scope**
- `gateway/parsers/`: each parser returns `Document(text, spans_to_source, metadata)`.
- PDF: text layer with pypdf, tables with pdfplumber, OCR with pytesseract when a page has no text layer. No PyMuPDF (AGPL).
- DOCX with python-docx and XLSX with openpyxl, preserving structure. EML with stdlib `email` (headers and body).
- Writers for the redacted copy in each format.

**Exit criteria**
- Round-trip tests on a corpus generated with Faker (no real documents committed).
- Scanned-PDF test skipped with a clear reason when Tesseract is not installed.
- `safeguard mask contract.docx -o out.docx` verified by opening the output. Tag `v0.3.0`.

**Open questions:** PDF write-back strategy (redact the text layer and overlay boxes, or flatten to an image); how to treat embedded images in DOCX.
