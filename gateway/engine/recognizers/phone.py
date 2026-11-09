"""Phone numbers via `phonenumbers` (Apache-2.0), one pass per region (ADR 0011, point 5).

National formats such as "8 (912) 345-67-89" or "010 123456" only parse with
their region, so each configured region gets its own pass; the merge step
keeps the longest of overlapping matches.
"""

import re
from collections.abc import Iterator, Sequence

import phonenumbers
from phonenumbers import Leniency, PhoneNumberMatcher

from gateway.engine.types import EntityType, Span

SCORE = 0.95
DEFAULT_REGIONS: tuple[str, ...] = ("US", "GB", "DE", "FR", "IT", "ES", "NL", "RU", "AM")
# The matcher gives up after this many invalid candidates; a long numeric
# column must not hide a phone below it (ADR 0013).
_LIBRARY_MAX_TRIES = 65535

# Digits and phone punctuation on one line. The matcher rejects such a run as a
# whole when another number shares it ("+7 912 345-67-89 125009 Moscow").
_CANDIDATE = re.compile(r"\+?\(?\d[\d \-().]{6,}\d")
_TOKEN = re.compile(r"\S+")
_MAX_DROPPED_LEFT = 2
_MAX_DROPPED_RIGHT = 3
# A phone has at most six groups ("+33 6 12 34 56 78"). A run with more tokens
# than that plus the ones we may drop is a table of numbers; it is not searched.
_MAX_TOKENS = 6 + _MAX_DROPPED_LEFT + _MAX_DROPPED_RIGHT


def _balanced(text: str, start: int, end: int) -> tuple[int, int]:
    """Drop a bracket at either edge that has no partner inside the match.

    phonenumbers keeps "(" in "(+1 201...)" because "(201) 555..." is a real
    format; only a bracket without its partner is surrounding punctuation.
    """
    value = text[start:end]
    while value.startswith("(") and value.count("(") > value.count(")"):
        start, value = start + 1, value[1:]
    while value.endswith(")") and value.count(")") > value.count("("):
        end, value = end - 1, value[:-1]
    while value[:1].isspace():
        start, value = start + 1, value[1:]
    return start, end


def _trimmed_runs(start: int, candidate: str) -> Iterator[tuple[int, int]]:
    """Sub-runs with up to 2 tokens dropped on the left and 3 on the right, longest first."""
    tokens = [(start + m.start(), start + m.end()) for m in _TOKEN.finditer(candidate)]
    if len(tokens) > _MAX_TOKENS:
        return
    for dropped in range(_MAX_DROPPED_LEFT + _MAX_DROPPED_RIGHT + 1):
        for left in range(min(dropped, _MAX_DROPPED_LEFT) + 1):
            right = dropped - left
            if right <= _MAX_DROPPED_RIGHT and left + right < len(tokens):
                yield tokens[left][0], tokens[-1 - right][1]


class PhoneRecognizer:
    name = "phone"

    def __init__(self, regions: Sequence[str] = DEFAULT_REGIONS) -> None:
        self.regions = tuple(regions)

    def _is_phone(self, value: str) -> bool:
        for region in self.regions:
            try:
                number = phonenumbers.parse(value, region)
            except phonenumbers.NumberParseException:
                continue
            if not phonenumbers.is_valid_number(number):
                continue  # the cheap test first; most candidates stop here
            # parse() alone accepts dates and postcodes ("20240115" in DE); the
            # matcher's VALID checks, on this value alone, are the ones the main pass uses.
            matches = PhoneNumberMatcher(value, region, leniency=Leniency.VALID)
            if any(m.start == 0 and m.end == len(value) for m in matches):
                return True
        return False

    def _next_to_other_numbers(self, text: str, covered: set[int]) -> list[tuple[int, int]]:
        found = []
        for candidate in _CANDIDATE.finditer(text):
            start, end = candidate.span()
            if all(i in covered for i in range(start, end) if text[i].isdigit()):
                continue  # the main pass already found every digit here
            for sub_start, sub_end in _trimmed_runs(start, candidate.group()):
                if self._is_phone(text[sub_start:sub_end]):
                    found.append(_balanced(text, sub_start, sub_end))
                    break
        return found

    def find(self, text: str) -> list[Span]:
        found: dict[tuple[int, int], None] = {}  # insertion-ordered set
        tries = max(_LIBRARY_MAX_TRIES, len(text))
        for region in self.regions:
            for match in PhoneNumberMatcher(text, region, leniency=Leniency.VALID, max_tries=tries):
                found.setdefault(_balanced(text, match.start, match.end))
        covered = {i for start, end in found for i in range(start, end)}
        for span in self._next_to_other_numbers(text, covered):
            found.setdefault(span)
        return [Span(start, end, EntityType.PHONE, SCORE, self.name) for start, end in found]
