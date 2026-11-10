# 0001. Placeholder format

- Status: Accepted
- Date: 2026-11-09

## Context

The masked text goes to a cloud model and comes back with placeholders that we
must restore. The format has to survive a language model (which may reformat or
paraphrase it), be readable in the UI, and never be confused with real text.
The brief requires `rehydrate(mask(x)) == x` for every input.

## Decision

1. Placeholders look like `[PERSON_1]`, `[PHONE_2]`: square brackets, the
   entity type in upper case from a fixed enum, underscore, a counter.
2. Counters are per entity type and per session, start at 1, and are assigned
   in order of first appearance in the text (messages are scanned in order).
3. The map key is the **exact surface string**. `123-45-6789` and <!-- fake example; pragma: allowlist secret -->
   `123 45 6789` get two different placeholders. This is what makes the
   round-trip property exact. Grouping variants of one value (case changes,
   "Smith" vs "John Smith") is a later improvement that must be measured
   against the benchmark before it ships.
4. If the input already contains placeholder-shaped text (for example a user
   pasting `[PERSON_1]` from an earlier masked answer), that text is itself
   masked as `[LITERAL_n]`, so restoring is never ambiguous.
5. The mapping holds `placeholder -> original` only. It never contains the
   masked text.

## Consequences

- The model sees two placeholders for two spellings of the same value. This
  costs a little reasoning quality and never leaks anything.
- The entity-type enum becomes part of the wire format. Renaming a type later
  breaks rehydration of saved sessions, so the enum is versioned with the
  session store (ADR 0002).
