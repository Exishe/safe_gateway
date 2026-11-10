"""Leak-hunter round 1d: the clean view, dates, documents, separators and routing after 1c.

Every test here is a strict xfail: it fails today because a value, or part of it, is
left outside every span. Each docstring says what detect() (or mask) returned instead,
as observed at fa75c3f. Where it helps, a test first asserts a control: the same value
in a form the engine already masks, so what fails is the trick, not the value. Each
leak was also checked with every model installed (spaCy en and ru, GLiNER): the models
did not cover the leaked characters. Values are invalid by construction or invented.
Characters that are invisible or easily confused are built with chr(), so the source
holds none of them.
"""

from collections.abc import Sequence

import pytest

import gateway.engine.detect as detect_module
from gateway.engine.detect import detect
from gateway.engine.gliner_ner import gliner_model_dir
from gateway.engine.mask import mask_messages
from gateway.engine.ner import SPACY_MODEL_DIRS, ModelMissing

Case = tuple[str, str]  # (text, value as written)

REASON = "engine red-team finding (1d)"

SSN = "123-45-6780"  # pragma: allowlist secret
CARD = "4111 1111 1111 1111"  # pragma: allowlist secret
CARD_LUHN_INVALID = "4000 1234 5678 9010"  # pragma: allowlist secret
# +99 is not an assigned country code: a mistyped number, for phone_shape (ADR 0023).
PHONE_UNASSIGNED = "+99 912 345 6789"  # pragma: allowlist secret
EMAIL = "selene.quillin@example.com"  # pragma: allowlist secret
RU_SERIES, RU_NUMBER = "4599", "012345"  # pragma: allowlist secret

# Digit look-alikes: Unicode "numeric" characters that are not decimal digits (No).
SUPERSCRIPTS = dict(
    zip("0123456789", map(chr, (0x2070, 0xB9, 0xB2, 0xB3, *range(0x2074, 0x207A))), strict=True)
)
CIRCLED = dict(zip("0123456789", map(chr, (0x24EA, *range(0x2460, 0x2469))), strict=True))

FULL_WIDTH_AT = chr(0xFF20)
IDEOGRAPHIC_FULL_STOP = chr(0x3002)
FULL_WIDTH_SOLIDUS = chr(0xFF0F)
COMBINING_ACUTE = chr(0x0301)
COMBINING_LOW_LINE = chr(0x0332)
HANGUL_FILLER = chr(0x3164)  # Default_Ignorable, category Lo: renders as nothing
MONGOLIAN_FVS1 = chr(0x180B)  # Default_Ignorable, category Mn: renders as nothing
ZERO_WIDTH_SPACE = chr(0x200B)
CYRILLIC_CAPITAL_A = chr(0x0410)


def spelled(value: str, digits: dict[str, str]) -> str:
    return "".join(digits.get(c, c) for c in value)


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


# ------------------------------------------------------------ clean view (ADR 0016 amended, 1)


def test_digit_look_alikes_that_are_not_decimal() -> None:
    """The fold covers str.isdecimal() only. Circled digits and superscripts are
    'numeric' (category No), read as digits by people and by the cloud model, and
    match neither \\d nor the fold. Circled SSN: only 'SSN' came back (ORG, ner_en);
    superscript '+' phone: []."""
    assert leaks([(f"Her SSN is {SSN} per the intake form.", SSN)]) == []  # control
    circled_ssn = spelled(SSN, CIRCLED)
    superscript_phone = spelled(PHONE_UNASSIGNED, SUPERSCRIPTS)
    cases = [
        (f"Her SSN is {circled_ssn} per the intake form.", circled_ssn),
        (f"Please call {superscript_phone} after five.", superscript_phone),
    ]
    assert leaks(cases) == []


def test_invisible_default_ignorable_characters_that_are_not_format_characters() -> None:
    """The clean view removes category Cf, U+034F and the tags block. The Hangul filler
    (Lo) and the Mongolian free variation selectors (Mn) are Default_Ignorable too and
    render as nothing, but stay in the view and split the number. SSN: [] (GLiNER read
    'Her' as a PERSON); card: the groups came back as ORG from ner_en, one group bare."""
    with_zwsp = SSN[:5] + ZERO_WIDTH_SPACE + SSN[5:]
    assert leaks([(f"Her SSN is {with_zwsp} today.", with_zwsp)]) == []  # control
    ssn = SSN[:5] + HANGUL_FILLER + SSN[5:]
    card = CARD[:5] + MONGOLIAN_FVS1 + CARD[5:]
    cases = [
        (f"Her SSN is {ssn} per the intake form.", ssn),
        (f"Please charge card {card} for the invoice.", card),
    ]
    assert leaks(cases) == []


