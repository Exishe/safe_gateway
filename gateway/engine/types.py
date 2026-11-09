"""Core types of the detection engine (ADR 0011)."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class EntityType(StrEnum):
    # The brief's minimum list, plus DATE (HIPAA Safe Harbor), CODENAME
    # (gazetteer entries) and LITERAL (placeholder-shaped input, ADR 0001).
    PERSON = "PERSON"
    ORG = "ORG"
    LOCATION = "LOCATION"
    ADDRESS = "ADDRESS"
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    DATE = "DATE"
    DATE_OF_BIRTH = "DATE_OF_BIRTH"
    SSN = "SSN"
    PASSPORT = "PASSPORT"
    DRIVER_LICENSE = "DRIVER_LICENSE"
    NATIONAL_ID = "NATIONAL_ID"
    CREDIT_CARD = "CREDIT_CARD"
    IBAN = "IBAN"
    BANK_ACCOUNT = "BANK_ACCOUNT"
    IP_ADDRESS = "IP_ADDRESS"
    MEDICAL_RECORD_NUMBER = "MEDICAL_RECORD_NUMBER"
    HEALTH_CONDITION = "HEALTH_CONDITION"
    CASE_NUMBER = "CASE_NUMBER"
    URL_WITH_TOKEN = "URL_WITH_TOKEN"  # noqa: S105  (an entity name, not a secret)
    CODENAME = "CODENAME"
    LITERAL = "LITERAL"


@dataclass(frozen=True)
class Span:
    """A detected value: text[start:end] in the original, unmodified text."""

    start: int
    end: int
    entity_type: EntityType
    score: float
    recognizer: str

    def __post_init__(self) -> None:
        if self.start < 0 or self.start >= self.end:
            msg = f"invalid span {self.start}..{self.end}"
            raise ValueError(msg)
        if not 0.0 <= self.score <= 1.0:
            msg = f"invalid span score {self.score}"
            raise ValueError(msg)

    @property
    def length(self) -> int:
        return self.end - self.start


class Recognizer(Protocol):
    name: str

    def find(self, text: str) -> list[Span]: ...
