from itertools import pairwise

import pytest
from hypothesis import given
from hypothesis import strategies as st

from gateway.engine.merge import defer, merge
from gateway.engine.types import EntityType, Span

E = EntityType


def span(start: int, end: int, score: float = 1.0, kind: E = E.EMAIL, by: str = "a") -> Span:
    return Span(start, end, kind, score, by)


# ------------------------------------------------------------------ defer (ADR 0023)

DEFERS = {"shape": "valid"}


def test_a_fallback_span_keeps_only_what_its_validating_recognizer_left() -> None:
    text = "+374 77 123456 0010 Yerevan"  # pragma: allowlist secret
    valid = span(0, 14, 0.95, E.PHONE, "valid")
    shape = span(0, 19, 0.85, E.PHONE, "shape")
    assert defer([valid, shape], DEFERS, text) == [valid, span(15, 19, 0.85, E.PHONE, "shape")]


def test_a_fallback_span_inside_a_validated_one_is_dropped() -> None:
    valid, shape = span(0, 14, 0.95, E.PHONE, "valid"), span(2, 10, 0.85, E.PHONE, "shape")
    assert defer([valid, shape], DEFERS) == [valid]


def test_overlapping_validated_spans_are_united_before_the_fallback_is_cut() -> None:
    validated = [span(0, 10, by="valid"), span(5, 20, by="valid")]
    shape = span(0, 25, 0.85, by="shape")
    assert defer([*validated, shape], DEFERS) == [*validated, span(20, 25, 0.85, by="shape")]


def test_a_fallback_span_nothing_overlaps_keeps_its_punctuation() -> None:
    text = "call +999 1234 5678 now"  # pragma: allowlist secret
    shape = span(5, 19, 0.85, E.PHONE, "shape")
    assert defer([shape], DEFERS, text) == [shape]


def test_spans_of_other_recognizers_pass_through_unchanged() -> None:
    spans = [span(0, 10, by="valid"), span(0, 20, by="other"), span(30, 40, 0.85, by="shape")]
    assert defer(spans, DEFERS) == spans


def test_spans_below_their_threshold_are_dropped() -> None:
    kept = merge([span(0, 5, 0.4), span(10, 15, 0.6)])
    assert kept == [span(10, 15, 0.6)]


def test_per_type_thresholds_override_the_default() -> None:
    spans = [span(0, 5, 0.6, E.SSN), span(10, 15, 0.6, E.EMAIL)]
    assert merge(spans, thresholds={E.SSN: 0.7}) == [span(10, 15, 0.6, E.EMAIL)]


def test_disjoint_spans_are_all_kept_and_sorted() -> None:
    assert merge([span(10, 15), span(0, 5)]) == [span(0, 5), span(10, 15)]


def test_nested_span_loses_to_the_outer_one() -> None:
    assert merge([span(2, 4), span(0, 10)]) == [span(0, 10)]


def test_partial_overlap_keeps_the_longer_span_and_the_rest_of_the_shorter() -> None:
    assert merge([span(0, 6), span(4, 14)]) == [span(0, 4), span(4, 14)]


def test_equal_length_the_higher_score_wins_the_overlap() -> None:
    spans = [span(0, 5, 0.7, by="a"), span(2, 7, 0.9, by="b")]
    assert merge(spans) == [span(0, 2, 0.7, by="a"), span(2, 7, 0.9, by="b")]


def test_equal_length_and_score_keeps_the_earlier_recognizer() -> None:
    spans = [span(0, 5, by="late"), span(0, 5, kind=E.PHONE, by="early")]
    assert merge(spans, priority=["early", "late"]) == [span(0, 5, kind=E.PHONE, by="early")]


def test_touching_spans_do_not_overlap() -> None:
    assert merge([span(0, 5), span(5, 9)]) == [span(0, 5), span(5, 9)]


