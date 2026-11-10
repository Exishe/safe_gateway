"""Replace detected values with placeholders (ADR 0001, ADR 0002)."""

import threading
from collections.abc import Iterable, Sequence
from collections.abc import Mapping as MappingABC
from dataclasses import dataclass

from gateway.engine.detect import EngineConfig, detect
from gateway.engine.known import KnownValues
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
        self._known: KnownValues | None = None  # compiled on demand, dropped on change
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
            if entity_type is not EntityType.LITERAL:
                self._known = None
            return placeholder

    def original(self, placeholder: str) -> str | None:
        with self._lock:
            return self._originals.get(placeholder)

    def known_values(self) -> dict[str, EntityType]:
        """A snapshot of surface -> type for every value except placeholder-shaped text.

        Literals are left out: they are not personal data, and the token grammar
        decides where they are (ADR 0014 point 4).
        """
        with self._lock:
            return self._known_values_locked()

    def known(self) -> KnownValues:
        """The known values compiled for searching; rebuilt only after a value is added."""
        with self._lock:
            if self._known is None:
                self._known = KnownValues(self._known_values_locked())
            return self._known

    def _known_values_locked(self) -> dict[str, EntityType]:
        known: dict[str, EntityType] = {}
        for surface, placeholder in self._placeholders.items():
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
            self._known = None


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


def _literal_regions(
    text: str, start: int, end: int, keep: frozenset[str] = frozenset()
) -> list[_Region]:
    """Placeholder-shaped tokens of text[start:end], an unmasked stretch (ADR 0001 point 4).

    Searched as the stretch will read in the masked text: a placeholder's bracket on
    each side that borders a masked region. So a label such as `SSN ` before a masked
    number stays, and nothing that would read as a token after masking slips through.
    A token in `keep` (a placeholder the session issued, ADR 0025) stays as written.
    """
    left = "]" if start > 0 else ""
    probe = left + text[start:end] + ("[" if end < len(text) else "")
    offset = start - len(left)
    return [
        _Region(m.start() + offset, m.end() + offset, EntityType.LITERAL)
        for m in TOKEN.finditer(probe, len(left))
        if m.group() not in keep
    ]


def _regions(text: str, spans: Iterable[Span], keep: frozenset[str]) -> list[_Region]:
    regions: list[_Region] = []
    cursor = 0
    for region in _span_regions(text, spans):
        regions.extend(_literal_regions(text, cursor, region.start, keep))
        regions.append(region)
        cursor = region.end
    regions.extend(_literal_regions(text, cursor, len(text), keep))
    return regions


def _outside_kept(text: str, spans: Iterable[Span], keep: frozenset[str]) -> list[Span]:
    """The spans that do not lie wholly inside a kept placeholder (ADR 0025).

    A kept placeholder is text the gateway wrote, not personal data, so a span inside
    it is a false positive. A span that crosses its edge is kept, and unites with it.
    """
    spans = list(spans)
    if not keep:
        return spans
    kept = [(m.start(), m.end()) for m in TOKEN.finditer(text) if m.group() in keep]
    return [s for s in spans if not any(a <= s.start and s.end <= b for a, b in kept)]


@dataclass(frozen=True)
class Replacement:
    """text[start:end] becomes `placeholder`. Writers use it to place tokens (ADR 0017)."""

    start: int
    end: int
    placeholder: str


def _remember(
    text: str, spans: Iterable[Span], mapping: Mapping, keep: frozenset[str] = frozenset()
) -> list[Span]:
    """Give each span's value its placeholder now, so its repeats are found as known."""
    spans = _outside_kept(text, spans, keep)
    for region in _span_regions(text, spans):
        mapping.placeholder_for(text[region.start : region.end], region.entity_type)
    return spans


def _replacements_remembered(
    text: str, spans: list[Span], mapping: Mapping, keep: frozenset[str] = frozenset()
) -> list[Replacement]:
    # Every exact occurrence of a value the mapping holds is masked too, wherever
    # detection missed it (ADR 0014 amendment 1). Overlaps unite, never drop.
    known = _outside_kept(text, mapping.known().spans(text), keep)
    return [
        Replacement(
            region.start,
            region.end,
            mapping.placeholder_for(text[region.start : region.end], region.entity_type),
        )
        for region in _regions(text, [*spans, *known], keep)
    ]


def replacements(text: str, spans: Iterable[Span], mapping: Mapping) -> list[Replacement]:
    """Where mask() puts each placeholder, in order; `mapping` is extended in place.

    Includes every occurrence of a value the mapping already holds, as mask() does.
    """
    return _replacements_remembered(text, _remember(text, spans, mapping), mapping)


def apply_replacements(text: str, found: Iterable[Replacement]) -> str:
    pieces: list[str] = []
    cursor = 0
    for replacement in found:
        pieces.append(text[cursor : replacement.start])
        pieces.append(replacement.placeholder)
        cursor = replacement.end
    pieces.append(text[cursor:])
    return "".join(pieces)


def mask(text: str, spans: Iterable[Span], mapping: Mapping | None = None) -> tuple[str, Mapping]:
    """`text` with each span, each placeholder-shaped token, and every other occurrence
    of a value the mapping holds (this text's spans included) replaced by a placeholder.

    A given mapping is extended in place and returned, so several texts can share one.
    """
    mapping = Mapping() if mapping is None else mapping
    return apply_replacements(text, replacements(text, spans, mapping)), mapping


def mask_messages(
    texts: Sequence[str], lang: str = "auto", config: EngineConfig | None = None
) -> tuple[list[str], Mapping]:
    """Detect and mask each text in order with one mapping (ADR 0002 point 1).

    Every text is detected before any is masked, so a value detected in a later
    message is also masked where an earlier one holds it undetected. Numbering
    follows first detection across the messages, so resending a conversation gives
    each value the same placeholder on every turn.
    """
    mapping = Mapping()
    spans = [detect(text, lang=lang, config=config) for text in texts]
    return mask_detected_messages(texts, spans, mapping), mapping


def mask_detected_messages(
    texts: Sequence[str],
    spans: Sequence[Iterable[Span]],
    mapping: Mapping,
    keep: frozenset[str] = frozenset(),
) -> list[str]:
    """Mask already-detected texts as `mask_messages` does, extending `mapping` in place.

    `keep` holds placeholders a session issued (ADR 0025): where one appears exactly
    as issued it stays as written instead of becoming `[LITERAL_n]`, and spans wholly
    inside it are ignored. Every other placeholder-shaped token is still a literal.
    """
    detected = [_remember(t, s, mapping, keep) for t, s in zip(texts, spans, strict=True)]
    return [
        apply_replacements(t, _replacements_remembered(t, s, mapping, keep))
        for t, s in zip(texts, detected, strict=True)
    ]
