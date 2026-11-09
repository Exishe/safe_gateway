"""Put the original values back into a model's answer (ADR 0003, ADR 0004 point 3)."""

from gateway.engine.mask import Mapping
from gateway.engine.placeholders import TOKEN, canonical_of


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
