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

## Amendment 1, 2026-11-09: known values, Unicode edges, a shared mapping (leak hunt 2)

1. **Known values are masked wherever they recur.** Detection can miss a value it
   found elsewhere (`ref123-45-6780.pdf`). Besides the spans it is given, `mask`
   replaces every exact occurrence of a value the mapping holds, this text's spans
   included; `mask_messages` detects every message before masking any, so a value
   detected in a later message is also masked in an earlier one. Matching uses the
   mapping's own key, the exact surface (ADR 0001 point 3): case-sensitive, no
   normalisation; another spelling is left to detection. Literals are not swept:
   they are not personal data, and point 4 decides where they are.
2. **Boundary rule.** An occurrence counts unless a digit touches a digit edge of
   the value or a letter (any script) touches a letter edge. So a letter or `_`
   next to a number does not hide it (`ref123-45-6780.pdf`, `123-45-6780th`), and a
   letter next to a name means another word (`Also` does not hold `Al`), while
   `Al's` and `Al2` do. An edge that is neither digit nor letter (`+`, `(`) has no
   condition. A digit next to a digit edge is another number, left to detection.
3. **Overlaps.** At each start the longest value wins, and overlapping occurrences
   are all found and united under point 5, so overlapping values are never split.
4. **Cost.** All values are compiled into one regular expression shaped as a
   prefix tree (flat below 24 levels, so values that prefix each other cannot
   exhaust the compiler), cached on the mapping until a value is added and
   dropped from `re`'s module-wide cache, which would otherwise keep the values
   after the mapping is cleared. Compiling 5,000 values takes about 0.4 s, once
   per change of the mapping; scanning 960k characters with them takes well under
   a second (`tests/unit/engine/test_known.py`).
5. **Numbering.** `mask_messages` numbers values in order of first detection
   across messages, so an earlier message keeps its placeholders when a later turn
   adds a message; a value swept in an earlier message takes the next number.
6. **Unicode edges.** Point 3's "word character" is any Unicode word character
   (`\w` without `re.ASCII`), so `PERSON_1` followed by a full-width 2 is not
   restored. The token grammar itself stays ASCII: type names are spelled as
   explicit `[Pp][Ee]...` classes, because Unicode case folding would accept the
   long s or the Kelvin sign.
7. **A shared mapping.** `Mapping` allocates under its own lock, so concurrent
   requests on one session never share a number. `from_dict` refuses a counter
   gap and a `[LITERAL_n]` that does not hold a placeholder-shaped token, as mask
   never writes either.

## Amendment 2, 2026-11-09: the sweep compares the clean view (leak hunt 1d)

Amends point 1 of amendment 1. An SSN detected with a zero-width space inside was
swept only in that spelling, and its plain repeat glued to a file name, the case the
sweep exists for, went out unmasked (`test_leak_hunt_1d.py`; also the known limit
`test_zero_width_space_inside_a_known_value_glued_to_a_word` of leak hunt 2b, now
passing).

1. **Values and text are compared as the clean view reads them** (ADR 0013, ADR 0016
   amended): `KnownValues` compiles the clean form of each surface, searches the
   clean view of the text, and maps each match back, so `text[start:end]` is the
   occurrence as written. A value stored with an invisible character, a no-break
   space, a typographic dash or non-ASCII digits matches its plain repeat, and the
   other way round. Matching stays case-sensitive; NFC and NFD forms of a name are
   still two spellings (the 2b known limit for them stays).
2. Two surfaces with one clean form share one pattern entry; the first one's type
   is used for the sweep. Each occurrence still gets the placeholder of its own
   surface (ADR 0001 point 3), so a plain repeat of a value stored with a zero-width
   space gets a new number of the same type. A surface that cleans to nothing is
   not swept.
3. The boundary rule of point 2 is applied to the clean view's characters.
