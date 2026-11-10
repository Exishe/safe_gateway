# 0004. Long documents, chunking and streamed rehydration

- Status: Accepted
- Date: 2026-11-09

## Context

Regex recognizers handle any length. spaCy has a default `max_length` of one
million characters. GLiNER models see a short token window (a few hundred
tokens), so long inputs must be split. Streaming responses arrive in chunks,
and a placeholder can be split across two chunks.

## Decision

1. **Detection:** regex and checksum recognizers run on the full text. NER
   runs on windows cut at sentence or whitespace boundaries, sized to the
   model's window, with an overlap (starting value 200 characters, tuned by
   benchmark). Spans found twice in an overlap are merged by the normal merge
   step (longest span wins, then highest score).
2. **Upstream context:** the proxy does not split prompts. If the masked
   prompt is too long for the upstream model, the upstream error is passed
   through unchanged. The web UI warns before sending a very large document.
3. **Streaming:** the rehydrator holds back any tail of the buffer that could
   still become a placeholder (from the last `[` or upper-case type prefix, up
   to the longest possible placeholder length) and emits it once it is
   complete or proven not to be one. Emitted text is therefore never wrong,
   only slightly delayed.

## Consequences

- NER recall near window edges is a measured number in `docs/BENCHMARKS.md`,
  not an assumption.
- Streaming adds a small fixed delay, bounded by the longest placeholder.
