from gateway.engine.mask import Replacement
from gateway.engine.types import EntityType, Span
from gateway.proxy.detections import _describe


def test_a_stretch_no_recognizer_scored_is_a_repeat_without_a_score() -> None:
    # The known-value sweep masked it (ADR 0014 amendment 1); no span covers it here.
    found = _describe(Replacement(10, 20, "[EMAIL_1]"), [])
    assert found == {
        "start": 10,
        "end": 20,
        "type": "EMAIL",
        "placeholder": "[EMAIL_1]",
        "score": None,
        "source": "repeat",
    }


def test_the_best_overlapping_score_is_reported() -> None:
    spans = [
        Span(0, 5, EntityType.PERSON, 0.4, "a"),
        Span(3, 9, EntityType.PERSON, 0.9, "b"),
        Span(20, 25, EntityType.PERSON, 1.0, "elsewhere"),
    ]
    found = _describe(Replacement(0, 9, "[PERSON_1]"), spans)
    assert (found["source"], found["score"]) == ("detected", 0.9)
