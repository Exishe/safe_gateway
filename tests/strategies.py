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


# ------------------------------------------------------------------ Phase 2: masking

import itertools  # noqa: E402

from gateway.engine.types import EntityType, Span  # noqa: E402

# Pieces of placeholders and their tolerant forms (ADR 0003), whole and broken, so
# generated texts keep landing on the edges of the token grammar.
PLACEHOLDER_FRAGMENTS = (
    "[PERSON_1]",
    "[person 1]",
    "[Person-2]",
    "PERSON_1",
    "PERSON 2",
    "person 1",
    "[LITERAL_1]",
    "LITERAL_2",
    "[DATE_OF_BIRTH_1]",
    "DATE_OF_BIRTH 3",
    "SSN_1",
    "[EMAIL_12]",
    "[SSN_",
    "_1]",
    "PERSON",
    "DATE",
    "[",
    "]",
    "_",
    "-",
    " ",
    "1",
    "12",
    "X",
    # Non-ASCII word characters touching a token (ADR 0014 point 3): a full-width two,
    # an Arabic-Indic one, a Cyrillic letter, an Armenian letter; and a long s and a
    # Kelvin sign, which Unicode case folding would read as `s` and `k`.
    chr(0xFF12),
    chr(0x661),
    chr(0x436),
    chr(0x561),
    f"[PER{chr(0x17F)}ON_1]",
    f"[{chr(0x212A)}EY_1]",
    "١",  # Arabic-Indic one
)


def text_pieces() -> st.SearchStrategy[str]:
    """Arbitrary unicode, placeholder fragments and structured identifiers."""
    return st.one_of(
        st.text(max_size=10),
        st.sampled_from(PLACEHOLDER_FRAGMENTS),
        credit_cards(),
        us_ssns(),
        ibans(),
        emails(),
        phones(),
    )


@st.composite
def texts_with_identifiers(draw: st.DrawFn) -> str:
    pieces = draw(st.lists(text_pieces(), max_size=8))
    return draw(st.sampled_from(["", " ", "\n"])).join(pieces)


@st.composite
def placeholder_texts(draw: st.DrawFn) -> str:
    """Texts made mostly of placeholder fragments, for rehydrate and streaming."""
    pieces = st.one_of(st.sampled_from(PLACEHOLDER_FRAGMENTS), st.text(max_size=4))
    return "".join(draw(st.lists(pieces, max_size=12)))


# Pieces of links (ADR 0003 amendment 1): openers and closers of every context in
# which a placeholder is kept as written, so generated texts move in and out of them.
LINK_FRAGMENTS = (
    "](",
    "![s](",
    ")",
    "(",
    "<",
    ">",
    "https://",
    "//",
    "/",
    "www.",
    "<https:",
    "<mailto:",
    "<img src=",
    '<a href="',
    "<p title='",
    "=",
    '"',
    "'",
    "\n",
    "\n\n",
    "[1]: ",
    "\n[1]:",
    " ",
    "?d=",
)


@st.composite
def link_texts(draw: st.DrawFn) -> str:
    """Placeholder fragments mixed with pieces of Markdown and HTML links."""
    pieces = st.one_of(
        st.sampled_from(PLACEHOLDER_FRAGMENTS),
        st.sampled_from(LINK_FRAGMENTS),
        st.text(max_size=3),
    )
    return "".join(draw(st.lists(pieces, max_size=16)))


LINK_OPENERS = (
    "![s](",
    "[x](",
    "[x](<",
    "https://attacker.example/",
    "//attacker.example/",
    "www.attacker.example/",
    "<https://attacker.example/",
    "<mailto:x@attacker.example?body=",
    '<img src="',
    "<img src='",
    "<img src=",
    '<a title="t" href = "',
    "[1]: ",
    "[1]:\n<",
)


@st.composite
def link_attacks(draw: st.DrawFn, token: str) -> str:
    """`token` placed inside a link that the text opens, after any text that ends a line."""
    before = draw(st.sampled_from(["", "Done.\n\n", "Text [PERSON_1] text\n\n", "a b\n"]))
    opener = draw(st.sampled_from(LINK_OPENERS))
    middle = draw(st.text(alphabet="abc123/?=&.-_%", max_size=12))
    after = draw(st.sampled_from(["", ")", ">", '">', "' alt=x>", " more", "\n"]))
    # `=` before the token: a word character there would make it no token at all.
    return f"{before}{opener}{middle}={token}{after}"


