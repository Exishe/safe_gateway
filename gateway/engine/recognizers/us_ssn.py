"""US Social Security numbers. There is no checksum, so the score reflects the format."""

from gateway.engine.checksums import digits_of
from gateway.engine.digits import accepted_windows, digit_runs, group_sizes
from gateway.engine.types import EntityType, Span

SCORE_GROUPED = 0.85  # NNN-NN-NNNN: the shape people write SSNs in
SCORE_BARE = 0.6  # nine bare digits could be many things; reported, but as "check this"

# Group sizes an SSN is written in. One digit per box is a PDF comb field (ADR 0013).
_SHAPES = {(3, 2, 4): SCORE_GROUPED, (9,): SCORE_BARE, (1,) * 9: SCORE_BARE}

# Numbers published in advertising or never issued (SSA and Presidio's list).
_KNOWN_FAKE = frozenset({"078051120", "219099999", "123456789"})


def _possible(digits: str) -> bool:
    area, group, serial = digits[:3], digits[3:5], digits[5:]
    return (
        area not in {"000", "666"}
        and not area.startswith("9")
        and group != "00"
        and serial != "0000"
        and digits not in _KNOWN_FAKE
    )


def _glued_to_a_letter(text: str, start: int, end: int) -> bool:
    # Digit runs end at digits, not words (ADR 0013). Without a checksum, nine
    # digits inside a word ("ref123456789") are far more likely a code.
    return (start > 0 and text[start - 1].isalpha()) or (end < len(text) and text[end].isalpha())


class UsSsnRecognizer:
    name = "us_ssn"

    def find(self, text: str) -> list[Span]:
        def is_ssn(start: int, end: int) -> bool:
            return (
                group_sizes(text, start, end) in _SHAPES
                and _possible(digits_of(text[start:end]))
                and not _glued_to_a_letter(text, start, end)
            )

        return [
            Span(start, end, EntityType.SSN, _SHAPES[group_sizes(text, start, end)], self.name)
            for run in digit_runs(text)
            for start, end in accepted_windows(run, 9, 9, is_ssn)
        ]


US_SSN = UsSsnRecognizer()
