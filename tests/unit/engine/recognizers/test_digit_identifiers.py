import pytest

from gateway.engine.digits import digit_runs, windows
from gateway.engine.recognizers.credit_card import CREDIT_CARD
from gateway.engine.recognizers.iban import IBAN
from gateway.engine.recognizers.ru_inn import RU_INN
from gateway.engine.recognizers.ru_snils import RU_SNILS
from gateway.engine.recognizers.us_ssn import US_SSN
from gateway.engine.types import Recognizer

CARD = "4111 1111 1111 1111"  # pragma: allowlist secret
IBAN_DE = "DE89 3704 0044 0532 0130 00"  # pragma: allowlist secret


def found(recognizer: Recognizer, text: str) -> list[str]:
    return [text[s.start : s.end] for s in recognizer.find(text)]


# ------------------------------------------------------------------ digit runs


def test_digit_runs_split_on_single_separators_only() -> None:
    text = "a 12 34-56 x 78  90"
    assert [[text[s:e] for s, e in run] for run in digit_runs(text)] == [
        ["12", "34", "56"],
        ["78"],
        ["90"],
    ]


def test_windows_are_group_aligned_and_longest_first() -> None:
    run = [(0, 2), (3, 5), (6, 8)]  # three groups of two digits
    assert windows(run, 4, 6) == [(0, 8), (0, 5), (3, 8)]


# ------------------------------------------------------------------ cards


def test_card_found_and_one_changed_digit_rejected() -> None:
    assert found(CREDIT_CARD, f"card {CARD}.") == [CARD]
    assert found(CREDIT_CARD, f"card {CARD[:-1]}2.") == []


def test_card_right_after_another_number_is_found() -> None:
    assert found(CREDIT_CARD, f"qty 2 {CARD}") == [CARD]


@pytest.mark.parametrize(
    "number",
    [
        "0000 0000 0000 0000",  # pragma: allowlist secret
        "9000 0000 0000 0001",  # pragma: allowlist secret  (Luhn-valid, impossible prefix)
    ],
)
def test_luhn_valid_numbers_with_impossible_prefixes_are_not_cards(number: str) -> None:
    assert found(CREDIT_CARD, f"order {number}") == []


def test_card_in_arabic_indic_digits_is_found_at_its_original_offsets() -> None:
    card = "".join(chr(0x660 + int(c)) if c.isdigit() else c for c in CARD)
    assert found(CREDIT_CARD, f"رقم {card} هنا") == [card]


# ------------------------------------------------------------------ SSN


def test_ssn_with_separators_scores_higher_than_bare_digits() -> None:
    (dashed,) = US_SSN.find("ssn 123-45-6780")  # pragma: allowlist secret
    (bare,) = US_SSN.find("ssn 123456780")  # pragma: allowlist secret
    assert dashed.score > bare.score >= 0.5


@pytest.mark.parametrize(
    "value",
    [
        "000-12-3456",  # pragma: allowlist secret  (area 000)
        "666-12-3456",  # pragma: allowlist secret  (area 666)
        "912-12-3456",  # pragma: allowlist secret  (area 9xx)
        "123-00-4567",  # pragma: allowlist secret  (group 00)
        "123-45-0000",  # pragma: allowlist secret  (serial 0000)
        "078-05-1120",  # pragma: allowlist secret  (the Woolworth wallet number)
        "12-345-6789",  # wrong grouping
    ],
)
def test_impossible_ssns_are_rejected(value: str) -> None:
    assert found(US_SSN, f"ssn {value}") == []


# ------------------------------------------------------------------ INN and SNILS


def test_inn_found_and_bad_checksum_rejected() -> None:
    assert found(RU_INN, "ИНН 7707083893,") == ["7707083893"]  # pragma: allowlist secret
    assert found(RU_INN, "ИНН 7707083894,") == []  # pragma: allowlist secret


def test_snils_in_its_usual_format() -> None:
    assert found(RU_SNILS, "СНИЛС 112-233-445 95") == ["112-233-445 95"]  # pragma: allowlist secret


# ------------------------------------------------------------------ IBAN


def test_iban_found_with_and_without_spaces() -> None:
    assert found(IBAN, f"pay to {IBAN_DE} today") == [IBAN_DE]
    compact = IBAN_DE.replace(" ", "")
    assert found(IBAN, f"pay to {compact}.") == [compact]


def test_iban_followed_by_an_upper_case_word_stops_at_its_length() -> None:
    assert found(IBAN, f"{IBAN_DE} BANK") == [IBAN_DE]


def test_iban_with_a_bad_checksum_or_glued_text_is_rejected() -> None:
    assert found(IBAN, "DE89 3704 0044 0532 0130 01") == []  # pragma: allowlist secret
    assert found(IBAN, "DE89370400440532013000X") == []  # pragma: allowlist secret
