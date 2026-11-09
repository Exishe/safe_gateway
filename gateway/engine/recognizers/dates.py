"""Dates written in numbers or with an English or Russian month name, in any language (ADR 0016).

The Russian NER model has no DATE label, and a date alone in a paragraph reaches
no model at all, so dates get a structured recognizer of their own. NER keeps
finding the forms it knows ('last March'); the merge resolves the overlap.

A date of birth or of an admission identifies a person (HIPAA Safe Harbor
lists all dates more specific than a year). The shapes are checked for a real
month and day; version numbers, IP addresses and phone numbers are refused by
their boundaries and by those checks.
"""

import re
from collections.abc import Iterator

from gateway.engine.types import EntityType, Span

SCORE = 0.8

# Not inside a longer number: no digit, and no dot or colon after a digit, on either side.
# "192.168.10.12" and "1.2.3.4.5" hold no date; "12.03.1985." does.
_BEFORE = r"(?<!\d)(?<!\d[.:])"
_AFTER = r"(?!\d)(?![.:]\d)"
# A hyphen, comma, slash or en dash between a digit and a date is allowed only where both
# sides are dates: a range or a list written without spaces (a date, "-", a date).
# Otherwise the date is part of a longer number ("12-03-1985-1234") and is refused.
_JOINERS = "-,/\u2013"
_YEAR = r"[12]\d{3}"

# dd.mm.yyyy, dd/mm/yyyy, mm/dd/yyyy, dd-mm-yyyy: one separator, used twice.
_DAY_MONTH_YEAR = re.compile(rf"{_BEFORE}(\d{{1,2}})([./-])(\d{{1,2}})\2({_YEAR}){_AFTER}")
# dd.mm.yy: two digits each, or "3.11.12" (a version number) would be a date.
_DAY_MONTH_SHORT_YEAR = re.compile(
    rf"{_BEFORE}(?<![^\W\d_])(\d{{2}})\.(\d{{2}})\.(\d{{2}}){_AFTER}"
)
# yyyy-mm-dd (ISO 8601) and yyyy/mm/dd.
_YEAR_MONTH_DAY = re.compile(rf"{_BEFORE}({_YEAR})([/-])(\d{{1,2}})\2(\d{{1,2}}){_AFTER}")

_EN_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_EN_MONTH_NAMES = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)
_EN_MONTH = rf"({_EN_MONTH_NAMES})\.?"
_ORDINAL = r"(?:st|nd|rd|th)?"
# 12 March 1985, 12th of March, 1985, 12 Mar. 1985
_EN_DAY_FIRST = re.compile(
    rf"{_BEFORE}\b(\d{{1,2}}){_ORDINAL}\s+(?:of\s+)?{_EN_MONTH},?\s+({_YEAR}){_AFTER}",
    re.IGNORECASE,
)
# March 12, 1985, Mar 12 1985, March 12th, 1985
_EN_MONTH_FIRST = re.compile(
    rf"\b{_EN_MONTH}\s+(\d{{1,2}}){_ORDINAL},?\s+({_YEAR}){_AFTER}",
    re.IGNORECASE,
)

