"""Shape and keyword-gated recognizers (ADR 0023).

Values here have the shape of an identifier but no checksum to validate, or fail the
one they have. All are invalid by construction: unassigned country codes (+99x),
Luhn-invalid card numbers, and letter-digit codes that belong to no one.
"""

import unicodedata

import pytest

from gateway.engine.checksums import luhn_valid
from gateway.engine.recognizers.credit_card import CREDIT_CARD_SHAPE
from gateway.engine.recognizers.id_documents import (
    _LOOK_ALIKES,
    ID_CARD_SHAPE,
    ID_DOCUMENT_CONTEXT,
    PASSPORT_SHAPE,
    latin_look_alikes,
)
from gateway.engine.recognizers.phone_shape import PHONE_CONTEXT, PHONE_SHAPE
from gateway.engine.types import EntityType as E
from gateway.engine.types import Recognizer


def found(recognizer: Recognizer, text: str) -> list[str]:
    return [text[s.start : s.end] for s in recognizer.find(text)]


def typed(recognizer: Recognizer, text: str) -> list[tuple[str, E, float, str]]:
    return [
        (text[s.start : s.end], s.entity_type, s.score, s.recognizer) for s in recognizer.find(text)
    ]


# ------------------------------------------------------------------ phone, "+" prefix


@pytest.mark.parametrize(
    "number",
    [
        "+99 912345678",  # pragma: allowlist secret
        "+9991234-5678",  # pragma: allowlist secret
        "+99.99.999.9999",  # pragma: allowlist secret
        "+99-99.999-9999",  # pragma: allowlist secret
        "+99 (912) 345-6789",  # pragma: allowlist secret
        "+(99) 912 345 678",  # pragma: allowlist secret
        "+999123456789012",  # pragma: allowlist secret  (15 digits)
        # Leak hunt 1c: doubled spaces, a spaced hyphen, slashes (ADR 0023 amended).
        "+99  912  345  678",  # pragma: allowlist secret
        "+99 - 912 - 345 - 6789",  # pragma: allowlist secret
        "+99/912/345/6789",  # pragma: allowlist secret
        "+99 (912)  345 - 6789",  # pragma: allowlist secret
    ],
)
def test_plus_numbers_with_ten_to_fifteen_digits_are_phones(number: str) -> None:
    assert typed(PHONE_SHAPE, f"call me at {number} today") == [
        (number, E.PHONE, 0.85, "phone_shape")
    ]


@pytest.mark.parametrize(
    "text",
    [
        "short +99 1234 567 here",  # pragma: allowlist secret  (9 digits)
        "server 192.168.100.200 down",
        "Due 2024-01-15 or 15.01.2024",
        "units 12.345.678.901 sold",
        "a+99123456789 is a sum",  # pragma: allowlist secret  (glued to a word)
        "three   +99   912   345   678 spaces",  # pragma: allowlist secret  (three spaces)
        "two +99 -- 912 -- 345 -- 678",  # pragma: allowlist secret  (doubled hyphens)
    ],
)
def test_plus_shape_needs_the_plus_ten_digits_and_single_separators(text: str) -> None:
    assert found(PHONE_SHAPE, text) == []


def test_plus_phone_followed_by_a_postcode_drops_the_postcode() -> None:
    text = "Smith +99 20 7946 0958 10001 NY"  # pragma: allowlist secret
    assert found(PHONE_SHAPE, text) == ["+99 20 7946 0958"]  # pragma: allowlist secret


@pytest.mark.parametrize(
    ("recognizer", "text", "number"),
    [
        (
            PHONE_CONTEXT,
            "Tel: 012.345.6789 01K@example.com",  # pragma: allowlist secret
            "012.345.6789",  # pragma: allowlist secret
        ),
    ],
)
def test_a_last_group_glued_to_an_address_is_not_part_of_the_phone(
    recognizer: Recognizer, text: str, number: str
) -> None:
    assert found(recognizer, text) == [number]


