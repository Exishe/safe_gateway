# 0016. What the NER models read, and a recognizer for dates

- Status: Accepted
- Date: 2026-11-09
- Amends: ADR 0012, point 4; ADR 0013, point 1

## Context

The second leak-hunter round (`tests/unit/engine/test_leak_hunt_1b.py`) found
11 inputs where the NER path missed a value that the same model finds in
another form of the same text. A name in capitals, a name with decomposed
accents (NFD), a typographic apostrophe or an emoji variation selector reached
the model as written, and both models read case and whole letters as cues.
The router missed page breaks, paragraph separators and classic-Mac blank
lines, read an English request above a pasted Russian clause (one line break,
as a chat box sends it) as one Russian paragraph, and gave a paragraph with no
letters, such as a date alone, to no model. The Russian model has no DATE label,
so no Russian date was ever masked. A lone surrogate crashed spaCy, and the
address pattern made routing quadratic on long runs without whitespace.

## Decision

1. **Capitals are title-cased for the models.** Before a segment goes to spaCy,
   each line in which more than 70% of at least 3 cased letters are capitals is
   title-cased, one character at a time. A character whose case mapping is not
   one character long (`İ`) stays as it is, so offsets never move and spans map
   back to the capitals as written. This is a heuristic: a name in capitals
   inside a mostly lower-case line is not changed.
2. **The models read NFC.** The NER input is composed per cluster (a character
   and the combining marks after it), with a map back. Composing shortens the
   text and, for a few characters, lengthens it, so `TextView` gains end
   shifts: each composed character maps back to its whole cluster, and a span
   never leaves a combining mark outside it. The structured recognizers still
   read the cleaned view, which gains typographic apostrophes (to `'`),
   variation selectors (removed) and lone surrogates (to U+FFFD), all 1:1 or
   removed as in ADR 0013.
3. **Lines of different scripts are split.** Paragraphs end at a blank line
   made of any line breaks (LF, CR, CR LF, U+2028), at a form feed and at
   U+2029. Inside a paragraph, a line break between lines of different dominant
   scripts ends a segment, so each line goes to the model of its own script;
   lines of one script stay together. A line with no model language (no letters,
   or another script) stays with its paragraph's language.
4. **A paragraph without letters joins its neighbours,** the language before it,
   else the one after it, instead of getting no model. A text with no letters
   at all stays undetermined.
5. **Dates get a structured recognizer** (`gateway/engine/recognizers/dates.py`)
   for every language: `dd.mm.yyyy`, `dd/mm/yyyy`, `mm/dd/yyyy`, `dd-mm-yyyy`,
   `yyyy-mm-dd`, `dd.mm.yy` (two digits each), English month names
   (`12 March 1985`, `March 12, 1985`, `Mar 12 1985`), and Russian month names
   in the genitive (`12 марта 1985`, `12 мар. 1985`). Months must be 1 to 12
   and days 1 to 31; a four-digit year starts with 1 or 2. No digit or
   separator-and-digit may touch either end, which keeps IP addresses and
   longer dotted numbers out. Type DATE, score 0.8. NER DATE stays; the merge
   resolves overlaps.

## Consequences

- Title-casing a whole line can make a model tag ordinary words of a heading
  as a name or organisation: more over-masking in headers, not less masking.
- A short line in another script (a Latin signature under Russian text) now
  reaches its own model with little context.
- A date in a form outside the list (`the 12th`, `12.III.1985`, a Russian date
  without a year) is found only by the English model, and only in English.
- `dd.mm.yy` also matches a version number with two digits in each of three
  parts written after a space (`version 10.12.15`): over-masking, accepted.
- The date recognizer adds about 0.25 s per MB to `detect()`.

## Amended 2026-11-09: leak hunt 1c

The third leak-hunter round (`tests/unit/engine/test_leak_hunt_1c.py`) found
15 inputs that leaked; the ones below are decided here, the shape and keyword
ones in ADR 0023. Each test failed before its fix and passes after it.

