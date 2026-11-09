"""The customer's list of names and codenames that must always be masked (ADR 0011, point 8)."""

import csv
import io
import re
from collections.abc import Mapping
from pathlib import Path

from gateway.engine.types import EntityType, Span

SCORE = 1.0


def _key(text: str) -> str:
    return " ".join(text.casefold().split())


def _pattern(entries: Mapping[str, EntityType]) -> re.Pattern[str] | None:
    if not entries:
        return None
    # Longest first, so "Acme Holdings" wins over "Acme" at the same position.
    alternatives = sorted(entries, key=len, reverse=True)
    escaped = (r"\s+".join(re.escape(word) for word in entry.split()) for entry in alternatives)
    # (?<!\w)/(?!\w) rather than \b: entries may end in punctuation ("Co.").
    return re.compile(r"(?<!\w)(?:" + "|".join(escaped) + r")(?!\w)", re.IGNORECASE)


class Gazetteer:
    name = "gazetteer"

    def __init__(self, entries: Mapping[str, EntityType]) -> None:
        self.entries = dict(entries)
        self._types = {_key(value): kind for value, kind in self.entries.items()}
        self._pattern = _pattern(self.entries)

    def find(self, text: str) -> list[Span]:
        if self._pattern is None:
            return []
        return [
            Span(m.start(), m.end(), self._types[_key(m.group())], SCORE, self.name)
            for m in self._pattern.finditer(text)
        ]


def load_gazetteer(path: Path) -> Gazetteer:
    """Read a CSV with a `value` column and an optional `entity_type` column."""
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        msg = f"{path} is not UTF-8; save it as 'CSV UTF-8' and try again"
        raise ValueError(msg) from error

    entries: dict[str, EntityType] = {}
    reader = csv.DictReader(io.StringIO(content))
    for row in reader:
        value = (row.get("value") or "").strip()
        if not value:
            continue
        raw_type = (row.get("entity_type") or "").strip()
        try:
            entries[value] = EntityType(raw_type.upper()) if raw_type else EntityType.CODENAME
        except ValueError as error:
            msg = f"{path}, line {reader.line_num}: unknown entity type {raw_type!r}"
            raise ValueError(msg) from error
    return Gazetteer(entries)
