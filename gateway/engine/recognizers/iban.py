"""IBANs: country code, check digits, then the country's exact length; mod-97 valid."""

import re

from gateway.engine.checksums import IBAN_LENGTHS, iban_valid
from gateway.engine.types import EntityType, Span

_START = re.compile(r"(?<!\w)[A-Za-z]{2}\d{2}")


def _is_iban_char(char: str) -> bool:
    return char.isascii() and char.isalnum()


def _collect(text: str, start: int, length: int) -> list[int] | None:
    """Positions of the IBAN's characters, allowing single spaces between them."""
    positions: list[int] = []
    index, after_space = start, False
    while index < len(text) and len(positions) < length:
        char = text[index]
        if _is_iban_char(char):
            positions.append(index)
            after_space = False
        elif char == " " and positions and not after_space:
            after_space = True
        else:
            break
        index += 1
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
