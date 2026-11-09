# 0011. Shape of the detection engine

- Status: Accepted
- Date: 2026-11-09

## Context

Phase 1 builds `detect(text) -> list[Span]`. The brief names Presidio with
spaCy, custom checksum recognizers, GLiNER as an extra recognizer, and a merge
step. Structured identifiers must reach 100% detection on generated data. The
ML parts are large downloads (spaCy `lg` models, torch, GLiNER weights), and
the brief's first-choice model name `fastino/gliner2-pii` does not resolve on
Hugging Face as written.

## Decision

1. **Phase 1 runs in three steps, one tag.** 1a: structured recognizers,
   gazetteer, merge, `detect()`, `safeguard scan`; no ML. 1b: spaCy NER through
   Presidio, language detection, sourced Armenian and Russian document formats.
   1c: GLiNER, `safeguard models pull`, the OpenPII benchmark, tag `v0.1.0`.
2. **The engine owns the recognizer interface and the merge.** A recognizer is
   an object with `name` and `find(text) -> list[Span]`. Structured recognizers
   are plain Python, so their tests run in milliseconds without loading a
   model. Presidio is one NER backend behind the same interface (1b), not the
   orchestrator; its own regex recognizers are not used, ours validate more.
3. **Offsets are never shifted.** Recognizers read the original text. Unicode
   digits are matched with `\d` and converted with `unicodedata.digit` for
   checksums, so full-width or Arabic-Indic digits are found without rewriting
   the text.
4. **Digit groups.** Card, SSN, INN and SNILS candidates come from runs of digit
   groups joined by a single space, hyphen or dot. Every group-aligned window of
   the right length is tried, so a card next to another number is still found.
5. **Phones** use `phonenumbers` (Apache-2.0) with `Leniency.VALID`, one pass per
   configured region (default US, GB, DE, FR, IT, ES, NL, RU, AM). Partial
   matches from the wrong region lose to the full match in the merge.
6. **Scores.** Checksum-validated: 1.0. Valid phone, email: 0.95. IP address,
   URL with a token: 0.9. SSN with separators: 0.85; nine bare digits: 0.6
   (SSN has no checksum). Gazetteer: 1.0. Default threshold per type: 0.5.
7. **Merge.** Overlapping spans keep the longest; then the higher score; then
   the recognizer listed first. Touching spans do not overlap.
8. **Gazetteer** is a CSV with `value` and an optional `entity_type`; blank
   means `CODENAME`, a type added to the brief's minimum list. Matching is
   case-insensitive on word boundaries.
9. **Not guessed.** Armenian passport and social card, Russian passport and
   other national formats wait for a cited source (1b). OCR noise (`O` for `0`)
   waits for the first leak-hunter round.

## Consequences

- 1a ships a useful CLI with no model download and no new heavy dependency.
- The engine gains one runtime dependency, `phonenumbers` (Apache-2.0).
- Nine bare digits are ambiguous; they are reported at 0.6 so the UI can treat
  them as "check this" rather than certain.
