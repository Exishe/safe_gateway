"""The text recognizers read, and the map from it back to the original (ADR 0013, ADR 0016).

Word, PDF and web copies put no-break spaces, typographic dashes and invisible
format characters inside identifiers. Every recognizer would have to allow
for them; instead `detect()` hands recognizers a cleaned copy and maps each
span back, so `text[span.start:span.end]` is always the value as written.

The NER models get one more view of each segment (`ner_view`): accents
composed (NFC) and lines in capitals title-cased, because both models read
case and whole letters as cues.
"""

import re
import unicodedata
from bisect import bisect_right
from collections.abc import Iterator
from dataclasses import dataclass, replace

# Code points, not literal characters, so the source holds nothing invisible.
# Unicode space separators (category Zs) other than the ASCII space: 1:1 to " ".
SPACES = "".join(map(chr, (0x00A0, 0x1680, *range(0x2000, 0x200B), 0x202F, 0x205F, 0x3000)))
# Hyphen and dash variants, minus signs included: 1:1 to "-".
DASHES = "".join(map(chr, (*range(0x2010, 0x2016), 0x2212, 0xFE58, 0xFE63, 0xFF0D)))
# Typographic apostrophes (Word's autocorrect, the modifier letter, full width): 1:1 to "'".
APOSTROPHES = "".join(map(chr, (0x02BC, 0x2019, 0xFF07)))
# Variation selectors pick an emoji or glyph style; they show nothing of their own.
VARIATION_SELECTORS = "".join(map(chr, (*range(0xFE00, 0xFE10), *range(0xE0100, 0xE01F0))))
# Unicode keeps format characters (category Cf) in the BMP and planes 1 and 14 only;
# scanning those, not all 1.1 million code points, keeps the import fast. A test scans
# every code point, so a future Unicode release that adds one elsewhere is caught.
_SCANNED_PLANES = (range(0x10000), range(0x10000, 0x20000), range(0xE0000, 0xF0000))


def _format_characters() -> str:
    return "".join(
        chr(code)
        for plane in _SCANNED_PLANES
        for code in plane
        if unicodedata.category(chr(code)) == "Cf"
    )


# Invisible characters that split a value without showing: removed. Every format
# character (soft hyphen, zero-width space and joiners, bidi marks and isolates, word
# joiner, BOM, Arabic letter mark, the tag characters U+E0001 and U+E0020-E007F), the
# combining grapheme joiner (category Mn, but it shows nothing) and the whole tags
# block U+E0000-E007F, unassigned code points included.
# Default_Ignorable_Code_Point characters that are not format characters (Unicode
# DerivedCoreProperties): they render as nothing but are letters (Hangul fillers), marks
# (Mongolian free variation selectors, Khmer inherent vowels) or unassigned code points
# reserved as ignorable. Python's unicodedata does not expose the property, so the list
# is written out; a test checks each entry is still ignorable-shaped (ADR 0016, 1d).
_IGNORABLE_RANGES = (
    (0x115F, 0x1160),  # Hangul choseong and jungseong fillers (Lo)
    (0x17B4, 0x17B5),  # Khmer inherent vowels (Mn)
    (0x180B, 0x180D),  # Mongolian free variation selectors 1-3 (Mn)
    (0x180F, 0x180F),  # Mongolian free variation selector 4 (Mn)
    (0x2065, 0x2065),  # reserved
    (0x3164, 0x3164),  # Hangul filler (Lo)
    (0xFFA0, 0xFFA0),  # half-width Hangul filler (Lo)
    (0xFFF0, 0xFFF8),  # reserved
    (0xE0080, 0xE00FF),  # reserved
    (0xE01F0, 0xE0FFF),  # reserved
)
DEFAULT_IGNORABLE = "".join(
    chr(code) for low, high in _IGNORABLE_RANGES for code in range(low, high + 1)
)
INVISIBLE = (
    "".join(
        sorted(
            {
                *_format_characters(),
                chr(0x034F),  # combining grapheme joiner
                *map(chr, range(0xE0000, 0xE0080)),  # tags block
                *DEFAULT_IGNORABLE,
            }
        )
    )
    + VARIATION_SELECTORS
)
# Every digit of every script to its ASCII digit, 1:1, so every recognizer reads one kind
# of digit and offsets do not move: decimal digits (Arabic-Indic, full width, Devanagari)
# and the digit look-alikes that are numeric but not decimal (superscripts, subscripts,
# circled, parenthesised and full-stop digits), which people and models read as digits.
_DIGIT_FOLD = {
    code: str(unicodedata.digit(chr(code)))
    for plane in _SCANNED_PLANES[:2]
    for code in plane
    if not chr(code).isascii() and unicodedata.digit(chr(code), None) is not None
}
NON_ASCII_DIGITS = "".join(map(chr, _DIGIT_FOLD))
# Punctuation that a CJK keyboard or IME writes in addresses, dates and numbers, folded
# 1:1 as NFKC would: full-width and small "@", ideographic, full-width, half-width and
# small full stops, full-width solidus, plus sign and brackets.
PUNCTUATION_FOLD = {
    0xFF20: "@",
    0xFE6B: "@",
    0x3002: ".",
    0xFF0E: ".",
    0xFF61: ".",
    0xFE52: ".",
    0xFF0F: "/",
    0xFF0B: "+",
    0xFF08: "(",
    0xFF09: ")",
}
_FOLD = {**_DIGIT_FOLD, **PUNCTUATION_FOLD}
# Combining marks (Mn, Me) of the BMP and plane 1. A mark on a digit or on a separator of
# a digit run (`-`, `.`, `/`, a space) is dropped: underlined text (U+0332 after each
# character) or a keycap still reads as the number.
COMBINING_MARKS = "".join(
    chr(code)
    for plane in _SCANNED_PLANES[:2]
    for code in plane
    if unicodedata.category(chr(code)) in {"Mn", "Me"}
)
REPLACEMENT = chr(0xFFFD)  # what a lone surrogate becomes, 1:1


