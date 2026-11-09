"""Payment card numbers: 13 to 19 digits, Luhn-valid, issuer prefix 2 to 6."""

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
