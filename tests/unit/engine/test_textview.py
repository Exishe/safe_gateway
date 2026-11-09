"""The clean text view: what recognizers read, and the map back to the original (ADR 0013)."""

import sys
import unicodedata

import pytest
from hypothesis import given
from hypothesis import strategies as st

from gateway.engine.textview import (
    APOSTROPHES,
    DASHES,
    INVISIBLE,
    REPLACEMENT,
    VARIATION_SELECTORS,
    clean_view,
    composed_view,
    ner_view,
    title_cased_capitals,
)

NBSP = chr(0x00A0)
NNBSP = chr(0x202F)
EN_DASH = chr(0x2013)
MINUS = chr(0x2212)
ZWSP = chr(0x200B)
SHY = chr(0x00AD)
BOM = chr(0xFEFF)


def test_plain_text_is_unchanged_and_maps_to_itself() -> None:
    view = clean_view("card 4111 here")
    assert view.text == "card 4111 here"
    assert view.original_span(5, 9) == (5, 9)


def test_unicode_spaces_and_dashes_are_replaced_one_for_one() -> None:
    text = f"a{NBSP}b{NNBSP}c{EN_DASH}d{MINUS}e"
    view = clean_view(text)
    assert view.text == "a b c-d-e"
    assert view.original_span(0, len(view.text)) == (0, len(text))


def test_invisible_characters_are_removed_and_offsets_map_back() -> None:
    text = f"an{ZWSP}na@example.com"
    view = clean_view(text)
    assert view.text == "anna@example.com"
    start, end = view.original_span(0, len(view.text))
    assert text[start:end] == text


def test_invisible_characters_at_the_edges_of_a_span_stay_outside_it() -> None:
    text = f"x {SHY}12{ZWSP}34{BOM} y"
    view = clean_view(text)
    assert view.text == "x 1234 y"
    start, end = view.original_span(2, 6)
    assert text[start:end] == f"12{ZWSP}34"


def test_every_unicode_space_separator_is_mapped_to_a_space() -> None:
    view = clean_view("".join(chr(c) for c in range(sys.maxunicode + 1) if _is_zs(c)))
    assert set(view.text) == {" "}


def test_every_listed_dash_becomes_a_hyphen_and_every_invisible_character_goes() -> None:
    assert clean_view("".join(DASHES)).text == "-" * len(DASHES)
    assert clean_view("".join(INVISIBLE)).text == ""
    required = {0x200B, 0x200C, 0x200D, 0x2060, 0x00AD, 0xFEFF}
    assert required <= {ord(c) for c in INVISIBLE}


def _is_zs(code_point: int) -> bool:
    return unicodedata.category(chr(code_point)) == "Zs"


def test_every_format_character_the_grapheme_joiner_and_every_tag_are_removed() -> None:
    """ADR 0013 amended (1c): the whole Cf category, U+034F and U+E0000-E007F go."""
    format_chars = [chr(c) for c in range(sys.maxunicode + 1) if _is_cf(c)]
    tags = [chr(c) for c in range(0xE0000, 0xE0080)]
    text = "a" + "".join(format_chars) + chr(0x034F) + "".join(tags) + "b"
    view = clean_view(text)
    assert view.text == "ab"
    assert view.original_span(1, 2) == (len(text) - 1, len(text))


def _is_cf(code_point: int) -> bool:
    return unicodedata.category(chr(code_point)) == "Cf"


def test_every_unicode_decimal_digit_becomes_its_ascii_digit_one_for_one() -> None:
    decimals = [chr(c) for c in range(sys.maxunicode + 1) if chr(c).isdecimal()]
    view = clean_view("".join(decimals))
    assert view.text == "".join(str(unicodedata.decimal(c)) for c in decimals)
    full_width = "".join(chr(0xFF10 + int(c)) if c.isdigit() else c for c in "12.03.1985")
    view = clean_view(f"DOB {full_width}")
    assert view.text == "DOB 12.03.1985"
    assert view.original_span(4, 14) == (4, 14)


