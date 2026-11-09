"""Passports, ID cards and driving licences, by shape or by a keyword before them (ADR 0023).

None of these formats carries a checksum we can validate, so a shape alone is
used only where it is distinctive (two letters and seven digits; two letters,
five digits, two letters). Any other letter-digit code is reported only when a
document keyword stands in the 40 characters before it, because invoice, ticket
and part numbers look the same. A Russian series and number ("серия 4599 №
012345") is one value, gated by the word for "series" itself.
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
_MIN_DIGITS_ONE_LETTER = 7  # "N1234567": Indian and Australian passports, Californian licences
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


# Upper or lower case (a number typed in a chat), not mixed: "Xy1234567" reads as a word.
PASSPORT_SHAPE = ShapeRecognizer(
    "passport_shape",
    re.compile(r"(?<![\w-])(?:[A-Z]{2}|[a-z]{2})\d{7}(?![\w-])"),
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
# Armenian: անձնագ- (passport), վարորդական (driver's), սոցիալական քարտ (social card),
# ՀԾՀ (public service number, on the social card), նույնականացման (identification).
_KEYWORD = re.compile(
    r"(?P<passport>(?i:passport|паспорт|անձնագ))"
    r"|(?P<licence>(?i:licen[cs]e|driver|водительск|վարորդական)|\bDL\b)"
    r"|(?P<identity>(?i:\bID\b|identity|identification|удостоверени"
    r"|սոցիալական\s+քարտ|(?<!\w)հծհ(?!\w)|նույնականացման))"
)
_SPECIFIC = {"passport": EntityType.PASSPORT, "licence": EntityType.DRIVER_LICENSE}
# A part: a word with a digit in any case ("xy1234567"), or a word in capitals ("KELDA").
# Parts are joined by single spaces or hyphens, whole words only. A lower-case word
# without a digit ("number", "is") is never a part, so it is never masked as a code.
_CODE_PART = r"(?:[A-Za-z]*\d[A-Za-z0-9]*|[A-Z]+)(?!\w)"
_RUN = re.compile(rf"(?<!\w){_CODE_PART}(?:[ \-]{_CODE_PART})*")
_PART = re.compile(r"[A-Za-z0-9]+")
# A Russian series and number: "серия 4599 № 012345", "серии 45 99 номер 012345".
_RU_SERIES_NUMBER = re.compile(
    # The word for series, spelled as code points: its first three letters have Latin lookalikes.
    r"(?i:(?<!\w)\u0441\u0435\u0440(?:ия|ии|\.))\s*:?\s*"
    r"(\d{2} ?\d{2}\s*[,;]?\s*(?:(?:№|N|No\.?|номер|ном\.)\s*:?\s*)?\d{6})(?!\d)",
)


def _is_code(value: str) -> bool:
    """8 to 20 characters: 9 to 14 digits, one letter and at least seven digits, or at
    least two letters and two digits."""
    if not _MIN_LENGTH <= len(value) <= _MAX_LENGTH:
        return False
    compact = value.replace(" ", "").replace("-", "")
    digits = sum(c.isdigit() for c in compact)
    letters = len(compact) - digits
    if letters == 0:
        return _MIN_DIGITS_ONLY <= digits <= _MAX_DIGITS_ONLY
    if letters == 1:
        return digits >= _MIN_DIGITS_ONE_LETTER
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
        found = self._series_and_numbers(text, keywords)
        if not keywords:
            return found
        for run in _RUN.finditer(text):
            parts = [
                (run.start() + m.start(), run.start() + m.end())
                for m in _PART.finditer(run.group())
            ]
            found.extend(self._codes(text, parts, keywords))
        # A series and number in a code's shape ("4599 N012345") is found by both rules.
        return list(dict.fromkeys(found))

    def _series_and_numbers(self, text: str, keywords: _Keywords) -> list[Span]:
        """Russian series and number; a passport keyword nearby makes it a PASSPORT."""
        found = []
        for match in _RU_SERIES_NUMBER.finditer(text):
            start, end = match.span(1)
            kind = keywords.kind_before(match.start()) or EntityType.NATIONAL_ID
            found.append(Span(start, end, kind, CONTEXT_SCORE, self.name))
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
