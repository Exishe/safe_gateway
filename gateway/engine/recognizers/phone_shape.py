"""Phone numbers by shape, for values `phonenumbers` rejects (ADR 0023).

Mistyped and synthetic numbers fail libphonenumber's validation (an unassigned
prefix, a wrong length for the region) but are still somebody's phone in a
document. Two rules catch them without the library:

- `phone_shape`: a leading "+" and 10 to 15 digits (E.164 allows at most 15).
  The "+" is required, so IP addresses, dates and amounts are never read; with
  it, groups may also be split by two spaces, a spaced hyphen or a slash.
- `phone_context`: no "+", so a phone keyword must stand in the 40 characters
  before, and the number needs three digit groups or an opening bracket.
"""

import re
from bisect import bisect_right

from gateway.engine.types import EntityType, Span

SHAPE_SCORE = 0.85
CONTEXT_SCORE = 0.8
MAX_DIGITS = 15  # E.164
KEYWORD_DISTANCE = 40

# Digits joined by one space, dot or hyphen, or around a bracketed group:
# "+99 (912) 345-6789", "(012)-3456789". Every repetition consumes one digit and
# the separators are unambiguous, so the pattern does not backtrack.
_DIGITS = r"\d(?:(?:[ .\-]|[ .\-]?\(|\)[ .\-]?)?\d)*"
# After a "+" the number is a phone whatever its separators, so a gap may also be two
# spaces (justified PDF text), a spaced hyphen or a slash: "+99  912 - 345/6789".
_PLUS_GAP = r"(?: - |  ?|[./\-])"
_PLUS_DIGITS = rf"\d(?:(?:{_PLUS_GAP}|{_PLUS_GAP}?\(|\){_PLUS_GAP}?)?\d)*"
_PLUS = re.compile(rf"(?<![\w+])\+\(?{_PLUS_DIGITS}")
# Not inside a word or a "+" number, and not continuing a number ("1.555...").
_BARE = re.compile(rf"(?<![\w+(])(?<!\d[ .\-/])\(?{_DIGITS}")
_GROUP = re.compile(r"\d+")
_WORD = re.compile(r"\w")
# The word after the digits is an e-mail address: an "@" before the next whitespace.
_ADDRESS_AFTER = re.compile(r"[^\s@]{0,64}@")
# "phone" may end a word (telephone, smartphone); the rest must start one, so
# "cancellation" does not read as "cell" and "hotel" not as "tel".
_KEYWORD = re.compile(
    r"phone|\b(?:tel|mobile|mob|cell|call|fax|contact|whatsapp|телефон|тел|моб|հեռ)",
    re.IGNORECASE,
)


def _within_digit_limit(text: str, start: int, end: int) -> tuple[int, int]:
    """The longest prefix ending at a group with at most 15 digits.

    A number followed by a postcode on the same line ("+99 20 7946 0958 10001")
    joins it into one run; the trailing groups are dropped, not the phone. A last
    group glued to a word keeps its digits ("+99 912 345 6789Fax:", PDF columns run
    together), unless the word is an e-mail address ("01K@example.com"): then the
    group belongs to the address and is dropped.
    """
    groups = [(start + m.start(), start + m.end()) for m in _GROUP.finditer(text[start:end])]
    glued = end < len(text) and _WORD.match(text, end) is not None
    if len(groups) > 1 and glued and _ADDRESS_AFTER.match(text, end):
        groups.pop()
    count, last = 0, 0
    for index, (group_start, group_end) in enumerate(groups):
        count += group_end - group_start
        if count > MAX_DIGITS:
            break
        last = index
    return start, groups[last][1]


def _digit_count(value: str) -> int:
    return sum(c.isdigit() for c in value)


class PhoneShapeRecognizer:
    """`+` and 10 to 15 digits."""

    name = "phone_shape"

    def find(self, text: str) -> list[Span]:
        found = []
        for match in _PLUS.finditer(text):
            start, end = _within_digit_limit(text, *match.span())
            if 10 <= _digit_count(text[start:end]) <= MAX_DIGITS:  # noqa: PLR2004
                found.append(Span(start, end, EntityType.PHONE, SHAPE_SCORE, self.name))
        return found


class PhoneContextRecognizer:
    """A number after a phone keyword: three digit groups or a bracket, 9 to 15 digits."""

    name = "phone_context"

    def find(self, text: str) -> list[Span]:
        keywords = [(m.start(), m.end()) for m in _KEYWORD.finditer(text)]
        if not keywords:
            return []
        ends = [end for _, end in keywords]
        found = []
        for match in _BARE.finditer(text):
            start, end = _within_digit_limit(text, *match.span())
            value = text[start:end]
            shaped = value.startswith("(") or len(_GROUP.findall(value)) >= 3  # noqa: PLR2004
            if not shaped or not 9 <= _digit_count(value) <= MAX_DIGITS:  # noqa: PLR2004
                continue
            # Keywords do not overlap, so the one ending last before the number is
            # also the one starting last: if it is too far, every other one is too.
            nearest = bisect_right(ends, start) - 1
            if nearest >= 0 and keywords[nearest][0] >= start - KEYWORD_DISTANCE:
                found.append(Span(start, end, EntityType.PHONE, CONTEXT_SCORE, self.name))
        return found


PHONE_SHAPE = PhoneShapeRecognizer()
PHONE_CONTEXT = PhoneContextRecognizer()
