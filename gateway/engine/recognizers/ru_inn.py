"""Russian taxpayer numbers (INN): 10 digits for organisations, 12 for people, checksummed."""

from gateway.engine.checksums import digits_of, inn_valid
from gateway.engine.digits import accepted_windows, digit_runs
from gateway.engine.types import EntityType, Span


class RuInnRecognizer:
    name = "ru_inn"

    def find(self, text: str) -> list[Span]:
        def is_inn(start: int, end: int) -> bool:
            return inn_valid(digits_of(text[start:end]))

        return [
            Span(start, end, EntityType.NATIONAL_ID, 1.0, self.name)
            for run in digit_runs(text)
            for start, end in accepted_windows(run, 10, 12, is_inn)
        ]


RU_INN = RuInnRecognizer()
