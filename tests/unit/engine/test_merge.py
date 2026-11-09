import pytest

from gateway.engine.merge import merge
from gateway.engine.types import EntityType, Span

E = EntityType


def span(start: int, end: int, score: float = 1.0, kind: E = E.EMAIL, by: str = "a") -> Span:
    return Span(start, end, kind, score, by)


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


def test_partial_overlap_keeps_the_longer_span() -> None:
    assert merge([span(0, 6), span(4, 14)]) == [span(4, 14)]


def test_equal_length_keeps_the_higher_score() -> None:
    assert merge([span(0, 5, 0.7, by="a"), span(2, 7, 0.9, by="b")]) == [span(2, 7, 0.9, by="b")]


def test_equal_length_and_score_keeps_the_earlier_recognizer() -> None:
    spans = [span(0, 5, by="late"), span(0, 5, kind=E.PHONE, by="early")]
    assert merge(spans, priority=["early", "late"]) == [span(0, 5, kind=E.PHONE, by="early")]


def test_touching_spans_do_not_overlap() -> None:
    assert merge([span(0, 5), span(5, 9)]) == [span(0, 5), span(5, 9)]


def test_a_loser_does_not_knock_out_a_third_span() -> None:
    # 0-10 beats 8-12; 11-15 overlaps only the loser, so it stays.
    assert merge([span(0, 10), span(8, 12), span(11, 15)]) == [span(0, 10), span(11, 15)]


@pytest.mark.parametrize(("start", "end", "score"), [(5, 5, 1.0), (6, 5, 1.0), (0, 5, 1.5)])
def test_invalid_spans_are_rejected(start: int, end: int, score: float) -> None:
    with pytest.raises(ValueError, match="span"):
        Span(start, end, E.EMAIL, score, "a")
