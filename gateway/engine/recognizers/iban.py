"""IBANs: country code, check digits, then the country's exact length; mod-97 valid."""

import re

from gateway.engine.checksums import IBAN_LENGTHS, iban_valid
from gateway.engine.types import EntityType, Span

# No lookbehind: "IBANDE89..." is an IBAN glued to its label (ADR 0013). The
# country's exact length and the mod-97 check keep this from matching noise.
_START = re.compile(r"[A-Za-z]{2}\d{2}")
_SEPARATORS = ("\r\n", " ", "-", "\n")


def _is_iban_char(char: str) -> bool:
    return char.isascii() and char.isalnum()


def _separator_at(text: str, index: int) -> int:
    """Length of the one separator allowed between IBAN characters at `index`, or 0."""
    return next((len(s) for s in _SEPARATORS if text.startswith(s, index)), 0)


def _collect(text: str, start: int, length: int) -> list[int] | None:
    """Positions of the IBAN's characters, allowing one space, hyphen or line break between them."""
    positions: list[int] = []
    index, after_separator = start, False
    while index < len(text) and len(positions) < length:
        if _is_iban_char(text[index]):
            positions.append(index)
            after_separator = False
            index += 1
            continue
        separator = _separator_at(text, index)
        if not separator or not positions or after_separator:
            break
        after_separator = True
        index += separator
    if len(positions) != length:
        return None
    end = positions[-1] + 1
    if end < len(text) and _is_iban_char(text[end]):
        return None  # glued to more text: not this IBAN
    return positions


class IbanRecognizer:
    name = "iban"

    def find(self, text: str) -> list[Span]:
        spans = []
        for match in _START.finditer(text):
            length = IBAN_LENGTHS.get(match.group()[:2].upper())
            if length is None:
                continue
            positions = _collect(text, match.start(), length)
            if positions and iban_valid("".join(text[p] for p in positions)):
                spans.append(
                    Span(match.start(), positions[-1] + 1, EntityType.IBAN, 1.0, self.name)
                )
        return spans


IBAN = IbanRecognizer()
