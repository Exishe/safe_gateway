"""Email addresses, including internationalised local parts and domains."""

import re

from gateway.engine.types import EntityType, Span

SCORE = 0.95
_EMAIL = re.compile(r"(?<![\w.%+'-])[\w.%+'-]+@[\w-]+(?:\.[\w-]+)*\.[^\W\d_]{2,}(?![\w-])")


class EmailRecognizer:
    name = "email"

    def find(self, text: str) -> list[Span]:
        return [
            Span(m.start(), m.end(), EntityType.EMAIL, SCORE, self.name)
            for m in _EMAIL.finditer(text)
        ]


EMAIL = EmailRecognizer()