@pytest.mark.parametrize(
    ("text", "number"),
    [
        ("Phone: +99 912 345 6789Fax: none", "+99 912 345 6789"),  # pragma: allowlist secret
        ("+99 912 345 678 9ab", "+99 912 345 678 9"),  # pragma: allowlist secret
    ],
)
def test_a_last_group_glued_to_a_word_keeps_its_digits(text: str, number: str) -> None:
    """Leak hunt 1c #10: PDF columns run together; the phone ends at the letter."""
    assert found(PHONE_SHAPE, text) == [number]


# ------------------------------------------------------------------ phone, keyword-gated


@pytest.mark.parametrize(
    ("text", "number"),
    [
        ("Tel: 012 345 6789", "012 345 6789"),  # pragma: allowlist secret
        ("call me on (012)-3456789 please", "(012)-3456789"),  # pragma: allowlist secret
        ("PHONE 012.345.6789", "012.345.6789"),  # pragma: allowlist secret
        ("Mobile: 01234-567-890", "01234-567-890"),  # pragma: allowlist secret
        ("WhatsApp (012) 3456789", "(012) 3456789"),  # pragma: allowlist secret
        ("fax 012 34 56 78 90", "012 34 56 78 90"),  # pragma: allowlist secret
        ("Телефон: 012.345.67.89", "012.345.67.89"),  # pragma: allowlist secret
        ("моб. 012 345 67 89", "012 345 67 89"),  # pragma: allowlist secret
        ("հեռ. 012 34 56 78 9", "012 34 56 78 9"),  # pragma: allowlist secret
    ],
)
def test_numbers_after_a_phone_keyword_are_phones(text: str, number: str) -> None:
    assert typed(PHONE_CONTEXT, text) == [(number, E.PHONE, 0.8, "phone_context")]


@pytest.mark.parametrize(
    "value",
    [
        "192.168.100.200",
        "12.345.678.901",
        "123-45-6789",  # pragma: allowlist secret  (an SSN with dashes)
        "2024-01-15",
        "15.01.2024",
        "2024-01-15 10 30",  # pragma: allowlist secret  (a coincidental INN)
        "012 345 6789",  # pragma: allowlist secret
    ],
)
def test_without_a_phone_keyword_nothing_is_reported(value: str) -> None:
    assert found(PHONE_CONTEXT, f"Reference {value} here") == []


@pytest.mark.parametrize(
    "text",
    [
        "Phone 012.3456789",  # pragma: allowlist secret  (two groups, no bracket)
        "Tel 12 34 56 78",  # eight digits
        "Phone (012) 3456789012345",  # pragma: allowlist secret  (sixteen digits)
        "after the cancellation 012 345 6789",  # pragma: allowlist secret  ("cell" inside)
        "Phone list: see the annex of the contract; 012 345 6789",  # pragma: allowlist secret
    ],
)
def test_keyword_gate_still_needs_the_phone_shape_and_proximity(text: str) -> None:
    assert found(PHONE_CONTEXT, text) == []


# ------------------------------------------------------------------ card, shape only


@pytest.mark.parametrize(
    "number",
    [
        "4111111111111112",  # pragma: allowlist secret
        "4111 1111 1111 1112",  # pragma: allowlist secret
        "5500-0000-0000-0005",  # pragma: allowlist secret
        "2221000000000001",  # pragma: allowlist secret
        "341111111111112",  # pragma: allowlist secret  (15 digits, starts with 3)
        "6011111111111111112",  # pragma: allowlist secret  (19 digits)
        "6011 1111 1111 1111 112",  # pragma: allowlist secret
        # Leak hunt 1c #9: mixed single separators, doubled spaces (ADR 0023 amended).
        "4111 1111-1111 1112",  # pragma: allowlist secret
        "4111.1111 1111-1112",  # pragma: allowlist secret
        "4111  1111  1111  1112",  # pragma: allowlist secret
        "4111  1111 1111-1112",  # pragma: allowlist secret
    ],
)
def test_luhn_invalid_numbers_of_an_exact_card_shape_are_cards(number: str) -> None:
    assert not luhn_valid("".join(c for c in number if c.isdigit()))
    assert typed(CREDIT_CARD_SHAPE, f"card {number} expired") == [
        (number, E.CREDIT_CARD, 0.55, "credit_card_shape")
    ]


