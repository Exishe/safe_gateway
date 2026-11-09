"""Leak-hunter round 3 (Phase 1c): dates, ADR 0023 shapes and keywords, routing by script.

Every test here failed when it was written (run with --runxfail). Each one names the
trick; the docstring says what detect() returned instead. Where it makes sense a test
first asserts a control, the same value in a form the engine already masks, so what
fails is the trick, not the value. Values are invalid by construction or Faker-style
inventions. Characters that are invisible or easily confused are built with chr(), so
the source holds none of them.

Tried and handled (no test): ner_view/composed_view offsets (200,000 random strings of
NFD marks, composition exclusions, Hangul, invisibles, surrogates, dotted I: every view
character maps back to a range whose NFC holds it), merge.defer (20,000 random phone
strings: no digit a phone rule found was left uncovered), GLiNER windows (an Armenian
name behind up to ~1,950 sub-word tokens of dense filler in its 128-word window is found).
"""

from collections.abc import Sequence

import pytest

import gateway.engine.detect as detect_module
from gateway.engine.detect import detect
from gateway.engine.gliner_ner import gliner_model_dir
from gateway.engine.ner import SPACY_MODEL_DIRS, ModelMissing

# Each finding stays a strict xfail until its fix lands; the mark is then removed.
FINDING = pytest.mark.xfail(strict=True, reason="engine red-team finding (1c)")

Case = tuple[str, str]  # (text, value as written)

SSN = "123-45-6780"  # pragma: allowlist secret
CARD = "4111 1111 1111 1111"  # pragma: allowlist secret
IBAN = "DE89 3704 0044 0532 0130 00"  # pragma: allowlist secret
CARD_LUHN_INVALID = "4000 1234 5678 9010"  # pragma: allowlist secret
# +99 is not an assigned country code: a mistyped number, for phone_shape (ADR 0023).
PHONE_UNASSIGNED = "+99 912 345 6789"  # pragma: allowlist secret
RU_PASSPORT_SERIES, RU_PASSPORT_NUMBER = "4599", "012345"  # pragma: allowlist secret

CGJ = chr(0x034F)  # combining grapheme joiner: invisible, category Mn
ALM = chr(0x061C)  # Arabic letter mark: invisible bidi mark, category Cf
TAG_SPACE = chr(0xE0020)  # tag space: invisible, category Cf

FULL_WIDTH_ZERO = 0xFF10
ARABIC_INDIC_ZERO = 0x0660


def in_digits(value: str, zero: int) -> str:
    return "".join(chr(zero + int(c)) if c.isdigit() else c for c in value)


def leaks(cases: Sequence[Case]) -> list[str]:
    """Every case with a letter or digit of its value outside all spans, and what came back."""
    report = []
    for text, value in cases:
        spans = detect(text)
        start = text.index(value)
        bare = "".join(
            text[i]
            for i in range(start, start + len(value))
            if text[i].isalnum() and not any(s.start <= i < s.end for s in spans)
        )
        if bare:
            got = [(text[s.start : s.end], s.entity_type.value) for s in spans]
            report.append(f"{bare!r} of {value!r} unmasked in {text!r}; got {got!r}")
    return report


@pytest.fixture
def real_models() -> None:
    try:
        for lang in SPACY_MODEL_DIRS:
            detect_module.model_dir(lang)
        gliner_model_dir()
    except ModelMissing as error:
        pytest.skip(f"model absent: {error}")


# ------------------------------------------------------------------ dates (ADR 0016 point 5)


def test_date_range_or_list_without_spaces() -> None:
    """_BEFORE/_AFTER refuse a date touching '-', ',' or '/' and a digit, to keep IP
    addresses out, so both dates of a range 'dd.mm.yyyy-dd.mm.yyyy' are refused. The
    Russian model has no DATE label: the range keeps only a coincidental ru_inn hit across
    the hyphen and leaks the first day and the last year; the list and the ISO interval
    return []."""
    assert leaks([("Поступил 12.03.1985 по направлению.", "12.03.1985")]) == []  # control
    date_range = "12.03.1985-15.03.1985"  # pragma: allowlist secret
    cases = [
        (f"Пациент находился в стационаре {date_range} по направлению.", "12.03.1985"),
        ("Даты визитов: 12.03.1985,15.03.1985 и далее.", "12.03.1985,15.03.1985"),
        ("Период: 1985-03-12/1985-03-15 включительно.", "1985-03-12/1985-03-15"),
    ]
    assert leaks(cases) == []


def test_russian_date_with_the_day_in_guillemets() -> None:
    """The Russian contract form puts the day in guillemets ('«12» <month> 1985'), so a
    closing guillemet stands between the day and the month; _RU_DAY_FIRST needs
    whitespace there. detect() returned only the city: day, month and year unmasked."""
    assert leaks([("Договор заключён 12 марта 1985 г. в Москве.", "12 марта 1985")]) == []
    assert leaks([("Договор заключён «12» марта 1985 г. в Москве.", "12» марта 1985")]) == []


