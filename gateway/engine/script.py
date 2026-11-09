"""Which model reads which part of a text: by script, not by a detector (ADR 0012, ADR 0016).

English, Russian and Armenian use different alphabets, so counting letters is
enough: each line goes to the language of the script most of its letters
belong to. Latin is `en`, Cyrillic `ru`, Armenian `hy`; another script is
`und`. Text with no letters at all (a date, a number) goes with its neighbours.
No language-detection library is involved.

Every pattern here is linear in the length of the text: segments() runs on
every prompt, whether or not a model is installed.
"""

import re
from collections.abc import Iterator


def _ranges(*ranges: tuple[int, int]) -> str:
    # Built from code points, so the source holds no lookalike or invisible characters.
    return "".join(f"\\u{low:04x}-\\u{high:04x}" for low, high in ranges)


def _letters(*ranges: tuple[int, int]) -> re.Pattern[str]:
    return re.compile("[" + _ranges(*ranges) + "]")


_CYRILLIC = ((0x400, 0x52F), (0x1C80, 0x1C8F), (0x2DE0, 0x2DFF), (0xA640, 0xA69F))
# Basic Latin letters, the Latin-1 ordinal indicators and micro sign (ª º µ, as in "5 µg"),
# Latin-1 letters, Latin Extended-A and -B, Latin Extended Additional.
_LATIN = (
    (0x41, 0x5A),
    (0x61, 0x7A),
    (0xAA, 0xAA),
    (0xB5, 0xB5),
    (0xBA, 0xBA),
    (0xC0, 0xD6),
    (0xD8, 0xF6),
    (0xF8, 0x24F),
    (0x1E00, 0x1EFF),
)


# Listed so that a tie goes to Cyrillic or Armenian: Latin also turns up inside Russian
# and Armenian text (brands, codes, addresses), the other way round much less.
_SCRIPTS = (
    # Cyrillic, Cyrillic Supplement, Extended-C, -A and -B.
    ("ru", _letters(*_CYRILLIC)),
    # Armenian capitals and small letters, Armenian ligatures.
    ("hy", _letters((0x531, 0x556), (0x561, 0x587), (0xFB13, 0xFB17))),
    ("en", _letters(*_LATIN)),
)
_MODEL_LANGUAGES = frozenset(lang for lang, _ in _SCRIPTS)
_LETTER = re.compile(r"[^\W\d_]")
# A letter of a script with no spaCy model: neither Latin nor Cyrillic (ADR 0016 amended).
_OTHER_SCRIPT_LETTER = re.compile("[^\\W\\d_" + _ranges(*_LATIN, *_CYRILLIC) + "]")
# Any line or page break.
_ANY_BREAK = re.compile("[\\n\\r\\x0b\\x0c\\x85\\u2028\\u2029]")
# Email addresses and links are Latin whatever the language around them: not counted.
_ADDRESS_MARKS = ("@", "://", "www.")

# One line break: CR LF, CR, LF, vertical tab, next line, line separator. "\r(?!\n)" keeps
# a CR LF from also counting as two breaks when the pattern backtracks.
_LINE_BREAK = "(?:\\r\\n|\\r(?!\\n)|[\\n\\x0b\\x85\\u2028])"
# Whitespace that is not a line or page break.
_INLINE_SPACE = "[^\\S\\r\\n\\x0b\\x0c\\x85\\u2028\\u2029]"
# A blank line (two breaks with only spaces between), a form feed (pdftotext's page
# break) or a paragraph separator; then any whitespace after it.
_PARAGRAPH_BREAK = re.compile(f"(?:{_LINE_BREAK}{_INLINE_SPACE}*{_LINE_BREAK}|[\\x0c\\u2029])\\s*")
_LINE_BREAK_PATTERN = re.compile(_LINE_BREAK)

Unit = tuple[int, int, str | None]  # (start, end, language); None: no letters


