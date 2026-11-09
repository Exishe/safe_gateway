"""The clean text view: what recognizers read, and the map back to the original (ADR 0013)."""

import sys
import unicodedata

from hypothesis import given
from hypothesis import strategies as st

from gateway.engine.textview import DASHES, INVISIBLE, clean_view

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


_SPECIALS = [NBSP, NNBSP, EN_DASH, MINUS, ZWSP, SHY, BOM]


@given(st.text(alphabet=st.sampled_from(["a", "1", " ", "-", *_SPECIALS]), max_size=40))
def test_every_cleaned_slice_maps_to_an_original_slice_that_cleans_to_it(text: str) -> None:
    view = clean_view(text)
    for start in range(len(view.text)):
        for end in range(start + 1, len(view.text) + 1):
            original_start, original_end = view.original_span(start, end)
            assert clean_view(text[original_start:original_end]).text == view.text[start:end]
