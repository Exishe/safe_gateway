# 0018. Redacted copies: what each format keeps, replaces and drops

- Status: Accepted
- Date: 2026-11-09
- Builds on: ADR 0017
- Amended: 2026-11-09, after the Phase 3 security review (findings I1, I2, M4,
  M5, M6): DOCX copies are cut to an allowlist; XLSX copies lose tables,
  filter criteria, sort state, scenarios and custom number formats; EML
  multipart types are rebuilt; zip size caps; the claim in Consequences.

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
2. **DOCX: an allowlist, not a denylist.** The review found text a reader or a
   tool can see in places the first writer did not know about: Office Math,
   legacy form-field defaults and help text, bookmark names, content-control
   aliases and tags, mail-merge settings, custom style names. A denylist fails
   open on the next construct nobody listed, so each kept part is now cut to
   the elements and attributes in `gateway/parsers/docx_allow.py`:
   - Story parts (body, headers, footers, notes, comments): paragraphs, runs,
     text, tables, sections, tracked-change markers and their formatting.
     Any other element is removed with everything inside it; any other
     attribute is removed. Text sits only in `w:t`, `w:delText`, `w:instrText`
     and `w:delInstrText`, which are masked; text and tails of every other
     element are cleared; XML comments are removed. Text under an element that
     is not on the list is not read either, so the masked text the model sees
     is exactly the text the copy holds. A removed element that held text is
     counted in the notes ("unrecognised element with text").
   - Containers whose text is wanted are unwrapped to their children:
     hyperlinks and simple fields (their targets and instructions go),
     content controls (`w:sdt`: alias, tag, data binding and list values
     go), smart tags, custom XML, `w:dir`, `w:bdo`. Bookmarks, permissions,
     proofing marks and form-field data (`w:ffData`) are not on the list.
   - Drawings, pictures, embedded objects, text boxes, `w:altChunk`,
     sub-documents and **equations** (`m:oMath`, `m:oMathPara`) are removed
     and counted. *Equations are removed, not read:* reading `m:t` would mean
     allowlisting the whole Office Math vocabulary, and the customers' documents
     (law, accounting, medical and HR) rarely carry equations. The note tells
     the user what went.
   - Styles: every style gets a fixed id. Built-in names (the latent-style
     names of python-docx's blank document) are kept; custom styles become
     `Custom N`; aliases go. A style reference to an id that is not in the copy
     is removed. Numbering: a list label with a word in it (two letters in a
     row) becomes `%N.`. Settings: a short list of layout and compatibility
     settings; mail merge, document variables, the attached template and rsids
     go. Font table: font names and metrics; embedded fonts go.
   - `w:author` becomes `Redacted` and `w:initials` empty in every kept part,
     not only story parts; `w:date` goes. Core properties are rebuilt empty.
     The theme is replaced by python-docx's default theme.
   - Kept relationship types: office document, core properties, styles,
     numbering, settings, font table, theme, headers, footers, notes,
     comments. Everything else (app and custom properties, people, glossary,
     custom XML, thumbnails, web settings, comment extensions, every external
     target) is dropped. Part names and relationship ids are renamed to fixed
     forms (`/word/header1.xml`, `rId1`), because both are free text in the
     source. The writer refuses the copy if it would hold a zip member outside
     a fixed list.
   *Embedded images in DOCX (open question of the outline): removed, not
   OCRed, in this phase.*
3. **XLSX:** a changed cell gets its masked text as a string. An unchanged cell
   keeps its value and type. Formulas are flattened to their cached values,
   because a formula can carry a value in a literal and recomputing it after
   masking means nothing. A masked comment gets author `Redacted`. A masked sheet
   title keeps the placeholder without brackets (Excel forbids `[]`), so
   `SSN_1`, which rule 3 of ADR 0003 still restores, falls back to `SheetN`
   when it collides or is too long. Removed: images, charts, pivot tables,
   hyperlinks, data validations, conditional formats, defined names, print
   headers and footers, external links, and document and custom properties.
   Also removed (security review I1): **tables** (their column names repeat the
   header cells and their totals rows hold labels; the cells stay, the table
   styling goes, and the copy's notes say so), **filter criteria and sort
   state** (the filter range and its drop-downs stay), what-if scenarios,
   differential formats and custom table styles, and sheet and workbook code
   names. **Custom number formats** become built-in ones, because a format code
   can hold literal text shown next to a value: dates and times get a built-in
   date or time format, everything else General. Custom cell-style names become
   `Style N`, and the theme is openpyxl's default. openpyxl writes the package
   from its own model, so parts it does not model are not copied; the writer
   also refuses the copy if it would hold a zip member outside a fixed list.
4. **EML:** From, To, Cc and Subject are written with their masked values as
   unstructured headers. Every other header is dropped, except MIME-Version,
   Content-Type and Content-Transfer-Encoding, on every part. That drops
   Received, Message-ID, Reply-To and X- headers. A multipart Content-Type is
   rebuilt as `multipart/<subtype>` alone: its parameters and its boundary are
   free text (`x-owner="..."`), so the copy gets a new random boundary.
   `text/plain` parts get their
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

6. **Input limits (security review M6).** Before a .docx or .xlsx is opened,
   the sizes its zip directory declares are checked: at most 200 MB
   uncompressed in all and 10,000 members. Python's zipfile stops reading a
   member at its declared size and then fails its CRC check, so a member cannot
   inflate past what was checked. Beyond a cap the file is refused, not
   truncated. A file that is not a zip, and malformed XML (lxml's
   `XMLSyntaxError`, `xml.etree`'s `ParseError`), are `DocumentError`s, so the
   CLI exits 2 with a reason instead of a traceback. Entity expansion in a
   spreadsheet is refused by expat's own amplification limit (or by defusedxml
   when it is installed); python-docx parses with entity resolution off.

## Consequences

- A redacted PDF looks like a plain transcript laid out where the lines were.
  Graphics, images, colours and fonts are gone, and Cyrillic and Armenian text
  is invisible on screen while still present in the text layer. Better fidelity
  needs either a renderer (pypdfium2 after review) or an embedded font after
  license review. Either needs a new ADR.
- DOCX copies lose images, charts, text boxes, equations and any element not
  on the allowlist. XLSX copies lose formulas, charts, pivots and tables. EML copies lose attachments and HTML formatting. These are
  deliberate: each would otherwise need its own reader to be masked.
- What the tests check, per format: Faker-built files carrying values in every
  place the review found (and the constructs listed above) are masked through
  `formats.mask_file` or the CLI, and the tests assert that no original value
  occurs in the raw bytes of any zip member, of the .eml file, or of the PDF's
  decompressed streams; where the format allows exact equality, the re-parsed
  copy equals the masked text. Tests can only check the places someone thought
  of. The allowlist is what makes an unknown DOCX construct fail closed, and
  openpyxl's own model plays that role for XLSX.
- Known limits of the copies:
  - A value the detector missed is copied like any other text (hard rule 7).
  - DOCX: the values of allowed attributes are not checked against their
    schema types, so a hand-crafted file can still hide text in, for example,
    a `w:val` of a formatting element or a font name. Font names are kept.
    Built-in style names are kept. A list label without a two-letter word is
    kept as written. Equations, text boxes and unknown elements are lost from
    the copy and from what the model reads.
  - XLSX: what openpyxl models is kept unless listed above, for example font
    names, print settings, sheet views and sheet protection hashes. Custom
    number formats (currency symbols, thousands separators, colours) are
    lost. Tables lose their structured references and styling.
  - EML: HTML parts lose their formatting (`<pre>` of the masked text).
  - Size caps bound what is inflated, not the parser's memory: a document
    just under 200 MB can still need several times that in memory.
- This reduces exposure; it does not anonymise.

## Amended 2026-11-10: attribute values and list labels

A kept attribute used to be copied as long as its name was on the list, so free text in
a typed attribute (`<w:lang w:val="a@b.org"/>`) reached the copy and was never read for
detection.

- **One validator.** `docx_allow.keep_attribute` judges every kept value: a language tag
  on `w:lang` and `w:themeFontLang` (`^[A-Za-z]{2,8}(-[A-Za-z0-9]{1,8})*$`); elsewhere
  a number, a 2 to 8 digit hex value (colours, ids), or a word from the schema's
  enumerations. A value that matches none is dropped (fail closed).
- **Font names** (`rFonts`, `sym`, the font table) stay only if they are on a list of
  common families in `docx_allow._FONT_FAMILIES` (Calibri, Cambria, Arial, Times New
  Roman, Aptos, Helvetica, Courier New, Georgia, Verdana, Tahoma, Segoe UI, the
  Liberation, DejaVu, Noto (including the Armenian and Cyrillic variants) and PT
  families, and similar); any other becomes Calibri. Case and spacing are normalised and
  the listed spelling is written. An earlier version kept any name of the shape
  `[A-Za-z][A-Za-z0-9 -]{0,31}`, which let a person's name used as a font name through;
  the list closes that. The cost is that a less common font in a copy is shown as
  Calibri, which changes the look and not the text. A person whose name equals a listed
  family (a Georgia, say) is not distinguishable, and the family name is not personal
  data in that spot.
- **Judged later, so skipped here:** style ids and style references (rewritten to the
  copy's own ids), style names (built-in or `Custom N`), author and initials (replaced),
  `r:id` (checked against the kept relationships), compat settings (`_clean_settings`).
- **List labels.** `_clean_numbering` keeps a `w:lvlText` only when it is `%1` to `%9`
  level references plus at most two characters that are not letters or digits (a lone
  non-digit such as a bullet `o` is kept). Anything else, such as a phone number, becomes
  `%N.` for its level.
