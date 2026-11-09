# 0003. Limits of tolerant rehydration

- Status: Accepted
- Date: 2026-11-09

## Context

Models sometimes return `PERSON 1`, `[person_1]` or "the client" instead of
`[PERSON_1]`. Restoring too eagerly can put a real name where the model meant
something else, for example "only person 1 of 3 signed".

## Decision

Rehydration runs in this order and stops at the first rule that matches:

1. **Exact match** of a placeholder present in the mapping.
2. **Bracketed, case-insensitive**, with `_`, space or `-` as the separator:
   `[person 1]`, `[Person-1]`. Only when type and number exist in the mapping.
3. **Unbracketed, upper-case type only**: `PERSON_1`, `PERSON 1`. Lower-case
   unbracketed forms are never restored.

Never: edit-distance matching on free text, guessing from paraphrases, or
restoring a type/number that is not in the mapping.

Anything placeholder-shaped that could not be restored is left visible,
returned in an `unresolved` list, and highlighted in the UI. Paraphrases
("the client") are not restorable and are not guessed.

## Consequences

- Some answers will show raw placeholders. That is the honest failure mode and
  the UI must make it obvious.
- The system instruction sent upstream asks the model to keep placeholders
  verbatim, which keeps rule 1 the common case.
