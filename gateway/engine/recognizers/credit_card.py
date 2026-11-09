"""Payment card numbers: Luhn-valid, or Luhn-invalid in an exact card shape (ADR 0023)."""

import re

from gateway.engine.checksums import digits_of, luhn_valid
from gateway.engine.digits import accepted_windows, digit_runs
from gateway.engine.types import EntityType, Span

# Major networks start with 2 (Mastercard 2-series), 3 (Amex, JCB, Diners),
# 4 (Visa), 5 (Mastercard) or 6 (Discover, UnionPay). A Luhn-valid number
# starting with 0, 1, 7, 8 or 9 is far more likely an order or account number.
_ISSUER_DIGITS = "23456"


class CreditCardRecognizer:
    name = "credit_card"

    def find(self, text: str) -> list[Span]:
        def is_card(start: int, end: int) -> bool:
            digits = digits_of(text[start:end])
            return digits[0] in _ISSUER_DIGITS and luhn_valid(digits)

        return [
            Span(start, end, EntityType.CREDIT_CARD, 1.0, self.name)
            for run in digit_runs(text)
            for start, end in accepted_windows(run, 13, 19, is_card)
        ]


CREDIT_CARD = CreditCardRecognizer()


# ------------------------------------------------------------------ shape only (ADR 0023)

SHAPE_SCORE = 0.55  # above the default threshold 0.5, below a Luhn-valid card's 1.0
# Digit count -> first digits that make a card shape: 16 (Visa, Mastercard,
# Discover, UnionPay), 15 (Amex), 19 (Visa, Discover, UnionPay, Maestro).
_SHAPES = {16: "23456", 15: "3", 19: "3456"}
# A whole number, never part of a longer run: contiguous, or grouped by four with a last
# group of one to four digits. Each gap is one space, dot or hyphen, or two spaces
# (justified PDF text); the gaps need not match (a space here, a hyphen there).
_GAP = r"(?:  ?|[.\-])"
_CARD_SHAPE = re.compile(
    r"(?<!\w)(?<!\d[ .\-])(?<!\d  )"
    rf"(?:\d{{15,19}}|\d{{4}}{_GAP}\d{{4}}(?:{_GAP}\d{{4}})*(?:{_GAP}\d{{1,3}})?)"
    r"(?!\w)(?![ .\-]\d)(?!  \d)"
)


class CreditCardShapeRecognizer:
    """Luhn-invalid numbers of an exact card shape: mistyped or synthetic cards.

    Scored 0.55, so they are masked by default; a customer who sees too many order
    numbers masked raises the CREDIT_CARD threshold above 0.55.
    """

    name = "credit_card_shape"

    def find(self, text: str) -> list[Span]:
        found = []
        for match in _CARD_SHAPE.finditer(text):
            digits = digits_of(match.group())
            if digits[0] in _SHAPES.get(len(digits), ""):
                found.append(
                    Span(match.start(), match.end(), EntityType.CREDIT_CARD, SHAPE_SCORE, self.name)
                )
        return found


CREDIT_CARD_SHAPE = CreditCardShapeRecognizer()