@st.composite
def any_spans(draw: st.DrawFn, text: str, *, disjoint: bool) -> list[Span]:
    """Hand-made spans over `text`: disjoint (touching allowed), or anything."""
    kinds = st.sampled_from(list(EntityType))
    if not text:
        return []
    if disjoint:
        cuts = sorted(draw(st.sets(st.integers(0, len(text)), max_size=8)))
        pairs = zip(cuts[0::2], cuts[1::2], strict=False)
        return [Span(a, b, draw(kinds), 0.9, "test") for a, b in pairs if a < b]
    starts = draw(st.lists(st.integers(0, len(text) - 1), max_size=5))
    spans = []
    for start in starts:
        end = draw(st.integers(start + 1, len(text)))
        spans.append(Span(start, end, draw(kinds), 0.9, "test"))
    return spans


@st.composite
def chunkings(draw: st.DrawFn, text: str) -> list[str]:
    """`text` cut into consecutive chunks at random points (empty chunks included)."""
    cuts = sorted(draw(st.lists(st.integers(0, len(text)), max_size=12)))
    bounds = [0, *cuts, len(text)]
    return [text[a:b] for a, b in itertools.pairwise(bounds)]


# ------------------------------------------------------------------ dates (ADR 0016)

import calendar  # noqa: E402

EN_MONTH_NAMES = tuple(calendar.month_name[1:])
RU_MONTHS_GENITIVE = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля")
RU_MONTHS_GENITIVE += ("августа", "сентября", "октября", "ноября", "декабря")
RU_MONTHS_SHORT = ("янв.", "февр.", "мар.", "апр.", "мая", "июн.", "июл.")
RU_MONTHS_SHORT += ("авг.", "сент.", "окт.", "нояб.", "дек.")


def _ordinal(day: int) -> str:
    if day in {11, 12, 13}:
        return f"{day}th"
    return f"{day}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th') }"


def _date_forms(day: int, month: int, year: int, *, pad: bool) -> list[str]:
    d, m = (f"{day:02d}", f"{month:02d}") if pad else (str(day), str(month))
    name = EN_MONTH_NAMES[month - 1]
    return [
        f"{d}.{m}.{year}",
        f"{d}/{m}/{year}",
        f"{m}/{d}/{year}",
        f"{d}-{m}-{year}",
        f"{year}-{month:02d}-{day:02d}",
        f"{day:02d}.{month:02d}.{year % 100:02d}",
        f"{day} {name} {year}",
        f"{name} {day}, {year}",
        f"{name[:3]} {day} {year}",
        f"{day} {name[:3]}. {year}",
        f"{name} {_ordinal(day)}, {year}",
        f"{day} {RU_MONTHS_GENITIVE[month - 1]} {year}",
        f"{day} {RU_MONTHS_SHORT[month - 1]} {year}",
        # Leak hunt 1c (ADR 0016 amended): a quoted day, month names between separators.
        f"{chr(0xAB)}{day}{chr(0xBB)} {RU_MONTHS_GENITIVE[month - 1]} {year}",
        f"{d}-{name[:3]}-{year}",
        f"{d}/{name[:3]}/{year}",
        f"{year}-{name[:3]}-{d}",
        f"{d}-{RU_MONTHS_SHORT[month - 1].rstrip('.')}-{year}",
    ]


@st.composite
def dates(draw: st.DrawFn) -> str:
    """A real calendar date in one of the forms DateRecognizer reads, sometimes in capitals."""
    year = draw(st.integers(1900, 2099))
    month = draw(st.integers(1, 12))
    day = draw(st.integers(1, calendar.monthrange(year, month)[1]))
    value = draw(st.sampled_from(_date_forms(day, month, year, pad=draw(st.booleans()))))
    return value.upper() if draw(st.booleans()) else value


# ------------------------------------------------------------------ shapes (ADR 0023)

