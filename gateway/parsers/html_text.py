"""HTML to plain text with the standard library only (ADR 0017 point 4).

Only character data becomes text: attributes (link targets, image sources) never do.
"""

import re
from html.parser import HTMLParser

_BLOCK = frozenset(
    {
        "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt",
        "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li",
        "main", "nav", "ol", "p", "pre", "section", "table", "tr", "ul",
    }
)  # fmt: skip
_CELL = frozenset({"td", "th"})
_SKIPPED = frozenset({"head", "script", "style", "template", "title"})
_SPACES = re.compile(r"[ \t\r\n\f\v]+")
_BLANK_LINES = re.compile(r"\n{3,}")


class _TextCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self._skipping = 0
        self._pre = 0
        self._cell_open = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:  # noqa: ARG002
        if tag in _SKIPPED:
            self._skipping += 1
        elif tag in _CELL:
            if self._cell_open:
                self.chunks.append("\t")
            self._cell_open = True
        elif tag in _BLOCK:
            self._break()
            if tag == "tr":
                self._cell_open = False
            if tag == "pre":
                self._pre += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIPPED:
            self._skipping = max(self._skipping - 1, 0)
        elif tag in _BLOCK:
            self._break()
            if tag == "pre":
                self._pre = max(self._pre - 1, 0)

    def _break(self) -> None:
        """One line break between blocks: adjacent block tags do not make blank lines."""
        tail = "".join(self.chunks[-3:]).rstrip(" ")
        if tail and not tail.endswith("\n"):
            self.chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skipping:
            return
        self.chunks.append(data if self._pre else _SPACES.sub(" ", data))


def to_text(html: str) -> str:
    collector = _TextCollector()
    collector.feed(html)
    collector.close()
    lines = "".join(collector.chunks).split("\n")
    text = "\n".join(line.strip(" ") for line in lines)
    return _BLANK_LINES.sub("\n\n", text).strip()
