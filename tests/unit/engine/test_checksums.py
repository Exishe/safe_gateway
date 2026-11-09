import pytest
from hypothesis import given
from hypothesis import strategies as st

from gateway.engine.checksums import digits_of, iban_valid, inn_valid, luhn_valid, snils_valid

# Published specification examples, not real accounts or people.
VALID_IBANS = [
    "DE89370400440532013000",  # pragma: allowlist secret
    "GB82WEST12345698765432",  # pragma: allowlist secret
    "FR1420041010050500013M02606",  # pragma: allowlist secret
    "NL91ABNA0417164300",  # pragma: allowlist secret
    "CH9300762011623852957",  # pragma: allowlist secret
]


def test_digits_of_reads_any_unicode_decimal_digit() -> None:
    # Arabic-Indic 4111, full-width 11, then ASCII 2.
    assert digits_of("\u0664\u0661\u0661\u0661 \uff11\uff11-2") == "4111112"


def test_luhn() -> None:
    assert luhn_valid("4111111111111111")  # pragma: allowlist secret
    assert not luhn_valid("4111111111111112")  # pragma: allowlist secret


@given(st.text(alphabet="0123456789", min_size=1, max_size=18))
def test_appending_the_luhn_check_digit_makes_a_valid_number(body: str) -> None:
    check = next(d for d in "0123456789" if luhn_valid(body + d))
    assert luhn_valid(body + check)


@pytest.mark.parametrize("iban", VALID_IBANS)
def test_iban_examples_are_valid(iban: str) -> None:
    assert iban_valid(iban)


@pytest.mark.parametrize("iban", VALID_IBANS)
def test_one_changed_digit_breaks_an_iban(iban: str) -> None:
    last = iban[-1]
    changed = iban[:-1] + ("1" if last != "1" else "2")
    assert not iban_valid(changed)


def test_iban_with_the_wrong_length_for_its_country_is_invalid() -> None:
    assert not iban_valid("DE8937040044053201300")  # pragma: allowlist secret


def test_iban_accepts_spaces_and_lower_case() -> None:
    assert iban_valid("de89 3704 0044 0532 0130 00")  # pragma: allowlist secret


def test_inn_ten_and_twelve_digits() -> None:
    assert inn_valid("7707083893")  # pragma: allowlist secret
    assert not inn_valid("7707083894")  # pragma: allowlist secret
    assert inn_valid("500100732259")  # pragma: allowlist secret
    assert not inn_valid("500100732258")  # pragma: allowlist secret
    assert not inn_valid("12345")


def test_snils() -> None:
    assert snils_valid("11223344595")  # pragma: allowlist secret
    assert not snils_valid("11223344596")  # pragma: allowlist secret
    # Numbers up to 001-001-998 were issued without a checksum: not treated as SNILS.
    assert not snils_valid("00100199800")
