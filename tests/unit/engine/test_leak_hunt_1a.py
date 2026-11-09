"""Leak-hunter round 1 (Phase 1a): inputs where detect() misses, mis-spans or stalls.

Every test here failed when it was written. Each one names the trick; the
docstring says what detect() returned instead. All values are fake: published
test numbers (4111..., 5500...0004, DE89..., 7707083893, 112-233-445 95),
reserved ranges (555-01xx, 192.0.2.0/24, example.com) or invalid-by-construction.
Separators are built with chr() so the source holds no invisible characters.
"""

import time

import pytest
from faker import Faker

from gateway.engine.detect import EngineConfig, detect
from gateway.engine.recognizers.gazetteer import Gazetteer
from gateway.engine.types import EntityType as E

Case = tuple[str, str, E]  # (text, value as written, expected type)

NBSP = chr(0x00A0)  # no-break space (Word, PDF, web copy)
NNBSP = chr(0x202F)  # narrow no-break space (CLDR digit grouping for fr, ru)
THIN = chr(0x2009)  # thin space
EN_DASH = chr(0x2013)
NB_HYPHEN = chr(0x2011)  # Word's non-breaking hyphen
ZWSP = chr(0x200B)  # zero-width space
ZWJ = chr(0x200D)  # zero-width joiner
SHY = chr(0x00AD)  # soft hyphen

CARD = "4111 1111 1111 1111"  # pragma: allowlist secret
CARD_COMPACT = "4111111111111111"  # pragma: allowlist secret
SSN = "123-45-6780"  # pragma: allowlist secret
INN = "7707083893"  # pragma: allowlist secret
SNILS = "112-233-445 95"  # pragma: allowlist secret
SNILS_COMPACT = "11223344595"  # pragma: allowlist secret
IBAN = "DE89 3704 0044 0532 0130 00"  # pragma: allowlist secret
IBAN_COMPACT = "DE89370400440532013000"  # pragma: allowlist secret
RU_PHONE = "+7 912 345 67 89"  # pragma: allowlist secret
FR_PHONE = "06 12 34 56 78"  # pragma: allowlist secret


def found(text: str, config: EngineConfig | None = None) -> list[tuple[str, E]]:
    return [(text[s.start : s.end], s.entity_type) for s in detect(text, config=config)]


def misses(cases: list[Case], config: EngineConfig | None = None) -> list[str]:
    """Every case whose value is not returned with its exact span and type."""
    report = []
    for text, value, kind in cases:
        got = found(text, config)
        if (value, kind) not in got:
            report.append(f"{value!r} as {kind} in {text!r}: detect() returned {got!r}")
    return report


def unmasked(cases: list[Case], config: EngineConfig | None = None) -> list[str]:
    """Every case with a letter or digit of its value outside all spans, or no span of its type.

    For values that share digits with a longer checksum-valid coincidence: the
    merge keeps the longer span and the value keeps only the rest (ADR 0013),
    so the value is masked whole but not as one span of its own.
    """
    report = []
    for text, value, kind in cases:
        spans = detect(text, config=config)
        start = text.index(value)
        end = start + len(value)
        over = [s for s in spans if s.start < end and s.end > start]
        bare = [i for i in range(start, end) if not any(s.start <= i < s.end for s in over)]
        leaked = "".join(text[i] for i in bare if text[i].isalnum())
        if leaked or kind not in {s.entity_type for s in over}:
            got = [(text[s.start : s.end], s.entity_type) for s in spans]
            report.append(f"{value!r} as {kind} in {text!r}: {leaked!r} unmasked; got {got!r}")
    return report


def embedded(value: str, kind: E, before: str = "number", after: str = "here") -> Case:
    return (f"{before} {value} {after}", value, kind)


def seconds(text: str, config: EngineConfig | None = None) -> float:
    started = time.perf_counter()
    detect(text, config=config)
    return time.perf_counter() - started


# ------------------------------------------------------------------ separators


