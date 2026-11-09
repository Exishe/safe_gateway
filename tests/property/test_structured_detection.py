"""Hard rule 4: every generated structured identifier is detected, at its exact offsets."""

from hypothesis import given
from hypothesis import strategies as st

from gateway.engine.recognizers.credit_card import CREDIT_CARD
from gateway.engine.recognizers.iban import IBAN
from gateway.engine.recognizers.ru_inn import RU_INN
from gateway.engine.recognizers.ru_snils import RU_SNILS
from gateway.engine.recognizers.us_ssn import US_SSN
from gateway.engine.types import EntityType, Recognizer
from tests.strategies import credit_cards, embedded, ibans, ru_inns, ru_snilses, us_ssns


def assert_found(recognizer: Recognizer, sample: tuple[str, str], kind: EntityType) -> None:
    text, value = sample
    found = [(text[s.start : s.end], s.entity_type) for s in recognizer.find(text)]
    assert (value, kind) in found, f"{value!r} not found in {text!r}: {found}"


@given(embedded(credit_cards()))
def test_every_generated_card_is_found(sample: tuple[str, str]) -> None:
    assert_found(CREDIT_CARD, sample, EntityType.CREDIT_CARD)


@given(embedded(us_ssns()))
def test_every_generated_ssn_is_found(sample: tuple[str, str]) -> None:
    assert_found(US_SSN, sample, EntityType.SSN)


@given(embedded(ru_inns()))
def test_every_generated_inn_is_found(sample: tuple[str, str]) -> None:
    assert_found(RU_INN, sample, EntityType.NATIONAL_ID)


@given(embedded(ru_snilses()))
def test_every_generated_snils_is_found(sample: tuple[str, str]) -> None:
    assert_found(RU_SNILS, sample, EntityType.NATIONAL_ID)


@given(embedded(ibans()))
def test_every_generated_iban_is_found(sample: tuple[str, str]) -> None:
    assert_found(IBAN, sample, EntityType.IBAN)


@given(st.text(alphabet="0123456789 -.", max_size=60))
def test_recognizers_never_crash_on_digit_soup(text: str) -> None:
    for recognizer in (CREDIT_CARD, US_SSN, RU_INN, RU_SNILS, IBAN):
        for span in recognizer.find(text):
            assert 0 <= span.start < span.end <= len(text)
