"""Dates in numbers or with an English, German or Russian month name, in any language (ADR 0016).

The Russian NER model has no DATE label, and a date alone in a paragraph reaches
no model at all, so dates get a structured recognizer of their own. NER keeps
finding the forms it knows ('last March'); the merge resolves the overlap.

A date of birth or of an admission identifies a person (HIPAA Safe Harbor
lists all dates more specific than a year). The shapes are checked for a real
month and day; version numbers, IP addresses and phone numbers are refused by
their boundaries and by those checks.
"""

import re
from collections.abc import Callable, Iterator

from gateway.engine.types import EntityType, Span

SCORE = 0.8

# Not inside a longer number: no digit, and no dot or colon after a digit, on either side.
# "192.168.10.12" and "1.2.3.4.5" hold no date; "12.03.1985." does.
_BEFORE = r"(?<!\d)(?<!\d[.:])"
_AFTER = r"(?!\d)(?![.:]\d)"
# Not glued to a letter before it ("v10.12.15" is a version).
_NO_LETTER_BEFORE = r"(?<![^\W\d_])"
# A hyphen, comma, slash or en dash between a digit and a date is allowed only where both
# sides are dates: a range or a list written without spaces (a date, "-", a date).
# Otherwise the date is part of a longer number ("12-03-1985-1234") and is refused.
_JOINERS = "-,/\u2013"
_YEAR = r"[12]\d{3}"
_DAY = r"(\d{1,2})"

# ------------------------------------------------------------------ numbers only

# dd.mm.yyyy, dd/mm/yyyy, mm/dd/yyyy, dd-mm-yyyy: one separator, used twice.
_DAY_MONTH_YEAR = re.compile(rf"{_BEFORE}{_DAY}([./-]){_DAY}\2({_YEAR}){_AFTER}")
# dd.mm.yy, dd/mm/yy, mm/dd/yy, dd-mm-yy: two digits each, or "3.11.12" (a version
# number) would be a date.
_DAY_MONTH_SHORT_YEAR = re.compile(
    rf"{_BEFORE}{_NO_LETTER_BEFORE}(\d{{2}})([./-])(\d{{2}})\2(\d{{2}}){_AFTER}"
)
# yyyy-mm-dd (ISO 8601), yyyy/mm/dd and yyyy.mm.dd.
_YEAR_MONTH_DAY = re.compile(rf"{_BEFORE}({_YEAR})([./-]){_DAY}\2{_DAY}{_AFTER}")
# Day and month twice before one year: 12.03-15.03.1985 (a stay, a sick leave).
_NUMERIC_RANGE = re.compile(rf"{_BEFORE}{_DAY}([./]){_DAY} ?- ?{_DAY}\2{_DAY}\2({_YEAR}){_AFTER}")
# Two days before one month and year: 12-15.03.1985.
_NUMERIC_DAY_RANGE = re.compile(rf"{_BEFORE}{_DAY} ?- ?{_DAY}\.{_DAY}\.({_YEAR}){_AFTER}")
# Day, month and year split by single spaces: 12 03 1985. Two digits for the day and the
# month, and no digit group joined by a space on either side, so the groups of a phone
# number ("+7 916 12 03 1985") are not read.
_SPACED = re.compile(rf"(?<!\d )(?<![\d.:+])(\d{{2}}) (\d{{2}}) ({_YEAR})(?! \d){_AFTER}")

