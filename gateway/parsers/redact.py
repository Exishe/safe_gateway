"""Mask a Document unit by unit with one Mapping, and spread a unit's result over its pieces.

Spans are clipped to units (ADR 0017 point 3): a value that crosses a unit boundary
becomes one placeholder per unit, so no character of it is ever left unmasked.
"""

from bisect import bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from gateway.engine.mask import Mapping, Replacement, apply_replacements, replacements
from gateway.engine.types import Span
from gateway.parsers.document import Document, DocumentError


@dataclass(frozen=True)
class UnitMask:
    text: str  # the unit's masked text
    replacements: tuple[Replacement, ...]  # offsets in the unit's original text


def _spans_per_unit(document: Document, spans: Iterable[Span]) -> list[list[Span]]:
    starts = [segment.start for segment in document.segments]
    per_unit: list[list[Span]] = [[] for _ in document.segments]
    for span in spans:
        index = max(bisect_right(starts, span.start) - 1, 0)
        while index < len(document.segments) and document.segments[index].start < span.end:
            segment = document.segments[index]
            start, end = max(span.start, segment.start), min(span.end, segment.end)
            if start < end:
                per_unit[index].append(
                    Span(
                        start - segment.start,
                        end - segment.start,
                        span.entity_type,
                        span.score,
                        span.recognizer,
                    )
                )
            index += 1
    return per_unit


def mask_units(
    document: Document, spans: Iterable[Span], mapping: Mapping | None = None
) -> tuple[list[UnitMask], Mapping]:
    """Each unit masked in document order; numbering follows first appearance."""
    mapping = Mapping() if mapping is None else mapping
    masks = []
    for index, unit_spans in enumerate(_spans_per_unit(document, spans)):
        text = document.segment_text(index)
        found = replacements(text, unit_spans, mapping)
        masks.append(UnitMask(apply_replacements(text, found), tuple(found)))
    return masks, mapping


def masked_text(document: Document, masks: Sequence[UnitMask]) -> str:
    """The document text with every unit replaced by its masked text, separators kept."""
    pieces: list[str] = []
    cursor = 0
    for segment, unit in zip(document.segments, masks, strict=True):
        pieces.append(document.text[cursor : segment.start])
        pieces.append(unit.text)
        cursor = segment.end
    pieces.append(document.text[cursor:])
    return "".join(pieces)


def _target(bounds: list[tuple[int, int]], writable: list[bool], found: Replacement) -> int:
    """The writable piece that receives a placeholder: the first one in its region,
    else the nearest one before it, else the nearest one after it."""
    inside = [
        i
        for i, (start, end) in enumerate(bounds)
        if writable[i] and start < found.end and end > found.start
    ]
    before = [i for i, (_, end) in enumerate(bounds) if writable[i] and end <= found.start]
    after = [i for i, (start, _) in enumerate(bounds) if writable[i] and start >= found.end]
    for candidates, pick in ((inside, min), (before, max), (after, min)):
        if candidates:
            return pick(candidates)
    msg = "no writable text element for a masked value"
    raise DocumentError(msg)


def distribute(
    pieces: Sequence[tuple[str, bool]], found: Sequence[Replacement]
) -> list[str | None]:
    """New text for each (text, writable) piece of one unit, given its replacements.

    A writable piece gets its characters outside every replaced region, plus the
    placeholders it receives. A fixed piece (a tab or a break element) keeps its text,
    or gets None when it lies inside a replaced region and must be removed.
    """
    bounds, cursor = [], 0
    for text, _ in pieces:
        bounds.append((cursor, cursor + len(text)))
        cursor += len(text)
    writable = [flag for _, flag in pieces]
    targets = [_target(bounds, writable, r) for r in found]

    def replaced(position: int) -> bool:
        return any(r.start <= position < r.end for r in found)

    result: list[str | None] = []
    for index, ((text, is_writable), (start, _)) in enumerate(zip(pieces, bounds, strict=True)):
        if not is_writable:
            inside = any(replaced(start + offset) for offset in range(len(text)))
            result.append(None if inside else text)
            continue
        # A placeholder goes before the first character at or after its region's start,
        # or at the end of the piece when the region starts after it.
        pending = [r for r, target in zip(found, targets, strict=True) if target == index]
        out: list[str] = []
        for offset, char in enumerate(text):
            position = start + offset
            while pending and pending[0].start <= position:
                out.append(pending.pop(0).placeholder)
            if not replaced(position):
                out.append(char)
        out.extend(r.placeholder for r in pending)
        result.append("".join(out))
    return result
