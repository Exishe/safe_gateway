"""IPv4 and IPv6 addresses, validated by the standard library's `ipaddress`."""

import ipaddress
import re

from gateway.engine.types import EntityType, Span

SCORE = 0.9
# Maximal runs only: boundaries are checked by hand, because a regex lookahead
# would backtrack and hand back a truncated address such as "2001:db8::".
_RUN = re.compile(r"[0-9A-Fa-f:.]+")


def _is_word_char(char: str) -> bool:
    return char.isalnum() or char == "_"


def _trimmed(text: str, start: int, end: int) -> tuple[int, int]:
    if text[start] == ":" and not text.startswith("::", start):
        start += 1  # "ip:2001:db8::1" - the colon belongs to the label
    while end > start and text[end - 1] in ".:" and not text.endswith("::", start, end):
        end -= 1  # sentence punctuation after the address
    return start, end


class IpAddressRecognizer:
    name = "ip_address"

    def find(self, text: str) -> list[Span]:
        spans = []
        for match in _RUN.finditer(text):
            start, end = match.span()
            glued_before = start > 0 and _is_word_char(text[start - 1]) and text[start] != ":"
            glued_after = end < len(text) and _is_word_char(text[end])
            if glued_before or glued_after:
                continue  # part of a longer token (hashes, words, version suffixes)
            start, end = _trimmed(text, start, end)
            candidate = text[start:end]
            if ":" not in candidate and "." not in candidate:
                continue
            try:
                ipaddress.ip_address(candidate)
            except ValueError:
                continue
            spans.append(Span(start, end, EntityType.IP_ADDRESS, SCORE, self.name))
        return spans


IP_ADDRESS = IpAddressRecognizer()
