"""What the chat routes need from an API shape, and helpers both shapes share (ADR 0019)."""

from collections.abc import Callable, Hashable, Sequence
from typing import Any, Protocol

from gateway.cloud.providers import ApiShape
from gateway.engine.links import LinkContext
from gateway.engine.mask import Mapping
from gateway.engine.placeholders import TOKEN
from gateway.engine.rehydrate import StreamingRehydrator, rehydrate
from gateway.proxy.sse import SSEEvent

Body = dict[str, Any]
Rewrite = Callable[[Body, Callable[[str], str]], Body]


class StreamRewriter(Protocol):
    def on_event(self, event: SSEEvent) -> list[SSEEvent]: ...

    def finish(self) -> list[SSEEvent]: ...

    @property
    def unresolved(self) -> list[str]: ...


class Shape(Protocol):
    api_shape: ApiShape

    def validate(self, body: Body) -> Body: ...

    def collect_texts(self, body: Body) -> list[str]: ...

    def with_texts(self, body: Body, texts: Sequence[str]) -> Body: ...

    def with_instruction(self, body: Body) -> Body: ...

    def rehydrate_reply(self, payload: Body, mapping: Mapping) -> list[str]: ...

    def stream_rewriter(self, mapping: Mapping) -> StreamRewriter: ...

    def error_body(self, error_type: str, message: str) -> Body: ...

    def stream_error(self, message: str) -> SSEEvent: ...


def collect(rewrite: Rewrite, body: Body) -> list[str]:
    """Every text `rewrite` visits, in its order."""
    texts: list[str] = []

    def keep(text: str) -> str:
        texts.append(text)
        return text

    rewrite(body, keep)
    return texts


def replace(rewrite: Rewrite, body: Body, texts: Sequence[str]) -> Body:
    """A copy of `body` with the visited texts replaced, in order, by `texts`."""
    remaining = iter(texts)
    missing = object()

    def take(_: str) -> str:
        text = next(remaining, missing)
        if not isinstance(text, str):
            msg = "fewer texts than text slots"
            raise ValueError(msg)  # noqa: TRY004  (a count mismatch, not a type error)
        return text

    out = rewrite(body, take)
    if next(remaining, missing) is not missing:
        msg = "more texts than text slots"
        raise ValueError(msg)
    return out


def rewrite_values(value: object, fn: Callable[[str], str]) -> object:
    """`value` with every string rewritten, object keys included, each key before its value.

    Keys are rewritten too (security review M3): in a JSON schema they are property
    names, which can hold personal data as well as values can. Mask gives two distinct
    keys two distinct masked forms, so no key is lost.
    """
    if isinstance(value, str):
        return fn(value)
    if isinstance(value, dict):
        # A dict comprehension evaluates each key before its value.
        return {fn(key): rewrite_values(item, fn) for key, item in value.items()}
    if isinstance(value, list):
        return [rewrite_values(item, fn) for item in value]
    return value


def stream_key(value: object) -> int | None:
    """A choice or block index from the upstream, if it is one: an int, never a bool."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def rehydrate_message(texts: Sequence[str], mapping: Mapping) -> tuple[list[str], list[str]]:
    """(each text restored, tokens left as written), the texts read as one message.

    A client joins a reply's blocks or choices, so a link one opens runs on into the
    next: one link context reads them all in order (security review 2, I-2).
    """
    message = LinkContext()
    restored: list[str] = []
    unresolved: dict[str, None] = {}
    for text in texts:
        out, tokens = rehydrate(text, mapping, message=message)
        restored.append(out)
        unresolved.update(dict.fromkeys(tokens))
    return restored, list(unresolved)


class Rehydrators:
    """One StreamingRehydrator per stream key (a choice, a content block), one message.

    Every key reads its text in its own link context and in the message's, which has
    read what every key emitted before (security review 2, I-2). A key that has been
    closed stays closed: later text for it passes as written, and the message's
    context still reads it, since the client shows it.

    `sequential`: keys follow one another (Anthropic's content blocks), so text for
    a new key closes every other key first; `close_others` returns their tails.
    """

    def __init__(self, mapping: Mapping, *, sequential: bool = False) -> None:
        self._mapping = mapping
        self._sequential = sequential
        self._message = LinkContext()
        self._open: dict[Hashable, StreamingRehydrator] = {}
        self._closed: set[Hashable] = set()
        self._unresolved: dict[str, None] = {}

    def close_others(self, key: Hashable) -> list[tuple[Hashable, str]]:
        """In sequential mode, close every open key but `key`, before `key` speaks."""
        if not self._sequential:
            return []
        return [(other, self.close(other)) for other in list(self._open) if other != key]

    def feed(self, key: Hashable, text: str) -> str:
        if key in self._closed:
            self._message.feed(text)
            self._unresolved.update(dict.fromkeys(m.group() for m in TOKEN.finditer(text)))
            return text
        rehydrator = self._open.get(key)
        if rehydrator is None:
            rehydrator = StreamingRehydrator(self._mapping, message=self._message)
            self._open[key] = rehydrator
        return rehydrator.feed(text)

    def close(self, key: Hashable) -> str:
        """What `key` still holds; the key is done afterwards, for good."""
        self._closed.add(key)
        rehydrator = self._open.pop(key, None)
        if rehydrator is None:
            return ""
        tail = rehydrator.close()
        self._unresolved.update(dict.fromkeys(rehydrator.unresolved))
        return tail

    def close_all(self) -> list[tuple[Hashable, str]]:
        return [(key, self.close(key)) for key in list(self._open)]

    @property
    def unresolved(self) -> list[str]:
        pending = [token for r in self._open.values() for token in r.unresolved]
        return list({**self._unresolved, **dict.fromkeys(pending)})
