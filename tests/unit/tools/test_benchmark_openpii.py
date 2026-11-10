import hashlib
import json
from pathlib import Path

import pytest

from gateway.engine.types import EntityType, Span
from tools.benchmark_openpii import (
    LABEL_MAP,
    GoldSpan,
    Tally,
    coverage,
    load_records,
    render_markdown,
    sample_indices,
    verify_sha256,
)


def span(start: int, end: int, kind: EntityType = EntityType.PERSON) -> Span:
    return Span(start, end, kind, 0.9, "test")


# ------------------------------------------------------------------ coverage


def test_coverage_full_partial_and_none() -> None:
    assert coverage(10, 20, [(10, 20)]) == 1.0
    assert coverage(10, 20, [(10, 15)]) == 0.5
    assert coverage(10, 20, [(30, 40)]) == 0.0


def test_coverage_counts_overlapping_detections_once() -> None:
    # Two detections that overlap each other must not push coverage above 1.
    assert coverage(0, 10, [(0, 8), (4, 10)]) == 1.0
    assert coverage(0, 10, [(0, 6), (4, 8)]) == 0.8


def test_coverage_of_a_span_spread_over_two_detections() -> None:
    assert coverage(0, 10, [(0, 5), (5, 10)]) == 1.0


# ------------------------------------------------------------------ tally


def test_masking_recall_uses_any_detected_type() -> None:
    tally = Tally()
    gold = [GoldSpan("CITY", 0, 10)]
    tally.add_record("en", gold, [span(0, 10, EntityType.ORG)])
    row = tally.gold["en", "CITY"]
    assert (row.total, row.masked, row.typed) == (1, 1, 0)


def test_typed_recall_needs_a_span_of_the_mapped_type() -> None:
    tally = Tally()
    tally.add_record("en", [GoldSpan("CITY", 0, 10)], [span(0, 10, EntityType.LOCATION)])
    row = tally.gold["en", "CITY"]
    assert (row.total, row.masked, row.typed) == (1, 1, 1)


def test_ninety_percent_threshold_is_inclusive() -> None:
    tally = Tally()
    tally.add_record("en", [GoldSpan("CITY", 0, 10)], [span(0, 9, EntityType.LOCATION)])
    tally.add_record("en", [GoldSpan("CITY", 0, 10)], [span(0, 8, EntityType.LOCATION)])
    row = tally.gold["en", "CITY"]
    assert (row.total, row.masked) == (2, 1)


def test_label_with_several_accepted_types() -> None:
    assert {EntityType.SSN, EntityType.NATIONAL_ID} <= LABEL_MAP["SOCIALNUM"]
    tally = Tally()
    tally.add_record("en", [GoldSpan("SOCIALNUM", 0, 11)], [span(0, 11, EntityType.NATIONAL_ID)])
    assert tally.gold["en", "SOCIALNUM"].typed == 1


def test_label_without_an_entity_type_is_listed_as_not_covered() -> None:
    assert LABEL_MAP["AGE"] == frozenset()
    tally = Tally()
    tally.add_record("en", [GoldSpan("AGE", 0, 2)], [])
    row = tally.gold["en", "AGE"]
    assert (row.total, row.masked, row.typed) == (1, 0, 0)
    assert tally.not_covered_labels() == {"AGE"}


def test_unknown_gold_label_is_rejected_not_dropped() -> None:
    with pytest.raises(KeyError, match="NEWLABEL"):
        Tally().add_record("en", [GoldSpan("NEWLABEL", 0, 2)], [])


def test_precision_counts_detected_spans_that_touch_a_gold_span() -> None:
    tally = Tally()
    detected = [span(0, 5), span(20, 25), span(100, 105, EntityType.EMAIL)]
    tally.add_record("en", [GoldSpan("GIVENNAME", 3, 8)], detected)
    assert (tally.detected["en", "PERSON"].total, tally.detected["en", "PERSON"].hit) == (2, 1)
    assert (tally.detected["en", "EMAIL"].total, tally.detected["en", "EMAIL"].hit) == (1, 0)


def test_languages_are_kept_apart() -> None:
    tally = Tally()
    tally.add_record("en", [GoldSpan("CITY", 0, 4)], [])
    tally.add_record("fr", [GoldSpan("CITY", 0, 4)], [span(0, 4, EntityType.LOCATION)])
    assert tally.gold["en", "CITY"].masked == 0
    assert tally.gold["fr", "CITY"].masked == 1


# ------------------------------------------------------------------ sampling


def test_sample_is_seeded_sorted_and_without_repeats() -> None:
    first = sample_indices(1000, 50, seed=7)
    assert first == sample_indices(1000, 50, seed=7)
    assert first != sample_indices(1000, 50, seed=8)
    assert first == sorted(set(first))
    assert len(first) == 50


def test_sample_larger_than_population_returns_everything() -> None:
    assert sample_indices(5, 50, seed=1) == [0, 1, 2, 3, 4]


# ------------------------------------------------------------------ files


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def row(language: str, text: str, mask: list[dict[str, object]]) -> dict[str, object]:
    return {"source_text": text, "privacy_mask": mask, "language": language}


def test_load_records_groups_by_language_and_checks_offsets(tmp_path: Path) -> None:
    path = tmp_path / "x.jsonl"
    good: dict[str, object] = {"label": "CITY", "start": 3, "end": 7, "value": "Oslo"}
    shifted: dict[str, object] = {"label": "CITY", "start": 0, "end": 4, "value": "Oslo"}
    write_jsonl(path, [row("en", "in Oslo", [good]), row("fr", "à Oslo", [shifted])])
    records, skipped = load_records(path)
    assert [r.text for r in records["en"]] == ["in Oslo"]
    assert records["en"][0].gold == [GoldSpan("CITY", 3, 7)]
    # A record whose offsets do not match its value is counted, not silently used.
    assert records.get("fr", []) == []
    assert skipped == {"fr": 1}


def test_verify_sha256(tmp_path: Path) -> None:
    path = tmp_path / "f"
    path.write_bytes(b"abc")
    verify_sha256(path, hashlib.sha256(b"abc").hexdigest())
    with pytest.raises(ValueError, match="SHA-256"):
        verify_sha256(path, "0" * 64)


# ------------------------------------------------------------------ report


def test_markdown_has_one_row_per_language_and_label() -> None:
    tally = Tally()
    tally.add_record("en", [GoldSpan("CITY", 0, 10)], [span(0, 10, EntityType.LOCATION)])
    tally.add_record("en", [GoldSpan("AGE", 0, 2)], [])
    text = render_markdown(tally, languages=["en"])
    assert "| en | CITY | LOCATION | 1 | 100.0% | 100.0% |" in text
    assert "not covered" in text
    assert "AGE" in text
