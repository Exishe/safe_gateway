"""Resolve overlapping detections into one list of disjoint spans (ADR 0011 point 7, ADR 0013).

Fail-safe: no character that any recognizer detected is left unmasked. The
best candidate wins an overlap; a loser keeps the parts nobody else covers.
"""

from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Mapping, Sequence

from gateway.engine.types import EntityType, Span

DEFAULT_THRESHOLD = 0.5


class _Covered:
    """Disjoint covered intervals, sorted; disjoint means starts and ends sort alike."""

    def __init__(self) -> None:
        self.starts: list[int] = []
        self.ends: list[int] = []

    def gaps(self, start: int, end: int) -> list[tuple[int, int]]:
        """The parts of start..end that no interval covers yet."""
        index = bisect_right(self.starts, start) - 1
        cursor = start
        if index >= 0 and self.ends[index] > start:
            cursor = self.ends[index]
        found = []
        index += 1
        while index < len(self.starts) and self.starts[index] < end:
            if cursor < self.starts[index]:
                found.append((cursor, self.starts[index]))
            cursor = max(cursor, self.ends[index])
            index += 1
        if cursor < end:
            found.append((cursor, end))
        return found

    def add(self, start: int, end: int) -> None:
        index = bisect_left(self.starts, start)
        self.starts.insert(index, start)
        self.ends.insert(index, end)


def _trimmed(text: str, start: int, end: int) -> tuple[int, int]:
    """Drop punctuation and spaces at the edges: a remainder "-6780" becomes "6780"."""
    while start < end and not text[start].isalnum():
        start += 1
    while end > start and not text[end - 1].isalnum():
        end -= 1
    return start, end


def _union(intervals: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    united: list[tuple[int, int]] = []
    for start, end in sorted(intervals):
        if united and start <= united[-1][1]:
            united[-1] = (united[-1][0], max(united[-1][1], end))
        else:
            united.append((start, end))
    return united


def defer(
    spans: Iterable[Span], defers_to: Mapping[str, str], text: str | None = None
) -> list[Span]:
    """Fallback spans cut down to what the recognizer they defer to did not find (ADR 0023).

    `defers_to` maps a fallback recognizer to a validating one. Where libphonenumber
    found "+374 77 123456", the phone shape "+374 77 123456 0010" keeps only "0010": the
    validated value keeps its exact span and no digit a rule found is dropped. With
    `text`, the parts lose non-alphanumeric edges, as merge() remainders do.
    """
    spans = list(spans)
    covered: dict[str, _Covered] = {}
    for name in set(defers_to.values()):
        covered[name] = _Covered()
        for start, end in _union((s.start, s.end) for s in spans if s.recognizer == name):
            covered[name].add(start, end)
    kept = []
    for span in spans:
        primary = defers_to.get(span.recognizer)
        gaps = [] if primary is None else covered[primary].gaps(span.start, span.end)
        if primary is None or gaps == [(span.start, span.end)]:
            kept.append(span)
            continue
        for gap_start, gap_end in gaps:
            start, end = (
                (gap_start, gap_end) if text is None else _trimmed(text, gap_start, gap_end)
            )
            if start < end:
                kept.append(Span(start, end, span.entity_type, span.score, span.recognizer))
    return kept


def merge(
    spans: Iterable[Span],
    thresholds: Mapping[EntityType, float] | None = None,
    priority: Sequence[str] = (),
    text: str | None = None,
) -> list[Span]:
    """Disjoint spans covering every character of every span above its threshold.

    With `text`, remainders lose non-alphanumeric edges, and one left empty is dropped.
    """
    thresholds = thresholds or {}
    rank = {name: index for index, name in enumerate(priority)}
    candidates = [s for s in spans if s.score >= thresholds.get(s.entity_type, DEFAULT_THRESHOLD)]
    # Best first: longest, then highest score, then the recognizer listed first.
    candidates.sort(key=lambda s: (-s.length, -s.score, rank.get(s.recognizer, len(rank)), s.start))

    covered = _Covered()
    kept: list[Span] = []
    for candidate in candidates:
        gaps = covered.gaps(candidate.start, candidate.end)
        if gaps == [(candidate.start, candidate.end)]:
            kept.append(candidate)
            covered.add(candidate.start, candidate.end)
            continue
        for gap_start, gap_end in gaps:
            start, end = (
                (gap_start, gap_end) if text is None else _trimmed(text, gap_start, gap_end)
            )
            if start < end:
                kept.append(
                    Span(start, end, candidate.entity_type, candidate.score, candidate.recognizer)
                )
                covered.add(start, end)
    return sorted(kept, key=lambda s: s.start)
