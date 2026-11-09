"""Phone numbers via `phonenumbers` (Apache-2.0), one pass per region (ADR 0011, point 5).

National formats such as "8 (912) 345-67-89" or "010 123456" only parse with
their region, so each configured region gets its own pass; the merge step
keeps the longest of overlapping matches.
"""

from collections.abc import Sequence

from phonenumbers import Leniency, PhoneNumberMatcher

from gateway.engine.types import EntityType, Span

SCORE = 0.95
DEFAULT_REGIONS: tuple[str, ...] = ("US", "GB", "DE", "FR", "IT", "ES", "NL", "RU", "AM")


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


class PhoneRecognizer:
    name = "phone"

    def __init__(self, regions: Sequence[str] = DEFAULT_REGIONS) -> None:
        self.regions = tuple(regions)

    def find(self, text: str) -> list[Span]:
        seen: set[tuple[int, int]] = set()
        spans = []
        for region in self.regions:
            for match in PhoneNumberMatcher(text, region, leniency=Leniency.VALID):
                start, end = _balanced(text, match.start, match.end)
                if (start, end) not in seen:
                    seen.add((start, end))
                    spans.append(Span(start, end, EntityType.PHONE, SCORE, self.name))
        return spans
