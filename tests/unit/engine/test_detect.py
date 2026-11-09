import time

import pytest

from gateway.engine.detect import EngineConfig, detect
from gateway.engine.recognizers.gazetteer import Gazetteer
from gateway.engine.types import EntityType as E
from tests.strategies import SENTENCES

# One fake value per structured type (published examples or invalid-by-construction).
SAMPLES = {
    "anna.petrova@example.org": E.EMAIL,
    "+7 912 345-67-89": E.PHONE,
    "4111 1111 1111 1111": E.CREDIT_CARD,  # pragma: allowlist secret
    "DE89 3704 0044 0532 0130 00": E.IBAN,  # pragma: allowlist secret
    "123-45-6780": E.SSN,  # pragma: allowlist secret
    "7707083893": E.NATIONAL_ID,  # pragma: allowlist secret
    "112-233-445 95": E.NATIONAL_ID,  # pragma: allowlist secret
    "203.0.113.7": E.IP_ADDRESS,
    "https://files.example.com/d?token=Zq9Xv2Lm8Pw4": E.URL_WITH_TOKEN,
}


def found(text: str, config: EngineConfig | None = None) -> list[tuple[str, E]]:
    return [(text[s.start : s.end], s.entity_type) for s in detect(text, config=config)]


def test_every_type_in_one_text_is_found_once_with_exact_offsets() -> None:
    text = "Notes: " + "; then ".join(SAMPLES) + ". End."
    assert found(text) == list(SAMPLES.items())


def test_gazetteer_entries_are_detected_when_configured() -> None:
    config = EngineConfig(gazetteer=Gazetteer({"Bluebird": E.CODENAME}))
    assert found("Project Bluebird is late", config) == [("Bluebird", E.CODENAME)]


EN_DASH = chr(0x2013)
NBSP = chr(0x00A0)
ZWSP = chr(0x200B)


@pytest.mark.parametrize(
    ("entry", "written"),
    [
        (f"Smith{EN_DASH}Jones LLP", f"Smith{EN_DASH}Jones{NBSP}LLP"),
        ("Smith-Jones", "Smith-Jones"),
        (f"Smith{EN_DASH}Jones", "Smith-Jones"),
        ("Smith-Jones", f"Smi{ZWSP}th{EN_DASH}Jones"),
    ],
)
def test_gazetteer_entries_match_whatever_dash_or_space_is_written(
    entry: str, written: str
) -> None:
    config = EngineConfig(gazetteer=Gazetteer({entry: E.ORG}))
    assert found(f"Retained {written} today", config) == [(written, E.ORG)]


def test_values_are_reported_as_written_in_the_original_text() -> None:
    card = f"4111{NBSP}1111{ZWSP}1111{EN_DASH}1111"  # pragma: allowlist secret
    assert found(f"card {card}, thanks") == [(card, E.CREDIT_CARD)]


def test_thresholds_can_drop_low_confidence_types() -> None:
    bare_ssn = "ssn 123456780"  # pragma: allowlist secret
    assert found(bare_ssn) == [("123456780", E.SSN)]
    assert found(bare_ssn, EngineConfig(thresholds={E.SSN: 0.7})) == []


@pytest.mark.parametrize("text", ["", "   \n\t "])
def test_empty_and_blank_text(text: str) -> None:
    assert detect(text) == []


def test_one_megabyte_with_fifty_identifiers_is_fast_and_complete() -> None:
    values = list(SAMPLES)
    parts, inserted = [], 0
    size = 0
    while size < 1_000_000:
        sentence = SENTENCES[len(parts) % len(SENTENCES)]
        if len(parts) % 300 == 0 and inserted < 50:
            sentence = f"{sentence} Ref {values[inserted % len(values)]} here."
            inserted += 1
        parts.append(sentence)
        size += len(sentence) + 1
    text = " ".join(parts)
    assert inserted == 50

    started = time.perf_counter()
    spans = detect(text)
    elapsed = time.perf_counter() - started

    assert elapsed < 2.0, f"{elapsed:.2f}s for {len(text)} characters"
    detected = {(text[s.start : s.end], s.entity_type) for s in spans}
    assert set(SAMPLES.items()) <= detected
    assert len(spans) >= 50