@pytest.mark.parametrize(
    "number",
    [
        "1234567812345678",  # pragma: allowlist secret  (16 digits, starts with 1)
        "9111111111111112",  # pragma: allowlist secret  (16 digits, starts with 9)
        "411111111111112",  # pragma: allowlist secret  (15 digits, not starting with 3)
        "2111111111111111111",  # pragma: allowlist secret  (19 digits, starts with 2)
        "41111111111111123",  # pragma: allowlist secret  (17 digits)
        "41111111111112",  # pragma: allowlist secret  (14 digits)
        "4111 11111111 1112",  # pragma: allowlist secret  (not grouped by four)
        "4111   1111   1111   1112",  # pragma: allowlist secret  (three spaces)
        "4111 -1111 1111 1112",  # pragma: allowlist secret  (two separators in one gap)
        "1234 4111 1111 1111 1112",  # pragma: allowlist secret  (a 4-digit group before)
        "4111 1111 1111 1112 1234",  # pragma: allowlist secret  (a 4-digit group after)
        "AB4111111111111112",  # pragma: allowlist secret  (glued to letters)
    ],
)
def test_other_lengths_prefixes_and_groupings_are_not_card_shapes(number: str) -> None:
    assert found(CREDIT_CARD_SHAPE, f"ref {number} here") == []


@pytest.mark.parametrize(
    ("text", "card"),
    [
        # Leak hunt 1d: a short group beside the card is a quantity or an expiry date, not
        # part of it (ADR 0023 amended); before, the whole run was refused.
        ("qty 12 4111 1111 1111 1112 ok", "4111 1111 1111 1112"),  # pragma: allowlist secret
        ("qty 12  4111 1111 1111 1112 ok", "4111 1111 1111 1112"),  # pragma: allowlist secret
        ("qty 2 4000-1234-5678-9010 ok", "4000-1234-5678-9010"),  # pragma: allowlist secret
        ("card 4000 1234 5678 9010 12/29 ok", "4000 1234 5678 9010"),  # pragma: allowlist secret
        ("card 4000 1234 5678 9010 12 29 ok", "4000 1234 5678 9010"),  # pragma: allowlist secret
        ("card 4000 1234 5678 9010 123 ok", "4000 1234 5678 9010 123"),  # pragma: allowlist secret
    ],
)
def test_a_short_group_beside_a_card_shape_is_left_out(text: str, card: str) -> None:
    assert found(CREDIT_CARD_SHAPE, text) == [card]


# ------------------------------------------------------------------ passport and ID card shapes


@pytest.mark.parametrize("value", ["AB1234567", "ab1234567"])  # pragma: allowlist secret
def test_two_letters_and_seven_digits_is_a_passport(value: str) -> None:
    """Lower case too (1c #7): a passport number typed in a chat (ADR 0023 amended)."""
    text = f"Bring {value} tomorrow"
    assert typed(PASSPORT_SHAPE, text) == [(value, E.PASSPORT, 0.7, "passport_shape")]


@pytest.mark.parametrize(
    "text",
    [
        "code Ab1234567 here",  # pragma: allowlist secret  (mixed case)
        "code ABC1234567 here",  # pragma: allowlist secret
        "code AB12345678 here",  # pragma: allowlist secret
        "code AB1234567X here",  # pragma: allowlist secret
        "code REF-AB1234567 here",  # pragma: allowlist secret
    ],
)
def test_passport_shape_is_exact_and_word_bounded(text: str) -> None:
    assert found(PASSPORT_SHAPE, text) == []


