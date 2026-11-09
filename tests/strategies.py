"""Hypothesis strategies that generate valid structured identifiers (fake by construction).

Every value is built from random digits plus a computed check digit, so none
belongs to a real person. Property tests assert that each one is detected.
"""

from faker import Faker
from hypothesis import assume
from hypothesis import strategies as st

from gateway.engine.checksums import IBAN_LENGTHS, inn_valid, luhn_valid, snils_valid

_fake = Faker(["en_US", "ru_RU"])
Faker.seed(20261109)
SENTENCES = [_fake.sentence(nb_words=8) for _ in range(200)]

SEPARATORS = (" ", "-", ".", "")
# Decimal digit zeros of several scripts: ASCII, Arabic-Indic, full-width, Devanagari.
DIGIT_ZEROS = (0x30, 0x660, 0xFF10, 0x966)


def _digits(size: int) -> st.SearchStrategy[str]:
    return st.text(alphabet="0123456789", min_size=size, max_size=size)


def formatted(digits: str, group_sizes: tuple[int, ...], separator: str) -> str:
    groups, index = [], 0
    for size in group_sizes:
        groups.append(digits[index : index + size])
        index += size
    return separator.join(groups)


def with_unicode_digits(value: str, zero: int) -> str:
    return "".join(chr(zero + int(c)) if c.isdigit() else c for c in value)


@st.composite
def embedded(draw: st.DrawFn, values: st.SearchStrategy[str]) -> tuple[str, str]:
    """(text, value): the value inside Faker sentences, sometimes in brackets or before a comma."""
    value = draw(values)
    before, after = draw(st.sampled_from(SENTENCES)), draw(st.sampled_from(SENTENCES))
    opening, closing = draw(st.sampled_from([("", ""), ("(", ")"), ("", ","), ("", ".")]))
    return f"{before} {opening}{value}{closing} {after}", value


# ------------------------------------------------------------------ cards

_CARD_PREFIXES = (("4", 16), ("4", 13), ("4", 19), ("51", 16), ("55", 16), ("2221", 16))
_CARD_PREFIXES += (("2720", 16), ("34", 15), ("37", 15), ("6011", 16), ("65", 16))
_CARD_GROUPS = {13: (4, 4, 5), 15: (4, 6, 5), 16: (4, 4, 4, 4), 19: (4, 4, 4, 4, 3)}


@st.composite
def credit_cards(draw: st.DrawFn) -> str:
    prefix, length = draw(st.sampled_from(_CARD_PREFIXES))
    body = prefix + draw(_digits(length - len(prefix) - 1))
    check = next(d for d in "0123456789" if luhn_valid(body + d))
    separator = draw(st.sampled_from(SEPARATORS))
    value = formatted(body + check, _CARD_GROUPS[length], separator)
    return with_unicode_digits(value, draw(st.sampled_from(DIGIT_ZEROS)))


# ------------------------------------------------------------------ US SSN

_INVALID_SSNS = {"078051120", "219099999", "123456789"}


@st.composite
def us_ssns(draw: st.DrawFn) -> str:
    area = draw(st.integers(1, 899).filter(lambda a: a != 666))
    group = draw(st.integers(1, 99))
    serial = draw(st.integers(1, 9999))
    digits = f"{area:03d}{group:02d}{serial:04d}"
    if digits in _INVALID_SSNS:
        digits = f"{area:03d}{group:02d}{(serial % 9998) + 1:04d}"
    separator = draw(st.sampled_from(SEPARATORS))
    return formatted(digits, (3, 2, 4), separator)


# ------------------------------------------------------------------ Russian INN and SNILS


@st.composite
def ru_inns(draw: st.DrawFn) -> str:
    length = draw(st.sampled_from([10, 12]))
    body = draw(_digits(length - (1 if length == 10 else 2)))
    for tail in (f"{n:0{length - len(body)}d}" for n in range(10 ** (length - len(body)))):
        if inn_valid(body + tail):
            return body + tail
    msg = "unreachable: every INN body has a valid check digit"
    raise AssertionError(msg)


