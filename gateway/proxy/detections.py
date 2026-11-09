"""What `/mask` found in each text, for the web UI to highlight (ADR 0024).

Each detection is a stretch of the original text and the placeholder that replaced
it. The list is exactly the replacements mask() made, so writing each placeholder
over its stretch gives the masked text back.
"""

from collections.abc import Sequence
from typing import TypedDict

from gateway.engine.detect import EngineConfig, detect
from gateway.engine.mask import Mapping, Replacement, apply_replacements, replacements
from gateway.engine.placeholders import CANONICAL
from gateway.engine.types import EntityType, Span


class Detection(TypedDict):
    start: int
    end: int
    type: str
    placeholder: str
    score: float | None  # None when no recognizer scored this stretch
    source: str  # detected | repeat | literal


def _describe(found: Replacement, spans: Sequence[Span]) -> Detection:
    match = CANONICAL.fullmatch(found.placeholder)
    entity_type = match.group("type") if match is not None else EntityType.LITERAL.value
    scores = [s.score for s in spans if s.start < found.end and found.start < s.end]
    if entity_type == EntityType.LITERAL.value:
        source, score = "literal", None
    elif scores:
        source, score = "detected", max(scores)
    else:
        # Another occurrence of a value the mapping already held (ADR 0014 amendment 1).
        source, score = "repeat", None
    return Detection(
        start=found.start,
        end=found.end,
        type=entity_type,
        placeholder=found.placeholder,
        score=score,
        source=source,
    )


def mask_detected_with_detections(
    texts: Sequence[str], spans: Sequence[Sequence[Span]], mapping: Mapping
) -> tuple[list[str], list[list[Detection]]]:
    """Mask each text's already-detected spans with `mapping` (extended in place), as
    mask() would, and say where. Detection runs apart, outside the session lock."""
    masked: list[str] = []
    detections: list[list[Detection]] = []
    for text, found_spans in zip(texts, spans, strict=True):
        found = replacements(text, found_spans, mapping)
        masked.append(apply_replacements(text, found))
        detections.append([_describe(item, found_spans) for item in found])
    return masked, detections


def mask_with_detections(
    texts: Sequence[str], config: EngineConfig, mapping: Mapping
) -> tuple[list[str], list[list[Detection]]]:
    """Mask each text with `mapping` (extended in place), as mask() would, and say where."""
    spans = [detect(text, config=config) for text in texts]
    return mask_detected_with_detections(texts, spans, mapping)