def test_two_letters_five_digits_two_letters_is_an_id_card() -> None:
    text = "card HF50607EJ on file"  # pragma: allowlist secret
    assert typed(ID_CARD_SHAPE, text) == [
        ("HF50607EJ", E.NATIONAL_ID, 0.65, "id_card_shape")  # pragma: allowlist secret
    ]


@pytest.mark.parametrize(
    "text",
    [
        "code hf50607ej here",  # pragma: allowlist secret
        "code HF506070EJ here",  # pragma: allowlist secret
        "code XHF50607EJ here",  # pragma: allowlist secret
        "postcode SW1A 2AA here",
    ],
)
def test_id_card_shape_is_exact_and_word_bounded(text: str) -> None:
    assert found(ID_CARD_SHAPE, text) == []


# ------------------------------------------------------------------ keyword-gated documents


@pytest.mark.parametrize(
    ("text", "value", "kind"),
    [
        ("Passport number: 123456789", "123456789", E.PASSPORT),  # pragma: allowlist secret
        ("паспорт 4509 123456 выдан", "4509 123456", E.PASSPORT),  # pragma: allowlist secret
        ("ID number: XT58ACI3SX.", "XT58ACI3SX", E.NATIONAL_ID),  # pragma: allowlist secret
        ("Identity card 7814996601436", "7814996601436", E.NATIONAL_ID),  # pragma: allowlist secret
        ("my identification, QN39968PC", "QN39968PC", E.NATIONAL_ID),  # pragma: allowlist secret
        ("удостоверение 12-34-567890", "12-34-567890", E.NATIONAL_ID),  # pragma: allowlist secret
        (
            "driver's license: KELDA 770032 K9 706",  # pragma: allowlist secret
            "KELDA 770032 K9 706",  # pragma: allowlist secret
            E.DRIVER_LICENSE,
        ),
        (
            "DL MAWLI-831198-MA-002",  # pragma: allowlist secret
            "MAWLI-831198-MA-002",  # pragma: allowlist secret
            E.DRIVER_LICENSE,
        ),
        (
            "Driving licence EK1282146018",  # pragma: allowlist secret
            "EK1282146018",  # pragma: allowlist secret
            E.DRIVER_LICENSE,
        ),
        (
            "водительское удостоверение 9912 345678",  # pragma: allowlist secret
            "9912 345678",  # pragma: allowlist secret
            E.DRIVER_LICENSE,
        ),
        # Leak hunt 1c (ADR 0023 amended): one letter and seven or more digits.
        ("Passport No: N1234567, issued", "N1234567", E.PASSPORT),  # pragma: allowlist secret
        (
            "Driver's license: D1234567 (CA)",
            "D1234567",
            E.DRIVER_LICENSE,
        ),  # pragma: allowlist secret
        ("ID: A1234567890", "A1234567890", E.NATIONAL_ID),  # pragma: allowlist secret
        # Any case after a keyword.
        ("my passport is ab1234567 thanks", "ab1234567", E.PASSPORT),  # pragma: allowlist secret
        ("licence no. dk12345678", "dk12345678", E.DRIVER_LICENSE),  # pragma: allowlist secret
        # Armenian keywords: social card, public service number, passport, ID card, licence.
        (
            "Սոցիալական քարտի համարը 1234567890 է։",
            "1234567890",
            E.NATIONAL_ID,
        ),  # pragma: allowlist secret
        ("ՀԾՀ 1234567890", "1234567890", E.NATIONAL_ID),  # pragma: allowlist secret
        ("Անձնագրի համարը AN0123456", "AN0123456", E.PASSPORT),  # pragma: allowlist secret
        ("նույնականացման քարտ 012345678", "012345678", E.NATIONAL_ID),  # pragma: allowlist secret
        ("վարորդական վկայական AB123456", "AB123456", E.DRIVER_LICENSE),  # pragma: allowlist secret
    ],
)
def test_identifiers_after_a_document_keyword_take_its_type(text: str, value: str, kind: E) -> None:
    assert typed(ID_DOCUMENT_CONTEXT, text) == [(value, kind, 0.75, "id_document_context")]


