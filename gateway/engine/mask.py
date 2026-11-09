"""Replace detected values with placeholders (ADR 0001, ADR 0002)."""

from collections.abc import Mapping as MappingABC

from gateway.engine.placeholders import CANONICAL, MAX_COUNTER, canonical
from gateway.engine.types import EntityType


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
