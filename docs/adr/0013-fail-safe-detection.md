# 0013. Fail-safe detection: clean text, digit runs, nothing left uncovered

- Status: Accepted
- Date: 2026-11-09
- Amends: ADR 0011, points 3, 4 and 7

## Context

The first leak-hunter round on Phase 1a found 20 inputs where `detect()`
missed a value, reported only part of it, or stalled
(`tests/unit/engine/test_leak_hunt_1a.py`). Most came from three sources:
values written with no-break spaces, typographic dashes or invisible
characters; digit groups split by a line break or glued to a label; and a
merge that dropped a shorter span overlapping a longer one, so a coincidental
checksum-valid window could hide part of a real identifier.

## Decision

1. **Recognizers read a clean view.** `gateway/engine/textview.py` builds a
   copy of the text in which Unicode space separators become an ASCII space and
   dash variants a hyphen, one for one, and invisible format characters (zero
   width space and joiners, soft hyphen, word joiner, bidi marks, BOM) are
   removed. Every span is mapped back, so `text[start:end]` is still the value
   as written. Gazetteer entries are cleaned the same way.
2. **Digit runs end at digits, not at letters.** Groups may be joined by one
   space, dot or hyphen, a spaced hyphen (` - `), or a line break. A value glued
   to a label or file name (`scan_4111...`) is read; the US SSN, which has no
   checksum, is still rejected next to a letter.
3. **Nothing detected is left uncovered.** A digit window that overlaps an
   accepted one is accepted too, unless it lies inside it. The merge still
   keeps the best candidate (longest, then score, then priority), but a loser
   keeps the parts nobody else covers, as spans of its own type, without
   punctuation at their edges. The merge is O(n log n).
4. **Deferred, and listed in SECURITY.md:** OCR letter-for-digit noise; 100 KB
   of single-spaced digits (several seconds, every window is checked); a 100 KB
   phone directory (about 3 seconds, one matcher pass per region).

## Consequences

- A value that shares digits with a longer checksum-valid coincidence (a
  postcode followed by a card) is masked in pieces, partly under the
  coincidence's type, and the neighbouring number is masked too. No digit of
  the value is left unmasked; the placeholders are less tidy. The leak-hunt tests for a card after a
  postcode, an identifier after another number, and a US phone before a ZIP
  code therefore check that no letter or digit is left unmasked, not the exact
  span. Stricter rules (card prefix ranges, INN only as one group) would give
  exact spans but narrow detection; they are not adopted here.
- Line breaks between numbers join them into one run, so tables produce more
  candidate windows and more coincidental matches: more over-masking, not less
  masking.
- The three deferred inputs stay in the leak-hunt file, marked
  `xfail(run=False)`: they are not run, so the mark is removed when a fix lands.