def test_digit_groups_with_unicode_spaces_dashes_or_line_breaks() -> None:
    """digits._RUN joins groups only with one ASCII space, hyphen or dot, and
    phonenumbers does not accept U+2009/U+202F; every case returns []."""
    cases = [
        embedded(CARD.replace(" ", NBSP), E.CREDIT_CARD),
        embedded(CARD.replace(" ", EN_DASH), E.CREDIT_CARD),
        embedded(CARD[:9] + "\n" + CARD[10:], E.CREDIT_CARD),
        embedded(SSN.replace("-", NNBSP), E.SSN),
        embedded(SSN.replace("-", NB_HYPHEN), E.SSN),
        embedded(SSN.replace("-", " - "), E.SSN),
        embedded(SNILS.replace("-", THIN).replace(" ", THIN), E.NATIONAL_ID),
        embedded(RU_PHONE.replace(" ", NNBSP), E.PHONE),
        embedded(FR_PHONE.replace(" ", NNBSP), E.PHONE),
    ]
    assert misses(cases) == []


def test_invisible_format_characters_inside_identifiers() -> None:
    """Zero-width space/joiner and soft hyphen (category Cf) split the value:
    [] for card, SSN, INN, phone and IBAN; the e-mail keeps only 'na@example.com'."""
    cases = [
        embedded(CARD.replace(" ", ZWSP), E.CREDIT_CARD),
        embedded(SSN.replace("-", SHY), E.SSN),
        embedded(INN[:4] + ZWJ + INN[4:], E.NATIONAL_ID),
        embedded(RU_PHONE.replace(" ", ZWJ), E.PHONE),
        embedded(IBAN.replace(" ", ZWSP), E.IBAN),
        embedded(f"an{ZWSP}na@example.com", E.EMAIL),
    ]
    assert misses(cases) == []


def test_iban_with_hyphens_no_break_spaces_or_a_line_break() -> None:
    """iban._collect allows only single ASCII spaces: [] or just the tail
    '0532-0130-00' as NATIONAL_ID, leaving country, check digits and bank code."""
    cases = [
        embedded(IBAN.replace(" ", "-"), E.IBAN),
        embedded(IBAN.replace(" ", NBSP), E.IBAN),
        embedded(IBAN[:14] + "\n" + IBAN[15:], E.IBAN),
    ]
    assert misses(cases) == []


# ------------------------------------------------------------------ boundaries and shapes


def test_checksummed_identifier_glued_to_letters_or_underscore() -> None:
    """(?<!\\w)/(?!\\w) in digits._RUN and iban._START reject checksum-valid values
    in file names or after a label without a space: detect() returns []."""
    cases: list[Case] = [
        (f"file scan_{CARD_COMPACT}.pdf", CARD_COMPACT, E.CREDIT_CARD),
        (f"file snils_{SNILS_COMPACT}.pdf", SNILS_COMPACT, E.NATIONAL_ID),
        (f"СНИЛС{SNILS_COMPACT} ok", SNILS_COMPACT, E.NATIONAL_ID),
        (f"IBAN{IBAN_COMPACT} ok", IBAN_COMPACT, E.IBAN),
    ]
    assert misses(cases) == []


def test_ssn_in_a_pdf_comb_field_one_digit_per_box() -> None:
    """us_ssn accepts only group sizes (3, 2, 4) or (9,); a form field with one
    digit per box extracts as nine single digits and returns []."""
    comb = " ".join(SSN.replace("-", ""))
    assert misses([(f"SSN: {comb}", comb, E.SSN)]) == []


@pytest.mark.xfail(run=False, strict=True, reason="known limit: see docs/SECURITY.md")
def test_ocr_letter_for_digit_noise() -> None:
    """No OCR confusable handling (ADR 0011, point 9): O for 0 returns []."""
    card = "5500 0000 0000 0004".replace("0", "O")  # pragma: allowlist secret
    cases = [
        embedded(card, E.CREDIT_CARD),
        embedded(SSN[:-1] + "O", E.SSN),
    ]
    assert misses(cases) == []


# ------------------------------------------------------------------ window choice and merge


