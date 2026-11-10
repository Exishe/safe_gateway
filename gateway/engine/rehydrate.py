"""Put the original values back into a model's answer (ADR 0003, ADR 0004 point 3)."""

from collections.abc import Sequence
from enum import StrEnum

from gateway.engine.links import LinkContext
from gateway.engine.mask import Mapping
from gateway.engine.placeholders import MAX_PARTIAL, TOKEN, canonical_of, could_become_token


class UnresolvedReason(StrEnum):
    """Why a placeholder-shaped token was left as written."""

    NOT_IN_MAPPING = "not_in_mapping"
    # ADR 0003 amendment 1: a value is never restored inside a link, where a client
    # could send it to a host the answer chose (security review I3).
    INSIDE_LINK = "inside_link"


Unresolved = dict[str, UnresolvedReason]
# The contexts a text is read in: its own, and the message's when it is one block of
# several that a client joins (security review 2, I-2). Inside any one means inside.
Contexts = Sequence[LinkContext]


def _inside(links: Contexts) -> bool:
    return any(context.inside for context in links)


def _feed(links: Contexts, text: str) -> None:
    for context in links:
        context.feed(text)


def _restore(  # noqa: PLR0913  (the scan's whole state, passed in by both callers)
    text: str,
    start: int,
    stop: int,
    *,
    mapping: Mapping,
    unresolved: Unresolved,
    links: Contexts,
) -> str:
    """text[start:stop] with its tokens restored; tokens left as written go to `unresolved`.

    The scan neither slices nor sets an end position, so rule 3's edges see the real
    characters on both sides. The caller picks `stop` so that no token straddles it.
    A restored value is never scanned again for tokens, but `links` reads it: the
    client sees it, so it is part of the context of what follows.
    """
    pieces: list[str] = []
    cursor = start
    for match in TOKEN.finditer(text, start):
        if match.start() >= stop:
            break
        plain = text[cursor : match.start()]
        _feed(links, plain)
        pieces.append(plain)
        token = match.group()
        restored = _restore_token(token, canonical_of(match), mapping, links)
        if restored is None:
            unresolved.setdefault(token, _reason(links))
            restored = token
        _feed(links, restored)
        pieces.append(restored)
        cursor = match.end()
    tail = text[cursor:stop]
    _feed(links, tail)
    pieces.append(tail)
    return "".join(pieces)


def _restore_token(token: str, key: str, mapping: Mapping, links: Contexts) -> str | None:
    """The original value of a token, or None when it stays as written."""
    if _inside(links):
        return None
    # ADR 0003, first rule that matches wins. TOKEN only matches the three shapes:
    # rule 1 is an exact key; rules 2 (bracketed, any case, `_`/space/`-`) and 3
    # (unbracketed, upper case) restore when their canonical key is in the mapping.
    original = mapping.original(token)
    return original if original is not None else mapping.original(key)


def _reason(links: Contexts) -> UnresolvedReason:
    return UnresolvedReason.INSIDE_LINK if _inside(links) else UnresolvedReason.NOT_IN_MAPPING


def _contexts(message: LinkContext | None) -> tuple[LinkContext, ...]:
    own = LinkContext()
    return (own,) if message is None else (own, message)


def rehydrate_explained(
    text: str, mapping: Mapping, *, message: LinkContext | None = None
) -> tuple[str, Unresolved]:
    """(restored text, each token left as written -> why), tokens once each, in order.

    `message`, when given, is the context of the whole message that `text` is one
    block of: it has read the blocks before, and reads this one too.
    """
    unresolved: Unresolved = {}
    links = _contexts(message)
    restored = _restore(text, 0, len(text), mapping=mapping, unresolved=unresolved, links=links)
    return restored, unresolved


def rehydrate(
    text: str, mapping: Mapping, *, message: LinkContext | None = None
) -> tuple[str, list[str]]:
    """(restored text, placeholder-shaped tokens left as written, each once, in order)."""
    restored, unresolved = rehydrate_explained(text, mapping, message=message)
    return restored, list(unresolved)


def _hold_from(pending: str) -> int:
    """Where the tail that could still become, or change, a token starts (ADR 0004 point 3).

    Only a `[` or an upper-case letter can start a token, and no partial token is
    longer than MAX_PARTIAL, so only that many characters are ever examined.
    """
    for index in range(max(len(pending) - MAX_PARTIAL, 0), len(pending)):
        char = pending[index]
        if (char == "[" or "A" <= char <= "Z") and could_become_token(pending[index:]):
            return index
    return len(pending)


class StreamingRehydrator:
    """Rehydrate a streamed answer chunk by chunk; the joined output equals rehydrate().

    Emitted text is never wrong, only delayed by at most MAX_PARTIAL characters.
    """

    def __init__(self, mapping: Mapping, *, message: LinkContext | None = None) -> None:
        self._mapping = mapping
        self._pending = ""
        self._before = ""  # the last character already emitted, for rule 3's left edge
        self._unresolved: Unresolved = {}
        # Fed exactly what has been emitted; `message` as in rehydrate_explained().
        self._links = _contexts(message)

    @property
    def unresolved(self) -> list[str]:
        return list(self._unresolved)

    @property
    def reasons(self) -> Unresolved:
        return dict(self._unresolved)

    @property
    def held(self) -> int:
        """How many characters are held back, waiting for the next chunk."""
        return len(self._pending)

    def feed(self, chunk: str) -> str:
        self._pending += chunk
        return self._emit(_hold_from(self._pending))

    def close(self) -> str:
        """Flush what is held: the stream has ended, so nothing more can complete it."""
        return self._emit(len(self._pending))

    def _emit(self, cut: int) -> str:
        if cut == 0:
            return ""
        text = self._before + self._pending
        start = len(self._before)
        out = _restore(
            text,
            start,
            start + cut,
            mapping=self._mapping,
            unresolved=self._unresolved,
            links=self._links,
        )
        self._before = self._pending[cut - 1]
        self._pending = self._pending[cut:]
        return out
