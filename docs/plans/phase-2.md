# Phase 2: Masking and rehydration (outline)

> Outline only. Brainstorm, then write the task plan here when the phase starts.

**Goal:** `mask` and `rehydrate` with the round-trip property `rehydrate(mask(x)) == x` for every input.

**Scope**
- `gateway/engine/mask.py`: `mask(text, spans, session) -> (masked_text, mapping)` per ADR 0001 (exact-surface keys, per-type counters in order of first appearance, `[LITERAL_n]` for placeholder-shaped input). Multi-message masking with one shared mapping (ADR 0002).
- `gateway/engine/rehydrate.py`: exact match, then the tolerant rules of ADR 0003; returns `(text, unresolved)`.
- Streaming rehydrator that holds back a possible placeholder prefix (ADR 0004).
- Session store: in-memory with idle TTL; opt-in Fernet-encrypted disk store keyed through `keyring` (ADR 0002).
- CLI: `safeguard mask FILE -o OUT`.

**Exit criteria**
- Property tests: round trip for all generated inputs; masking never changes text outside spans; streaming output equals non-streaming output for every chunking of the same text.
- Tolerant-match tests for every ADR 0003 rule, including the "only person 1 of 3" non-match.
- `leak-hunter` pass on the mask output. Tag `v0.2.0`.
