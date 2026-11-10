"""Shape recognizers find every generated value at its exact offsets (ADR 0023, leak hunt 1c).

Values are invalid by construction: "+99" is not an assigned country code, card shapes
skip the Luhn check, and letter-digit codes are random.
"""

from hypothesis import given

from gateway.engine.detect import EngineConfig, detect
from gateway.engine.recognizers.credit_card import CREDIT_CARD, CREDIT_CARD_SHAPE
from gateway.engine.recognizers.id_documents import ID_CARD_SHAPE, PASSPORT_SHAPE
from gateway.engine.recognizers.phone_shape import PHONE_SHAPE
from gateway.engine.types import EntityType, Recognizer
from tests.strategies import (
    card_shapes,
    card_shapes_beside_short_numbers,
    disguised_passport_numbers,
    embedded,
    id_card_numbers,
    loose_credit_cards,
    passport_numbers,
    plus_phones,
)


def assert_found(recognizer: Recognizer, sample: tuple[str, str], kind: EntityType) -> None:
    text, value = sample
    found = [(text[s.start : s.end], s.entity_type) for s in recognizer.find(text)]
    assert (value, kind) in found, f"{value!r} not found in {text!r}: {found}"


def assert_masked(sample: tuple[str, str]) -> None:
    """detect() leaves no letter or digit of the value outside a span."""
    text, value = sample
    spans = detect(text, config=EngineConfig(ner="off"))
    start = text.index(value)
    bare = [
        i
        for i in range(start, start + len(value))
        if text[i].isalnum() and not any(s.start <= i < s.end for s in spans)
    ]
    assert not bare, f"{value!r} partly unmasked in {text!r}"


@given(embedded(plus_phones()))
def test_every_generated_plus_phone_is_found(sample: tuple[str, str]) -> None:
    assert_found(PHONE_SHAPE, sample, EntityType.PHONE)
    assert_masked(sample)


@given(embedded(passport_numbers()))
def test_every_generated_passport_shape_is_found(sample: tuple[str, str]) -> None:
    assert_found(PASSPORT_SHAPE, sample, EntityType.PASSPORT)
    assert_masked(sample)


@given(embedded(id_card_numbers()))
def test_every_generated_id_card_shape_is_found(sample: tuple[str, str]) -> None:
    assert_found(ID_CARD_SHAPE, sample, EntityType.NATIONAL_ID)
    assert_masked(sample)


@given(embedded(card_shapes()))
def test_every_generated_card_shape_with_mixed_separators_is_found(
    sample: tuple[str, str],
) -> None:
    assert_found(CREDIT_CARD_SHAPE, sample, EntityType.CREDIT_CARD)
    assert_masked(sample)


# ------------------------------------------------------------------ leak hunt 1d


@given(embedded(loose_credit_cards()))
def test_every_luhn_valid_card_with_loose_separators_is_found(sample: tuple[str, str]) -> None:
    assert_found(CREDIT_CARD, sample, EntityType.CREDIT_CARD)
    assert_masked(sample)


@given(card_shapes_beside_short_numbers())
def test_every_card_shape_beside_a_short_number_is_found(sample: tuple[str, str]) -> None:
    assert_found(CREDIT_CARD_SHAPE, sample, EntityType.CREDIT_CARD)
    assert_masked(sample)


@given(disguised_passport_numbers())
def test_every_passport_shape_with_look_alikes_or_underscores_is_found(
    sample: tuple[str, str],
) -> None:
    assert_found(PASSPORT_SHAPE, sample, EntityType.PASSPORT)
    assert_masked(sample)
