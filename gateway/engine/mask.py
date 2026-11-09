"""Replace detected values with placeholders (ADR 0001, ADR 0002)."""

from collections.abc import Iterable, Sequence
from collections.abc import Mapping as MappingABC
from dataclasses import dataclass

from gateway.engine.detect import EngineConfig, detect
from gateway.engine.placeholders import CANONICAL, MAX_COUNTER, TOKEN, canonical
from gateway.engine.types import EntityType, Span


class Mapping:
    """Placeholder -> original value. Never holds masked text (ADR 0001 point 5).

    Keys are exact surface strings: one surface always gets one placeholder, and
    two spellings of a value get two (ADR 0001 point 3).
    """

    def __init__(self) -> None:
        self._originals: dict[str, str] = {}
        self._placeholders: dict[str, str] = {}  # the same pairs, looked up by surface
        self._counters: dict[EntityType, int] = {}

    def placeholder_for(self, surface: str, entity_type: EntityType) -> str:
        """The placeholder of `surface`, assigning the type's next number if it is new."""
        if not surface:
            msg = "cannot mask an empty value"
            raise ValueError(msg)
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
        return self._originals.get(placeholder)

    def __contains__(self, placeholder: object) -> bool:
        return placeholder in self._originals

    def __len__(self) -> int:
        return len(self._originals)

    def to_dict(self) -> dict[str, str]:
        return dict(self._originals)

    @classmethod
    def from_dict(cls, data: MappingABC[str, object]) -> "Mapping":
        """Rebuild a mapping that `to_dict` wrote; refuse anything mask would not write."""
        mapping = cls()
        for placeholder, original in data.items():
            match = CANONICAL.fullmatch(placeholder)
            # Error messages name the placeholder only: the original is personal data.
            if match is None or not isinstance(original, str) or not original:
                msg = f"invalid map entry for {placeholder!r}"
                raise ValueError(msg)
            if original in mapping._placeholders:
                msg = f"invalid map: {placeholder!r} repeats another entry's value"
                raise ValueError(msg)
            entity_type = EntityType(match.group("type"))
            number = int(match.group("number"))
            mapping._originals[placeholder] = original
            mapping._placeholders[original] = placeholder
            mapping._counters[entity_type] = max(mapping._counters.get(entity_type, 0), number)
        return mapping

    def clear(self) -> None:
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