def test_digits_that_are_not_decimal_are_left_alone() -> None:
    superscript_two, circled_one = chr(0x00B2), chr(0x2460)
    assert (
        clean_view(f"m{superscript_two} {circled_one}").text == f"m{superscript_two} {circled_one}"
    )


_SPECIALS = [
    NBSP,
    NNBSP,
    EN_DASH,
    MINUS,
    ZWSP,
    SHY,
    BOM,
    chr(0x034F),  # combining grapheme joiner
    chr(0x061C),  # Arabic letter mark
    chr(0xE0020),  # tag space
    chr(0xFF11),  # full-width one
    chr(0x0663),  # Arabic-Indic three
]


@given(st.text(alphabet=st.sampled_from(["a", "1", " ", "-", *_SPECIALS]), max_size=40))
def test_every_cleaned_slice_maps_to_an_original_slice_that_cleans_to_it(text: str) -> None:
    view = clean_view(text)
    for start in range(len(view.text)):
        for end in range(start + 1, len(view.text) + 1):
            original_start, original_end = view.original_span(start, end)
            assert clean_view(text[original_start:original_end]).text == view.text[start:end]


# ------------------------------------------------------------------ ADR 0016

APOSTROPHE = chr(0x2019)
VS16 = chr(0xFE0F)
LONE_SURROGATE = chr(0xD83D)
ACUTE = chr(0x0301)  # combining acute accent
DIAERESIS = chr(0x0308)  # combining diaeresis
DOT_BELOW = chr(0x0323)  # combining dot below: sorts before the acute


def test_typographic_apostrophes_become_ascii_one_for_one() -> None:
    text = f"D{APOSTROPHE}Arbanne D{chr(0x02BC)}Orvalle O{chr(0xFF07)}Neil"
    view = clean_view(text)
    assert view.text == "D'Arbanne D'Orvalle O'Neil"
    assert view.original_span(0, len(view.text)) == (0, len(text))
    assert clean_view(APOSTROPHES).text == "'" * len(APOSTROPHES)


def test_variation_selectors_are_removed() -> None:
    text = f"{chr(0x2696)}{VS16}Zorvath Quil{chr(0xE0100)}lin"
    view = clean_view(text)
    assert view.text == f"{chr(0x2696)}Zorvath Quillin"
    start, end = view.original_span(1, len(view.text))
    assert text[start:end] == f"Zorvath Quil{chr(0xE0100)}lin"
    assert clean_view(VARIATION_SELECTORS).text == ""
    assert len(VARIATION_SELECTORS) == 16 + 240


def test_lone_surrogates_become_the_replacement_character_one_for_one() -> None:
    text = f"a{LONE_SURROGATE}b{chr(0xDC00)}c"
    view = clean_view(text)
    assert view.text == f"a{REPLACEMENT}b{REPLACEMENT}c"
    assert view.original_span(1, 4) == (1, 4)
    view.text.encode()  # UTF-8 can encode what the models read


def test_composed_view_composes_accents_and_maps_whole_clusters_back() -> None:
    text = unicodedata.normalize("NFD", "José Quillén, Пётр")
    view = composed_view(text)
    assert view.text == "José Quillén, Пётр"
    for value in ("José", "Quillén", "Пётр", "é", "J"):
        start = view.text.index(value)
        original_start, original_end = view.original_span(start, start + len(value))
        assert text[original_start:original_end] == unicodedata.normalize("NFD", value)


def test_composed_view_of_composed_text_is_the_identity() -> None:
    view = composed_view("José Quillén")
    assert view.text == "José Quillén"
    assert view.original_span(0, 4) == (0, 4)


def test_composed_view_keeps_marks_that_do_not_compose_with_their_letter() -> None:
    # No precomposed character holds both marks: the cluster is reordered, not shortened.
    text = f"q{ACUTE}{DOT_BELOW}x"
    view = composed_view(text)
    assert view.text == f"q{DOT_BELOW}{ACUTE}x"
    assert view.original_span(0, 1) == (0, 3)
    assert view.original_span(1, 2) == (0, 3)
    assert view.original_span(len(view.text) - 1, len(view.text)) == (3, 4)