def test_combining_low_line_under_each_digit() -> None:
    """Text 'underlined' with U+0332 after each character (Unicode text generators,
    some PDF converters) still reads as the number, but combining marks are not removed
    from the clean view, so no digit run forms. detect() returned only 'SSN' (ORG)."""
    ssn = "".join(c + COMBINING_LOW_LINE for c in SSN)
    assert leaks([(f"Her SSN is {ssn} per the intake form.", ssn)]) == []


def test_email_with_a_full_width_at_or_an_ideographic_full_stop() -> None:
    """IDNA reads U+3002 as a label dot and mail clients accept a full-width '@' from a
    CJK keyboard; the clean view folds neither, so _EMAIL never matches. Both: []."""
    assert leaks([(f"Please write to {EMAIL} about the lease.", EMAIL)]) == []  # control
    full_width_at = EMAIL.replace("@", FULL_WIDTH_AT)
    ideographic_dot = EMAIL.replace(".com", f"{IDEOGRAPHIC_FULL_STOP}com")
    cases = [
        (f"Please write to {full_width_at} about the lease.", full_width_at),
        (f"Please write to {ideographic_dot} about the lease.", ideographic_dot),
    ]
    assert leaks(cases) == []


def test_email_with_a_decomposed_accent_in_the_local_part() -> None:
    """macOS writes 'é' as 'e' + U+0301 (NFD). The recognizers read the clean view, which
    is not composed, and \\w does not match a combining mark, so the address starts after
    the accent: '.quillin@example.com' came back as EMAIL, 'jose' leaked."""
    email = f"jose{COMBINING_ACUTE}.quillin@example.com"  # pragma: allowlist secret
    assert leaks([(f"Please write to {email} about the lease.", email)]) == []


# ------------------------------------------------------------------ dates (ADR 0016 amended, 2)


def test_russian_day_range_before_one_month_name() -> None:
    """'12-15 <month> 1985' and 'from 12 to 15 <month> 1985' share one month and year.
    The first is refused whole (a hyphen and a digit before '15'); the second masks
    '15 <month> 1985' and leaves '12'. The Russian model has no DATE label: nothing else
    covers them."""
    cases = [
        ("Пациент госпитализирован 12-15 марта 1985 г. в терапию.", "12-15 марта 1985"),
        ("Пациент госпитализирован с 12 по 15 марта 1985 г.", "12 по 15"),
    ]
    assert leaks(cases) == []


def test_numeric_range_sharing_one_year() -> None:
    """'12.03-15.03.1985' writes the year once. '15.03.1985' is refused because a digit
    and a hyphen stand before it and '12.03' is not a date: [] in Russian and English."""
    cases = [
        ("Пациент находился в стационаре 12.03-15.03.1985 по направлению.", "12.03-15.03.1985"),
        ("Patient stayed 12.03-15.03.1985 on ward 4.", "12.03-15.03.1985"),
    ]
    assert leaks(cases) == []


def test_two_digit_years_in_russian_text() -> None:
    """Only dd.mm.yy accepts a two-digit year. '12/03/85', '12-MAR-85', the same with a
    Russian month abbreviation, and '12 <month> 85' all return [] in a Russian sentence
    (no Russian DATE label)."""
    cases = [
        ("Дата рождения: 12/03/85, пол мужской.", "12/03/85"),
        ("Дата рождения: 12-MAR-85, пол мужской.", "12-MAR-85"),
        ("Дата рождения: 12-мар-85, пол мужской.", "12-мар-85"),
        ("Дата рождения: 12 марта 85 г., пол мужской.", "12 марта 85"),
    ]
    assert leaks(cases) == []


def test_date_forms_left_out_of_the_named_and_numeric_patterns() -> None:
    """Each returns [] in a Russian sentence: year first with dots, a month name glued to
    its numbers, mixed separators around a month name, spaced guillemets, a full-width
    slash (not folded by the clean view), and day, month and year split by spaces (two
    groups become a PHONE, the day leaks)."""
    cases = [
        ("Дата рождения: 1985.03.12, пол мужской.", "1985.03.12"),
        ("Дата рождения: 12MAR1985, пол мужской.", "12MAR1985"),
        ("Дата рождения: 12-Mar/1985, пол мужской.", "12-Mar/1985"),
        ("Договор от « 12 » марта 1985 г. подписан.", "12 » марта 1985"),
        (f"Дата рождения: 12{FULL_WIDTH_SOLIDUS}03{FULL_WIDTH_SOLIDUS}1985, пол мужской.", "12"),
        ("Дата рождения: 12 03 1985, пол мужской.", "12 03 1985"),
    ]
    assert leaks(cases) == []


# ------------------------------------------------------------ documents (ADR 0023 amended, 1-4)


