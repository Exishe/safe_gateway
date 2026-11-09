"""The web UI's copy of the entity types matches the engine's (ADR 0024).

The UI finds unresolved placeholders with the same grammar as the engine; a type
missing there would hide an unresolved placeholder of that type.
"""

import re
from pathlib import Path

from gateway.engine.types import EntityType

ENTITIES_TS = Path(__file__).resolve().parents[2] / "apps" / "web" / "src" / "lib" / "entities.ts"


def test_web_entity_types_match_the_engine() -> None:
    source = ENTITIES_TS.read_text(encoding="utf-8")
    block = re.search(r"ENTITY_TYPES = \[(.*?)\] as const", source, re.DOTALL)
    assert block is not None
    names = re.findall(r'"([A-Z_]+)"', block.group(1))
    assert names == [t.value for t in EntityType]
