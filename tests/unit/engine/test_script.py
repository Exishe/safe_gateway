"""Paragraphs to languages by script, not by a detector (ADR 0012 point 4)."""

import itertools

import pytest
from hypothesis import given
from hypothesis import strategies as st

from gateway.engine.script import segments

EN = "Zorvath Quillin joined Brightwater Analytics in Portland."
RU = "Договор подписал Зорват Квиллин в Новосибирске."
HY = "Զորվաթ Քվիլլինը աշխատում է Երևանում։"


def labelled(text: str) -> list[tuple[str, str]]:
    return [(text[start:end], lang) for start, end, lang in segments(text)]


def test_each_paragraph_goes_to_the_language_of_its_script() -> None:
    text = f"{EN}\n\n{RU}\n\n{HY}\n"
    assert labelled(text) == [(EN, "en"), (RU, "ru"), (HY, "hy")]


def test_neighbouring_paragraphs_of_one_language_form_one_segment() -> None:
    text = f"{EN}\n\nSecond English paragraph.\n\n{RU}"
    assert labelled(text) == [(f"{EN}\n\nSecond English paragraph.", "en"), (RU, "ru")]


def test_the_dominant_script_wins_inside_a_paragraph() -> None:
    mixed = "Договор с компанией Acme Corp заключён в Москве на три года."
    assert labelled(mixed) == [(mixed, "ru")]
    assert labelled("Meeting with Зорват at the London office of Acme.") == [
        ("Meeting with Зорват at the London office of Acme.", "en")
    ]


def test_emails_and_links_do_not_count_toward_the_script() -> None:
    # 16 Armenian letters against 16 Latin ones in the address, 28 with the link.
    hy = "Զորվաթ Քվիլլին, հեռ. +374 77 123456, zorvath@example.am, https://example.am/zq"
    ru = "Пишите Зорвату: zorvath.quillin@example.org или www.example.org/contact-form"
    assert labelled(f"{hy}\n\n{ru}") == [(hy, "hy"), (ru, "ru")]


def test_a_tie_goes_to_cyrillic_or_armenian_not_latin() -> None:
    # Latin also turns up inside Russian and Armenian text: brands, codes, addresses.
    assert labelled("Acme Сбер") == [("Acme Сбер", "ru")]
    assert labelled("Acme Արամ") == [("Acme Արամ", "hy")]


@pytest.mark.parametrize(
    "paragraph",
    [
        "4111 1111 1111 1111 - 2024",  # pragma: allowlist secret
        "Ζορβάθ Κουιλλίν, Αθήνα",  # Greek: no model
        "李明在北京工作",  # Han: no model
        "---",
    ],
)
def test_other_scripts_and_letterless_paragraphs_are_undetermined(paragraph: str) -> None:
    assert labelled(f"{EN}\n\n{paragraph}") == [(EN, "en"), (paragraph, "und")]


def test_blank_lines_with_spaces_and_windows_line_ends_separate_paragraphs() -> None:
    text = f"  {EN}\r\n \r\n\t{RU}  \n\n\n"
    assert labelled(text) == [(EN, "en"), (RU, "ru")]


def test_a_single_line_break_does_not_end_a_paragraph() -> None:
    text = f"Zorvath Quillin\n{RU}"  # one paragraph: 14 Latin letters, 40 Cyrillic
    assert labelled(text) == [(text, "ru")]


@pytest.mark.parametrize("text", ["", "   ", "\n\n\n"])
def test_blank_text_has_no_segments(text: str) -> None:
    assert segments(text) == []


@given(
    st.lists(st.sampled_from([EN, RU, HY, "12 34", "Ok.", "Да."]), min_size=1, max_size=8).map(
        "\n\n".join
    )
)
def test_segments_are_ordered_disjoint_and_hold_every_letter(text: str) -> None:
    found = segments(text)
    for (_, end, lang), (next_start, _, next_lang) in itertools.pairwise(found):
        assert end < next_start
        assert lang != next_lang
    covered = "".join(text[start:end] for start, end, _ in found)
    assert sorted(c for c in covered if not c.isspace()) == sorted(
        c for c in text if not c.isspace()
    )