def test_composed_view_maps_a_character_that_composition_lengthens() -> None:
    # U+0958 is excluded from composition: NFC writes it as two characters.
    text = f"a {chr(0x0958)} b"
    view = composed_view(text)
    assert view.text == unicodedata.normalize("NFC", text)
    assert len(view.text) == len(text) + 1
    assert view.original_span(2, 3) == (2, 3)
    assert view.original_span(3, 4) == (2, 3)
    assert view.original_span(5, 6) == (4, 5)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("BETWEEN: SELENE MARCHETTI (THE CLAIMANT)", "Between: Selene Marchetti (The Claimant)"),
        ("ИСТЕЦ: КВИЛЛИН ЗОРВАТ", "Истец: Квиллин Зорват"),
        (
            "Kind regards,\n\nPTOLEMY HARROWGATE\nSenior Partner",
            "Kind regards,\n\nPtolemy Harrowgate\nSenior Partner",
        ),
        ("TAVIEL D'ARBANNE, LEFÈVRE-QUILLIN, 3RD", "Taviel D'Arbanne, Lefèvre-Quillin, 3rd"),
        # Mostly lower case: only runs of two or more words in capitals (1c #15).
        ("Met ACME LLC in London.", "Met Acme Llc in London."),
        ("Met IBM in London.", "Met IBM in London."),  # one word in capitals: left alone
        ("Пациент ЗОРВАТ КВИЛЛИН поступил", "Пациент Зорват Квиллин поступил"),
        ("signed by SELENE MARCHETTI-QUILLIN today", "signed by Selene Marchetti-Quillin today"),
        ("ask the NHS GP or the GMC", "ask the NHS GP or the GMC"),  # words under three letters
        ("codes ABC123 DEF456 here", "codes ABC123 DEF456 here"),  # not letters only
        ("one LINE\nTWO words", "one LINE\nTWO words"),  # a run never crosses a line
        ("IBM", "Ibm"),
        ("OK", "OK"),  # fewer than three cased letters
        ("4111 1111 1111 1111", "4111 1111 1111 1111"),  # pragma: allowlist secret
    ],
)
def test_lines_in_capitals_are_title_cased(text: str, expected: str) -> None:
    assert title_cased_capitals(text) == expected


def test_title_casing_keeps_characters_whose_case_mapping_is_longer() -> None:
    # Dotted capital I lowers to two characters: kept. Capital sharp S lowers to one.
    text = f"D{chr(0x0130)}YARBAKIR STRA{chr(0x1E9E)}E"
    cased = title_cased_capitals(text)
    assert cased == f"D{chr(0x0130)}yarbakir Stra{chr(0xDF)}e"


def test_ner_view_composes_then_title_cases_and_maps_back() -> None:
    text = unicodedata.normalize("NFD", "WITNESS: JOSÉ QUILLÉN") + "\nSigned."
    view = ner_view(text)
    assert view.text == "Witness: José Quillén\nSigned."
    start = view.text.index("José Quillén")
    original_start, original_end = view.original_span(start, start + len("José Quillén"))
    assert text[original_start:original_end] == unicodedata.normalize("NFD", "JOSÉ QUILLÉN")


@given(
    st.text(
        alphabet=st.sampled_from(["a", "E", "Ё", " ", "\n", ACUTE, DIAERESIS, chr(0x0958), "İ"]),
        max_size=30,
    )
)
def test_every_ner_view_slice_maps_to_an_original_slice_that_composes_to_it(text: str) -> None:
    view = ner_view(text)
    for start in range(len(view.text)):
        for end in range(start + 1, len(view.text) + 1):
            original_start, original_end = view.original_span(start, end)
            assert 0 <= original_start < original_end <= len(text)
            original = composed_view(text[original_start:original_end]).text
            assert view.text[start:end].casefold() in original.casefold()
