"""Passports, ID cards and driving licences, by shape or by a keyword before them (ADR 0023).

None of these formats carries a checksum we can validate, so a shape alone is
used only where it is distinctive (two letters and seven digits; two letters,
five digits, two letters). Any other letter-digit code is reported only when a
document keyword stands in the 40 characters before it, because invoice, ticket
and part numbers look the same.
"""

import re
from bisect import bisect_right
from dataclasses import dataclass

from gateway.engine.types import EntityType, Span

KEYWORD_DISTANCE = 40
CONTEXT_SCORE = 0.75
_MIN_LENGTH, _MAX_LENGTH = 8, 20
_MIN_DIGITS_ONLY, _MAX_DIGITS_ONLY = 9, 14
_MIN_EACH = 2  # letters and digits in a mixed code
# A code has at most this many space- or hyphen-joined parts within 20 characters.
_MAX_PARTS = 10


@dataclass
class ShapeRecognizer:
    """One exact, word-bounded shape reported as one type."""

    name: str
    pattern: re.Pattern[str]
    entity_type: EntityType
    score: float

    def find(self, text: str) -> list[Span]:
        return [
            Span(m.start(), m.end(), self.entity_type, self.score, self.name)
            for m in self.pattern.finditer(text)
        ]


PASSPORT_SHAPE = ShapeRecognizer(
    "passport_shape",
    re.compile(r"(?<![\w-])[A-Z]{2}\d{7}(?![\w-])"),
    EntityType.PASSPORT,
    0.7,
)
ID_CARD_SHAPE = ShapeRecognizer(
    "id_card_shape",
    re.compile(r"(?<![\w-])[A-Z]{2}\d{5}[A-Z]{2}(?![\w-])"),
    EntityType.NATIONAL_ID,
    0.65,
)


# ------------------------------------------------------------------ keyword-gated

# Specific keywords name the document; the generic ones only say "an identifier".
_KEYWORD = re.compile(
    r"(?P<passport>(?i:passport|паспорт))"
    r"|(?P<licence>(?i:licen[cs]e|driver|водительск)|\bDL\b)"
    r"|(?P<identity>(?i:\bID\b|identity|identification|удостоверени))"
)
_SPECIFIC = {"passport": EntityType.PASSPORT, "licence": EntityType.DRIVER_LICENSE}
# Upper-case letters and digits, joined by single spaces or hyphens, whole words only.
_RUN = re.compile(r"(?<!\w)[A-Z0-9]+(?!\w)(?:[ \-][A-Z0-9]+(?!\w))*")
_PART = re.compile(r"[A-Z0-9]+")


def _is_code(value: str) -> bool:
    """8 to 20 characters: 9 to 14 digits, or at least two letters and two digits."""
    if not _MIN_LENGTH <= len(value) <= _MAX_LENGTH:
        return False
    compact = value.replace(" ", "").replace("-", "")
    digits = sum(c.isdigit() for c in compact)
    letters = len(compact) - digits
    if letters == 0:
        return _MIN_DIGITS_ONLY <= digits <= _MAX_DIGITS_ONLY
    return letters >= _MIN_EACH and digits >= _MIN_EACH


class _Keywords:
    """Keyword matches in one text, for "which document is named just before here?"."""

    def __init__(self, text: str) -> None:
        matches = list(_KEYWORD.finditer(text))
        self.starts = [m.start() for m in matches]
        self.ends = [m.end() for m in matches]
        self.kinds = [_SPECIFIC.get(m.lastgroup or "", EntityType.NATIONAL_ID) for m in matches]
        self.specific = [m.lastgroup in _SPECIFIC for m in matches]

    def __bool__(self) -> bool:
        return bool(self.starts)

    def kind_before(self, position: int) -> EntityType | None:
        """The nearest specific keyword's type within reach, else NATIONAL_ID for a
        generic one ("ID", "identity"), else None."""
        index = bisect_right(self.ends, position) - 1
        generic = False
        while index >= 0 and self.starts[index] >= position - KEYWORD_DISTANCE:
            if self.specific[index]:
                return self.kinds[index]
            generic = True
            index -= 1
        return EntityType.NATIONAL_ID if generic else None


class IdDocumentContextRecognizer:
    name = "id_document_context"

    def find(self, text: str) -> list[Span]:
        keywords = _Keywords(text)
        if not keywords:
            return []
        found = []
        for run in _RUN.finditer(text):
            parts = [
                (run.start() + m.start(), run.start() + m.end())
                for m in _PART.finditer(run.group())
            ]
            found.extend(self._codes(text, parts, keywords))
        return found

    def _codes(self, text: str, parts: list[tuple[int, int]], keywords: _Keywords) -> list[Span]:
        """Left to right, the longest code starting at each part, without overlaps."""
        found = []
        first = 0
        while first < len(parts):
            start = parts[first][0]
            kind = keywords.kind_before(start)
            last = self._longest_code(text, parts, first) if kind is not None else None
            if kind is None or last is None:
                first += 1
                continue
            found.append(Span(start, parts[last][1], kind, CONTEXT_SCORE, self.name))
            first = last + 1
        return found

    @staticmethod
    def _longest_code(text: str, parts: list[tuple[int, int]], first: int) -> int | None:
        start = parts[first][0]
        for last in range(min(len(parts) - 1, first + _MAX_PARTS), first - 1, -1):
            value = text[start : parts[last][1]]
            # The keyword itself ("ID AB12345CD") is not part of the value.
            if _is_code(value) and _KEYWORD.search(value) is None:
                return last
        return None


ID_DOCUMENT_CONTEXT = IdDocumentContextRecognizer()