def test_russian_series_and_number_in_capitals_or_other_wording() -> None:
    """_RU_SERIES_NUMBER makes only the word for 'series' case-insensitive; the word for
    'number' must be lower case, and the series word nominative or genitive (or its
    abbreviation). 'SERIYA ... NOMER' in capitals, the instrumental 'seriei', 'and number'
    between the two, 'N.' and the number written before the series all leave the series
    bare; '012345' alone came back as a PHONE."""
    series, number = RU_SERIES, RU_NUMBER
    assert leaks([(f"Паспорт серия {series} № {number} выдан ОВД.", series)]) == []  # control
    cases = [
        (f"ПАСПОРТ СЕРИЯ {series} НОМЕР {number} ВЫДАН ОВД.", series),
        (f"Гражданин, паспорт серией {series} № {number}, выдан ОВД.", series),
        (f"Паспорт серия {series} и номер {number} выдан ОВД.", series),
        (f"Паспорт серия {series} N. {number} выдан ОВД.", series),
        (f"Паспорт номер {number} серия {series} выдан ОВД.", series),
    ]
    assert leaks(cases) == []


def test_document_codes_with_cyrillic_letters() -> None:
    """_CODE_PART and PASSPORT_SHAPE read [A-Za-z] only. A Russian driving licence (two
    digits, two Cyrillic letters, six digits) and passport letters typed on a Russian
    keyboard are Cyrillic, so the keyword finds no code: [] for both (ner_ru found only
    the issuing authority)."""
    cases = [
        (
            "Водительское удостоверение 77 АВ 123456 выдано ГИБДД.",
            "77 АВ 123456",
        ),  # pragma: allowlist secret
        ("Загранпаспорт: АВ1234567, выдан МИД.", "АВ1234567"),  # pragma: allowlist secret
    ]
    assert leaks(cases) == []


def test_document_keyword_just_outside_the_window() -> None:
    """A parenthesis after the keyword ('Passport number (as printed on the data page):')
    puts the code 47 characters after the keyword's start, beyond KEYWORD_DISTANCE (40).
    One letter and eight digits matches no ungated shape: []."""
    code = "X12345678"  # pragma: allowlist secret
    assert leaks([(f"Passport number: {code}", code)]) == []  # control
    assert leaks([(f"Passport number (as printed on the data page): {code}", code)]) == []


def test_passport_shape_glued_to_a_file_name() -> None:
    """PASSPORT_SHAPE and _RUN refuse a word character or hyphen before the code, so a
    scan named 'scan_passport_AB1234567.pdf' leaks the number even with the keyword in
    the same word: []."""
    code = "AB1234567"  # pragma: allowlist secret
    assert leaks([(f"Attached scan of passport {code} for review.", code)]) == []  # control
    assert leaks([(f"Attached scan_passport_{code}.pdf for review.", code)]) == []


def test_passport_shape_with_a_cyrillic_homoglyph() -> None:
    """A Cyrillic capital A (U+0410) for the first letter looks the same and is not
    folded, so '[A-Z]{2}' fails: []."""
    latin = "AB1234567"  # pragma: allowlist secret
    code = CYRILLIC_CAPITAL_A + latin[1:]
    assert leaks([(f"Reference {latin} on file.", latin)]) == []  # control
    assert leaks([(f"Reference {code} on file.", code)]) == []


# ------------------------------------------------------------ separators (ADR 0023 amended, 5-7)


def test_luhn_valid_card_with_other_separators() -> None:
    """A Luhn-valid card must reach 100% (hard rule 4), but digits.py joins groups only
    by one space, dot or hyphen, ' - ' or a line break. A slash, a tab, three spaces, a
    hyphen or dot followed by a space, and a line break followed by a space all break
    the run into groups too short to be a card: [] or groups tagged ORG by ner_en."""
    assert leaks([(f"Please charge card {CARD} for the invoice.", CARD)]) == []  # control
    forms = [CARD.replace(" ", gap) for gap in ("/", "\t", "   ", "- ", ". ")]
    forms.append(CARD[:9] + "\n " + CARD[10:])  # a line break, then the next line's indent
    cases = [(f"Please charge card {form} for the invoice.", form) for form in forms]
    assert leaks(cases) == []


def test_card_shape_beside_an_expiry_date_or_a_quantity() -> None:
    """_CARD_SHAPE refuses a card followed by ' 12' or preceded by '2 ', to keep it a
    whole run. A Luhn-invalid card written with its expiry ('... 9010 12/29') kept only
    '5678 9010 12' as a PHONE and leaked '4000 1234'; after a quantity it returned []."""
    card = CARD_LUHN_INVALID
    hyphenated = card.replace(" ", "-")
    assert leaks([(f"Please charge card {card} for the invoice.", card)]) == []  # control
    cases = [
        (f"Please charge card {card} 12/29 for the invoice.", card),
        (f"Item qty 2 {hyphenated} charged.", hyphenated),
    ]
    assert leaks(cases) == []