# Gaps a "+" phone may use (ADR 0023 amended in 1c): one space, dot, hyphen or slash,
# two spaces, or a spaced hyphen; since 1d also tabs and a line break with spaces round it.
PLUS_PHONE_GAPS = (" ", "  ", ".", "-", "/", " - ", "\t", "\t\t", "\n", " \n", "\n  ", "\r\n")
# Gaps a card shape may use, mixed freely: one space, dot or hyphen, or two spaces.
CARD_GAPS = (" ", "  ", ".", "-")
_UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


@st.composite
def plus_phones(draw: st.DrawFn) -> str:
    """'+99' (not an assigned country code) and groups of 1-4 digits, 10-15 digits in all."""
    total = draw(st.integers(10, 15))
    groups, left = ["99"], total - 2
    while left:
        size = draw(st.integers(1, min(4, left)))
        groups.append(draw(_digits(size)))
        left -= size
    if len(groups) > 2 and draw(st.booleans()):
        groups[1] = f"( {groups[1]} )" if draw(st.booleans()) else f"({groups[1]})"
    value = "+" + groups[0]
    for group in groups[1:]:
        value += draw(st.sampled_from(PLUS_PHONE_GAPS)) + group
    return value


@st.composite
def passport_numbers(draw: st.DrawFn) -> str:
    """Two letters and seven digits, upper or lower case."""
    letters = draw(st.text(alphabet=_UPPER, min_size=2, max_size=2))
    value = letters + draw(_digits(7))
    return value.lower() if draw(st.booleans()) else value


@st.composite
def id_card_numbers(draw: st.DrawFn) -> str:
    """Two letters, five digits, two letters."""
    first = draw(st.text(alphabet=_UPPER, min_size=2, max_size=2))
    last = draw(st.text(alphabet=_UPPER, min_size=2, max_size=2))
    return first + draw(_digits(5)) + last


_CARD_SHAPE_PREFIXES = {16: "23456", 15: "3", 19: "3456"}


@st.composite
def card_shapes(draw: st.DrawFn) -> str:
    """A card-shaped number (Luhn not checked), contiguous or grouped by four, each gap
    drawn on its own from CARD_GAPS, so separators are mixed."""
    length = draw(st.sampled_from(sorted(_CARD_SHAPE_PREFIXES)))
    first = draw(st.sampled_from(_CARD_SHAPE_PREFIXES[length]))
    digits = first + draw(_digits(length - 1))
    if draw(st.booleans()):
        return digits
    groups = [digits[i : i + 4] for i in range(0, length, 4)]
    value = groups[0]
    for group in groups[1:]:
        value += draw(st.sampled_from(CARD_GAPS)) + group
    return value


# ------------------------------------------------------------------ leak hunt 1d (ADR 0023 amended)

# Gaps a Luhn-valid card may also use: one to three characters that are neither letters
# nor digits, nor a comma or semicolon (a list).
LOOSE_CARD_GAPS = ("/", "\t", "   ", "- ", ". ", "\n ", " / ", "_", "|", " ", "-", "\n")


@st.composite
def loose_credit_cards(draw: st.DrawFn) -> str:
    """A Luhn-valid card in its usual grouping, each gap drawn from LOOSE_CARD_GAPS."""
    prefix, length = draw(st.sampled_from(_CARD_PREFIXES))
    body = prefix + draw(_digits(length - len(prefix) - 1))
    check = next(d for d in "0123456789" if luhn_valid(body + d))
    groups, index = [], 0
    for size in _CARD_GROUPS[length]:
        groups.append((body + check)[index : index + size])
        index += size
    value = groups[0]
    for group in groups[1:]:
        value += draw(st.sampled_from(LOOSE_CARD_GAPS)) + group
    return value


@st.composite
def card_shapes_beside_short_numbers(draw: st.DrawFn) -> tuple[str, str]:
    """(text, card): a grouped card shape after a quantity or before an expiry date."""
    card = draw(card_shapes().filter(lambda value: not value.isdigit()))
    short = draw(_digits(draw(st.integers(1, 3))))
    gap = draw(st.sampled_from(CARD_GAPS))
    if draw(st.booleans()):
        return f"Item qty {short}{gap}{card} charged.", card
    month = draw(st.integers(1, 12))
    return f"Please charge {card}{gap}{month:02d}/29 today.", card