def test_card_after_a_year_or_postcode_loses_its_last_group() -> None:
    """windows() tries equal-length windows earliest first, so the window made of the
    leading number plus the card's first three groups wins when it happens to be
    Luhn-valid: the CREDIT_CARD span starts at '2028' and the card's last group leaks.

    Both coincidences stay Luhn-valid and at least as long as the card, so they keep
    their span under ADR 0013; the check is that no digit of the card is left unmasked."""
    cases = [
        embedded(CARD, E.CREDIT_CARD, before="Ivanov 2028", after="paid"),
        embedded(CARD, E.CREDIT_CARD, before="Moscow 659223", after="paid"),
    ]
    assert unmasked(cases) == []


def test_identifier_after_a_number_is_dropped_for_a_longer_coincidental_window() -> None:
    """merge() drops a shorter span that only partly overlaps a longer one: a
    coincidental INN (the ZIP plus the SSN's first two groups) beats the SSN, whose
    serial leaks; a coincidental card (the SNILS's last three groups plus the next
    number) beats the SNILS, whose first group leaks.

    The longer coincidence still wins the span under ADR 0013; the check is that the
    loser keeps every digit the winner does not cover."""
    cases = [
        embedded(SSN, E.SSN, before="Springfield IL 55794", after="filed"),
        embedded(SNILS, E.NATIONAL_ID, before="Row 798114", after="245502 end"),
    ]
    assert unmasked(cases) == []


# ------------------------------------------------------------------ email


def test_email_followed_by_underscore_or_hyphen() -> None:
    """The (?![\\w-]) lookahead fails on markdown '_..._' and on '--': []."""
    cases: list[Case] = [
        ("write _anna@example.com_ now", "anna@example.com", E.EMAIL),
        ("write anna@example.com--thanks", "anna@example.com", E.EMAIL),
    ]
    assert misses(cases) == []


def test_email_with_a_punycode_tld() -> None:
    """The TLD must be letters only, so .xn--p1ai (.рф) and .xn--y9a3aq (.հայ) return []."""
    cases = [
        embedded("anna@example.xn--p1ai", E.EMAIL),
        embedded("anna@example.xn--y9a3aq", E.EMAIL),
    ]
    assert misses(cases) == []


# ------------------------------------------------------------------ IP address


def test_ipv4_with_a_port_a_hex_label_or_a_glued_sentence() -> None:
    """ip_address validates the whole [0-9A-Fa-f:.] run ('192.0.2.10:8080',
    'db:192.0.2.10') and checks glue before trimming the full stop: []."""
    ip = "192.0.2.10"
    cases: list[Case] = [
        (f"server {ip}:8080 down", ip, E.IP_ADDRESS),
        (f"db:{ip} down", ip, E.IP_ADDRESS),
        (f"Server {ip}.Next one", ip, E.IP_ADDRESS),
    ]
    assert misses(cases) == []


# ------------------------------------------------------------------ URL with token


def test_token_in_a_url_fragment() -> None:
    """_parameter_names reads only the query; OAuth implicit flow puts
    access_token after '#': []."""
    url = "https://app.example.com/cb#access_token=Zq9Xv2Lm8Pw4"  # pragma: allowlist secret
    assert misses([embedded(url, E.URL_WITH_TOKEN)]) == []


def test_token_url_with_an_html_escaped_ampersand() -> None:
    """Copied from HTML, '&amp;token=' makes the parameter name 'amp;token': []."""
    url = "https://files.example.com/d?id=7&amp;token=Zq9Xv2Lm8Pw4"  # pragma: allowlist secret
    assert misses([embedded(url, E.URL_WITH_TOKEN)]) == []


def test_token_url_with_a_camel_case_parameter() -> None:
    """Suffixes need '_' or '-', so 'resetToken' and 'accessToken' return []."""
    reset = "https://example.com/reset?resetToken=Zq9Xv2Lm8Pw4"  # pragma: allowlist secret
    access = "https://example.com/r?accessToken=Zq9Xv2Lm8Pw4"  # pragma: allowlist secret
    cases = [embedded(reset, E.URL_WITH_TOKEN), embedded(access, E.URL_WITH_TOKEN)]
    assert misses(cases) == []


