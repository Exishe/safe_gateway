"""Russian insurance numbers (SNILS): 11 digits, usually 123-456-789 01, checksummed."""

from gateway.engine.checksums import digits_of, snils_valid
from gateway.engine.digits import accepted_windows, digit_runs
from gateway.engine.types import EntityType, Span


class RuSnilsRecognizer:
    name = "ru_snils"

    def find(self, text: str) -> list[Span]:
        def is_snils(start: int, end: int) -> bool:
            return snils_valid(digits_of(text[start:end]))

        return [
            Span(start, end, EntityType.NATIONAL_ID, 1.0, self.name)
            for run in digit_runs(text)
            for start, end in accepted_windows(run, 11, 11, is_snils)
        ]


RU_SNILS = RuSnilsRecognizer()
