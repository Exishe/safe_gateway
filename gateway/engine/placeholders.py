"""The placeholder grammar shared by mask and rehydrate (ADR 0001, ADR 0003, ADR 0014).

Mask and rehydrate must agree exactly on what is placeholder-shaped, or the round
trip `rehydrate(mask(x)) == x` breaks; so both import these patterns and neither
defines its own.
"""

import re

from gateway.engine.types import EntityType

# Six digits bound a token's length, which bounds what streaming holds back (ADR 0004).
MAX_COUNTER = 999_999
_COUNTER_DIGITS = len(str(MAX_COUNTER))
_NAMES = sorted((t.value for t in EntityType), key=len, reverse=True)  # longest first
_TYPES = "|".join(_NAMES)
_NUMBER = f"[0-9]{{1,{_COUNTER_DIGITS}}}"
# A word character or a square bracket next to an unbracketed token disqualifies it.
# Any Unicode word character counts (ADR 0014 point 3): a full-width digit after
# `PERSON_1` makes another number, not PERSON_1 followed by text. Brackets matter: in
# masked text the neighbours of an original segment's edges are placeholder brackets,
# so excluding them means no new token can appear at a seam.
_EDGE = r"[\w\[\]]"


def _any_case(word: str) -> str:
    """`word` in any ASCII case: PERSON -> [Pp][Ee]... (Unicode IGNORECASE would also
    take the long s or the Kelvin sign, and the grammar itself stays ASCII)."""
    return "".join(f"[{c.upper()}{c.lower()}]" if c.isalpha() else re.escape(c) for c in word)


_ANY_CASE_TYPES = "|".join(_any_case(name) for name in _NAMES)

# Compiled without re.ASCII so that \w at the edges is Unicode; every other part of the
# grammar is spelled out in ASCII.
TOKEN = re.compile(
    # Rules 1 and 2: bracketed, type in any case, `_`, space or `-`.
    rf"\[(?P<btype>{_ANY_CASE_TYPES})[_ \-](?P<bnum>{_NUMBER})\]"
    # Rule 3: unbracketed, upper-case type only, `_` or space.
    rf"|(?<!{_EDGE})(?P<utype>{_TYPES})[_ ](?P<unum>{_NUMBER})(?!{_EDGE})"
)

CANONICAL = re.compile(rf"\[(?P<type>{_TYPES})_(?P<number>[1-9][0-9]{{0,{_COUNTER_DIGITS - 1}}})\]")


def canonical(entity_type: EntityType, number: int) -> str:
    """The placeholder mask writes: `[TYPE_N]`."""
    if not 1 <= number <= MAX_COUNTER:
        msg = f"placeholder counter {number} outside 1..{MAX_COUNTER}"
        raise ValueError(msg)
    return f"[{entity_type.value}_{number}]"


def canonical_of(match: re.Match[str]) -> str:
    """The mapping key a TOKEN match would be restored from."""
    if match.group("btype") is not None:
        return f"[{match.group('btype').upper()}_{match.group('bnum')}]"
    return f"[{match.group('utype')}_{match.group('unum')}]"


def _prefixes(word: str) -> str:
    """A pattern for every non-empty prefix of `word`: ABC -> A(?:B(?:C)?)?"""
    pattern = ""
    for char in reversed(word):
        pattern = re.escape(char) + (f"(?:{pattern})?" if pattern else "")
    return pattern


_TYPE_PREFIXES = "|".join(_prefixes(name) for name in _NAMES)
_PARTIAL_NUMBER = f"[0-9]{{0,{_COUNTER_DIGITS}}}"
_PARTIAL = re.compile(
    # A bracketed token not closed yet; a closed one is final.
    rf"\[(?i:{_TYPE_PREFIXES}|(?:{_TYPES})[_ \-]{_PARTIAL_NUMBER})?"
    # An unbracketed token, complete ones included: the next character decides them.
    rf"|{_TYPE_PREFIXES}|(?:{_TYPES})[_ ]{_PARTIAL_NUMBER}",
    re.ASCII,
)
# "[" + the longest type + separator + the longest number.
MAX_PARTIAL = 1 + len(_NAMES[0]) + 1 + _COUNTER_DIGITS


def could_become_token(tail: str) -> bool:
    """Whether more text after `tail` could still make it, or change, a TOKEN match."""
    return _PARTIAL.fullmatch(tail) is not None
