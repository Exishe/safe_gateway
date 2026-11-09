# Phase 2: Masking and rehydration

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `safeguard mask notes.txt -o notes.masked.txt` replaces every detected value with a placeholder such as `[SSN_1]`, and `rehydrate(mask(x)) == x` holds for every input.

**Architecture:** `gateway/engine/placeholders.py` owns the one token grammar that both sides share: the canonical form `[TYPE_N]`, the tolerant shapes of ADR 0003, and the "could this tail still become a token" test used by streaming. `mask.py` turns `detect()` spans plus every placeholder-shaped token already in the input into masked regions and fills a `Mapping`. `rehydrate.py` scans once, left to right, applying ADR 0003's three rules; `StreamingRehydrator` runs the same scan and only holds back a tail that could still become a token (ADR 0004 point 3). `session.py` keeps mappings in memory under random ids with an idle TTL (ADR 0002 point 2). The CLI gains `safeguard mask`.

**Tech Stack:** Python 3.12 standard library only (`re`, `secrets`, `threading`, `time`). Hypothesis and Faker for tests. No new dependency in this pass.

**Spec:** `docs/adr/0001`, `0002`, `0003`, `0004`; the engine API in `gateway/engine/{types,detect,merge}.py`.

## Global Constraints

- `gateway/engine/` imports no network, process or dynamic-import module (checked on every edit).
- One grammar: mask and rehydrate import the same compiled patterns from `placeholders.py`; neither defines its own.
- The `Mapping` holds `placeholder -> original` only; it never holds masked text (ADR 0001 point 5).
- Map keys are exact surface strings: one surface, one placeholder; two spellings, two placeholders (ADR 0001 point 3).
- Fail-safe: a character inside any given span is always masked. Overlapping inputs are united, never dropped.
- Counters are bounded to 6 digits (`MAX_COUNTER = 999_999`), so a token has a bounded length and the streaming hold-back is bounded too. Exceeding it raises instead of producing an ambiguous token.
- Fake data only; literal identifiers in tests carry `# pragma: allowlist secret`.
- Tests not marked `models` run with no NER model (conftest); mask tests use structured identifiers or hand-made spans.

## Token grammar (decided here, recorded in ADR 0014)

- **Canonical:** `[TYPE_N]`, `TYPE` an `EntityType` value, `N` 1 to 6 ASCII digits, no leading zero when we write it.
- **Bracketed (rules 1 and 2):** `[` type in any ASCII case, one of `_`, space, `-`, 1 to 6 digits, `]`.
- **Unbracketed (rule 3):** type in upper case, `_` or space, 1 to 6 digits; not touching a word character or a square bracket on either side. Excluding brackets as neighbours is what keeps the round trip exact: in masked text the neighbours of an original segment's edges are placeholder brackets, so no new token can appear at a seam.
- **Placeholder-shaped** means either shape. On input, every placeholder-shaped token is masked as `[LITERAL_n]` (ADR 0001 point 4), whether or not it would be restored. On output, every placeholder-shaped token that is not restored is listed as unresolved.
- Known cost: upper-case text such as `DATE 12` in an input is masked as a literal. It is never lost (it round-trips) and the model sees `[LITERAL_1]`.

## Review Focus

1. "only person 1 of 3 signed" is never restored, even with `[PERSON_1]` in the mapping; `[PERSON_7]` with no PERSON_7 in the mapping is left as is and listed unresolved. Tests in Task 3.
2. An input that already contains `[PERSON_1]`, `PERSON 1` or `[LITERAL_1]` round-trips exactly, including when a detected span touches it (`XPERSON_1` with `X` detected, `PERSON_12` with `2` detected). Property test in Task 2, examples in Task 2.
3. Streaming output equals non-streaming output for every chunking, including a placeholder split one character per chunk and a token whose end is decided by the next chunk (`PERSON_1` then `2`). Tests in Task 4.
4. The streaming hold-back never exceeds the longest partial token (29 characters) and is empty for text with no `[` and no upper-case type prefix at its end. Test in Task 4.
5. The map file written by `safeguard mask --json-map` is created with mode 0600 and the command warns that it is personal data; `-o` refuses to overwrite the input file. Tests in Task 6.

---

### Task 1: Token grammar and `Mapping`

