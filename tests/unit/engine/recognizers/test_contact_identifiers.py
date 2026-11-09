import pytest

from gateway.engine.recognizers.email import EMAIL
from gateway.engine.recognizers.ip_address import IP_ADDRESS
from gateway.engine.recognizers.phone import PhoneRecognizer
from gateway.engine.recognizers.url_token import URL_WITH_TOKEN
from gateway.engine.types import Recognizer


def found(recognizer: Recognizer, text: str) -> list[str]:
    return [text[s.start : s.end] for s in recognizer.find(text)]


# ------------------------------------------------------------------ email


def test_email_with_a_plus_tag_before_a_full_stop() -> None:
    text = "Write to anna.ivanova+tax@example.ru."
    assert found(EMAIL, text) == ["anna.ivanova+tax@example.ru"]


@pytest.mark.parametrize("text", ["@example.com alone", "a@b.c", "price 5 @ 10.00"])
def test_email_near_misses(text: str) -> None:
    assert found(EMAIL, text) == []


# ------------------------------------------------------------------ IP address


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("server 192.168.1.20, rack 3", ["192.168.1.20"]),
        ("v6 host 2001:db8::1.", ["2001:db8::1"]),
        ("ip:2001:db8::7 here", ["2001:db8::7"]),
    ],
)
def test_ip_addresses_are_found(text: str, expected: list[str]) -> None:
    assert found(IP_ADDRESS, text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "version 1.2.3.4.5",
        "out of range 999.1.1.1",
        "at 12:30:45 sharp",
        "mac aa:bb:cc:dd:ee:ff",
        "words like cafe and deadbeef",
    ],
)
def test_ip_near_misses(text: str) -> None:
    assert found(IP_ADDRESS, text) == []


# ------------------------------------------------------------------ URL with token


def test_url_with_a_token_is_found_whole() -> None:
    url = "https://example.com/reset?token=abc123XYZ&x=1"
    assert found(URL_WITH_TOKEN, f"Open {url} now") == [url]


def test_trailing_punctuation_is_not_part_of_the_url() -> None:
    url = "https://example.com/a?api_key=K3yK3yK3y"
    assert found(URL_WITH_TOKEN, f"(see {url}).") == [url]


@pytest.mark.parametrize(
    "text",
    ["https://example.com/page?id=5", "https://example.com/token/5", "token=abc outside a url"],
)
def test_urls_without_a_secret_parameter_are_ignored(text: str) -> None:
    assert found(URL_WITH_TOKEN, text) == []


# ------------------------------------------------------------------ phone


@pytest.mark.parametrize(
    "number",
    ["8 (912) 345-67-89", "+7 912 345-67-89", "077 123456", "+374 77 123456", "(201) 555-0123"],
)
def test_phones_in_national_and_international_formats(number: str) -> None:
    assert number in found(PhoneRecognizer(), f"call {number} today")


def test_phone_in_arabic_indic_digits() -> None:
    number = "".join(chr(0x660 + int(c)) if c.isdigit() else c for c in "+374 77 123456")
    assert number in found(PhoneRecognizer(), f"رقم {number} هنا")


def test_national_formats_need_their_region() -> None:
    assert found(PhoneRecognizer(regions=("US",)), "Yerevan 010 123456") == []
    assert found(PhoneRecognizer(regions=("AM",)), "Yerevan 010 123456") == ["010 123456"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("call (+12015550123) now", "+12015550123"),
        ("office ((201) 555-0123)", "(201) 555-0123"),
    ],
)
def test_unbalanced_brackets_around_a_phone_are_not_part_of_it(text: str, expected: str) -> None:
    assert expected in found(PhoneRecognizer(regions=("US",)), text)
    assert all(s.count("(") == s.count(")") for s in found(PhoneRecognizer(regions=("US",)), text))


def test_short_numbers_are_not_phones() -> None:
    assert found(PhoneRecognizer(), "room 12345, floor 3") == []
