"""URLs that carry a secret in their query string (reset links, signed URLs, API keys).

The query is split by hand: the engine may not import urllib (ADR 0006), even
for parsing.
"""

import re

from gateway.engine.types import EntityType, Span

SCORE = 0.9
_URL = re.compile(r"https?://[^\s<>\"'()\[\]{}]+", re.IGNORECASE)
_TRAILING = ".,;:!?"
_SECRET_NAMES = frozenset(
    {"token", "access_token", "api_key", "apikey", "key", "sig", "signature", "auth"}
    | {"password", "passwd", "secret", "session", "sessionid", "code"}
)
_SECRET_SUFFIXES = ("_token", "-token", "_key", "-key", "secret", "signature", "password")


def _is_secret_name(name: str) -> bool:
    return name in _SECRET_NAMES or name.endswith(_SECRET_SUFFIXES)


def _parameter_names(url: str) -> list[str]:
    query = url.partition("?")[2].partition("#")[0]
    return [part.partition("=")[0].lower() for part in query.split("&") if "=" in part]


class UrlTokenRecognizer:
    name = "url_token"

    def find(self, text: str) -> list[Span]:
        spans = []
        for match in _URL.finditer(text):
            start, end = match.span()
            while end > start and text[end - 1] in _TRAILING:
                end -= 1
            if any(_is_secret_name(n) for n in _parameter_names(text[start:end])):
                spans.append(Span(start, end, EntityType.URL_WITH_TOKEN, SCORE, self.name))
        return spans


URL_WITH_TOKEN = UrlTokenRecognizer()