**Files:** create `gateway/engine/placeholders.py`, `gateway/engine/mask.py` (Mapping only); tests `tests/unit/engine/test_placeholders.py`, `tests/unit/engine/test_mask.py`.

**Interfaces (produces):**
- `placeholders.MAX_COUNTER = 999_999`; `canonical(entity_type: EntityType, number: int) -> str`; `TOKEN: re.Pattern[str]` with groups `btype`, `bnum` (bracketed) and `utype`, `unum` (unbracketed); `canonical_of(match) -> str` (the canonical key a match would restore from); `could_become_token(tail: str) -> bool` (a proper prefix of a bracketed token, or a prefix of an unbracketed token including a complete one, whose end the next character decides); `MAX_PARTIAL` (29).
- `class Mapping`: `placeholder_for(surface: str, entity_type: EntityType) -> str` (get or assign; counters per type in order of first call); `original(placeholder: str) -> str | None`; `__contains__`, `__len__`; `to_dict() -> dict[str, str]`; `Mapping.from_dict(data) -> Mapping` (rejects a non-canonical key and rebuilds the counters); `clear()`.

- [ ] Tests: `canonical(EntityType.SSN, 1) == "[SSN_1]"`; TOKEN finds `[PERSON_1]`, `[person 1]`, `[Person-1]`, `PERSON_1`, `PERSON 1`, `[DATE_OF_BIRTH_2]` (whole type, not `DATE`); not `person 1`, `XPERSON_1`, `PERSON_1x`, `PERSON_1234567`, `[FOO_1]`, `[PERSON_1` ; `could_become_token` true for `[`, `[pe`, `[PERSON_`, `[PERSON_12`, `P`, `PERSON_1`, false for `[PERSON_1]`, `person`, `x`, `PERSON_1 `; Mapping: same surface same placeholder, a new surface of the same type gets `_2`, different types count separately, `to_dict`/`from_dict` round-trip and continue numbering, `from_dict` rejects `{"PERSON_1": "x"}`, counter overflow raises.
- [ ] Run, FAIL; implement; PASS; commit `feat(engine): placeholder grammar and mapping`.

### Task 2: `mask()` and `mask_messages()`

**Files:** modify `gateway/engine/mask.py`; tests `tests/unit/engine/test_mask.py`, `tests/property/test_mask_roundtrip.py`, `tests/strategies.py` (texts with embedded identifiers and placeholder fragments; disjoint hand-made spans).

**Interfaces (produces):**
- `mask(text: str, spans: Iterable[Span], mapping: Mapping | None = None) -> tuple[str, Mapping]`: regions are the union of the spans and the placeholder-shaped tokens of `text`; a region holding any span takes the type of the first span in it, otherwise LITERAL; each region becomes `mapping.placeholder_for(text[start:end], type)`. A given mapping is extended in place and returned. A span outside the text raises ValueError.
- `mask_messages(texts: Sequence[str], lang: str = "auto", config: EngineConfig | None = None) -> tuple[list[str], Mapping]`: `detect()` then `mask()` on each text in order with one mapping (ADR 0002 point 1).

- [ ] Tests: one fake SSN written twice with dashes gives `[SSN_1]` twice; the same digits written with spaces get `[SSN_2]` (ADR 0001 point 3); `[PERSON_1]` in the input becomes `[LITERAL_1]`; overlapping spans are united; a span touching a literal (`XPERSON_1`) round-trips; the mapping never contains the masked text; `mask_messages` numbers the same value identically in two messages and a new value continues the counter.
- [ ] Property tests (Hypothesis): `rehydrate(mask(x)) == (x, [])` with `detect()` spans on texts mixing arbitrary unicode, embedded identifiers and placeholder fragments; the same with arbitrary disjoint hand-made spans; text outside the spans is unchanged when the text holds no placeholder-shaped token; same surface gives same placeholder, different surfaces different ones.
- [ ] Run, FAIL; implement; PASS; commit `feat(engine): mask with exact-surface mapping`.

### Task 3: `rehydrate()`

**Files:** create `gateway/engine/rehydrate.py`; test `tests/unit/engine/test_rehydrate.py`.

**Interfaces (produces):** `rehydrate(text: str, mapping: Mapping) -> tuple[str, list[str]]`: one left-to-right scan with `TOKEN`; rule 1 exact key in the mapping; rule 2 bracketed, case-insensitive, `_`/space/`-`, canonical key in the mapping; rule 3 unbracketed upper-case, canonical key in the mapping; anything else that matched stays as written and is listed unresolved (each distinct token once, in order of first appearance). A restored value is never scanned again.

