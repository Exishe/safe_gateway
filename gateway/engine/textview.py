"""The text recognizers read, and the map from it back to the original (ADR 0013).

Word, PDF and web copies put no-break spaces, typographic dashes and invisible
format characters inside identifiers. Every recognizer would have to allow
for them; instead `detect()` hands recognizers a cleaned copy and maps each
span back, so `text[span.start:span.end]` is always the value as written.
"""

import re
from bisect import bisect_right
from dataclasses import dataclass

# Code points, not literal characters, so the source holds nothing invisible.
# Unicode space separators (category Zs) other than the ASCII space: 1:1 to " ".
SPACES = "".join(map(chr, (0x00A0, 0x1680, *range(0x2000, 0x200B), 0x202F, 0x205F, 0x3000)))
# Hyphen and dash variants, minus signs included: 1:1 to "-".
DASHES = "".join(map(chr, (*range(0x2010, 0x2016), 0x2212, 0xFE58, 0xFE63, 0xFF0D)))
# Invisible format characters (category Cf) that split a value without showing: removed.
INVISIBLE = "".join(
    map(
        chr,
        (
            0x00AD,  # soft hyphen
            0x180E,  # Mongolian vowel separator
            *range(0x200B, 0x2010),  # zero-width space, (non-)joiner, LRM, RLM
            *range(0x202A, 0x202F),  # bidi embeddings and overrides
            *range(0x2060, 0x2065),  # word joiner, invisible operators
            *range(0x2066, 0x206A),  # bidi isolates
            0xFEFF,  # byte order mark, zero-width no-break space
        ),
    )
)


def _char_class(chars: str) -> str:
    return "[" + "".join(f"\\u{ord(c):04x}" for c in chars) + "]"


_SPACE = re.compile(_char_class(SPACES))
_DASH = re.compile(_char_class(DASHES))
_INVISIBLE_RUN = re.compile(_char_class(INVISIBLE) + "+")
_ANY_SPECIAL = re.compile(_char_class(SPACES + DASHES + INVISIBLE))


@dataclass(frozen=True)
class TextView:
    """`text` is the cleaned copy. After each removed run the offsets shift once more:
    a cleaned index `i` at or after `segment_starts[k]` sits at `i + shifts[k]` in the original.
    """

    text: str
    segment_starts: tuple[int, ...] = (0,)
    shifts: tuple[int, ...] = (0,)

    def original_index(self, index: int) -> int:
        return index + self.shifts[bisect_right(self.segment_starts, index) - 1]

    def original_span(self, start: int, end: int) -> tuple[int, int]:
        """The original span of cleaned `text[start:end]` (end exclusive, end > start)."""
        return self.original_index(start), self.original_index(end - 1) + 1


def clean_view(text: str) -> TextView:
    if _ANY_SPECIAL.search(text) is None:
        return TextView(text)  # the common case: nothing to clean, offsets are the same
    replaced = _DASH.sub("-", _SPACE.sub(" ", text))
    pieces, segment_starts, shifts = [], [0], [0]
    cursor = removed = 0
    for run in _INVISIBLE_RUN.finditer(replaced):
        pieces.append(replaced[cursor : run.start()])
        removed += run.end() - run.start()
        cursor = run.end()
        segment_starts.append(cursor - removed)
        shifts.append(removed)
    pieces.append(replaced[cursor:])
    return TextView("".join(pieces), tuple(segment_starts), tuple(shifts))
