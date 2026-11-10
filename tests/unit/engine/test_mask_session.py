"""Masking a chat request in a session: issued placeholders stay as written (ADR 0025)."""

from gateway.engine.mask import Mapping, mask_detected_messages
from gateway.engine.rehydrate import rehydrate
from gateway.engine.types import EntityType, Span

NAME = "Avery Quill"  # invalid-by-construction: never detected, only given as a span
EMAIL = "avery@quill.invalid"


def _session() -> Mapping:
    mapping = Mapping()
    mapping.placeholder_for(NAME, EntityType.PERSON)
    mapping.placeholder_for(EMAIL, EntityType.EMAIL)
    return mapping


def test_issued_placeholders_are_kept_and_forged_ones_wrapped() -> None:
    mapping = _session()
    keep = frozenset(mapping.to_dict())
    text = "Write to [PERSON_1] at [EMAIL_1]; also [PERSON_7] and [person_1]."
    (masked,) = mask_detected_messages([text], [[]], mapping, keep)
    assert masked == "Write to [PERSON_1] at [EMAIL_1]; also [LITERAL_1] and [LITERAL_2]."
    assert mapping.original("[LITERAL_1]") == "[PERSON_7]"
    assert rehydrate(masked, mapping) == (
        f"Write to {NAME} at {EMAIL}; also [PERSON_7] and [person_1].",
        [],
    )


def test_without_keep_issued_placeholders_are_literals_as_before() -> None:
    mapping = _session()
    (masked,) = mask_detected_messages(["[PERSON_1]"], [[]], mapping)
    assert masked == "[LITERAL_1]"


def test_values_in_clear_are_masked_in_the_same_session() -> None:
    mapping = _session()
    keep = frozenset(mapping.to_dict())
    other = "Rowan Tilde"
    text = f"[PERSON_1], {EMAIL} and {other}"
    span = Span(text.index(other), len(text), EntityType.PERSON, 1.0, "test")
    (masked,) = mask_detected_messages([text], [[span]], mapping, keep)
    assert masked == "[PERSON_1], [EMAIL_1] and [PERSON_2]"
    assert mapping.original("[PERSON_2]") == other


def test_a_span_inside_an_issued_placeholder_is_ignored() -> None:
    mapping = _session()
    keep = frozenset(mapping.to_dict())
    text = "see [EMAIL_1]"
    inner = Span(5, 10, EntityType.PERSON, 0.5, "test")  # "EMAIL"
    (masked,) = mask_detected_messages([text], [[inner]], mapping, keep)
    assert masked == "see [EMAIL_1]"
    assert "EMAIL" not in mapping.known_values()


def test_a_value_detected_later_is_masked_in_an_earlier_text() -> None:
    mapping = Mapping()
    first, second = "ref Rowan Tilde", "Rowan Tilde here"
    span = Span(0, 11, EntityType.PERSON, 1.0, "test")
    masked = mask_detected_messages([first, second], [[], [span]], mapping)
    assert masked == ["ref [PERSON_1]", "[PERSON_1] here"]