- [ ] Tests: one per rule; `[person 1]` and `[Person-1]` restored; `PERSON_1` and `PERSON 1` restored; `person_1` and "only person 1 of 3 signed" not restored and not listed; `[PERSON_7]` not in the mapping listed unresolved; `[PERSON_01]` not restored; a value that itself looks like a placeholder is not re-expanded; paraphrase "the client" untouched.
- [ ] Run, FAIL; implement; PASS; commit `feat(engine): rehydrate with ADR 0003 rules`.

### Task 4: `StreamingRehydrator`

**Files:** modify `gateway/engine/rehydrate.py`; tests `tests/unit/engine/test_rehydrate.py`, `tests/property/test_streaming.py`.

**Interfaces (produces):** `StreamingRehydrator(mapping)` with `feed(chunk: str) -> str`, `close() -> str`, `unresolved: list[str]`. The tail held back starts at the earliest position within the last `MAX_PARTIAL` characters whose suffix `could_become_token`; the scan keeps one character of left context so rule 3's boundary sees the real neighbour.

- [ ] Tests: `[PER` + `SON_1]` emits nothing then the name; `PERSON_1` + `2` gives `PERSON_12` unresolved, not the name plus `2`; plain text is emitted at once; held tail never longer than `MAX_PARTIAL`; exhaustive: every two-chunk split and one-character chunks of a sample answer equal `rehydrate()`.
- [ ] Property test: for random texts built from placeholder fragments and random chunkings, joined output and unresolved equal `rehydrate()`.
- [ ] Run, FAIL; implement; PASS; commit `feat(engine): streaming rehydrator`.

### Task 5: In-memory session store

**Files:** create `gateway/engine/session.py`; test `tests/unit/engine/test_session.py`.

**Interfaces (produces):** `SessionNotFound(KeyError)`; `SessionStore(ttl_seconds: float = 1800, clock: Callable[[], float] = time.monotonic)` with `create(mapping: Mapping | None = None) -> str` (id from `secrets.token_urlsafe(32)`), `get(session_id) -> Mapping` (refreshes the idle timer; raises SessionNotFound when missing or idle past the TTL, and drops it), `delete(session_id) -> bool` (clears the mapping), `purge_expired() -> int`, `__len__`. A lock guards the dict (the proxy will call it from worker threads).

- [ ] Tests: ids are distinct and at least 32 characters; get returns the same Mapping object; idle past TTL raises and the session is gone; a get inside the TTL extends it; delete clears the mapping and a second delete returns False; purge counts and removes expired sessions only.
- [ ] Run, FAIL; implement; PASS; commit `feat(engine): in-memory session store with idle TTL`.

### Task 6: `safeguard mask`

**Files:** modify `gateway/cli.py`; tests `tests/unit/test_cli.py`.

**Interfaces (produces):** `safeguard mask FILE [-o OUT] [--json-map MAP] [--gazetteer CSV] [--no-ner]`: masked text to OUT (or stdout); with `--json-map`, the `placeholder -> original` map as JSON written with mode 0600 and a warning on stderr that the map is personal data. Exit 2 for an unreadable or non-UTF-8 input, or when OUT or MAP is the input file.

- [ ] Tests: output file holds `[EMAIL_1]` and `[CREDIT_CARD_1]` and none of the values; stdout mode; the JSON map restores the original through `rehydrate`; map mode is 0600 and the warning is printed; `-o` equal to the input exits 2 and leaves it intact; missing file exits 2.
- [ ] Run, FAIL; implement; PASS; run `uv run safeguard mask` on a sample and read the output; commit `feat: safeguard mask`.

### Task 7: Close-out

- [ ] ADR 0014 (token grammar and the literal rule) and `docs/adr/README.md` index.
- [ ] Full verification: ruff format and check, ty, no-egress, pytest, `HYPOTHESIS_PROFILE=ci` property tests; a round-trip script on a sample file.
- [ ] Later in this phase (not in this pass): Fernet-encrypted opt-in disk store keyed through `keyring` (ADR 0002 point 3; needs `cryptography` and `keyring`, license audit first), `leak-hunter` round on mask output, tag `v0.2.0`.
