import pytest

from gateway.engine.digits import accepted_windows, digit_runs, windows
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


def test_digit_runs_join_groups_across_a_spaced_hyphen_or_a_line_break() -> None:
    text = "a 123 - 45\n6780 b 12\r\n34 c 5  - 6"
    assert [[text[s:e] for s, e in run] for run in digit_runs(text)] == [
        ["123", "45", "6780"],
        ["12", "34"],
        ["5"],
        ["6"],
    ]


def test_digit_runs_end_where_the_digits_end_not_at_letters() -> None:
    text = "ab12cd scan_345.pdf"
    assert [[text[s:e] for s, e in run] for run in digit_runs(text)] == [["12"], ["345"]]


def test_windows_are_group_aligned_and_longest_first() -> None:
    run = [(0, 2), (3, 5), (6, 8)]  # three groups of two digits
    assert windows(run, 4, 6) == [(0, 8), (0, 5), (3, 8)]


def test_overlapping_valid_windows_are_all_accepted_but_contained_ones_are_not() -> None:
    run = [(0, 2), (3, 5), (6, 8)]
    assert accepted_windows(run, 4, 4, lambda _s, _e: True) == [(0, 5), (3, 8)]
    assert accepted_windows(run, 2, 6, lambda _s, _e: True) == [(0, 8)]


# ------------------------------------------------------------------ cards


def test_card_found_and_one_changed_digit_rejected() -> None:
    assert found(CREDIT_CARD, f"card {CARD}.") == [CARD]
    assert found(CREDIT_CARD, f"card {CARD[:-1]}2.") == []


def test_card_right_after_another_number_is_found() -> None:
    assert found(CREDIT_CARD, f"qty 2 {CARD}") == [CARD]


def test_card_is_found_even_when_a_longer_window_before_it_is_also_luhn_valid() -> None:
    # The postcode plus the card's first three groups is Luhn-valid by coincidence;
    # the card must still be reported.
    assert CARD in found(CREDIT_CARD, f"Moscow 659223 {CARD} paid")


def test_card_glued_to_letters_or_an_underscore_is_found() -> None:
    compact = CARD.replace(" ", "")
    assert found(CREDIT_CARD, f"scan_{compact}.pdf") == [compact]
    assert found(CREDIT_CARD, f"card{compact}x") == [compact]


@pytest.mark.parametrize(
    "number",
    [
        "0000 0000 0000 0000",  # pragma: allowlist secret
        "9000 0000 0000 0001",  # pragma: allowlist secret  (Luhn-valid, impossible prefix)
    ],
)
def test_luhn_valid_numbers_with_impossible_prefixes_are_not_cards(number: str) -> None:
    assert found(CREDIT_CARD, f"order {number}") == []


@pytest.mark.parametrize(
    "number",
    [
        "676343636590",  # pragma: allowlist secret  (Faker maestro)
        "501800000009",  # pragma: allowlist secret
        "5698 0012 3451",  # pragma: allowlist secret
        "691234567895",  # pragma: allowlist secret
    ],
)
def test_twelve_digit_maestro_numbers_are_cards(number: str) -> None:
    assert found(CREDIT_CARD, f"card {number} on file") == [number]


@pytest.mark.parametrize(
    "text",
    [
        "ref 411111111117 ok",  # pragma: allowlist secret  (Luhn-valid, not a Maestro IIN)
        "ref 701234567892 ok",  # pragma: allowlist secret  (Luhn-valid, not a Maestro IIN)
        "ref 501800000008 ok",  # pragma: allowlist secret  (Maestro IIN, Luhn-invalid)
        "tel 501800000009 77",  # pragma: allowlist secret  (12 digits inside a longer run)
    ],
)
def test_other_twelve_digit_numbers_are_not_cards(text: str) -> None:
    assert found(CREDIT_CARD, text) == []


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


@pytest.mark.parametrize(
    "text",
    [
        "ref123-45-6780",  # pragma: allowlist secret
        "123-45-6780x",  # pragma: allowlist secret
        "СНИЛС123456780",  # pragma: allowlist secret
    ],
)
def test_ssn_glued_to_a_letter_is_rejected(text: str) -> None:
    # SSN has no checksum: nine digits inside a word are far more likely a code.
    assert found(US_SSN, text) == []


def test_ssn_in_a_comb_field_is_found_as_check_this() -> None:
    comb = "1 2 3 4 5 6 7 8 0"  # pragma: allowlist secret
    (span,) = US_SSN.find(f"SSN: {comb}")
    assert (span.start, span.end, span.score) == (5, 5 + len(comb), 0.6)


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


@pytest.mark.parametrize("separator", ["-", "\n", "\r\n"])
def test_iban_groups_may_be_joined_by_a_hyphen_or_a_line_break(separator: str) -> None:
    written = IBAN_DE.replace(" ", separator)
    assert found(IBAN, f"pay to {written} today") == [written]


def test_iban_glued_after_letters_is_found() -> None:
    compact = IBAN_DE.replace(" ", "")
    assert found(IBAN, f"IBAN{compact} ok") == [compact]


def test_iban_with_two_separators_in_a_row_is_not_joined() -> None:
    assert found(IBAN, IBAN_DE.replace(" ", " - ", 1)) == []


def test_iban_with_a_bad_checksum_or_glued_text_is_rejected() -> None:
    assert found(IBAN, "DE89 3704 0044 0532 0130 01") == []  # pragma: allowlist secret
    assert found(IBAN, "DE89370400440532013000X") == []  # pragma: allowlist secret
