"""What the chat routes need from an API shape, and helpers both shapes share (ADR 0019)."""

from collections.abc import Callable, Hashable, Sequence
from typing import Any, Protocol

from gateway.cloud.providers import ApiShape
from gateway.engine.mask import Mapping
from gateway.engine.rehydrate import StreamingRehydrator
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


class Rehydrators:
    """One StreamingRehydrator per stream key (a choice, a content block)."""

    def __init__(self, mapping: Mapping) -> None:
        self._mapping = mapping
        self._open: dict[Hashable, StreamingRehydrator] = {}
        self._unresolved: dict[str, None] = {}

    def feed(self, key: Hashable, text: str) -> str:
        rehydrator = self._open.get(key)
        if rehydrator is None:
            rehydrator = self._open[key] = StreamingRehydrator(self._mapping)
        return rehydrator.feed(text)

    def close(self, key: Hashable) -> str:
        """What `key` still holds; the key is done afterwards."""
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
