"""US Social Security numbers. There is no checksum, so the score reflects the format."""

from gateway.engine.checksums import digits_of
from gateway.engine.digits import accepted_windows, digit_runs, group_sizes
from gateway.engine.types import EntityType, Span

SCORE_GROUPED = 0.85  # NNN-NN-NNNN: the shape people write SSNs in
SCORE_BARE = 0.6  # nine bare digits could be many things; reported, but as "check this"

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


class UsSsnRecognizer:
    name = "us_ssn"

    def find(self, text: str) -> list[Span]:
        spans = []
        for run in digit_runs(text):

            def is_ssn(start: int, end: int) -> bool:
                sizes = group_sizes(text, start, end)
                return sizes in {(3, 2, 4), (9,)} and _possible(digits_of(text[start:end]))

            for start, end in accepted_windows(run, 9, 9, is_ssn):
                grouped = group_sizes(text, start, end) == (3, 2, 4)
                score = SCORE_GROUPED if grouped else SCORE_BARE
                spans.append(Span(start, end, EntityType.SSN, score, self.name))
        return spans


US_SSN = UsSsnRecognizer()
