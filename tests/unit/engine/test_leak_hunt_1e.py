"""Red-team findings against the detection engine, round 1e.

Each test feeds `detect()` a structured identifier written in a shape the engine should
fold or group but does not, and asserts the whole value is covered by a span of the
right type. Every one was a known bypass; the fixes landed on 2026-11-10 (ADR 0023).
"""

from gateway.engine.detect import EngineConfig, detect
from gateway.engine.types import EntityType

_STRUCTURED = EngineConfig(ner="off")


def _covers(text: str, value: str, entity_type: EntityType) -> bool:
    start = text.index(value)
    end = start + len(value)
    return any(
        span.entity_type == entity_type and span.start <= start and span.end >= end
        for span in detect(text, config=_STRUCTURED)
    )


def test_iban_split_across_line_with_indent() -> None:
    """An IBAN wrapped onto a second, indented line is not detected.

    `iban._collect` allows a single separator between characters, so a line break
    followed by the next line's indent (`\\n` then spaces) ends the IBAN early. The
    country code and check digits before the break leak unmasked.
    """
    # Classic mod-97-valid test IBAN.
    value = "DE89 3704 0044\n    0532 0130 00"  # pragma: allowlist secret
    text = f"Please wire the fee to {value} before Friday."
    assert _covers(text, value, EntityType.IBAN)


def test_credit_card_shape_slash_separators() -> None:
    """A card-shaped number grouped with slashes escapes the shape recognizer.

    `credit_card._GAP` for the Luhn-invalid shape allows only spaces, a dot or a hyphen,
    so "/" (one separator just outside the set) is missed, while the same number grouped
    with hyphens is caught at score 0.55.
    """
    # 16-digit Visa shape, Luhn-invalid by construction (last digit changed).
    card = "4111/1111/1111/1112"  # pragma: allowlist secret
    text = f"Charged to card {card} today."
    assert _covers(text, card, EntityType.CREDIT_CARD)


def test_iban_wrap_only_between_groups_of_four() -> None:
    """A wrap mid-group, or a wrap that breaks mod-97, is not an IBAN."""
    mid_group = "DE89 3704 00\n    440532 0130 00"  # pragma: allowlist secret
    invalid = "DE88 3704 0044\n    0532 0130 00"  # pragma: allowlist secret
    for value in (mid_group, invalid):
        assert not _covers(f"wire {value} now", value, EntityType.IBAN)


def test_dates_with_slashes_are_not_cards() -> None:
    """Slash-grouped dates and mixed-separator runs stay out of the card shape."""
    for text in ("born 12/03/1985 in Ulm", "dated 2026/11/10/2026 and 1111/2222-3333/4444"):
        assert not any(
            span.entity_type == EntityType.CREDIT_CARD for span in detect(text, config=_STRUCTURED)
        )
