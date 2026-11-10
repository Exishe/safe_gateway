"""Known values are masked wherever they recur (ADR 0014 amendment 1, leak hunt 2)."""

import re
import time

import pytest

from gateway.engine.detect import EngineConfig
from gateway.engine.known import KnownValues
from gateway.engine.mask import Mapping, mask, mask_messages
from gateway.engine.rehydrate import rehydrate
from gateway.engine.types import EntityType, Span

NO_NER = EngineConfig(ner="off")
SSN = "123-45-6780"  # pragma: allowlist secret
SSN_2 = "234-56-7890"  # pragma: allowlist secret
P = EntityType.PERSON


def known(*values: str) -> Mapping:
    mapping = Mapping()
    for value in values:
        mapping.placeholder_for(value, EntityType.SSN if value[0].isdigit() else P)
    return mapping


def masked(text: str, mapping: Mapping) -> str:
    out, _ = mask(text, [], mapping)
    assert rehydrate(out, mapping) == (text, [])
    return out


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"ref{SSN}.pdf", "ref[SSN_1].pdf"),  # a letter touches a digit edge: still there
        (f"{SSN}th", "[SSN_1]th"),
        (f"_{SSN}_", "_[SSN_1]_"),
        (f"1{SSN}", f"1{SSN}"),  # a digit continues the number: another number
        (f"{SSN}0", f"{SSN}0"),
    ],
)
def test_a_digit_edge_is_blocked_only_by_a_digit(text: str, expected: str) -> None:
    assert masked(text, known(SSN)) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Also Al came", "Also [PERSON_1] came"),  # a letter continues the word
        ("Al's file, Al2, (Al)", "[PERSON_1]'s file, [PERSON_1]2, ([PERSON_1])"),
        ("metal", "metal"),
        (f"{chr(0x436)}Al", f"{chr(0x436)}Al"),  # any script's letter counts
        ("al", "al"),  # exact surface: another spelling is detection's job
    ],
)
def test_a_letter_edge_is_blocked_only_by_a_letter(text: str, expected: str) -> None:
    assert masked(text, known("Al")) == expected


def test_the_longest_known_value_wins() -> None:
    mapping = known("Anna", "Anna Petrova")
    assert masked("Anna Petrova and Anna", mapping) == "[PERSON_2] and [PERSON_1]"


def test_overlapping_known_values_are_united_never_split() -> None:
    mapping = known("123-45", "45-6780")
    out = masked(f"x {SSN} y", mapping)
    assert "6780" not in out
    assert "123" not in out


def test_a_value_repeated_in_the_same_text_is_masked_where_detection_misses_it() -> None:
    text = f"SSN {SSN}; scan saved as ref{SSN}.pdf"
    out, _ = mask_messages([text], config=NO_NER)
    assert out == ["SSN [SSN_1]; scan saved as ref[SSN_1].pdf"]


def test_a_value_detected_in_a_later_message_is_masked_in_an_earlier_one() -> None:
    texts = [f"Scan saved as ref{SSN}.pdf", f"SSN {SSN} and SSN {SSN_2}."]
    out, mapping = mask_messages(texts, config=NO_NER)
    assert out == ["Scan saved as ref[SSN_1].pdf", "SSN [SSN_1] and SSN [SSN_2]."]
    assert [rehydrate(m, mapping)[0] for m in out] == texts


def test_numbering_of_earlier_messages_is_stable_when_a_turn_is_added() -> None:
    first = [f"SSN {SSN_2}, file ref{SSN}.pdf"]
    later = [*first, f"SSN {SSN}."]
    before, _ = mask_messages(first, config=NO_NER)
    after, _ = mask_messages(later, config=NO_NER)
    assert before == [f"SSN [SSN_1], file ref{SSN}.pdf"]
    assert after[0] == "SSN [SSN_1], file ref[SSN_2].pdf"


def test_a_session_mapping_masks_a_value_from_an_earlier_request() -> None:
    mapping = Mapping()
    mask(f"SSN {SSN}", [Span(4, 15, EntityType.SSN, 0.9, "test")], mapping)
    assert mask(f"see ref{SSN}.pdf", [], mapping)[0] == "see ref[SSN_1].pdf"