def segments(text: str) -> list[tuple[int, int, str]]:
    """(start, end, language) for each run of lines that share a dominant script.

    Neighbouring lines and paragraphs of one language form one segment, so a model
    sees as much context as it can; a line break between two scripts ends one (a
    chat sends Enter as one line break). A paragraph with no letters joins the
    language before it, else the one after it. Whitespace around lines is left out.
    """
    units = [
        unit for start, end in _paragraphs(text) for unit in _lines_by_language(text, start, end)
    ]
    found: list[tuple[int, int, str]] = []
    for start, end, lang in _letterless_joined(units):
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


def _lines(text: str, start: int, end: int) -> Iterator[tuple[int, int]]:
    for line_break in _LINE_BREAK_PATTERN.finditer(text, start, end):
        yield from _stripped(text, start, line_break.start())
        start = line_break.end()
    yield from _stripped(text, start, end)


def _stripped(text: str, start: int, end: int) -> Iterator[tuple[int, int]]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if start < end:
        yield start, end


def _lines_by_language(text: str, start: int, end: int) -> list[Unit]:
    """The paragraph's lines, grouped by language.

    A line without a model language (no letters, or another script) goes with the
    model language before it in the paragraph, else the one after it, as the whole
    paragraph did before lines were told apart.
    """
    lines = [(s, e, _language(text, s, e)) for s, e in _lines(text, start, end)]
    if not any(lang in _MODEL_LANGUAGES for _, _, lang in lines):
        own = "und" if any(lang == "und" for _, _, lang in lines) else None
        return [(start, end, own)]
    languages = _filled([lang if lang in _MODEL_LANGUAGES else None for _, _, lang in lines])
    grouped: list[Unit] = []
    for (line_start, line_end, _), lang in zip(lines, languages, strict=True):
        if grouped and grouped[-1][2] == lang:
            grouped[-1] = (grouped[-1][0], line_end, lang)
        else:
            grouped.append((line_start, line_end, lang))
    return grouped


def _letterless_joined(units: list[Unit]) -> list[tuple[int, int, str]]:
    languages = _filled([lang for _, _, lang in units])
    return [
        (start, end, lang or "und") for (start, end, _), lang in zip(units, languages, strict=True)
    ]


def _filled(languages: list[str | None]) -> list[str | None]:
    """Each None replaced by the nearest language before it, else the nearest after it."""
    filled = list(languages)
    previous: str | None = None
    for index, lang in enumerate(filled):
        if lang is None:
            filled[index] = previous
        else:
            previous = lang
    following: str | None = None
    for index in range(len(filled) - 1, -1, -1):
        if filled[index] is None:
            filled[index] = following
        else:
            following = filled[index]
    return filled


def _language(text: str, start: int, end: int) -> str | None:
    """The language of the dominant script, "und" for another script, None without letters."""
    # Whitespace-separated words, tested with `in`: a pattern such as \S*@\S* would retry
    # every start in a long run with no whitespace (a base64 blob), which is quadratic.
    prose = " ".join(
        word for word in text[start:end].split() if not any(m in word for m in _ADDRESS_MARKS)
    )
    letters = len(_LETTER.findall(prose))
    if letters == 0:
        return None
    counts = {lang: len(pattern.findall(prose)) for lang, pattern in _SCRIPTS}
    lang, count = max(counts.items(), key=lambda item: item[1])  # a tie: first listed
    others = letters - sum(counts.values())
    return lang if count > 0 and count >= others else "und"


def lines_in_other_scripts(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """The lines of text[start:end] holding a letter of a script with no spaCy model.

    A line goes to the model of its dominant script, so an Armenian or Greek clause after
    an English instruction ("Translate this: ...") reaches only the English model, which
    does not read that script. detect() sends these lines to GLiNER as well.
    """
    if _OTHER_SCRIPT_LETTER.search(text, start, end) is None:
        return []
    found = []
    line_start = start
    for line_break in [*_ANY_BREAK.finditer(text, start, end), None]:
        line_end = end if line_break is None else line_break.start()
        for s, e in _stripped(text, line_start, line_end):
            if _OTHER_SCRIPT_LETTER.search(text, s, e) is not None:
                found.append((s, e))
        if line_break is not None:
            line_start = line_break.end()
    return found