_LAST_FOUR_DIGIT_CODE_POINT = 0xFFFF


def _escaped(char: str) -> str:
    code = ord(char)
    return f"\\u{code:04x}" if code <= _LAST_FOUR_DIGIT_CODE_POINT else f"\\U{code:08x}"


def _char_class(chars: str, extra: str = "") -> str:
    """A class of `chars` written as ranges: a long list of single characters (the
    format characters, the digits of every script) makes every search slow."""
    codes = sorted(set(map(ord, chars)))
    ranges: list[list[int]] = []
    for code in codes:
        if ranges and code == ranges[-1][1] + 1:
            ranges[-1][1] = code
        else:
            ranges.append([code, code])
    parts = (
        _escaped(chr(low)) if low == high else f"{_escaped(chr(low))}-{_escaped(chr(high))}"
        for low, high in ranges
    )
    return "[" + "".join(parts) + extra + "]"


# Half of a UTF-16 pair with no other half (JavaScript cut an emoji): UTF-8 cannot encode it.
_SURROGATES = "\\ud800-\\udfff"

_SPACE = re.compile(_char_class(SPACES))
_DASH = re.compile(_char_class(DASHES))
_APOSTROPHE = re.compile(_char_class(APOSTROPHES))
_SURROGATE = re.compile(f"[{_SURROGATES}]")
_INVISIBLE_CLASS = _char_class(INVISIBLE)
MARK_CLASS = _char_class(COMBINING_MARKS)
# A class with any member outside the BMP is matched by walking its ranges, about a
# hundred times slower than the bitmap `re` builds for a BMP-only class, so searches that
# run on every text use BMP classes and test for astral characters separately.
_BMP_LIMIT = 0x10000
MARK_CLASS_BMP = _char_class("".join(c for c in COMBINING_MARKS if ord(c) < _BMP_LIMIT))
# What the clean view removes: a run of invisible characters, or the combining marks on a
# digit or a digit separator (invisible characters between them go too). The base is the
# character before the run in the folded text, so the rule reads one character of context.
_REMOVED = re.compile(
    f"(?<=[0-9./ \\-])(?:{_INVISIBLE_CLASS}*{MARK_CLASS})+{_INVISIBLE_CLASS}*|{_INVISIBLE_CLASS}+"
)
_SPECIALS = (
    SPACES
    + DASHES
    + APOSTROPHES
    + INVISIBLE
    + NON_ASCII_DIGITS
    + "".join(map(chr, PUNCTUATION_FOLD))
    + COMBINING_MARKS
)
_ANY_SPECIAL = re.compile(
    _char_class("".join(c for c in _SPECIALS if ord(c) < _BMP_LIMIT), _SURROGATES)
)
_ANY_ASTRAL = re.compile("[\\U00010000-\\U0010ffff]")