def test_the_keyword_itself_is_not_part_of_the_value() -> None:
    text = "ID AB12345CD"  # pragma: allowlist secret
    assert found(ID_DOCUMENT_CONTEXT, text) == ["AB12345CD"]  # pragma: allowlist secret


def test_the_nearest_specific_keyword_decides_the_type() -> None:
    text = "Passport P1234567X and driver license DK12345678"  # pragma: allowlist secret
    assert [(v, k) for v, k, _, _ in typed(ID_DOCUMENT_CONTEXT, text)] == [
        ("P1234567X", E.PASSPORT),  # pragma: allowlist secret
        ("DK12345678", E.DRIVER_LICENSE),  # pragma: allowlist secret
    ]


@pytest.mark.parametrize(
    "text",
    [
        "Invoice INV20240001 due",  # pragma: allowlist secret
        "Ticket JIRA123456 closed",  # pragma: allowlist secret
        "Contract ref ACME2024X1",  # pragma: allowlist secret
        "Order 123456789 shipped",  # pragma: allowlist secret
        "INV20240001",  # pragma: allowlist secret
        "JIRA123456",  # pragma: allowlist secret
        "ACME2024X1",  # pragma: allowlist secret
    ],
)
def test_codes_without_a_document_keyword_are_not_reported(text: str) -> None:
    assert found(ID_DOCUMENT_CONTEXT, text) == []


@pytest.mark.parametrize(
    "text",
    [
        "ID: 12345678",  # pragma: allowlist secret  (eight digits)
        "ID: 123456789012345",  # pragma: allowlist secret  (fifteen digits)
        "ID: AB1",
        "ID: ABCDEFGHJK",  # letters only
        "ID: A123456",  # pragma: allowlist secret  (one letter, six digits)
        "passport number abc",
        "passport: see section 12",
        "Passport office hours are Monday to Friday; ref 123456789",  # pragma: allowlist secret
    ],
)
def test_document_keyword_still_needs_the_identifier_shape_and_proximity(text: str) -> None:
    assert found(ID_DOCUMENT_CONTEXT, text) == []


# ------------------------------------------------------------------ Russian series and number


@pytest.mark.parametrize(
    ("text", "value", "kind"),
    [
        (
            "Паспорт серия 4599 № 012345 выдан",
            "4599 № 012345",
            E.PASSPORT,
        ),  # pragma: allowlist secret
        (
            "Паспорт: серия 45 99 номер 012345, выдан",  # pragma: allowlist secret
            "45 99 номер 012345",  # pragma: allowlist secret
            E.PASSPORT,
        ),
        ("паспорт серии 4599 N012345", "4599 N012345", E.PASSPORT),  # pragma: allowlist secret
        (
            "Серия: 4599, номер: 012345",
            "4599, номер: 012345",
            E.NATIONAL_ID,
        ),  # pragma: allowlist secret
    ],
)
def test_russian_series_and_number_are_masked_together(text: str, value: str, kind: E) -> None:
    """1c #5: the series and the number, split by "№" or "номер", are one value."""
    assert typed(ID_DOCUMENT_CONTEXT, text) == [(value, kind, 0.75, "id_document_context")]