def test_date_with_a_hyphenated_month_abbreviation() -> None:
    """dd-Mon-yyyy (lab reports, airline and hospital systems) is not in the list:
    _EN_DAY_FIRST needs whitespace after the day. ADR 0016 says such forms are left to
    the English model, but it missed '12-MAR-1985' in English too: [] for both."""
    assert leaks([("DOB 12 MAR 1985 admitted.", "12 MAR 1985")]) == []  # control
    cases = [
        ("DOB 12-MAR-1985 admitted.", "12-MAR-1985"),
        ("Дата рождения: 12-Mar-1985, пол мужской.", "12-Mar-1985"),
    ]
    assert leaks(cases) == []


def test_date_in_full_width_or_arabic_indic_digits() -> None:
    """_YEAR is '[12]\\d{3}': the first digit of the year must be ASCII, although \\d
    elsewhere accepts every decimal digit (digits.py reads such cards). Both return []."""
    value = "12.03.1985"
    cases = [
        (
            f"Дата рождения: {in_digits(value, FULL_WIDTH_ZERO)} г.",
            in_digits(value, FULL_WIDTH_ZERO),
        ),
        (
            f"Дата рождения: {in_digits(value, ARABIC_INDIC_ZERO)} г.",
            in_digits(value, ARABIC_INDIC_ZERO),
        ),
    ]
    assert leaks(cases) == []


# ------------------------------------------------------------------ documents (ADR 0023)


def test_russian_passport_series_and_number_split_by_a_number_sign() -> None:
    """The usual Russian form 'серия 4599 № 012345' (or '... номер 012345') breaks the
    _RUN at '№' or a word; '4599' alone is under nine digits, so the series is never
    masked. detect() found '012345' only, as a PHONE; '4599' leaked."""
    series, number = RU_PASSPORT_SERIES, RU_PASSPORT_NUMBER
    control = f"Паспорт {series} {number} выдан ОВД."
    assert leaks([(control, f"{series} {number}")]) == []
    cases = [
        (f"Паспорт серия {series} № {number} выдан ОВД.", f"{series} № {number}"),
        (
            f"Паспорт: серия {series[:2]} {series[2:]} номер {number}, выдан ОВД.",
            f"{series[:2]} {series[2:]} номер {number}",
        ),
    ]
    assert leaks(cases) == []


def test_passport_or_licence_with_one_letter_and_seven_digits() -> None:
    """_is_code wants two letters for a mixed code, passport_shape two letters and seven
    digits. One letter and seven digits (Indian and Australian passports, California
    driving licences) is refused even right after the keyword: [] for both."""
    cases = [
        ("Passport No: N1234567, issued 2020.", "N1234567"),  # pragma: allowlist secret
        ("Driver's license: D1234567 (CA).", "D1234567"),  # pragma: allowlist secret
    ]
    assert leaks(cases) == []


def test_lower_case_passport_number() -> None:
    """passport_shape and the keyword _RUN accept only [A-Z]; a passport number typed in
    lower case in a chat is refused with and without the keyword: [] for both."""
    control = "AB1234567"  # pragma: allowlist secret
    assert leaks([(f"my passport is {control} thanks", control)]) == []
    cases = [
        ("my passport is ab1234567 thanks", "ab1234567"),  # pragma: allowlist secret
        ("number ab1234567 on file", "ab1234567"),  # pragma: allowlist secret
    ]
    assert leaks(cases) == []


def test_armenian_social_card_number_after_an_armenian_keyword() -> None:
    """ADR 0023 has an Armenian phone keyword (հեռ) but no Armenian document keyword, so
    a ten-digit social card number after 'սոցիալական քարտ' is never gated in: []."""
    value = "1234567890"  # pragma: allowlist secret
    assert leaks([(f"Սոցիալական քարտի համարը {value} է։", value)]) == []


# ------------------------------------------------------------------ cards and phones (ADR 0023)


@FINDING
def test_card_shape_with_mixed_or_doubled_separators() -> None:
    """_CARD_SHAPE wants one separator used throughout; a Luhn-invalid card typed with a
    space and a hyphen, or with double spaces (justified PDF text), returns []."""
    assert leaks([(f"Card {CARD_LUHN_INVALID} on file", CARD_LUHN_INVALID)]) == []  # control
    mixed = "4000 1234-5678 9010"  # pragma: allowlist secret
    doubled = CARD_LUHN_INVALID.replace(" ", "  ")
    assert leaks([(f"Card {mixed} on file", mixed), (f"Card {doubled} on file", doubled)]) == []


