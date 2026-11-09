"""Put the original values back into a model's answer (ADR 0003, ADR 0004 point 3)."""

from gateway.engine.mask import Mapping
from gateway.engine.placeholders import MAX_PARTIAL, TOKEN, canonical_of, could_become_token


def _restore(
    text: str, start: int, stop: int, mapping: Mapping, unresolved: dict[str, None]
) -> str:
    """text[start:stop] with its tokens restored; tokens left as written go to `unresolved`.

    The scan neither slices nor sets an end position, so rule 3's edges see the real
    characters on both sides. The caller picks `stop` so that no token straddles it.
    A restored value is never scanned again.
    """
    pieces: list[str] = []
    cursor = start
    for match in TOKEN.finditer(text, start):
        if match.start() >= stop:
            break
        pieces.append(text[cursor : match.start()])
        # ADR 0003, first rule that matches wins. TOKEN only matches the three shapes:
        # rule 1 is an exact key; rules 2 (bracketed, any case, `_`/space/`-`) and 3
        # (unbracketed, upper case) restore when their canonical key is in the mapping.
        token = match.group()
        original = mapping.original(token)
        if original is None:
            original = mapping.original(canonical_of(match))
        if original is None:
            unresolved[token] = None
            original = token
        pieces.append(original)
        cursor = match.end()
    pieces.append(text[cursor:stop])
    return "".join(pieces)


def rehydrate(text: str, mapping: Mapping) -> tuple[str, list[str]]:
    """(restored text, placeholder-shaped tokens left as written, each once, in order)."""
    unresolved: dict[str, None] = {}
    restored = _restore(text, 0, len(text), mapping, unresolved)
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

    def __init__(self, mapping: Mapping) -> None:
        self._mapping = mapping
        self._pending = ""
        self._before = ""  # the last character already emitted, for rule 3's left edge
        self._unresolved: dict[str, None] = {}

    @property
    def unresolved(self) -> list[str]:
        return list(self._unresolved)

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
        out = _restore(text, start, start + cut, self._mapping, self._unresolved)
        self._before = self._pending[cut - 1]
        self._pending = self._pending[cut:]
        return out