def test_plus_phone_split_by_a_tab_a_line_break_or_a_spaced_bracket() -> None:
    """_PLUS_GAP takes one or two spaces, ' - ', '.', '/' or '-'. A tab (table cells), a
    line break (a wrapped PDF line) and spaces inside the brackets end the number: []
    for the first two; '( 912 ) 345 6789' came back without '+99'."""
    assert leaks([(f"Please call {PHONE_UNASSIGNED} after five.", PHONE_UNASSIGNED)]) == []
    country, area, rest = PHONE_UNASSIGNED.split(" ", 2)
    forms = [
        PHONE_UNASSIGNED.replace(" ", "\t"),
        f"{country} {area}\n{rest}",
        f"{country} ( {area} ) {rest}",
    ]
    cases = [(f"Please call {form} after five.", form) for form in forms]
    assert leaks(cases) == []


# ------------------------------------------------------------------ known-value sweep (ADR 0014)


def test_known_value_with_an_invisible_character_misses_its_plain_repeat() -> None:
    """The mapping keeps the surface as written, so an SSN detected with a zero-width
    space inside is swept only in that spelling. Its plain repeat glued to a file name,
    the case the sweep exists for (ADR 0014 amendment 1), went out unmasked:
    ['SSN [SSN_1] on file', 'again ref123-45-6780.pdf']."""
    glued = f"again ref{SSN}.pdf"
    control, _ = mask_messages([f"SSN {SSN} on file", glued])
    assert SSN not in control[1]
    with_zwsp = SSN.replace("-6", f"{ZERO_WIDTH_SPACE}-6")
    masked, _ = mask_messages([f"SSN {with_zwsp} on file", glued])
    assert SSN not in masked[1], masked


# ------------------------------------------------- routing and ner_view (ADR 0016 amended, 3-4)


@pytest.mark.models
@pytest.mark.usefixtures("real_models")
@pytest.mark.xfail(strict=True, reason=REASON)
def test_cyrillic_name_inside_an_english_line() -> None:
    """lines_in_other_scripts() sends a line to GLiNER only for a letter outside Latin
    and Cyrillic, and the line goes to the model of its dominant script. A Russian name
    in an English sentence reaches only en_core_web_lg, which does not read Cyrillic: []
    (only 'yesterday' as a DATE in the second case)."""
    assert leaks([("Договор подписал Зорват Квиллин в нашем офисе вчера.", "Зорват Квиллин")]) == []
    cases = [
        (
            "Please translate this for the client: Зорват Квиллин signed the lease in our office.",
            "Зорват Квиллин",
        ),
        (
            "Our client Зорват Квиллин signed the lease in our office yesterday afternoon.",
            "Зорват Квиллин",
        ),
    ]
    assert leaks(cases) == []


@pytest.mark.models
@pytest.mark.usefixtures("real_models")
@pytest.mark.xfail(strict=True, reason=REASON)
def test_capitalised_russian_surname_after_an_initial() -> None:
    """_CAPS_RUN needs two neighbouring words of three or more capitals, so an initial and
    a surname in capitals ('Z. KVILLIN' in Cyrillic, the usual Russian form) stay in
    capitals inside a lower-case line and ru_core_news_lg misses them: []."""
    control = "договор подписал ЗОРВАТ КВИЛЛИН в нашем офисе вчера."
    assert leaks([(control, "ЗОРВАТ КВИЛЛИН")]) == []
    text = "договор подписал З. КВИЛЛИН в нашем офисе вчера."
    assert leaks([(text, "З. КВИЛЛИН")]) == []


@pytest.mark.models
@pytest.mark.usefixtures("real_models")
def test_english_dates_the_models_do_not_cover() -> None:
    """With every model on: '12-MAR-85' (lab systems) and a full-width slash return [];
    "12 Mar '85" kept only "'85" (ner_en); '12. März 1985' kept 'März 1985' and leaked
    the day."""
    cases = [
        ("Patient DOB 12-MAR-85, admitted for observation.", "12-MAR-85"),
        (
            f"Patient DOB 12{FULL_WIDTH_SOLIDUS}03{FULL_WIDTH_SOLIDUS}1985, admitted.",
            f"12{FULL_WIDTH_SOLIDUS}03{FULL_WIDTH_SOLIDUS}1985",
        ),
        ("Patient born 12 Mar '85, admitted for observation.", "12 Mar '85"),
        ("Der Patient wurde am 12. März 1985 geboren.", "12. März 1985"),
    ]
    assert leaks(cases) == []
