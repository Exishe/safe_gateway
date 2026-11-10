"""Find runs of digit groups and the candidate windows inside them (ADR 0011 point 4, ADR 0013).

`\\d` matches every Unicode decimal digit, so Arabic-Indic or full-width
numbers are found without rewriting the text, and offsets stay exact.
"""

import re
from bisect import bisect_right, insort
from collections.abc import Callable

Group = tuple[int, int]

# Digit groups joined by one space, dot or hyphen, a spaced hyphen, or a line
# break. A run ends where the digits end, not at a letter, so a number glued
# to a label or a file name ("scan_4111...") is still read.
_SEPARATOR = r"(?: - |[ .-]|\r?\n)"
_RUN = re.compile(rf"(?<!\d)\d+(?:{_SEPARATOR}\d+)*(?!\d)")
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
    """Every window that passes `accept` and is not inside one accepted before it.

    Overlaps are allowed: a coincidentally valid window must not hide a real
    value that shares some of its groups (the merge resolves the overlap).
    Accepted windows never nest, so sorted by start they are sorted by end too,
    and the one starting last at or before a window is the only one that can hold it.
    """
    starts: list[int] = []
    ends: list[int] = []
    for start, end in windows(run, min_digits, max_digits):
        index = bisect_right(starts, start) - 1
        if index >= 0 and ends[index] >= end:
            continue
        if accept(start, end):
            insort(starts, start)
            insort(ends, end)
    return list(zip(starts, ends, strict=True))
