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

# Not inside a longer number: no digit, and no separator after a digit, on either side.
# "192.168.10.12" and "1.2.3.4.5" hold no date; "12.03.1985." does.
_BEFORE = r"(?<!\d)(?<!\d[.,/:-])"
_AFTER = r"(?!\d)(?![.,/:-]\d)"
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
_EN_MONTH = (
    r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
)
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
_RU_MONTH = (
    r"(январ[яь]|янв|феврал[яь]|февр?|марта?|мар|апрел[яь]|апр|мая|май|июн[яь]|июн"
    r"|июл[яь]|июл|августа?|авг|сентябр[яь]|сент?|октябр[яь]|окт|ноябр[яь]|нояб?|декабр[яь]|дек)\.?"
)
# 12 марта 1985, 12 мар. 1985. The word for "year" after it (in full or cut short) is left out.
_RU_DAY_FIRST = re.compile(
    rf"{_BEFORE}(?<![^\W\d_])(\d{{1,2}})\s+{_RU_MONTH}\s+({_YEAR}){_AFTER}",
    re.IGNORECASE,
)


MAX_DAY, MAX_MONTH = 31, 12


def _valid(day: str, month: int) -> bool:
    return 1 <= int(day) <= MAX_DAY and 1 <= month <= MAX_MONTH


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


def _named(text: str) -> Iterator[tuple[int, int]]:
    for match in _EN_DAY_FIRST.finditer(text):
        if _valid(match.group(1), _EN_MONTHS[match.group(2)[:3].lower()]):
            yield match.span()
    for match in _EN_MONTH_FIRST.finditer(text):
        if _valid(match.group(2), _EN_MONTHS[match.group(1)[:3].lower()]):
            yield match.span()
    for match in _RU_DAY_FIRST.finditer(text):
        month = _RU_MONTHS["мая" if match.group(2).lower() == "май" else match.group(2)[:3].lower()]
        if _valid(match.group(1), month):
            yield match.span()


class DateRecognizer:
    name = "date"

    def find(self, text: str) -> list[Span]:
        found = {*_numeric(text), *_named(text)}
        return [Span(s, e, EntityType.DATE, SCORE, self.name) for s, e in sorted(found)]


DATE = DateRecognizer()