@dataclass(frozen=True)
class TextView:
    """`text` is a view of an original. Its index `i`, at or after `segment_starts[k]`,
    starts at `i + shifts[k]` in the original and ends at `i + 1 + end_shifts[k]`.

    `end_shifts` is None when every character ends where the next one starts (only
    removals, or nothing changed). A composed character stands for several original
    ones, so its end is further on than its start.
    """

    text: str
    segment_starts: tuple[int, ...] = (0,)
    shifts: tuple[int, ...] = (0,)
    end_shifts: tuple[int, ...] | None = None

    def original_index(self, index: int) -> int:
        return index + self.shifts[bisect_right(self.segment_starts, index) - 1]

    def original_span(self, start: int, end: int) -> tuple[int, int]:
        """The original span of view `text[start:end]` (end exclusive, end > start)."""
        last = end - 1
        segment = bisect_right(self.segment_starts, last) - 1
        end_shift = (self.end_shifts or self.shifts)[segment]
        return self.original_index(start), last + 1 + end_shift


def clean_view(text: str) -> TextView:
    # Every character cleaned here is outside ASCII; isascii() is far quicker than the search.
    if text.isascii() or (_ANY_SPECIAL.search(text) is None and _ANY_ASTRAL.search(text) is None):
        return TextView(text)  # the common case: nothing to clean, offsets are the same
    replaced = _SPACE.sub(" ", text)
    replaced = _DASH.sub("-", replaced)
    replaced = _APOSTROPHE.sub("'", replaced)
    replaced = _SURROGATE.sub(REPLACEMENT, replaced)
    replaced = replaced.translate(_FOLD)
    pieces, segment_starts, shifts = [], [0], [0]
    cursor = removed = 0
    for run in _REMOVED.finditer(replaced):
        pieces.append(replaced[cursor : run.start()])
        removed += run.end() - run.start()
        cursor = run.end()
        segment_starts.append(cursor - removed)
        shifts.append(removed)
    pieces.append(replaced[cursor:])
    return TextView("".join(pieces), tuple(segment_starts), tuple(shifts))


# ------------------------------------------------------------------ what the NER models read


def ner_view(text: str) -> TextView:
    """`text` with accents composed (NFC) and capitals title-cased, mapped back exactly."""
    composed = composed_view(text)
    return replace(composed, text=title_cased_capitals(composed.text))


class _MapBuilder:
    """Builds a TextView piece by piece, adding a segment only where the shifts change."""

    def __init__(self) -> None:
        self.pieces: list[str] = []
        self.length = 0
        self.segment_starts = [0]
        self.shifts = [0]
        self.end_shifts = [0]

    def _shift(self, start_shift: int, end_shift: int) -> None:
        if (self.shifts[-1], self.end_shifts[-1]) == (start_shift, end_shift):
            return
        if self.segment_starts[-1] == self.length:  # nothing written under the last one
            self.shifts[-1], self.end_shifts[-1] = start_shift, end_shift
            return
        self.segment_starts.append(self.length)
        self.shifts.append(start_shift)
        self.end_shifts.append(end_shift)

    def same(self, original_start: int, piece: str) -> None:
        """`piece` is copied from `original_start` one for one."""
        if piece:
            shift = original_start - self.length
            self._shift(shift, shift)
            self.pieces.append(piece)
            self.length += len(piece)

    def instead(self, original_start: int, original_end: int, piece: str) -> None:
        """Each character of `piece` stands for the whole original range."""
        for char in piece:
            self._shift(original_start - self.length, original_end - self.length - 1)
            self.pieces.append(char)
            self.length += 1

    def view(self) -> TextView:
        return TextView(
            "".join(self.pieces),
            tuple(self.segment_starts),
            tuple(self.shifts),
            tuple(self.end_shifts),
        )