@pytest.mark.parametrize(
    "text",
    [
        "серия 4599 № 01234",  # pragma: allowlist secret  (five-digit number)
        "серия 459 № 012345",  # pragma: allowlist secret  (three-digit series)
        "серия 4599 № 0123456",  # pragma: allowlist secret  (seven-digit number)
        "сериал 4599 012345",  # pragma: allowlist secret  (not the word)
        "4599 № 012345",  # pragma: allowlist secret  (no keyword)
    ],
)
def test_russian_series_and_number_need_the_keyword_and_the_shape(text: str) -> None:
    assert found(ID_DOCUMENT_CONTEXT, text) == []


# ------------------------------------------------------------------ leak hunt 1d (ADR 0023)

CYRILLIC_A, CYRILLIC_VE = chr(0x0410), chr(0x0412)


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("ПАСПОРТ СЕРИЯ 4599 НОМЕР 012345 ВЫДАН", "4599 НОМЕР 012345"),  # pragma: allowlist secret
        ("паспорт серией 4599 № 012345, выдан", "4599 № 012345"),  # pragma: allowlist secret
        ("паспорт серию 45 99 и номер 012345", "45 99 и номер 012345"),  # pragma: allowlist secret
        ("паспорт серия 4599 N. 012345 выдан", "4599 N. 012345"),  # pragma: allowlist secret
        ("паспорт номер 012345 серия 4599 выдан", "012345 серия 4599"),  # pragma: allowlist secret
        ("паспорт № 012345, серии 45 99", "012345, серии 45 99"),  # pragma: allowlist secret
    ],
)
def test_russian_series_and_number_in_other_cases_wordings_and_orders(
    text: str, value: str
) -> None:
    assert typed(ID_DOCUMENT_CONTEXT, text)[0] == (value, E.PASSPORT, 0.75, "id_document_context")


def test_a_number_without_the_word_for_series_is_not_a_series_and_number() -> None:
    assert found(ID_DOCUMENT_CONTEXT, "заказ номер 012345 от 4599") == []


def test_document_codes_may_have_cyrillic_letters() -> None:
    licence = f"77 {CYRILLIC_A}{CYRILLIC_VE} 123456"  # pragma: allowlist secret
    text = f"Водительское удостоверение {licence} выдано"
    assert typed(ID_DOCUMENT_CONTEXT, text) == [
        (licence, E.DRIVER_LICENSE, 0.75, "id_document_context")
    ]


@pytest.mark.parametrize(
    ("text", "found_codes"),
    [
        ("Passport number (as printed on the data page): X12345678", ["X12345678"]),
        ("Passport number (as printed on the data page; see below) X12345678", []),
    ],
)
def test_a_code_after_a_label_colon_may_stand_further_from_the_keyword(
    text: str, found_codes: list[str]
) -> None:
    assert found(ID_DOCUMENT_CONTEXT, text) == found_codes


@pytest.mark.parametrize(
    "text",
    [
        "scan_passport_AB1234567.pdf",  # pragma: allowlist secret
        "AB1234567_scan.pdf",  # pragma: allowlist secret
        f"ref {CYRILLIC_A}B1234567 on file",  # pragma: allowlist secret
        f"ref {CYRILLIC_A}{CYRILLIC_VE}1234567 on file",  # pragma: allowlist secret
    ],
)
def test_passport_shapes_beside_an_underscore_or_with_look_alike_letters(text: str) -> None:
    [span] = PASSPORT_SHAPE.find(text)
    assert text[span.start : span.end].endswith("1234567")
    assert span.end - span.start == len("AB1234567")


def test_a_passport_shape_after_a_hyphen_or_inside_a_word_is_still_refused() -> None:
    assert found(PASSPORT_SHAPE, "scan-AB1234567 xAB1234567") == []  # pragma: allowlist secret


def test_every_look_alike_folds_to_the_latin_letter_of_its_name() -> None:
    for code, latin in _LOOK_ALIKES.items():
        name = unicodedata.name(chr(code))
        assert name.startswith(("CYRILLIC", "GREEK")), name
        assert (latin.isupper()) == ("CAPITAL" in name), name
        assert latin_look_alikes(chr(code)) == latin
