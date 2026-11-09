"""Server-sent events: read the upstream's stream, write the client's (ADR 0019).

The decoder takes bytes as they arrive, split anywhere: inside a line, inside a
multi-byte character, between `\\r` and `\\n`. Comments and unknown fields are dropped.
"""

import codecs
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SSEEvent:
    data: str
    event: str | None = None
    id: str | None = None
    retry: str | None = None


@dataclass
class _Pending:
    data: list[str] = field(default_factory=list)
    event: str | None = None
    id: str | None = None
    retry: str | None = None


class SSEDecoder:
    def __init__(self) -> None:
        self._text = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._buffer = ""
        self._after_cr = False  # the last line ended in \r; a leading \n belongs to it
        self._pending = _Pending()

    def feed(self, chunk: bytes) -> list[SSEEvent]:
        return self._lines(self._text.decode(chunk))

    def close(self) -> list[SSEEvent]:
        """The stream ended: a last event without its blank line still counts."""
        events = self._lines(self._text.decode(b"", final=True))
        if self._buffer:
            events.extend(self._line(self._buffer))
            self._buffer = ""
        events.extend(self._line(""))
        return events

    def _lines(self, text: str) -> list[SSEEvent]:
        if self._after_cr and text.startswith("\n"):
            text = text[1:]
        if text:
            self._after_cr = False
        buffer = self._buffer + text
        events: list[SSEEvent] = []
        start = 0
        index = 0
        while index < len(buffer):
            char = buffer[index]
            if char in "\r\n":
                events.extend(self._line(buffer[start:index]))
                if char == "\r" and index + 1 == len(buffer):
                    self._after_cr = True
                elif char == "\r" and buffer[index + 1] == "\n":
                    index += 1
                start = index + 1
            index += 1
        self._buffer = buffer[start:]
        return events

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
