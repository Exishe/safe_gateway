# 0018. Redacted copies: what each format keeps, replaces and drops

- Status: Accepted
- Date: 2026-11-09
- Builds on: ADR 0017

## Context

`safeguard mask FILE -o OUT` writes a copy of a document, in the same format,
with every detected value replaced by its placeholder from the session mapping
(ADR 0001). A copy is only safe if *nothing* in the file still holds the value:
not the visible text, and not a text layer, a tracked deletion, a field code, a
hyperlink target, a cached formula, an image, an attachment or the metadata.
The outline asked how to write PDFs back and what to do with images in DOCX.

## Decision

**Principle: the copy keeps the masked text and the structure needed to read
it. Content we cannot read and mask is not copied, and metadata that names
people is cleared.** Removed items are counted in `Document` metadata, and the
CLI prints the counts. Every writer writes to a temporary file in the output
directory and renames it into place only on success.

1. **TXT:** the masked text, with the original line breaks and the BOM if the
   input had one.
2. **DOCX:** each paragraph's masked text is written back into its own `w:t`,
   `w:delText` and `w:instrText` elements. Text outside a replaced region keeps
   its run and formatting, and a placeholder takes the formatting of the first
   run of its region. Removed: drawings, pictures, embedded objects and text
   boxes (`w:drawing`, `w:pict`, `w:object`, `mc:AlternateContent`).
   Hyperlinks and simple fields are unwrapped to their text, and their targets
   and `w:instr` attributes go with them. Cleared: core properties, document
   variables, the attached template, and the `w:author`/`w:initials` of
   comments and tracked changes (set to `Redacted`). Relationships whose type is
   not on an allowlist (styles, numbering, settings, fonts, theme, headers,
   footers, notes, comments, web settings) are dropped. That drops app and custom
   properties, people, glossary, custom XML, thumbnails and every external
   target. *Embedded images in DOCX (open question of the outline): removed,
   not OCRed, in this phase.*
3. **XLSX:** a changed cell gets its masked text as a string. An unchanged cell
   keeps its value and type. Formulas are flattened to their cached values,
   because a formula can carry a value in a literal and recomputing it after
   masking means nothing. A masked comment gets author `Redacted`. A masked sheet
   title keeps the placeholder without brackets (Excel forbids `[]`), so
   `SSN_1`, which rule 3 of ADR 0003 still restores, falls back to `SheetN`
   when it collides or is too long. Removed: images, charts, pivot tables,
   hyperlinks, data validations, conditional formats, defined names, print
   headers and footers, external links, and document and custom properties.
4. **EML:** From, To, Cc and Subject are written with their masked values as
   unstructured headers. Every other header is dropped, except MIME-Version,
   Content-Type and Content-Transfer-Encoding, on every part. That drops
   Received, Message-ID, Reply-To and X- headers. `text/plain` parts get their
   masked text. A `text/html` part is replaced by the masked text inside
   `<pre>`, which also removes links, attributes and remote images. Attachments
   and non-text parts are removed.
5. **PDF: rebuilt, not edited.** Options considered:
   - *Boxes drawn over the text:* rejected. The text layer still holds the
     value.
   - *Removing glyphs from content streams:* rejected for now. Fonts with
     custom encodings, CID fonts, Form XObjects, `ActualText`, annotations and
     forms each need their own code, and a missed case is a leak.
   - *Render each page, paint boxes, write images:* best fidelity, but it needs
     a renderer. The only permissive one, pypdfium2, is held at license review
     (ADR 0017).
   - *Rebuild (chosen):* each output page is a new page of the source page's
     displayed size. It carries only the masked text of each source line (or
     OCR line), drawn at that line's baseline and font size. Nothing else is
     copied: no content streams, images, fonts, annotations, forms, outlines,
     attachments, JavaScript or metadata. The copy cannot hold a value that is
     not in the masked text, by construction.

   Characters in Windows-1252 are drawn in Helvetica, a standard PDF font that
   needs no embedding. Other characters (Cyrillic, Armenian) go into an
   invisible text layer with a Unicode map. They can be searched, copied and
   read by tools and models, but they are not drawn, because no font with those
   scripts under an allowed license is available offline.

## Consequences

- A redacted PDF looks like a plain transcript laid out where the lines were.
  Graphics, images, colours and fonts are gone, and Cyrillic and Armenian text
  is invisible on screen while still present in the text layer. Better fidelity
  needs either a renderer (pypdfium2 after review) or an embedded font after
  license review. Either needs a new ADR.
- DOCX copies lose images, charts and text boxes. XLSX copies lose formulas,
  charts and pivots. EML copies lose attachments and HTML formatting. These are
  deliberate: each would otherwise need its own reader to be masked.
- Tests prove the guarantee per format. The original values cannot be found by
  any of our parsers in the output, or in the output's raw bytes (zip members,
  decoded MIME parts, decompressed PDF streams). Where the format allows exact
  equality, the re-parsed output equals the masked text.
- This reduces exposure; it does not anonymise. A value the detector missed is
  copied like any other text (hard rule 7).