@FINDING
def test_plus_phone_glued_to_the_next_word() -> None:
    """_within_digit_limit drops a last group glued to a word ('01K@example.com'), so
    '+99 912 345 6789Fax:' (PDF columns run together) keeps eight digits and phone_shape
    reports nothing; libphonenumber reads '912 345 6789' alone. '99' leaks."""
    phone = PHONE_UNASSIGNED
    assert leaks([(f"Phone: {phone} Fax: none", phone)]) == []  # control
    assert leaks([(f"Phone: {phone}Fax: none", phone)]) == []


@FINDING
def test_plus_phone_with_doubled_spaced_or_slashed_separators() -> None:
    """phone_shape takes single space, dot or hyphen separators; double spaces, a spaced
    hyphen (which digits.py accepts) or slashes stop the run after '+99'. libphonenumber
    reads the rest as a national number; the country code '99' leaks in each case."""
    groups = PHONE_UNASSIGNED.split(" ")
    cases = [
        (f"Звоните {value} вечером", value)
        for value in ("  ".join(groups), " - ".join(groups), "/".join(groups))
    ]
    assert leaks(cases) == []


# ------------------------------------------------------------------ clean view (ADR 0013)


def test_invisible_characters_missing_from_the_clean_list() -> None:
    """INVISIBLE lists Cf characters but not the combining grapheme joiner (U+034F), the
    Arabic letter mark (U+061C) or the tag characters (U+E0020-E007F); each splits the
    value. SSN: nothing of it; card with U+061C: one half as ORG, '41111111' leaks; IBAN:
    '3704 0044 05' as a PHONE; card with a tag space: the first ten digits as a PHONE."""
    assert leaks([(f"SSN {SSN} on file", SSN), (f"IBAN {IBAN}", IBAN)]) == []  # controls
    ssn = SSN[:3] + CGJ + SSN[3:]
    card = CARD[:9] + ALM + CARD[9:]
    iban = IBAN[:17] + CGJ + IBAN[17:]
    tagged = CARD.replace(" ", "")[:10] + TAG_SPACE + CARD.replace(" ", "")[10:]
    cases = [
        (f"SSN {ssn} on file", ssn),
        (f"Card {card} on file", card),
        (f"IBAN {iban}", iban),
        (f"Card {tagged} on file", tagged),
    ]
    assert leaks(cases) == []


# ------------------------------------------------------------------ routing by script (ADR 0016)


@FINDING
@pytest.mark.models
@pytest.mark.usefixtures("real_models")
def test_armenian_clause_after_an_instruction_on_the_same_line() -> None:
    """_language picks one script per line. A chat prompt 'Translate ...: <Armenian>'
    has more Latin or Cyrillic letters than Armenian ones, so the line goes to spaCy
    en or ru, which do not tag the Armenian name; GLiNER never sees it. Alone, the same
    clause goes to GLiNER and the name is masked. detect() returned [] for each."""
    clauses = [
        ("պարոն Մանուկյանը ուշացել է", "Մանուկյան"),
        ("Արամ Գրիգորյանը ապրում է Գյումրիում", "Արամ Գրիգորյան"),
    ]
    assert leaks(clauses) == []  # control: the clause alone
    cases = [
        ("Translate this sentence to English: պարոն Մանուկյանը ուշացել է", "Մանուկյան"),
        ("Can you check the grammar here: պարոն Մանուկյանը ուշացել է", "Մանուկյան"),
        (
            "Переведи на английский язык это предложение: Արամ Գրիգորյանը ապրում է Գյումրիում",
            "Արամ Գրիգորյան",
        ),
    ]
    assert leaks(cases) == []


@FINDING
@pytest.mark.models
@pytest.mark.usefixtures("real_models")
def test_other_script_clause_after_an_english_instruction() -> None:
    """The same for a script with no spaCy model: 30 Latin letters against 24 Greek ones
    make the line 'en' instead of 'und', so GLiNER is skipped. detect() returned []."""
    clause = "Ο Νίκος Παπαδόγιαννης μένει στην Πάτρα"
    assert leaks([(clause, "Νίκος Παπαδόγιαννης")]) == []  # control
    text = "Translate this sentence to English: Ο Νίκος Παπαδόγιαννης μένει"
    assert leaks([(text, "Νίκος Παπαδόγιαννης")]) == []


@FINDING
@pytest.mark.models
@pytest.mark.usefixtures("real_models")
def test_capitalised_russian_name_inside_a_lower_case_line() -> None:
    """ADR 0016 point 1 title-cases only lines mostly in capitals (a documented limit).
    A name in capitals inside a Russian sentence: ner_ru tagged the surname as ORG and
    left the first name unmasked; the same name in title case is masked whole."""
    control = "Договор вчера подписал Зорват Квиллин в офисе компании в Новосибирске."
    assert leaks([(control, "Зорват Квиллин")]) == []
    text = "Договор вчера подписал ЗОРВАТ КВИЛЛИН в офисе компании в Новосибирске."
    assert leaks([(text, "ЗОРВАТ КВИЛЛИН")]) == []
