"""Find values a mapping already holds wherever they recur (ADR 0014, amendment 1).

Detection can miss a value it found elsewhere: `ref123-45-6780.pdf` glues an SSN to
a file name. Mask therefore also replaces every exact occurrence of a known value.
All values go into one regular expression shaped as a prefix tree, so a search costs
about the length of the text, not the text times the number of values.
"""

import re
from collections.abc import Mapping, Sequence

from gateway.engine.types import EntityType, Span

RECOGNIZER = "known_value"
# Deeper than this, the remaining suffixes are listed flat, longest first. Bounds the
# nesting of groups so a chain of values that prefix each other (A, AA, AAA...) cannot
# exhaust the regex compiler's recursion.
_MAX_DEPTH = 24
_DIGIT = r"\d"
_LETTER = r"[^\W\d_]"  # a Unicode word character that is neither a digit nor `_`
_IS_DIGIT = re.compile(_DIGIT)
_IS_LETTER = re.compile(_LETTER)


def _edge_class(char: str) -> str | None:
    """The class a neighbour must not belong to for a value edge ending in `char`.

    A digit touching a digit, or a letter touching a letter, continues the same word or
    number, so the value is not there. Any other neighbour ends it: `ref123-45-6780.pdf`
    holds the SSN (a letter touches its digit), `Also` does not hold the name `Al`.
    """
    if _IS_DIGIT.fullmatch(char):
        return _DIGIT
    if _IS_LETTER.fullmatch(char):
        return _LETTER
    return None


def _end(value: str) -> str:
    edge = _edge_class(value[-1])
    return f"(?!{edge})" if edge else ""


def _alternation(parts: Sequence[str]) -> str:
    return parts[0] if len(parts) == 1 else f"(?:{'|'.join(parts)})"


def _common_prefix(words: Sequence[str]) -> str:
    first, last = min(words), max(words)  # the sorted extremes share the common prefix
    size = 0
    while size < len(first) and first[size] == last[size]:
        size += 1
    return first[:size]


def _tree(items: Sequence[tuple[str, str]], depth: int) -> str:
    """A pattern for (rest, value) pairs: each rest, then the value's end check.

    At every branch the longer continuations come before a value that ends there, so
    at any start the longest known value wins.
    """
    if depth >= _MAX_DEPTH or len(items) == 1:
        ordered = sorted(items, key=lambda item: -len(item[0]))
        return _alternation([re.escape(rest) + _end(value) for rest, value in ordered])
    branches: dict[str, list[tuple[str, str]]] = {}
    ending: list[str] = []
    for rest, value in items:
        if rest:
            branches.setdefault(rest[0], []).append((rest, value))
        else:
            ending.append(value)
    parts = []
    for first in sorted(branches):
        group = branches[first]
        shared = _common_prefix([rest for rest, _ in group])
        tail = _tree([(rest[len(shared) :], value) for rest, value in group], depth + 1)
        parts.append(re.escape(shared) + tail)
    parts.extend(_end(value) for value in ending[:1])  # values are distinct: at most one
    return _alternation(parts)


class KnownValues:
    """Surface -> type for the values to find, compiled once."""

    def __init__(self, values: Mapping[str, EntityType]) -> None:
        self._types = dict(values)
        by_edge: dict[str | None, list[str]] = {}
        for value in self._types:
            by_edge.setdefault(_edge_class(value[0]), []).append(value)
        parts = [
            (f"(?<!{edge})" if edge else "") + _tree([(v, v) for v in group], 0)
            for edge, group in sorted(by_edge.items(), key=lambda item: item[0] or "")
        ]
        self._pattern = re.compile(_alternation(parts)) if parts else None
        # re keeps every compiled pattern in a module-wide cache, and this pattern spells
        # out the values: drop it there, so clearing the mapping really forgets them.
        re.purge()

    def __len__(self) -> int:
        return len(self._types)

    def spans(self, text: str) -> list[Span]:
        """Every occurrence of a known value, overlapping ones included.

        The search resumes one character after each match's start, so a value that
        overlaps another is still found; mask unites overlaps (ADR 0014 point 5).
        """
        if self._pattern is None:
            return []
        found = []
        position = 0
        while (match := self._pattern.search(text, position)) is not None:
            surface = match.group()
            found.append(Span(match.start(), match.end(), self._types[surface], 1.0, RECOGNIZER))
            position = match.start() + 1
        return found
