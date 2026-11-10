"""Server-sent events: read the upstream's stream, write the client's (ADR 0019).

The decoder takes bytes as they arrive, split anywhere: inside a line, inside a
multi-byte character, between `\\r` and `\\n`. Comments and unknown fields are dropped.

A line, and an event's data, may hold at most MAX_LINE_CHARS characters; past that the
decoder raises SSELineTooLongError instead of buffering without bound. Each chunk is
scanned once: an unfinished line is kept as pieces, never rescanned (review M12).
"""

import codecs
import re
from dataclasses import dataclass, field

MAX_LINE_CHARS = 1024 * 1024  # 1 MiB of decoded text
_LINE_END = re.compile(r"\r\n|\r|\n")


class SSELineTooLongError(ValueError):
    """The upstream sent a line or an event over MAX_LINE_CHARS characters."""

    def __init__(self) -> None:
        super().__init__("the upstream sent a stream line or event over 1 MiB")


@dataclass(frozen=True)
class SSEEvent:
    data: str
    event: str | None = None
    id: str | None = None
    retry: str | None = None


@dataclass
class _Pending:
    data: list[str] = field(default_factory=list)
    size: int = 0  # characters in `data`, joined
    event: str | None = None
    id: str | None = None
    retry: str | None = None


class SSEDecoder:
    def __init__(self) -> None:
        self._text = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._pieces: list[str] = []  # the unfinished line, as it arrived
        self._size = 0  # characters in `_pieces`
        self._after_cr = False  # the last line ended in \r; a leading \n belongs to it
        self._pending = _Pending()

    def feed(self, chunk: bytes) -> list[SSEEvent]:
        return self._lines(self._text.decode(chunk))

    def close(self) -> list[SSEEvent]:
        """The stream ended: a last event without its blank line still counts."""
        events = self._lines(self._text.decode(b"", final=True))
        if self._pieces:
            events.extend(self._line(self._take("")))
        events.extend(self._line(""))
        return events

    def _lines(self, text: str) -> list[SSEEvent]:
        if self._after_cr and text.startswith("\n"):
            text = text[1:]
        if text:
            self._after_cr = False
        events: list[SSEEvent] = []
        start = 0
        for match in _LINE_END.finditer(text):  # only the new text is scanned
            events.extend(self._line(self._take(text[start : match.start()])))
            start = match.end()
            # A \r that ends the chunk may be the first half of \r\n.
            self._after_cr = match.group() == "\r" and start == len(text)
        rest = text[start:]
        if rest:
            self._size += len(rest)
            if self._size > MAX_LINE_CHARS:
                raise SSELineTooLongError
            self._pieces.append(rest)
        return events

    def _take(self, end: str) -> str:
        """The unfinished line completed by `end`; the buffer is empty afterwards."""
        if self._size + len(end) > MAX_LINE_CHARS:
            raise SSELineTooLongError
        line = "".join(self._pieces) + end
        self._pieces.clear()
        self._size = 0
        return line

    def _line(self, line: str) -> list[SSEEvent]:
        pending = self._pending
        if not line:
            self._pending = _Pending()
            if not pending.data:
                return []
            data = "\n".join(pending.data)
            return [SSEEvent(data, pending.event, pending.id, pending.retry)]
        if line.startswith(":"):
            return []
        name, _, value = line.partition(":")
        value = value.removeprefix(" ")
        if name == "data":
            pending.size += len(value) + 1
            if pending.size > MAX_LINE_CHARS:
                raise SSELineTooLongError
            pending.data.append(value)
        elif name == "event":
            pending.event = value
        elif name == "id":
            pending.id = value
        elif name == "retry":
            pending.retry = value
        return []


def encode(event: SSEEvent) -> bytes:
    lines = []
    if event.event is not None:
        lines.append(f"event: {event.event}")
    if event.id is not None:
        lines.append(f"id: {event.id}")
    if event.retry is not None:
        lines.append(f"retry: {event.retry}")
    lines.extend(f"data: {line}" for line in event.data.split("\n"))
    return ("\n".join(lines) + "\n\n").encode()
