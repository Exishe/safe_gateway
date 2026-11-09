"""Paragraphs to languages by script, not by a detector (ADR 0012 point 4)."""

import itertools
import time

import pytest
from hypothesis import given
from hypothesis import strategies as st

from gateway.engine.script import lines_in_other_scripts, segments

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
        "Ζορβάθ Κουιλλίν, Αθήνα",  # Greek: no model
        "李明在北京工作",  # Han: no model
    ],
)
def test_other_scripts_are_undetermined(paragraph: str) -> None:
    assert labelled(f"{EN}\n\n{paragraph}") == [(EN, "en"), (paragraph, "und")]


@pytest.mark.parametrize(
    "paragraph",
    [
        "4111 1111 1111 1111 - 2024",  # pragma: allowlist secret
        "1985-03-12",
        "---",
        "anna.petrova@example.org",  # an address is not counted as letters
    ],
)
def test_a_paragraph_without_letters_joins_the_language_before_it(paragraph: str) -> None:
    text = f"{EN}\n\n{paragraph}\n\n{RU}"
    assert labelled(text) == [(f"{EN}\n\n{paragraph}", "en"), (RU, "ru")]


def test_a_paragraph_without_letters_at_the_start_joins_the_language_after_it() -> None:
    text = f"12.03.1985\n\n{RU}\n\n{EN}"
    assert labelled(text) == [(f"12.03.1985\n\n{RU}", "ru"), (EN, "en")]


@pytest.mark.parametrize("text", ["12 34", "12 34\n\n---", "+7 912 345-67-89"])
def test_a_text_without_letters_is_undetermined(text: str) -> None:
    assert labelled(text) == [(text, "und")]


def test_blank_lines_with_spaces_and_windows_line_ends_separate_paragraphs() -> None:
    text = f"  {EN}\r\n \r\n\t{RU}  \n\n\n"
    assert labelled(text) == [(EN, "en"), (RU, "ru")]


def test_a_single_line_break_between_two_scripts_ends_a_segment() -> None:
    # A chat box sends Enter as one line break: each line goes to the model of its script.
    request = "Please check this clause for Zorvath Quillin:"
    assert labelled(f"{request}\n{RU}") == [(request, "en"), (RU, "ru")]
    assert labelled(f"Zorvath Quillin\r\n{RU}\n{EN}") == [
        ("Zorvath Quillin", "en"),
        (RU, "ru"),
        (EN, "en"),
    ]


def test_lines_of_one_script_stay_together() -> None:
    text = f"{EN}\nSecond line.\n12 34\n{EN}"
    assert labelled(text) == [(text, "en")]


def test_a_line_without_a_model_language_stays_with_its_paragraph() -> None:
    # Letterless and other-script lines go with the paragraph's model language, before or after.
    text = f"12 34\nΑθήνα\n{RU}\n---"
    assert labelled(text) == [(text, "ru")]


@pytest.mark.parametrize(
    "separator",
    ["\r\r", "\n\f", "\f\n", "\f", chr(0x2029), chr(0x2028) * 2, "\r\n\r\n", "\r\n \r\n"],
)
def test_page_breaks_paragraph_separators_and_classic_mac_blank_lines_split(
    separator: str,
) -> None:
    text = f"{EN}{separator}{RU}"
    assert labelled(text) == [(EN, "en"), (RU, "ru")]
    assert labelled(f"{EN}{separator}Second English paragraph.") == [
        (f"{EN}{separator}Second English paragraph.", "en")
    ]


@pytest.mark.parametrize("line_break", ["\r\n", "\r", "\n", chr(0x2028), chr(0x85)])
def test_one_line_break_of_any_kind_is_one_line_break(line_break: str) -> None:
    assert labelled(f"Zorvath Quillin{line_break}{RU}") == [("Zorvath Quillin", "en"), (RU, "ru")]
    same = f"{EN}{line_break}{EN}"
    assert labelled(same) == [(same, "en")]


@pytest.mark.parametrize(
    "text",
    [
        "x" * 50_000,  # no whitespace, no address: every start was retried
        "a@" * 25_000,
        "\n" + " " * 200_000 + "x",
        "\n \n" * 50_000,
        "\r" * 200_000,
    ],
)
def test_routing_is_linear_in_the_length_of_the_text(text: str) -> None:
    started = time.perf_counter()
    segments(text)
    assert time.perf_counter() - started < 1.0


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


# ------------------------------------------------------------------ leak hunt 1c (ADR 0016 amended)


def other_script_lines(text: str) -> list[str]:
    return [text[start:end] for start, end in lines_in_other_scripts(text, 0, len(text))]


def test_lines_with_letters_of_a_script_without_a_spacy_model_are_listed() -> None:
    greek = "Translate this sentence to English: Ο Νίκος μένει στην Πάτρα"
    armenian = f"Переведи это предложение: {HY}"
    text = f"{EN}\n{greek}\n{RU}\n{armenian}\n\n{HY}"
    assert other_script_lines(text) == [greek, armenian, HY]


@pytest.mark.parametrize(
    "text",
    [
        EN,
        RU,
        "Zoë Brontë, François Lefèvre, Łukasz Żółć",  # Latin with diacritics
        "dose 5 µg, 2ª edición, nº 3",  # micro sign, ordinal indicators: Latin-1
        "4111 1111 1111 1111 — 12.03.1985",  # pragma: allowlist secret
    ],
)
def test_latin_and_cyrillic_lines_are_not_listed(text: str) -> None:
    assert other_script_lines(text) == []


def test_only_lines_inside_the_range_are_listed() -> None:
    text = f"{HY}\n{EN}\n{HY}"
    start = len(HY) + 1
    assert lines_in_other_scripts(text, start, len(text)) == [(start + len(EN) + 1, len(text))]