@st.composite
def ru_snilses(draw: st.DrawFn) -> str:
    body = f"{draw(st.integers(1_001_999, 999_999_999)):09d}"
    check = next(f"{n:02d}" for n in range(100) if snils_valid(body + f"{n:02d}"))
    style = draw(st.sampled_from(["dashes", "spaces", "plain"]))
    if style == "dashes":
        return f"{body[:3]}-{body[3:6]}-{body[6:]} {check}"
    if style == "spaces":
        return f"{body[:3]} {body[3:6]} {body[6:]} {check}"
    return body + check


# ------------------------------------------------------------------ IBAN

_ALNUM = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


@st.composite
def ibans(draw: st.DrawFn) -> str:
    country = draw(st.sampled_from(sorted(IBAN_LENGTHS)))
    bban_length = IBAN_LENGTHS[country] - 4
    bban = draw(st.text(alphabet=_ALNUM, min_size=bban_length, max_size=bban_length))
    as_number = "".join(str(int(c, 36)) for c in bban + country + "00")
    check = f"{98 - int(as_number) % 97:02d}"
    compact = country + check + bban
    if draw(st.booleans()):
        return " ".join(compact[i : i + 4] for i in range(0, len(compact), 4))
    return compact


# ------------------------------------------------------------------ email, IP, token URLs, phones

import ipaddress  # noqa: E402

import phonenumbers  # noqa: E402
from phonenumbers import PhoneNumberFormat, PhoneNumberType  # noqa: E402

from gateway.engine.recognizers.phone import DEFAULT_REGIONS  # noqa: E402

EMAILS = [_fake.email() for _ in range(300)] + [_fake.company_email() for _ in range(100)]
TOKEN_PARAMS = ("token", "access_token", "api_key", "apikey", "key", "sig", "signature")
TOKEN_PARAMS += ("auth", "password", "secret", "session", "refresh_token", "X-Amz-Signature")


def emails() -> st.SearchStrategy[str]:
    return st.sampled_from(EMAILS)


def ip_addresses() -> st.SearchStrategy[str]:
    # The unspecified address (0.0.0.0, ::) identifies no one.
    return (
        st.ip_addresses()
        .filter(lambda ip: not ip.is_unspecified)
        .map(lambda ip: str(ipaddress.ip_address(ip)))
    )


@st.composite
def token_urls(draw: st.DrawFn) -> str:
    host = draw(st.sampled_from([_fake.domain_name() for _ in range(50)]))
    path = draw(st.sampled_from(["", "/", "/reset", "/api/v2/files/42", "/s/abc-def"]))
    secret = draw(st.from_regex(r"[A-Za-z0-9_-]{8,40}", fullmatch=True))
    param = draw(st.sampled_from(TOKEN_PARAMS))
    others = draw(st.lists(st.sampled_from(["id=7", "lang=en", "page=2"]), max_size=2))
    query = "&".join([*others[:1], f"{param}={secret}", *others[1:]])
    return f"{draw(st.sampled_from(['https', 'http']))}://{host}{path}?{query}"


_PHONE_FORMATS = (PhoneNumberFormat.E164, PhoneNumberFormat.INTERNATIONAL)
_PHONE_FORMATS += (PhoneNumberFormat.NATIONAL,)


@st.composite
def phones(draw: st.DrawFn) -> str:
    region = draw(st.sampled_from(DEFAULT_REGIONS))
    kind = draw(st.sampled_from([PhoneNumberType.MOBILE, PhoneNumberType.FIXED_LINE]))
    example = phonenumbers.example_number_for_type(region, kind)
    assume(example is not None)
    assert example is not None  # for the type checker
    national = str(example.national_number)
    tail = draw(_digits(4))
    number = phonenumbers.parse(f"+{example.country_code}{national[:-4]}{tail}", None)
    if not phonenumbers.is_valid_number(number):
        number = example
    text = phonenumbers.format_number(number, draw(st.sampled_from(_PHONE_FORMATS)))
    # phonenumbers reads Arabic-Indic and full-width digits; Devanagari is not promised.
    return with_unicode_digits(text, draw(st.sampled_from(DIGIT_ZEROS[:3])))
