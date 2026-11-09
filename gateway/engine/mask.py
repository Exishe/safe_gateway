"""Replace detected values with placeholders (ADR 0001, ADR 0002)."""

import threading
from collections.abc import Iterable, Sequence
from collections.abc import Mapping as MappingABC
from dataclasses import dataclass

from gateway.engine.detect import EngineConfig, detect
from gateway.engine.placeholders import CANONICAL, MAX_COUNTER, TOKEN, canonical
from gateway.engine.types import EntityType, Span


class Mapping:
    """Placeholder -> original value. Never holds masked text (ADR 0001 point 5).

    Keys are exact surface strings: one surface always gets one placeholder, and
    two spellings of a value get two (ADR 0001 point 3). Safe to share between
    threads: a session's mapping is extended by concurrent requests (ADR 0002 point 2).
    """

    def __init__(self) -> None:
        self._originals: dict[str, str] = {}
        self._placeholders: dict[str, str] = {}  # the same pairs, looked up by surface
        self._counters: dict[EntityType, int] = {}
        # Allocation reads the counter, then writes it: two threads must not interleave.
        self._lock = threading.Lock()

    def placeholder_for(self, surface: str, entity_type: EntityType) -> str:
        """The placeholder of `surface`, assigning the type's next number if it is new."""
        if not surface:
            msg = "cannot mask an empty value"
            raise ValueError(msg)
        with self._lock:
            existing = self._placeholders.get(surface)
            if existing is not None:
                return existing
            number = self._counters.get(entity_type, 0) + 1
            if number > MAX_COUNTER:
                msg = f"placeholder counter for {entity_type} exceeds {MAX_COUNTER}"
                raise ValueError(msg)
            placeholder = canonical(entity_type, number)
            self._counters[entity_type] = number
            self._originals[placeholder] = surface
            self._placeholders[surface] = placeholder
            return placeholder

    def original(self, placeholder: str) -> str | None:
        with self._lock:
            return self._originals.get(placeholder)

    def known_values(self) -> dict[str, EntityType]:
        """A snapshot of surface -> type for every value except placeholder-shaped text."""
        with self._lock:
            pairs = list(self._placeholders.items())
        known: dict[str, EntityType] = {}
        for surface, placeholder in pairs:
            match = CANONICAL.fullmatch(placeholder)
            if match is not None and match.group("type") != EntityType.LITERAL.value:
                known[surface] = EntityType(match.group("type"))
        return known

    def __contains__(self, placeholder: object) -> bool:
        with self._lock:
            return placeholder in self._originals

    def __len__(self) -> int:
        with self._lock:
            return len(self._originals)

    def to_dict(self) -> dict[str, str]:
        with self._lock:
            return dict(self._originals)

    def __reduce__(self) -> tuple[object, tuple[dict[str, str]]]:
        # Pickle and copy go through from_dict: a lock cannot be pickled, each copy gets
        # its own, and what is loaded back is validated.
        return (Mapping.from_dict, (self.to_dict(),))

    @classmethod
    def from_dict(cls, data: object) -> "Mapping":
        """Rebuild a mapping that `to_dict` wrote; refuse anything mask would not write.

        Mask numbers each type 1, 2, 3... with no gap, and a `[LITERAL_n]` only ever
        holds a placeholder-shaped token. Error messages name the placeholder only:
        the original is personal data.
        """
        if not isinstance(data, MappingABC):
            msg = f"invalid map: expected an object, got {type(data).__name__}"
            raise ValueError(msg)  # noqa: TRY004  (a bad map is a bad value, as below)
        mapping = cls()
        numbers: dict[EntityType, set[int]] = {}
        for placeholder, original in data.items():
            match = CANONICAL.fullmatch(placeholder) if isinstance(placeholder, str) else None
            if match is None or not isinstance(original, str) or not original:
                msg = f"invalid map entry for {placeholder!r}"
                raise ValueError(msg)
            if original in mapping._placeholders:
                msg = f"invalid map: {placeholder!r} repeats another entry's value"
                raise ValueError(msg)
            entity_type = EntityType(match.group("type"))
            if entity_type is EntityType.LITERAL and TOKEN.fullmatch(original) is None:
                msg = f"invalid map: {placeholder!r} holds text that is not placeholder-shaped"
                raise ValueError(msg)
            numbers.setdefault(entity_type, set()).add(int(match.group("number")))
            mapping._originals[placeholder] = original
            mapping._placeholders[original] = placeholder
        for entity_type, used in numbers.items():
            if used != set(range(1, len(used) + 1)):
                msg = f"invalid map: {entity_type} placeholders are not numbered 1..{len(used)}"
                raise ValueError(msg)
            mapping._counters[entity_type] = len(used)
        return mapping

    def clear(self) -> None:
        with self._lock:
            self._originals.clear()
            self._placeholders.clear()
            self._counters.clear()


@dataclass
class _Region:
    start: int
    end: int
    entity_type: EntityType


def _span_regions(text: str, spans: Iterable[Span]) -> list[_Region]:
    """The spans, overlapping ones united under the first one's type (fail-safe)."""
    regions: list[_Region] = []
    for span in sorted(spans, key=lambda s: (s.start, -s.end)):
        if span.end > len(text):
            msg = f"span {span.start}..{span.end} outside a text of {len(text)} characters"
            raise ValueError(msg)
        if regions and span.start < regions[-1].end:  # touching spans stay apart
            regions[-1].end = max(regions[-1].end, span.end)
        else:
            regions.append(_Region(span.start, span.end, span.entity_type))
    return regions


def _literal_regions(text: str, start: int, end: int) -> list[_Region]:
    """Placeholder-shaped tokens of text[start:end], an unmasked stretch (ADR 0001 point 4).

    Searched as the stretch will read in the masked text: a placeholder's bracket on
    each side that borders a masked region. So a label such as `SSN ` before a masked
    number stays, and nothing that would read as a token after masking slips through.
    """
    left = "]" if start > 0 else ""
    probe = left + text[start:end] + ("[" if end < len(text) else "")
    offset = start - len(left)
    return [
        _Region(m.start() + offset, m.end() + offset, EntityType.LITERAL)
        for m in TOKEN.finditer(probe, len(left))
    ]


def _regions(text: str, spans: Iterable[Span]) -> list[_Region]:
    regions: list[_Region] = []
    cursor = 0
    for region in _span_regions(text, spans):
        regions.extend(_literal_regions(text, cursor, region.start))
        regions.append(region)
        cursor = region.end
    regions.extend(_literal_regions(text, cursor, len(text)))
    return regions


def mask(text: str, spans: Iterable[Span], mapping: Mapping | None = None) -> tuple[str, Mapping]:
    """`text` with each span, and each placeholder-shaped token, replaced by a placeholder.

    A given mapping is extended in place and returned, so several texts can share one.
    """
    mapping = Mapping() if mapping is None else mapping
    pieces: list[str] = []
    cursor = 0
    for region in _regions(text, spans):
        pieces.append(text[cursor : region.start])
        pieces.append(mapping.placeholder_for(text[region.start : region.end], region.entity_type))
        cursor = region.end
    pieces.append(text[cursor:])
    return "".join(pieces), mapping


def mask_messages(
    texts: Sequence[str], lang: str = "auto", config: EngineConfig | None = None
) -> tuple[list[str], Mapping]:
    """Detect and mask each text in order with one mapping (ADR 0002 point 1).

    Numbering follows first appearance across the messages, so resending a
    conversation gives each value the same placeholder on every turn.
    """
    mapping = Mapping()
    masked = [mask(text, detect(text, lang=lang, config=config), mapping)[0] for text in texts]
    return masked, mapping
