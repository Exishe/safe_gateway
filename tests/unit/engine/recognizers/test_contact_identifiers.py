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


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("in _anna@example.com_ italics", "anna@example.com"),
        ("anna@example.com--thanks", "anna@example.com"),
        ("anna@example.com_", "anna@example.com"),
        ("from my_name@example.com today", "my_name@example.com"),
        ("anna@example.xn--p1ai here", "anna@example.xn--p1ai"),
        ("anna@xn--80ak6aa92e.com here", "anna@xn--80ak6aa92e.com"),
    ],
)
def test_email_edges_in_markdown_dashes_and_punycode(text: str, expected: str) -> None:
    assert found(EMAIL, text) == [expected]


@pytest.mark.parametrize("text", ["anna@example.com-x", "anna@example.com5"])
def test_email_glued_to_more_of_a_name_is_not_cut_short(text: str) -> None:
    assert found(EMAIL, text) == []


# ------------------------------------------------------------------ IP address


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("server 192.168.1.20, rack 3", ["192.168.1.20"]),
        ("v6 host 2001:db8::1.", ["2001:db8::1"]),
        ("ip:2001:db8::7 here", ["2001:db8::7"]),
        ("server 192.0.2.10:8080 down", ["192.0.2.10"]),
        ("db:192.0.2.10 down", ["192.0.2.10"]),
        ("node:192.0.2.10 down", ["192.0.2.10"]),
        ("Server 192.0.2.10.Next one", ["192.0.2.10"]),
    ],
)
def test_ip_addresses_are_found(text: str, expected: list[str]) -> None:
    assert found(IP_ADDRESS, text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "version 1.2.3.4.5",
        "build 10.0.19045.1234",
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
    "url",
    [
        "https://app.example.com/cb#access_token=abc123XYZ&state=1",
        "https://app.example.com/#/reset?token=abc123XYZ",
        "https://example.com/d?id=7&amp;token=abc123XYZ",
        "https://example.com/d?id=7&amp;amp;token=abc123XYZ",
        "https://example.com/reset?resetToken=abc123XYZ",
        "https://example.com/r?accessToken=abc123XYZ",
        "https://example.com/r?sessionKey=abc123XYZ",
        "https://example.com/r?clientsecret=abc123XYZ",
        "https://example.com/r?X-Amz-Signature=abc123XYZ",
    ],
)
def test_secrets_in_fragments_escaped_queries_and_camel_case_names(url: str) -> None:
    assert found(URL_WITH_TOKEN, f"Open {url} now") == [url]


@pytest.mark.parametrize(
    "text",
    [
        "https://example.com/page?id=5",
        "https://example.com/token/5",
        "token=abc outside a url",
        "https://example.com/zoo?animal=monkey&monkey=1",
        "https://example.com/docs#section-token",
    ],
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


@pytest.mark.parametrize(
    ("region", "text", "number"),
    [
        ("RU", "Иванов +7 912 345-67-89 125009 Москва", "+7 912 345-67-89"),
        ("AM", "Petrosyan +374 77 123456 0010 Yerevan", "+374 77 123456"),
        ("US", "Smith (201) 555-0123 10001 NY", "(201) 555-0123"),  # pragma: allowlist secret
        ("RU", "кв. 12 8 (912) 345-67-89", "8 (912) 345-67-89"),
    ],
)
def test_phone_next_to_another_number_on_the_same_line(region: str, text: str, number: str) -> None:
    assert number in found(PhoneRecognizer(regions=(region,)), text)


@pytest.mark.parametrize(
    "text",
    [
        "Invoice 20240115 total 4500 1234 5678",
        "SSN 123-45-6780 filed",  # pragma: allowlist secret
        "due 2024-01-15 or 15.01.2024, box 1 2 3 4 5 6 7 8 0",  # pragma: allowlist secret
    ],
)
def test_numbers_valid_only_without_their_national_prefix_are_not_phones(text: str) -> None:
    assert found(PhoneRecognizer(), text) == []
