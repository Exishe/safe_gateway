"""Email addresses, including internationalised local parts and domains."""

import re

from gateway.engine.types import EntityType, Span

SCORE = 0.95
_EMAIL = re.compile(
    # Start after a character that cannot be in an address, or after an underscore
    # that opens markdown italics ("_anna@example.com_"); never on the underscore.
    r"(?:(?<![\w.%+'-])|(?<=_))(?!_)"
    r"[\w.%+'-]+@[\w-]+(?:\.[\w-]+)*"
    # The top-level domain: letters, or punycode for a non-Latin one (.xn--p1ai, Russia's .rf).
    r"\.(?:[Xx][Nn]--[A-Za-z0-9-]+|[^\W\d_]{2,})"
    # End before anything but a letter or digit, or a hyphen joined to more of a name;
    # "_" (markdown) and "--" (a dash) may follow.
    r"(?![^\W_])(?!-\w)"
)


class EmailRecognizer:
    name = "email"

    def find(self, text: str) -> list[Span]:
        return [
            Span(m.start(), m.end(), EntityType.EMAIL, SCORE, self.name)
            for m in _EMAIL.finditer(text)
        ]


EMAIL = EmailRecognizer()
