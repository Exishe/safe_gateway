"""URLs that carry a secret in their query or fragment (reset links, signed URLs, API keys).

The URL is split by hand: the engine may not import urllib (ADR 0006), even
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
# Any name ending in these is a secret, whatever comes before ("clientsecret").
_ANYWHERE_SUFFIXES = ("secret", "signature", "password")
# These count after a word boundary only: "access_token", "resetToken", but not "monkey".
_WORD_SUFFIXES = ("token", "key", "secret", "signature", "password")
_PARAMETER_SEPARATOR = re.compile(r"[&?#]")
_ESCAPED_AMPERSAND = "amp;"  # "&amp;token=" copied from HTML


def _ends_with_word(name: str, suffix: str) -> bool:
    if not name.lower().endswith(suffix):
        return False
    cut = len(name) - len(suffix)
    if cut == 0 or name[cut - 1] in "_-":
        return True
    return name[cut - 1].islower() and name[cut].isupper()  # camelCase: "resetToken"


def _is_secret_name(name: str) -> bool:
    lowered = name.lower()
    return (
        lowered in _SECRET_NAMES
        or lowered.endswith(_ANYWHERE_SUFFIXES)
        or any(_ends_with_word(name, suffix) for suffix in _WORD_SUFFIXES)
    )


def _parameter_names(url: str) -> list[str]:
    """Names of `name=value` pairs in the query and the fragment (OAuth puts tokens after "#")."""
    _, _, rest = url.partition("://")
    names = []
    for part in _PARAMETER_SEPARATOR.split(rest)[1:]:  # [0] is host and path
        name, equals, _ = part.partition("=")
        while name.lower().startswith(_ESCAPED_AMPERSAND):
            name = name[len(_ESCAPED_AMPERSAND) :]
        if equals and name:
            names.append(name)
    return names


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