# ------------------------------------------------------------------ phone


def test_phone_followed_by_another_number_on_the_same_line() -> None:
    """With Leniency.VALID the trailing postcode joins the candidate and the whole
    phone is dropped: [] for RU and AM; for US only '555-0123 10001' as NATIONAL_ID.

    '555-0123 10001' is a checksum-valid INN as long as the US phone and scores higher,
    so it keeps its span under ADR 0013; the US check is that no digit is left unmasked."""
    ru = "+7 912 345-67-89"  # pragma: allowlist secret
    am = "+374 77 123456"  # pragma: allowlist secret
    us = "(201) 555-0123"  # pragma: allowlist secret
    cases = [
        embedded(ru, E.PHONE, before="Иванов", after="125009 Москва"),
        embedded(am, E.PHONE, before="Petrosyan", after="0010 Yerevan"),
    ]
    assert misses(cases) == []
    assert unmasked([embedded(us, E.PHONE, before="Smith", after="10001 NY")]) == []


def test_phone_after_65535_other_numbers_is_still_found() -> None:
    """PhoneNumberMatcher stops after max_tries=65535 candidates, so a phone below a
    long numeric column (384 KB here) is silently skipped: no PHONE span at the end."""
    phone = "+7 912 345-67-89"  # pragma: allowlist secret
    text = "\n".join(str(1000 + row) for row in range(65_600)) + f"\nContact: {phone}\n"
    config = EngineConfig(phone_regions=("RU",))
    assert misses([(text, phone, E.PHONE)], config) == []


# ------------------------------------------------------------------ gazetteer


def test_gazetteer_entry_matched_through_turkish_dotted_or_dotless_i() -> None:
    """re.IGNORECASE matches capital dotted I (U+0130) and dotless i (U+0131) to 'i',
    but str.casefold() gives a different key, not in Gazetteer._types, so detect()
    raises KeyError."""
    config = EngineConfig(gazetteer=Gazetteer({"Istanbul": E.LOCATION, "Ivan": E.PERSON}))
    cases: list[Case] = [
        ("Office in İstanbul.", "İstanbul", E.LOCATION),
        ("Signed by ıvan.", "ıvan", E.PERSON),
    ]
    assert misses(cases, config) == []


# ------------------------------------------------------------------ time (1 s per ~100 KB)


@pytest.mark.xfail(run=False, strict=True, reason="known limit: see docs/SECURITY.md")
def test_100kb_of_single_spaced_digits_is_scanned_within_one_second() -> None:
    """digits.accepted_windows checks every window against every taken window
    (quadratic); with phone parsing this took ~18 s."""
    state, digits = 20261109, []
    for _ in range(50_000):  # a fixed linear congruential sequence: reproducible, no random
        state = (state * 1_103_515_245 + 12_345) % 2**31
        digits.append(str(state % 10))
    text = " ".join(digits)
    elapsed = seconds(text)
    assert elapsed < 1.0, f"{elapsed:.2f}s for {len(text)} characters"


def test_100kb_of_short_emails_is_merged_within_one_second() -> None:
    """merge() compares each candidate with every kept span (quadratic); 14k
    e-mail spans took ~4.7 s."""
    text = "a@b.cd " * 14_286
    elapsed = seconds(text)
    assert elapsed < 1.0, f"{elapsed:.2f}s for {len(text)} characters"


@pytest.mark.xfail(run=False, strict=True, reason="known limit: see docs/SECURITY.md")
def test_100kb_phone_directory_is_scanned_within_one_second() -> None:
    """Nine full PhoneNumberMatcher passes over the text: ~3 s for 3,300 contacts."""
    fake = Faker("en_US")
    fake.seed_instance(3)
    lines, size, row = [], 0, 0
    while size < 100_000:
        line = f"{fake.name()}: (201) 555-01{row % 100:02d}"
        lines.append(line)
        size += len(line) + 1
        row += 1
    text = "\n".join(lines)
    elapsed = seconds(text)
    assert elapsed < 1.0, f"{elapsed:.2f}s for {len(text)} characters"
