"""IPv4 and IPv6 addresses, validated by the standard library's `ipaddress`."""

import ipaddress
import re

from gateway.engine.types import EntityType, Span

SCORE = 0.9
# Maximal runs only: boundaries are checked by hand, because a regex lookahead
# would backtrack and hand back a truncated address such as "2001:db8::".
_RUN = re.compile(r"[0-9A-Fa-f:.]+")
# An IPv4 address inside a longer run: "192.0.2.10:8080", "db:192.0.2.10" (ADR 0013).
# Not part of a longer dotted number: "1.2.3.4.5" has none.
_IPV4_INSIDE = re.compile(r"(?<![\d.])\d{1,3}(?:\.\d{1,3}){3}(?!\.?\d)")


def _is_word_char(char: str) -> bool:
    return char.isalnum() or char == "_"


def _trimmed(text: str, start: int, end: int) -> tuple[int, int]:
    if text[start] == ":" and not text.startswith("::", start):
        start += 1  # "ip:2001:db8::1" - the colon belongs to the label
    while end > start and text[end - 1] in ".:" and not text.endswith("::", start, end):
        end -= 1  # sentence punctuation after the address
    return start, end


def _is_address(candidate: str) -> bool:
    if ":" not in candidate and "." not in candidate:
        return False
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return False
    return True


def _whole_run(text: str, run_start: int, run_end: int) -> tuple[int, int] | None:
    """The run as one address, or None if it is glued to a word or is not an address."""
    start, end = _trimmed(text, run_start, run_end)
    glued_before = start > 0 and _is_word_char(text[start - 1])
    glued_after = end < len(text) and _is_word_char(text[end])
    if glued_before or glued_after or not _is_address(text[start:end]):
        return None
    return start, end


def _ipv4_inside(text: str, run_start: int, run_end: int) -> list[tuple[int, int]]:
    run = text[run_start:run_end]
    return [
        (run_start + m.start(), run_start + m.end())
        for m in _IPV4_INSIDE.finditer(run)
        if _is_address(m.group())
    ]


class IpAddressRecognizer:
    name = "ip_address"

    def find(self, text: str) -> list[Span]:
        spans = []
        for match in _RUN.finditer(text):
            if ":" not in match.group() and "." not in match.group():
                continue  # a word fragment such as "face" or "de" holds no address
            whole = _whole_run(text, *match.span())
            # Fail-safe: a run that is not one address may still hold an IPv4 address,
            # after a port, a label ("node:"), or glued to a word.
            found = [whole] if whole else _ipv4_inside(text, *match.span())
            spans.extend(Span(s, e, EntityType.IP_ADDRESS, SCORE, self.name) for s, e in found)
        return spans


IP_ADDRESS = IpAddressRecognizer()
