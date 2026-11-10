"""Payment card numbers: Luhn-valid, or Luhn-invalid in an exact card shape (ADR 0023)."""

import re

from gateway.engine.checksums import digits_of, luhn_valid
from gateway.engine.digits import accepted_windows, digit_runs
from gateway.engine.types import EntityType, Span

# Major networks start with 2 (Mastercard 2-series), 3 (Amex, JCB, Diners),
# 4 (Visa), 5 (Mastercard) or 6 (Discover, UnionPay). A Luhn-valid number
# starting with 0, 1, 7, 8 or 9 is far more likely an order or account number.
_ISSUER_DIGITS = "23456"
# Maestro also issues 12-digit numbers. Twelve digits is a common length for order,
# account and national numbers, so a 12-digit card needs a Maestro IIN (50, 56-69),
# Luhn, and a run of exactly twelve digits: never part of a longer number.
_MAESTRO_LENGTH = 12
_MAESTRO_PREFIXES = frozenset({"50", *(str(prefix) for prefix in range(56, 70))})

# Card-grouped numbers whose gaps are any run of one to three characters that are
# neither letters nor digits: a slash, a tab, three spaces, "- ", ". ", a line break and
# the next line's indent (ADR 0023 amended, 1d). Luhn and the issuer digit keep these
# precise; the grouping (fours with a short last group, or Amex 4-6-5) keeps tables of
# small numbers out. A comma or semicolon separates a list, never a card's groups.
_LOOSE_GAP = r"(?:[^\w,;]|_){1,3}"
_LOOSE_CARD = re.compile(
    r"(?<!\d)(?:"
    rf"\d{{4}}(?:{_LOOSE_GAP}\d{{4}}){{2,3}}(?:{_LOOSE_GAP}\d{{1,4}})?"
    rf"|\d{{4}}{_LOOSE_GAP}\d{{6}}{_LOOSE_GAP}\d{{4,5}}"
    rf"|\d{{4}}{_LOOSE_GAP}\d{{4}}{_LOOSE_GAP}\d{{5}}"
    r")(?!\d)"
)
_GROUP = re.compile(r"\d+")


def _loose_runs(text: str) -> list[list[tuple[int, int]]]:
    return [
        [
            (match.start() + g.start(), match.start() + g.end())
            for g in _GROUP.finditer(match.group())
        ]
        for match in _LOOSE_CARD.finditer(text)
    ]


class CreditCardRecognizer:
    name = "credit_card"

    def find(self, text: str) -> list[Span]:
        found: dict[tuple[int, int], None] = {}
        for run in [*digit_runs(text), *_loose_runs(text)]:
            whole = (run[0][0], run[-1][1])

            def is_card(start: int, end: int, whole: tuple[int, int] = whole) -> bool:
                digits = digits_of(text[start:end])
                if len(digits) == _MAESTRO_LENGTH:
                    maestro = digits[:2] in _MAESTRO_PREFIXES and (start, end) == whole
                    return maestro and luhn_valid(digits)
                return digits[0] in _ISSUER_DIGITS and luhn_valid(digits)

            found.update(dict.fromkeys(accepted_windows(run, _MAESTRO_LENGTH, 19, is_card)))
        return [Span(start, end, EntityType.CREDIT_CARD, 1.0, self.name) for start, end in found]


CREDIT_CARD = CreditCardRecognizer()


# ------------------------------------------------------------------ shape only (ADR 0023)

SHAPE_SCORE = 0.55  # above the default threshold 0.5, below a Luhn-valid card's 1.0
# Digit count -> first digits that make a card shape: 16 (Visa, Mastercard,
# Discover, UnionPay), 15 (Amex), 19 (Visa, Discover, UnionPay, Maestro).
_SHAPES = {16: "23456", 15: "3", 19: "3456"}
# A run of digit groups. Each gap is one space, dot or hyphen, or two spaces (justified
# PDF text); the gaps need not match (a space here, a hyphen there).
_GAP = r"(?:  ?|[.\-])"
_SHAPE_RUN = re.compile(rf"(?<!\w)\d+(?:{_GAP}\d+)*(?!\w)")
_FOUR = 4


def _is_shape(text: str, groups: list[tuple[int, int]]) -> bool:
    """Contiguous, or grouped by four with a last group of one to four digits; a card's
    length for its first digit."""
    sizes = [end - start for start, end in groups]
    if len(sizes) > 1 and any(size != _FOUR for size in sizes[:-1]):
        return False  # the last group may be short; a contiguous number is one group
    digits = text[groups[0][0] : groups[0][1]]
    return digits[0] in _SHAPES.get(sum(sizes), "")


def _short_edges(sizes: list[int]) -> tuple[int, int]:
    """How many groups of fewer than four digits open and close the run."""
    leading = next((i for i, size in enumerate(sizes) if size >= _FOUR), len(sizes))
    trailing = next((i for i, size in enumerate(reversed(sizes)) if size >= _FOUR), len(sizes))
    return leading, trailing


def _card_in_run(text: str, groups: list[tuple[int, int]]) -> tuple[int, int] | None:
    """The whole run if it is a card shape; else the run without the fewest neighbouring
    groups of one to three digits (a quantity before it, an expiry month after it)."""
    leading, trailing = _short_edges([end - start for start, end in groups])
    candidates = sorted(
        ((lead, trail) for lead in range(leading + 1) for trail in range(trailing + 1)),
        key=sum,
    )
    for lead, trail in candidates:
        kept = groups[lead : len(groups) - trail]
        if kept and _is_shape(text, kept):
            return kept[0][0], kept[-1][1]
    return None


class CreditCardShapeRecognizer:
    """Luhn-invalid numbers of an exact card shape: mistyped or synthetic cards.

    Scored 0.55, so they are masked by default; a customer who sees too many order
    numbers masked raises the CREDIT_CARD threshold above 0.55.
    """

    name = "credit_card_shape"

    def find(self, text: str) -> list[Span]:
        found = []
        for match in _SHAPE_RUN.finditer(text):
            groups = [
                (match.start() + g.start(), match.start() + g.end())
                for g in _GROUP.finditer(match.group())
            ]
            card = _card_in_run(text, groups)
            if card is not None:
                found.append(Span(*card, EntityType.CREDIT_CARD, SHAPE_SCORE, self.name))
        return found


CREDIT_CARD_SHAPE = CreditCardShapeRecognizer()
