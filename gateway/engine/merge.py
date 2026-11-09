"""Resolve overlapping detections into one list of spans (ADR 0011, point 7)."""

from collections.abc import Iterable, Mapping, Sequence

from gateway.engine.types import EntityType, Span

DEFAULT_THRESHOLD = 0.5


def merge(
    spans: Iterable[Span],
    thresholds: Mapping[EntityType, float] | None = None,
    priority: Sequence[str] = (),
) -> list[Span]:
    thresholds = thresholds or {}
    rank = {name: index for index, name in enumerate(priority)}
    candidates = [s for s in spans if s.score >= thresholds.get(s.entity_type, DEFAULT_THRESHOLD)]
    # Best first: longest, then highest score, then the recognizer listed first.
    candidates.sort(key=lambda s: (-s.length, -s.score, rank.get(s.recognizer, len(rank)), s.start))

    kept: list[Span] = []
    for candidate in candidates:
        if all(candidate.end <= k.start or candidate.start >= k.end for k in kept):
            kept.append(candidate)
    return sorted(kept, key=lambda s: s.start)