def test_literals_are_not_swept() -> None:
    mapping = Mapping()
    mapping.placeholder_for("PERSON 2", EntityType.LITERAL)
    assert mask("xPERSON 2", [], mapping)[0] == "xPERSON 2"


def test_a_chain_of_values_that_prefix_each_other_compiles() -> None:
    # Deeper than the tree's 24 levels; values are listed flat from there on.
    values: dict[str, EntityType] = {"A" * size: P for size in range(1, 100)}
    found = KnownValues(values).spans("B " + "A" * 99 + " B " + "A" * 30 + "-")
    assert (found[0].start, found[0].end) == (2, 101)
    assert (found[-1].start, found[-1].end) == (104, 134)


def test_compiled_values_do_not_stay_in_the_regex_module_cache() -> None:
    marker = "Zorvath Quillin cache probe"
    re.compile("another caller's pattern")
    KnownValues({marker: P})
    # The module-wide cache itself: private, so read by name.
    cached = [*getattr(re, "_cache", {}), *getattr(re, "_cache2", {})]
    # Other callers keep their cached patterns (re.purge would cost them a recompile);
    # this also fails, as it should, if CPython moves the cache.
    assert any("another caller" in str(key) for key in cached), "the re cache moved"
    assert not any(marker in str(key) for key in cached)


def test_no_known_values_finds_nothing() -> None:
    assert KnownValues({}).spans("anything") == []


def five_thousand_ssns() -> Mapping:
    mapping = Mapping()
    for n in range(5000):
        value = f"{100 + n % 800:03d}-{10 + n % 89:02d}-{1000 + n:04d}"
        mapping.placeholder_for(value, EntityType.SSN)
    mapping.placeholder_for(SSN_2, EntityType.SSN)
    return mapping


def test_960k_characters_against_5000_known_values_is_fast() -> None:
    mapping = five_thousand_ssns()
    mapping.known()  # compiled once per change of the mapping; timed below
    filler = "The quick brown fox 12 jumps over 2024 lazy dogs. Ref 555 here. " * 15_000
    text = f"{filler[:960_000]} ref{SSN_2}x"

    started = time.perf_counter()
    out, _ = mask(text, [], mapping)
    elapsed = time.perf_counter() - started

    assert out.endswith("ref[SSN_5001]x")
    assert elapsed < 1.0, f"{elapsed:.2f}s for {len(text)} characters"


def test_5000_known_values_compile_within_seconds() -> None:
    mapping = five_thousand_ssns()
    started = time.perf_counter()
    mapping.known()
    elapsed = time.perf_counter() - started
    # About 0.4 s on its own; coverage tracing of the regex parser makes it ~2.5 s.
    assert elapsed < 6.0, f"{elapsed:.2f}s to compile 5001 values"


def test_the_compiled_values_are_reused_until_a_value_is_added() -> None:
    mapping = known(SSN)
    first = mapping.known()
    mapping.placeholder_for(SSN, EntityType.SSN)  # already there: no change
    mapping.placeholder_for("[PERSON_1]", EntityType.LITERAL)  # literals are not swept
    assert mapping.known() is first
    mapping.placeholder_for(SSN_2, EntityType.SSN)
    assert mapping.known() is not first
    mapping.clear()
    assert len(mapping.known()) == 0


# ------------------------------------------------------------------ clean view (leak hunt 1d)

ZWSP, NBSP = chr(0x200B), chr(0x00A0)


@pytest.mark.parametrize(
    ("stored", "written"),
    [
        (SSN[:4] + ZWSP + SSN[4:], SSN),  # stored with an invisible character, written plain
        (SSN, SSN[:4] + ZWSP + SSN[4:]),  # and the other way round
        ("Selene" + NBSP + "Quillin", "Selene Quillin"),
    ],
)
def test_known_values_match_in_the_clean_view_and_map_back(stored: str, written: str) -> None:
    values = KnownValues({stored: EntityType.SSN})
    text = f"file ({written}).pdf"
    [span] = values.spans(text)
    assert text[span.start : span.end] == written


def test_a_value_that_cleans_to_nothing_is_not_swept() -> None:
    assert KnownValues({ZWSP: P}).spans(f"a{ZWSP}b") == []
