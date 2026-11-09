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
    """Review Focus 5 of Phase 1a: the structured recognizers alone, so NER is off here.

    spaCy NER over 1 MB takes several seconds; its throughput is measured and
    reported, not asserted (Phase 1b).
    """
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
    spans = detect(text, config=EngineConfig(ner="off"))
    elapsed = time.perf_counter() - started

    assert elapsed < 2.0, f"{elapsed:.2f}s for {len(text)} characters"
    detected = {(text[s.start : s.end], s.entity_type) for s in spans}
    assert set(SAMPLES.items()) <= detected
    assert len(spans) >= 50


# ------------------------------------------------------------------ NER routing (Phase 1b)

import logging  # noqa: E402
import re  # noqa: E402
from pathlib import Path  # noqa: E402

import gateway.engine.detect as detect_module  # noqa: E402
from gateway.engine.ner import SPACY_MODEL_DIRS, ModelMissing  # noqa: E402
from gateway.engine.types import Span  # noqa: E402

EN = "Zorvath Quillin joined Brightwater Analytics in Portland last March."
RU = "Договор подписал Зорват Квиллин, директор ООО «Халверн Биотех», в Новосибирске."
HY = "Զորվաթ Քվիլլին, հեռ. +374 77 123456, zorvath@example.am"
MIXED = f"{EN}\n\n{RU}\n\n{HY}\n"


class RecordingNer:
    """Stands in for SpacyNerRecognizer: each capitalised word is a PERSON; records its input."""

    calls: list[tuple[str, str]]

    def __init__(self, lang: str, model_dir: Path) -> None:
        assert (model_dir / "config.cfg").is_file()
        self.lang = lang
        self.name = f"ner_{lang}"

    def find(self, text: str) -> list[Span]:
        RecordingNer.calls.append((self.lang, text))
        words = re.finditer(r"[A-ZА-ЯЁ][a-zа-яё]+", text)
        return [Span(m.start(), m.end(), E.PERSON, 0.85, self.name) for m in words]


@pytest.fixture
def fake_ner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Installed-looking model directories and a recording NER in place of Presidio."""
    monkeypatch.setenv("SAFEGUARD_HOME", str(tmp_path))
    for directory in SPACY_MODEL_DIRS.values():
        path = tmp_path / "models" / "spacy" / directory
        path.mkdir(parents=True)
        (path / "config.cfg").write_text("[nlp]\n", encoding="utf-8")
    monkeypatch.setattr(RecordingNer, "calls", [], raising=False)
    monkeypatch.setattr(detect_module, "SpacyNerRecognizer", RecordingNer)
    return RecordingNer.calls


def by_recognizer(text: str, spans: list[Span], name: str) -> list[str]:
    return [text[s.start : s.end] for s in spans if s.recognizer == name]


def test_each_paragraph_goes_to_the_model_of_its_script(fake_ner: list[tuple[str, str]]) -> None:
    spans = detect(MIXED)

    assert fake_ner == [("en", EN), ("ru", RU)]  # Armenian has no model (ADR 0012 point 4)
    assert by_recognizer(MIXED, spans, "ner_en") == [
        "Zorvath",
        "Quillin",
        "Brightwater",
        "Analytics",
        "Portland",
        "March",
    ]
    assert by_recognizer(MIXED, spans, "ner_ru") == [
        "Договор",
        "Зорват",
        "Квиллин",
        "Халверн",
        "Биотех",
        "Новосибирске",
    ]


def test_lang_forces_one_model_for_the_whole_text(fake_ner: list[tuple[str, str]]) -> None:
    text = f"{EN}\n\n{RU}"
    detect(text, lang="ru")
    assert fake_ner == [("ru", text)]


@pytest.mark.parametrize("lang", ["hy", "und"])
def test_languages_without_a_model_get_no_ner(fake_ner: list[tuple[str, str]], lang: str) -> None:
    detect(MIXED, lang=lang)
    assert fake_ner == []


def test_ner_off_loads_no_model(fake_ner: list[tuple[str, str]]) -> None:
    spans = detect(MIXED, config=EngineConfig(ner="off"))
    assert fake_ner == []
    assert not [s for s in spans if s.recognizer.startswith("ner_")]


def test_an_unknown_language_is_refused() -> None:
    with pytest.raises(ValueError, match="de"):
        detect("Guten Tag", lang="de")


def test_ner_spans_map_back_through_the_cleaned_view(fake_ner: list[tuple[str, str]]) -> None:
    # Invisible and no-break characters before and inside the name shift the cleaned offsets.
    text = f"Ref{ZWSP}{ZWSP}: card on file.\n\nMet Zorvath{NBSP}Quil{ZWSP}lin today."

    spans = detect(text)

    # Both paragraphs are English, so the model reads them as one segment of the cleaned text.
    assert fake_ner == [("en", "Ref: card on file.\n\nMet Zorvath Quillin today.")]
    assert by_recognizer(text, spans, "ner_en") == ["Ref", "Met", "Zorvath", f"Quil{ZWSP}lin"]


def test_armenian_text_returns_structured_and_gazetteer_hits(
    fake_ner: list[tuple[str, str]],
) -> None:
    config = EngineConfig(gazetteer=Gazetteer({"Զորվաթ Քվիլլին": E.PERSON}))
    assert found(HY, config) == [
        ("Զորվաթ Քվիլլին", E.PERSON),
        ("+374 77 123456", E.PHONE),
        ("zorvath@example.am", E.EMAIL),
    ]
    assert fake_ner == []


def test_missing_models_are_skipped_with_one_warning_per_language(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(detect_module, "_WARNED", set())
    text = f"{EN} Mail anna.petrova@example.org\n\n{RU}"

    with caplog.at_level(logging.WARNING):
        first = found(text)
        second = found(text)

    assert first == second == [("anna.petrova@example.org", E.EMAIL)]
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2
    assert "safeguard models pull --only en" in warnings[0]
    assert "safeguard models pull --only ru" in warnings[1]


def test_a_forced_language_without_its_model_raises() -> None:
    with pytest.raises(ModelMissing, match="safeguard models pull --only en"):
        detect(EN, lang="en")


# ------------------------------------------------------------------ the real models


@pytest.fixture
def real_models() -> None:
    for lang in SPACY_MODEL_DIRS:
        try:
            detect_module.model_dir(lang)
        except ModelMissing as error:
            pytest.skip(f"model absent: {error}")


@pytest.mark.models
@pytest.mark.usefixtures("real_models")
def test_real_models_route_a_mixed_english_russian_armenian_text() -> None:
    config = EngineConfig(gazetteer=Gazetteer({"Զորվաթ Քվիլլին": E.PERSON}))

    spans = detect(MIXED, config=config)

    assert [(MIXED[s.start : s.end], s.entity_type, s.recognizer) for s in spans] == [
        ("Zorvath Quillin", E.PERSON, "ner_en"),
        ("Brightwater Analytics", E.ORG, "ner_en"),
        ("Portland", E.LOCATION, "ner_en"),
        ("last March", E.DATE, "ner_en"),
        ("Зорват Квиллин", E.PERSON, "ner_ru"),
        ("ООО «Халверн Биотех»", E.ORG, "ner_ru"),
        ("Новосибирске", E.LOCATION, "ner_ru"),
        ("Զորվաթ Քվիլլին", E.PERSON, "gazetteer"),
        ("+374 77 123456", E.PHONE, "phone"),
        ("zorvath@example.am", E.EMAIL, "email"),
    ]