# Russian month names in the genitive ("12 марта"), and their abbreviations.
_RU_MONTHS = {
    "янв": 1,
    "фев": 2,
    "мар": 3,
    "апр": 4,
    "мая": 5,
    "июн": 6,
    "июл": 7,
    "авг": 8,
    "сен": 9,
    "окт": 10,
    "ноя": 11,
    "дек": 12,
}
_RU_MONTH_NAMES = (
    r"январ[яь]|янв|феврал[яь]|февр?|марта?|мар|апрел[яь]|апр|мая|май|июн[яь]|июн"
    r"|июл[яь]|июл|августа?|авг|сентябр[яь]|сент?|октябр[яь]|окт|ноябр[яь]|нояб?|декабр[яь]|дек"
)
_RU_MONTH = rf"({_RU_MONTH_NAMES})\.?"
# The Russian contract form puts the day in guillemets or quotes: «12» марта 1985.
_OPEN_QUOTE = "[\"'\u00ab\u201c\u201e]?"
_CLOSE_QUOTE = "[\"'\u00bb\u201c\u201d]?"
# 12 марта 1985, 12 мар. 1985. The word for "year" after it (in full or cut short) is left out.
_RU_DAY_FIRST = re.compile(
    rf"{_BEFORE}(?<![^\W\d_]){_OPEN_QUOTE}(\d{{1,2}}){_CLOSE_QUOTE}\s+{_RU_MONTH}\s+({_YEAR}){_AFTER}",
    re.IGNORECASE,
)
# A month name of either language between two hyphens, slashes or dots, as lab, airline
# and hospital systems write it, in any language around it: 12-MAR-1985, 12/Mar/1985,
# 12-мар-1985, and the year first: 1985-Mar-12.
_MONTH_NAMES = f"({_EN_MONTH_NAMES}|{_RU_MONTH_NAMES})"
_DAY_NAMED_MONTH_YEAR = re.compile(
    rf"{_BEFORE}(?<![^\W\d_])(\d{{1,2}})([-/.]){_MONTH_NAMES}\2({_YEAR}){_AFTER}",
    re.IGNORECASE,
)
_YEAR_NAMED_MONTH_DAY = re.compile(
    rf"{_BEFORE}(?<![^\W\d_])({_YEAR})([-/.]){_MONTH_NAMES}\2(\d{{1,2}}){_AFTER}",
    re.IGNORECASE,
)


MAX_DAY, MAX_MONTH = 31, 12


def _valid(day: str, month: int) -> bool:
    return 1 <= int(day) <= MAX_DAY and 1 <= month <= MAX_MONTH


def _month(name: str) -> int:
    """The number of an English or Russian month name or abbreviation."""
    key = name[:3].lower()
    if key == "май":
        return 5
    return _EN_MONTHS.get(key) or _RU_MONTHS[key]


def _numeric(text: str) -> Iterator[tuple[int, int]]:
    for match in _DAY_MONTH_YEAR.finditer(text):
        first, second = match.group(1), int(match.group(3))
        # Day first (dd.mm) or, with slashes, month first (US mm/dd).
        if _valid(first, second) or (match.group(2) == "/" and _valid(match.group(3), int(first))):
            yield match.span()
    for match in _DAY_MONTH_SHORT_YEAR.finditer(text):
        if _valid(match.group(1), int(match.group(2))):
            yield match.span()
    for match in _YEAR_MONTH_DAY.finditer(text):
        if _valid(match.group(4), int(match.group(3))):
            yield match.span()


# Each pattern with a month name, and the groups that hold its day and its month.
_NAMED = (
    (_EN_DAY_FIRST, 1, 2),
    (_EN_MONTH_FIRST, 2, 1),
    (_RU_DAY_FIRST, 1, 2),
    (_DAY_NAMED_MONTH_YEAR, 1, 3),
    (_YEAR_NAMED_MONTH_DAY, 4, 3),
)


def _named(text: str) -> Iterator[tuple[int, int]]:
    for pattern, day, month in _NAMED:
        for match in pattern.finditer(text):
            if _valid(match.group(day), _month(match.group(month))):
                yield match.span()


def _not_in_a_longer_number(text: str, found: set[tuple[int, int]]) -> list[tuple[int, int]]:
    """Dates whose joined neighbours ("-", ",", "/" or an en dash, then a digit) are dates."""
    starts = {start for start, _ in found}
    ends = {end for _, end in found}
    kept = []
    for start, end in sorted(found):
        joined_before = start >= 2 and text[start - 1] in _JOINERS and text[start - 2].isdigit()  # noqa: PLR2004
        joined_after = end + 1 < len(text) and text[end] in _JOINERS and text[end + 1].isdigit()
        if (joined_before and start - 1 not in ends) or (joined_after and end + 1 not in starts):
            continue
        kept.append((start, end))
    return kept


class DateRecognizer:
    name = "date"

    def find(self, text: str) -> list[Span]:
        found = _not_in_a_longer_number(text, {*_numeric(text), *_named(text)})
        return [Span(s, e, EntityType.DATE, SCORE, self.name) for s, e in found]


DATE = DateRecognizer()