def test_a_loser_does_not_knock_out_a_third_span() -> None:
    # 0-10 beats 8-12, which keeps 10-12; 11-15 keeps what is still uncovered, 12-15.
    assert merge([span(0, 10), span(8, 12), span(11, 15)]) == [
        span(0, 10),
        span(10, 12),
        span(12, 15),
    ]


def test_a_span_already_covered_by_kept_spans_is_dropped() -> None:
    assert merge([span(0, 5), span(5, 10), span(3, 7)]) == [span(0, 5), span(5, 10)]


def test_a_gap_between_two_kept_spans_is_filled_by_the_span_across_it() -> None:
    spans = [span(0, 10), span(12, 20), span(8, 14, kind=E.SSN)]
    assert merge(spans) == [span(0, 10), span(10, 12, kind=E.SSN), span(12, 20)]


# ------------------------------------------------------------------ remainders and text

TEXT = "x 55794 123-45-6780 y"  # pragma: allowlist secret


def test_a_remainder_keeps_its_own_type_and_score_and_loses_edge_punctuation() -> None:
    coincidence = span(2, 14, kind=E.NATIONAL_ID)  # "55794 123-45"  # pragma: allowlist secret
    ssn = span(8, 19, 0.85, kind=E.SSN, by="us_ssn")  # the SSN in TEXT
    assert merge([coincidence, ssn], text=TEXT) == [
        coincidence,
        span(15, 19, 0.85, kind=E.SSN, by="us_ssn"),
    ]
    assert TEXT[15:19] == "6780"


def test_without_text_a_remainder_is_not_trimmed() -> None:
    spans = [span(2, 14, kind=E.NATIONAL_ID), span(8, 19, kind=E.SSN)]
    assert merge(spans)[1] == span(14, 19, kind=E.SSN)


def test_a_remainder_of_punctuation_only_is_dropped() -> None:
    assert merge([span(0, 5), span(2, 6)], text="12-34 56") == [span(0, 5)]


def test_a_span_without_overlap_is_never_trimmed() -> None:
    assert merge([span(0, 5, kind=E.PHONE)], text="(201) 555") == [span(0, 5, kind=E.PHONE)]


# ------------------------------------------------------------------ fail-safe property

SCORES = st.sampled_from([0.5, 0.6, 0.85, 0.9, 0.95, 1.0])
KINDS = st.sampled_from([E.EMAIL, E.SSN, E.PHONE, E.NATIONAL_ID])


@st.composite
def spans_in(draw: st.DrawFn, size: int) -> list[Span]:
    result = []
    for _ in range(draw(st.integers(0, 12))):
        start = draw(st.integers(0, size - 1))
        end = draw(st.integers(start + 1, size))
        result.append(Span(start, end, draw(KINDS), draw(SCORES), draw(st.sampled_from("abc"))))
    return result


def covered(spans: list[Span]) -> set[int]:
    return {index for s in spans for index in range(s.start, s.end)}


def assert_disjoint(spans: list[Span]) -> None:
    for left, right in pairwise(spans):
        assert left.end <= right.start, f"{left} overlaps {right}"


@given(spans_in(40))
def test_no_detected_character_is_left_uncovered(spans: list[Span]) -> None:
    kept = merge(spans, priority=["a", "b", "c"])
    assert_disjoint(kept)
    assert covered(kept) == covered(spans)


@given(st.text(alphabet="a1-( ", min_size=40, max_size=40), spans_in(40))
def test_with_text_only_punctuation_may_be_left_uncovered(text: str, spans: list[Span]) -> None:
    kept = merge(spans, priority=["a", "b", "c"], text=text)
    assert_disjoint(kept)
    assert covered(kept) <= covered(spans)
    missing = covered(spans) - covered(kept)
    assert all(not text[index].isalnum() for index in missing)


@pytest.mark.parametrize(("start", "end", "score"), [(5, 5, 1.0), (6, 5, 1.0), (0, 5, 1.5)])
def test_invalid_spans_are_rejected(start: int, end: int, score: float) -> None:
    with pytest.raises(ValueError, match="span"):
        Span(start, end, E.EMAIL, score, "a")
