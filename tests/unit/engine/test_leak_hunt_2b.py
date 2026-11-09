"""Leak hunt 2, second pass over the fixes: what the known-value sweep still misses.

The sweep matches the exact surface the mapping holds (ADR 0014 amendment 1). These
are copies of a known value that are not that surface and that detection also
misses. Values are fake; invisible characters are built with chr().
"""

import unicodedata

import pytest

from gateway.engine.detect import EngineConfig
from gateway.engine.mask import Mapping, mask, mask_messages
from gateway.engine.types import EntityType

NO_NER = EngineConfig(ner="off")
SSN = "123-45-6780"  # pragma: allowlist secret
ZERO_WIDTH_SPACE = chr(0x200B)

KNOWN_LIMIT = pytest.mark.xfail(
    strict=True, reason="known limit: the sweep matches exact surfaces (ADR 0014 amendment 1)"
)


@KNOWN_LIMIT
def test_zero_width_space_inside_a_known_value_glued_to_a_word() -> None:
    """The SSN is known as [SSN_1]; `ref123-<ZWSP>45-6780.pdf` went out with the digits
    in clear: the zero-width space breaks the exact match, and detection does not see
    an SSN glued to `ref`."""
    hidden = SSN[:4] + ZERO_WIDTH_SPACE + SSN[4:]
    masked, _ = mask_messages([f"SSN {SSN}", f"Scan saved as ref{hidden}.pdf"], config=NO_NER)
    assert "45-6780" not in masked[1]


@KNOWN_LIMIT
def test_known_name_in_another_unicode_normalisation_form() -> None:
    """A mapping holds the composed name (as a gazetteer or an earlier NER pass wrote
    it); the same name in decomposed form (NFD) is another surface and is not swept."""
    name = "Zo" + chr(0xEB) + " Quillin"  # e with diaeresis, composed
    mapping = Mapping()
    mapping.placeholder_for(name, EntityType.PERSON)
    decomposed = unicodedata.normalize("NFD", name)
    masked, _ = mask(f"Dear {decomposed},", [], mapping)
    assert "Quillin" not in masked