_WORD = re.compile(r"\S+")


def composed_view(text: str) -> TextView:
    """NFC per cluster (a character and the combining marks after it), mapped back exactly.

    macOS file names and some PDF text layers write 'é' as 'e' plus U+0301; a model
    reads that as a word broken by a stray mark. Composing shortens the text (rarely
    lengthens it), so each composed character maps back to its whole cluster.
    """
    if unicodedata.is_normalized("NFC", text):
        return TextView(text)
    builder = _MapBuilder()
    cursor = 0
    for word in _WORD.finditer(text):  # combining marks never compose across whitespace
        if unicodedata.is_normalized("NFC", word.group()):
            continue
        builder.same(cursor, text[cursor : word.start()])
        for start, end in _clusters(text, word.start(), word.end()):
            cluster = text[start:end]
            composed = unicodedata.normalize("NFC", cluster)
            if composed == cluster:
                builder.same(start, cluster)
            else:
                builder.instead(start, end, composed)
        cursor = word.end()
    builder.same(cursor, text[cursor:])
    return builder.view()


def _clusters(text: str, start: int, end: int) -> Iterator[tuple[int, int]]:
    cluster_start = start
    for index in range(start + 1, end):
        if unicodedata.combining(text[index]) == 0:
            yield cluster_start, index
            cluster_start = index
    yield cluster_start, end


# A line: no line or paragraph break inside.
_LINE = re.compile("[^\\n\\r\\x0b\\x0c\\x85\\u2028\\u2029]+")
MOSTLY_CAPITALS = 0.7  # share of cased letters in capitals that makes a line "in capitals"
MIN_CASED_LETTERS = 3


# Capital letters of the BMP (every script the models read), as a class of ranges.
_CAPITAL = _char_class("".join(chr(code) for code in range(0x10000) if chr(code).isupper()))
_CAPS_WORD = f"(?<!\\w){_CAPITAL}{{3,}}(?!\\w)"
# Two or more words in capitals of three or more letters, joined by inline spaces or a
# hyphen: "SELENE MARCHETTI-QUILLIN", or a Russian name. Never across a line break.
_CAPS_RUN = re.compile(
    f"{_CAPS_WORD}(?:(?:[^\\S\\n\\r\\x0b\\x0c\\x85\\u2028\\u2029]+|-){_CAPS_WORD})+"
)


def title_cased_capitals(text: str) -> str:
    """Each line written mostly in capitals, and each run of words in capitals inside
    another line, title-cased one character for one.

    Both models read case as a cue: 'SELENE MARCHETTI' in a header is missed where
    'Selene Marchetti' is found. A heuristic: a line is "in capitals" when more than 70%
    of at least 3 cased letters are; inside other lines, two or more neighbouring words
    in capitals, each of three letters or more, are title-cased too (ADR 0016 amended),
    so an acronym pair ("ACME LLC") becomes a name-like pair: over-masking, accepted.
    A character whose case mapping is not one character ('İ') is left as it is, so
    offsets never move.
    """
    ranges = [(m.start(), m.end()) for m in _CAPS_RUN.finditer(text)]
    ranges += [(m.start(), m.end()) for m in _LINE.finditer(text) if _in_capitals(m.group())]
    if not ranges:
        return text
    chars = list(text)
    for start, end in ranges:
        for index in range(start, end):
            word_start = index == start or not text[index - 1].isalnum()
            cased = text[index].title() if word_start else text[index].lower()
            if len(cased) == 1:
                chars[index] = cased
    return "".join(chars)


def _in_capitals(line: str) -> bool:
    upper = sum(1 for char in line if char.isupper())
    lower = sum(1 for char in line if char.islower())
    cased = upper + lower
    return cased >= MIN_CASED_LETTERS and upper > MOSTLY_CAPITALS * cased
