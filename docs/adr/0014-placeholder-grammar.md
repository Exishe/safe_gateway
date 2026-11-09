# 0014. One placeholder grammar for mask and rehydrate

- Status: Accepted
- Date: 2026-11-09
- Refines: ADR 0001 point 4, ADR 0003, ADR 0004 point 3

## Context

ADR 0001 masks placeholder-shaped input as `[LITERAL_n]` and ADR 0003 restores
three shapes of placeholder. The round trip `rehydrate(mask(x)) == x` is exact
only if both sides agree on what is placeholder-shaped, including at the seams
where a placeholder now sits next to original text. Streaming (ADR 0004) needs
a bound on how long a token can be.

## Decision

1. `gateway/engine/placeholders.py` holds the only token grammar; mask and
   rehydrate import it.
2. A counter has at most six digits (`MAX_COUNTER = 999_999`). A mapping that
   would need a seventh raises instead of writing an ambiguous token. So no
   partial token is longer than 29 characters, which bounds the streaming
   hold-back.
3. Rule 3 (unbracketed, upper case, `_` or space) does not match when a word
   character or a square bracket touches either end. Rule 2 accepts `_`,
   space or `-`; rule 3 only `_` or space, as ADR 0003 lists them. A number
   with a leading zero (`[PERSON_01]`) is not the key `[PERSON_1]`.
4. Mask searches each unmasked stretch for tokens as it will read after
   masking: with a placeholder bracket on each side that borders a masked
   region. Every token found there becomes `[LITERAL_n]`. Because the bracket
   disqualifies a rule 3 token, a label before a masked value (`SSN 123...`)
   stays as written, and no text can turn into a token at a seam.
5. Overlapping spans given to mask are united under the first span's type,
   never dropped (fail-safe, as ADR 0013 for detection).
6. Rehydrate scans once, left to right, and never rescans a restored value.
   Every token it leaves as written is returned once, in order, as unresolved.

## Consequences

- Upper-case input such as `DATE 12` with nothing detected after it is sent as
  `[LITERAL_1]`. The text is not lost (it round-trips); the model sees less.
- Renaming an entity type or changing the counter width changes the wire
  format and needs a new ADR (ADR 0001, consequences).
- Property tests assert the round trip for detected and arbitrary spans, and
  that streamed output equals `rehydrate()` for random chunkings.
