"""Find runs of digit groups and the candidate windows inside them (ADR 0011, point 4).

`\\d` matches every Unicode decimal digit, so Arabic-Indic or full-width
numbers are found without rewriting the text, and offsets stay exact.
"""

import re
from collections.abc import Callable

Group = tuple[int, int]

# Digit groups joined by exactly one space, hyphen or dot, not touching a letter.
_RUN = re.compile(r"(?<!\w)\d+(?:[ .-]\d+)*(?!\w)")
_GROUP = re.compile(r"\d+")


def digit_runs(text: str) -> list[list[Group]]:
    runs = []
    for match in _RUN.finditer(text):
        offset = match.start()
        runs.append(
            [(offset + g.start(), offset + g.end()) for g in _GROUP.finditer(match.group())]
        )
    return runs


def windows(run: list[Group], min_digits: int, max_digits: int) -> list[Group]:
    """Every span of whole consecutive groups holding min..max digits, longest first."""
    found = []
    for first in range(len(run)):
        count = 0
        for last in range(first, len(run)):
            count += run[last][1] - run[last][0]
            if count > max_digits:
                break
            if count >= min_digits:
                found.append((run[first][0], run[last][1], count))
    found.sort(key=lambda w: (-w[2], w[0]))
    return [(start, end) for start, end, _ in found]


def group_sizes(text: str, start: int, end: int) -> tuple[int, ...]:
    return tuple(len(g) for g in _GROUP.findall(text[start:end]))


def accepted_windows(
    run: list[Group], min_digits: int, max_digits: int, accept: Callable[[int, int], bool]
) -> list[Group]:
    """Disjoint windows that pass `accept`, preferring the longest."""
    taken: list[Group] = []
    for start, end in windows(run, min_digits, max_digits):
        if all(end <= s or start >= e for s, e in taken) and accept(start, end):
            taken.append((start, end))
    return taken