1. **The clean view drops every format character and folds digits** (amends
   point 2 and ADR 0013 point 1). Removed: the whole Unicode category Cf (soft
   hyphen, zero-width characters, bidi marks and isolates, the Arabic letter
   mark U+061C, prepended number signs, BOM, tag characters), the combining
   grapheme joiner U+034F, and the whole tags block U+E0000-E007F. Every
   decimal digit outside ASCII (`str.isdecimal`, Arabic-Indic, full width,
   Devanagari and the rest) becomes its ASCII digit (`unicodedata.decimal`),
   one for one, so offsets do not move and every recognizer, not only the
   date one, reads one kind of digit. The lists are built at import by
   scanning planes 0, 1 and 14 (where Unicode keeps them); a test scans every
   code point so a later Unicode version cannot slip one past. Character
   classes are written as ranges, and pure ASCII text skips the search: the
   long class of single characters had made the clean view take a second per
   megabyte.
2. **Dates (point 5).**
   - A hyphen, comma, slash or en dash between a digit and a date is allowed
     when the other side is a date too, so a range or list written without
     spaces (two dates joined by `-`, or `1985-03-12/1985-03-15`) is masked whole.
     A date beside any other number (`12-03-1985-1234`) is still refused. Dots
     and colons still refuse a neighbouring digit, which keeps IP addresses out.
   - The Russian day may sit in guillemets or quotes: `«12» марта 1985`.
   - English and Russian month names, in full or abbreviated, between two
     hyphens, slashes or dots, day first or year first, in any language:
     `12-MAR-1985`, `12/Mar/1985`, `12-мар-1985`, `1985-Mar-12`.
3. **Mixed lines also go to GLiNER (point 3, ADR 0015 point 8).** A line goes
   to the model of its dominant script, so an Armenian or Greek clause after an
   English or Russian instruction reached only spaCy. Two fixes were offered:
   split each line into runs of one script, or send a mixed line to both models.
   The second is chosen: it is simpler (no run boundaries inside a line, where
   punctuation, digits and Latin brand names would each need a rule), and
   GLiNER reads the whole line as context. With `gliner="auto"`, each line of an
   `en` or `ru` segment that holds a letter of neither Latin nor Cyrillic is
   also read by GLiNER, beside spaCy; the merge resolves the overlap. Only the
   line goes, not the segment, so one Greek letter in a long English document
   costs one line of GLiNER, not the document. The Latin-1 ordinal indicators
   and micro sign (`ª º µ`) count as Latin, so `5 µg` sends nothing.
4. **Runs of capitals inside a mixed line are title-cased (point 1).** Besides
   lines mostly in capitals, two or more neighbouring words in capitals, each
   of three letters or more, joined by inline spaces or a hyphen, are
   title-cased for the NER view only, one character for one, so spans map back
   unchanged: `подписал ЗОРВАТ КВИЛЛИН в офисе` reaches the model as
   `Зорват Квиллин`. One word in capitals (`IBM`) and words of one or two
   letters (`NHS GP`) are left as they are.

Consequences.

- More over-masking: an acronym pair such as `ACME LLC` becomes `Acme Llc` for
  the model and may be tagged as an organisation; a hyphenated or comma-joined
  list of dates is masked whole; a format character that a document used on
  purpose (an Arabic number sign) is dropped from what recognizers read. The
  original text is never changed; only spans are.
- A Latin or Cyrillic line with one letter of another script now pays for a
  GLiNER pass on that line (about two orders of magnitude slower than spaCy
  per character; see `docs/BENCHMARKS.md`). Text forced to one language
  (`lang="en"`) is unchanged: it goes to that model alone.
- The import of `gateway.engine.textview` scans about 200,000 code points
  three times (format characters, digits, capitals): about 40 ms measured.
