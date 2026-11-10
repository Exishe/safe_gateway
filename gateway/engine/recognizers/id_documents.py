"""Passports, ID cards and driving licences, by shape or by a keyword before them (ADR 0023).

None of these formats carries a checksum we can validate, so a shape alone is
used only where it is distinctive (two letters and seven digits; two letters,
five digits, two letters). Any other letter-digit code is reported only when a
document keyword stands in the 40 characters before it (80 after a label's colon),
because invoice, ticket and part numbers look the same. A Russian series and number ("серия 4599 №
012345", or the number first) is one value, gated by the word for "series" itself.
"""

import re
from bisect import bisect_right
from dataclasses import dataclass

from gateway.engine.types import EntityType, Span

KEYWORD_DISTANCE = 40
# A code right after a label's colon may stand further from the keyword: "Passport number
# (as printed on the data page): X..." puts it 47 characters on (ADR 0023 amended, 1d).
LABEL_DISTANCE = 80
_AFTER_LABEL = re.compile(r":[ \t]*\Z")
CONTEXT_SCORE = 0.75
_MIN_LENGTH, _MAX_LENGTH = 8, 20
_MIN_DIGITS_ONLY, _MAX_DIGITS_ONLY = 9, 14
_MIN_EACH = 2  # letters and digits in a mixed code
_MIN_DIGITS_ONE_LETTER = 7  # "N1234567": Indian and Australian passports, Californian licences
# A code has at most this many space- or hyphen-joined parts within 20 characters.
_MAX_PARTS = 10


# Cyrillic and Greek letters that look like Latin ones, folded to them one for one for
# shape matching only: a Cyrillic capital A typed for a Latin one (ADR 0023 amended, 1d).
# Code points, so the source holds no look-alike.
_LOOK_ALIKE_CODES = {
    # Cyrillic capitals A, VE, IE, KA, EM, EN, O, ER, ES, TE, U, HA, I, JE, DZE.
    "ABEKMHOPCTYXIJS": "0410 0412 0415 041A 041C 041D 041E 0420 0421 0422 0423 0425 0406 0408 0405",
    # Cyrillic small a, ie, o, er, es, u, ha, i, je, dze.
    "aeopcyxijs": "0430 0435 043E 0440 0441 0443 0445 0456 0458 0455",
    # Greek capitals alpha, beta, epsilon, zeta, eta, iota, kappa, mu, nu, omicron, rho,
    # tau, upsilon, chi; small omicron.
    "ABEZHIKMNOPTYXo": "0391 0392 0395 0396 0397 0399 039A 039C 039D 039F 03A1 03A4 03A5 03A7 03BF",
}
_LOOK_ALIKES = {
    int(code, 16): latin
    for latins, codes in _LOOK_ALIKE_CODES.items()
    for latin, code in zip(latins, codes.split(), strict=True)
}


def latin_look_alikes(text: str) -> str:
    """`text` with Cyrillic and Greek look-alikes of Latin letters folded, 1:1."""
    return text.translate(_LOOK_ALIKES)


@dataclass
class ShapeRecognizer:
    """One exact, word-bounded shape reported as one type, look-alike letters folded."""

    name: str
    pattern: re.Pattern[str]
    entity_type: EntityType
    score: float

    def find(self, text: str) -> list[Span]:
        return [
            Span(m.start(), m.end(), self.entity_type, self.score, self.name)
            for m in self.pattern.finditer(latin_look_alikes(text))
        ]


# Not inside a word or after a hyphen; an underscore is a boundary, as in a file name
# ("scan_passport_AB1234567.pdf").
_BEFORE = r"(?<![^\W_])(?<!-)"
_AFTER = r"(?![^\W_])(?!-)"
# Upper or lower case (a number typed in a chat), not mixed: "Xy1234567" reads as a word.
PASSPORT_SHAPE = ShapeRecognizer(
    "passport_shape",
    re.compile(rf"{_BEFORE}(?:[A-Z]{{2}}|[a-z]{{2}})\d{{7}}{_AFTER}"),
    EntityType.PASSPORT,
    0.7,
)
ID_CARD_SHAPE = ShapeRecognizer(
    "id_card_shape",
    re.compile(rf"{_BEFORE}[A-Z]{{2}}\d{{5}}[A-Z]{{2}}{_AFTER}"),
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
# A part: a word with a digit in any case ("xy1234567"), or a word in capitals ("KELDA"),
# in Latin or Cyrillic letters (a Russian driving licence: two digits, two Cyrillic
# letters, six digits). Parts are
# joined by single spaces or hyphens, whole words only; an underscore is a boundary. A
# lower-case word without a digit ("number", "is") is never a part, so it is never masked.
_LOWER, _UPPER = "a-z\u0430-\u044f\u0451", "A-Z\u0410-\u042f\u0401"
_CODE_PART = rf"(?:[{_LOWER}{_UPPER}]*\d[{_LOWER}{_UPPER}0-9]*|[{_UPPER}]+)(?![^\W_])"
_RUN = re.compile(rf"(?<![^\W_]){_CODE_PART}(?:[ \-]{_CODE_PART})*")
_PART = re.compile(rf"[{_LOWER}{_UPPER}0-9]+")
# The words for series (any case: nominative, genitive, instrumental, accusative,
# abbreviated) and for number. Spelled as code points: the first three letters of the
# word for series have Latin look-alikes.
_SERIES_WORD = r"(?<!\w)\u0441\u0435\u0440(?:ия|ии|ией|ию|ий|\.)"
_NUMBER_WORD = r"(?:и\s+)?(?:№|N\.?|No\.?|Nr\.?|#|номер|ном\.)"
_SERIES = r"\d{2} ?\d{2}"
# A Russian series and number: "серия 4599 № 012345", the same in capitals,
# "серия 4599 и номер 012345".
_RU_SERIES_NUMBER = re.compile(
    rf"{_SERIES_WORD}\s*:?\s*"
    rf"({_SERIES}\s*[,;]?\s*(?:{_NUMBER_WORD}\s*:?\s*)?\d{{6}})(?!\d)",
    re.IGNORECASE,
)
# The number first: "номер 012345 серия 4599". Both words are the gate.
_RU_NUMBER_SERIES = re.compile(
    rf"(?<!\w){_NUMBER_WORD}\s*:?\s*(\d{{6}}\s*[,;]?\s*(?:и\s+)?{_SERIES_WORD}"
    rf"\s*:?\s*{_SERIES})(?!\d)",
    re.IGNORECASE,
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

    def kind_before(self, position: int, distance: int = KEYWORD_DISTANCE) -> EntityType | None:
        """The nearest specific keyword's type within reach, else NATIONAL_ID for a
        generic one ("ID", "identity"), else None."""
        index = bisect_right(self.ends, position) - 1
        generic = False
        while index >= 0 and self.starts[index] >= position - distance:
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
        for pattern in (_RU_SERIES_NUMBER, _RU_NUMBER_SERIES):
            for match in pattern.finditer(text):
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
            if kind is None and _AFTER_LABEL.search(text, max(0, start - 3), start):
                kind = keywords.kind_before(start, LABEL_DISTANCE)
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