# ------------------------------------------------------------------ month names

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
    # German, where the first three letters differ from English: März (Maerz, Mrz.),
    # Mai, Oktober, Dezember, Jänner (Austrian January).
    "mär": 3,
    "mae": 3,
    "mrz": 3,
    "mai": 5,
    "okt": 10,
    "dez": 12,
    "jän": 1,
}
_EN_MONTH_NAMES = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
    # German: Januar, Jänner, Februar, März, Maerz, Mrz., Mai, Juni, Juli, Oktober, Dezember.
    r"|januar|jän(?:ner)?|februar|märz?|maerz|mrz|mai|juni|juli"
    r"|okt(?:ober)?|dez(?:ember)?"
)
_EN_MONTH = rf"({_EN_MONTH_NAMES})\.?"
_ORDINAL = r"(?:st|nd|rd|th)?"
# A day before a month name: a space after it (an ordinal allowed), or a dot (German).
_DAY_END = rf"(?:{_ORDINAL}\s+|\.\s*)"
# A four-digit year, or two digits after an apostrophe: 12 Mar '85.
_EN_YEAR = rf"({_YEAR}|'\d{{2}})"
# 12 March 1985, 12th of March, 1985, 12 Mar. 1985, 12. März 1985, 12 Mar '85
_EN_DAY_FIRST = re.compile(
    rf"{_BEFORE}\b{_DAY}{_DAY_END}(?:of\s+)?{_EN_MONTH},?\s+{_EN_YEAR}{_AFTER}",
    re.IGNORECASE,
)
# March 12, 1985, Mar 12 1985, March 12th, 1985, Mar 12 '85
_EN_MONTH_FIRST = re.compile(
    rf"\b{_EN_MONTH}\s+{_DAY}{_ORDINAL},?\s+{_EN_YEAR}{_AFTER}",
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
# The Russian contract form puts the day in guillemets or quotes, spaced or not:
# «12» марта 1985, « 12 » марта 1985.
_OPEN_QUOTE = "(?:[\"'«“„]\\s*)?"
_CLOSE_QUOTE = "(?:\\s*[\"'»“”])?"
# A four-digit year; two digits only before the word for "year" (its first letter and a
# dot, or the word in full), so a count after a date ("12 марта 85 человек") is not
# read as its year.
_RU_YEAR = rf"({_YEAR}|'\d{{2}}|\d{{2}}(?=\s*\u0433(?:\.|ода|\b)))"
# 12 марта 1985, 12 мар. 1985, and a two-digit year before the word for "year", which
# is left out.
_RU_DAY_FIRST = re.compile(
    rf"{_BEFORE}{_NO_LETTER_BEFORE}{_OPEN_QUOTE}{_DAY}{_CLOSE_QUOTE}\s+{_RU_MONTH}\s+{_RU_YEAR}{_AFTER}",
    re.IGNORECASE,
)
# A month name of any of the three languages between hyphens, slashes or dots, not
# necessarily the same two, as lab, airline and hospital systems write it, in any
# language around it: 12-MAR-1985, 12/Mar/1985, 12-Mar/1985, 12-мар-85, and the year
# first: 1985-Mar-12. The month name gates a two-digit year.
_MONTH_NAMES = f"({_EN_MONTH_NAMES}|{_RU_MONTH_NAMES})"
_ANY_YEAR = rf"({_YEAR}|\d{{2}})"
_DAY_NAMED_MONTH_YEAR = re.compile(
    rf"{_BEFORE}{_NO_LETTER_BEFORE}{_DAY}[-/.]{_MONTH_NAMES}[-/.]{_ANY_YEAR}{_AFTER}",
    re.IGNORECASE,
)
_YEAR_NAMED_MONTH_DAY = re.compile(
    rf"{_BEFORE}{_NO_LETTER_BEFORE}({_YEAR})[-/.]{_MONTH_NAMES}[-/.]{_DAY}{_AFTER}",
    re.IGNORECASE,
)
# A month name glued to its numbers: 12MAR1985, 12mar85.
_GLUED = re.compile(
    rf"{_BEFORE}{_NO_LETTER_BEFORE}{_DAY}{_MONTH_NAMES}{_ANY_YEAR}{_AFTER}(?![^\W\d_])",
    re.IGNORECASE,
)
# Two days before one month name and year: 12-15 марта 1985, "from 12 to 15 <month> 1985",
# 12 to 15 March 1985.
_RANGE_JOINER = r"(?:\s*-\s*|\s+(?:по|до|и|to|and|&)\s+)"
_NAMED_DAY_RANGE = re.compile(
    rf"{_BEFORE}{_NO_LETTER_BEFORE}{_DAY}{_ORDINAL}{_RANGE_JOINER}{_DAY}{_DAY_END}"
    rf"(?:of\s+)?{_MONTH_NAMES}\.?,?\s+({_YEAR}|'\d{{2}}){_AFTER}",
    re.IGNORECASE,
)


MAX_DAY, MAX_MONTH = 31, 12


def _valid(day: str, month: int) -> bool:
    return 1 <= int(day) <= MAX_DAY and 1 <= month <= MAX_MONTH


def _month(name: str) -> int:
    """The number of an English, German or Russian month name or abbreviation."""
    key = name[:3].lower()
    if key == "май":
        return 5
    return _EN_MONTHS.get(key) or _RU_MONTHS[key]


def _day_month(first: str, second: str, separator: str) -> bool:
    """Day first (dd.mm) or, with slashes, month first (US mm/dd)."""
    return _valid(first, int(second)) or (separator == "/" and _valid(second, int(first)))


Match = re.Match[str]
# Each pattern and the check its match must pass.
_CHECKS: tuple[tuple[re.Pattern[str], Callable[[Match], bool]], ...] = (
    (_DAY_MONTH_YEAR, lambda m: _day_month(m[1], m[3], m[2])),
    (_DAY_MONTH_SHORT_YEAR, lambda m: _day_month(m[1], m[3], m[2])),
    (_YEAR_MONTH_DAY, lambda m: _valid(m[4], int(m[3]))),
    (_NUMERIC_RANGE, lambda m: _day_month(m[1], m[3], m[2]) and _day_month(m[4], m[5], m[2])),
    (_NUMERIC_DAY_RANGE, lambda m: _valid(m[1], int(m[3])) and _valid(m[2], int(m[3]))),
    (_SPACED, lambda m: _valid(m[1], int(m[2]))),
    (_EN_DAY_FIRST, lambda m: _valid(m[1], _month(m[2]))),
    (_EN_MONTH_FIRST, lambda m: _valid(m[2], _month(m[1]))),
    (_RU_DAY_FIRST, lambda m: _valid(m[1], _month(m[2]))),
    (_DAY_NAMED_MONTH_YEAR, lambda m: _valid(m[1], _month(m[2]))),
    (_YEAR_NAMED_MONTH_DAY, lambda m: _valid(m[3], _month(m[2]))),
    (_GLUED, lambda m: _valid(m[1], _month(m[2]))),
    (_NAMED_DAY_RANGE, lambda m: _valid(m[1], _month(m[3])) and _valid(m[2], _month(m[3]))),
)


# Every date holds a digit, so the patterns read only the text around digit runs: from 32
# characters before a run ("September " and an ordinal, a quote) to 8 after it (the
# lookaheads, the word for "year" after a year). Windows that touch are joined, so a date
# whose numbers are apart ("12th of September, 1985") is read in one window. Lookbehinds
# and \b see the text before a window; a window ends far enough after its last digit for
# the lookaheads. Text with few digits (most prose) is then read far faster.
_DIGIT_RUN = re.compile(r"\d+")
_REACH_BEFORE, _REACH_AFTER = 32, 8


def _windows(text: str) -> list[tuple[int, int]]:
    windows: list[tuple[int, int]] = []
    for run in _DIGIT_RUN.finditer(text):
        start, end = max(0, run.start() - _REACH_BEFORE), run.end() + _REACH_AFTER
        if windows and start <= windows[-1][1]:
            windows[-1] = (windows[-1][0], end)
        else:
            windows.append((start, end))
    return windows


def _candidates(text: str) -> Iterator[tuple[int, int]]:
    for start, end in _windows(text):
        for pattern, check in _CHECKS:
            for match in pattern.finditer(text, start, end):
                if check(match):
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
        found = _outermost(_not_in_a_longer_number(text, set(_candidates(text))))
        return [Span(s, e, EntityType.DATE, SCORE, self.name) for s, e in found]


def _outermost(found: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Sorted spans without those inside another: "15 March 1985" in a range of days."""
    kept: list[tuple[int, int]] = []
    for start, end in sorted(found, key=lambda span: (span[0], -span[1])):
        if kept and end <= kept[-1][1]:
            continue
        kept.append((start, end))
    return kept


DATE = DateRecognizer()
