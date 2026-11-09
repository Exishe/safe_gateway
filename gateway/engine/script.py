"""Which model reads which part of a text: by script, not by a detector (ADR 0012 point 4).

English, Russian and Armenian use different alphabets, so counting letters is
enough: each paragraph goes to the language of the script most of its letters
belong to. Latin is `en`, Cyrillic `ru`, Armenian `hy`; another script, or no
letters at all, is `und`. No language-detection library is involved.
"""

import re
from collections.abc import Iterator


def _letters(*ranges: tuple[int, int]) -> re.Pattern[str]:
    # Built from code points, so the source holds no lookalike or invisible characters.
    return re.compile("[" + "".join(f"\\u{low:04x}-\\u{high:04x}" for low, high in ranges) + "]")


# Listed so that a tie goes to Cyrillic or Armenian: Latin also turns up inside Russian
# and Armenian text (brands, codes, addresses), the other way round much less.
_SCRIPTS = (
    # Cyrillic, Cyrillic Supplement, Extended-C, -A and -B.
    ("ru", _letters((0x400, 0x52F), (0x1C80, 0x1C8F), (0x2DE0, 0x2DFF), (0xA640, 0xA69F))),
    # Armenian capitals and small letters, Armenian ligatures.
    ("hy", _letters((0x531, 0x556), (0x561, 0x587), (0xFB13, 0xFB17))),
    # Basic Latin letters, Latin-1 letters, Latin Extended-A and -B, Latin Extended Additional.
    (
        "en",
        _letters(
            (0x41, 0x5A), (0x61, 0x7A), (0xC0, 0xD6), (0xD8, 0xF6), (0xF8, 0x24F), (0x1E00, 0x1EFF)
        ),
    ),
)
_LETTER = re.compile(r"[^\W\d_]")
# Email addresses and links are Latin whatever the language around them: not counted.
_ADDRESS = re.compile(r"\S*(?:@|://|www\.)\S*")
_PARAGRAPH_BREAK = re.compile(r"\n[^\S\n]*\n\s*")  # one or more blank lines


def segments(text: str) -> list[tuple[int, int, str]]:
    """(start, end, language) for each run of paragraphs that share a dominant script.

    Neighbouring paragraphs of one language form one segment, so a model sees as
    much context as it can. Whitespace around paragraphs is left out.
    """
    found: list[tuple[int, int, str]] = []
    for start, end in _paragraphs(text):
        lang = _language(text, start, end)
        if found and found[-1][2] == lang:
            found[-1] = (found[-1][0], end, lang)
        else:
            found.append((start, end, lang))
    return found


def _paragraphs(text: str) -> Iterator[tuple[int, int]]:
    start = 0
    for paragraph_break in _PARAGRAPH_BREAK.finditer(text):
        yield from _stripped(text, start, paragraph_break.start())
        start = paragraph_break.end()
    yield from _stripped(text, start, len(text))


def _stripped(text: str, start: int, end: int) -> Iterator[tuple[int, int]]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if start < end:
        yield start, end


def _language(text: str, start: int, end: int) -> str:
    prose = _ADDRESS.sub(" ", text[start:end])
    counts = {lang: len(pattern.findall(prose)) for lang, pattern in _SCRIPTS}
    lang, count = max(counts.items(), key=lambda item: item[1])  # a tie: first listed
    others = len(_LETTER.findall(prose)) - sum(counts.values())
    return lang if count > 0 and count >= others else "und"
