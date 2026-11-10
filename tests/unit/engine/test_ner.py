"""Offline NER through Presidio and spaCy (ADR 0012).

Most tests replace Presidio with a stub analyzer and always run. Tests marked
`models` load the real spaCy models from `safeguard models pull` and are
skipped, with the reason, when the model directory is absent.
"""

import itertools
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from gateway.cloud.models import SPACY_MODELS, install_dir
from gateway.engine.model_marker import Pin, write_marker
from gateway.engine.ner import (
    IGNORED_SPACY_LABELS,
    OVERLAP,
    SPACY_MAX_LENGTH,
    SPACY_MODEL_DIRS,
    SPACY_MODEL_PINS,
    SPACY_TO_PRESIDIO,
    WINDOW,
    ModelMissing,
    SpacyNerRecognizer,
    model_dir,
    windows,
)
from gateway.engine.types import EntityType as E
from gateway.engine.types import Span

# ------------------------------------------------------------------ a stand-in for Presidio


@dataclass(frozen=True)
class Finding:
    entity_type: str
    start: int
    end: int
    score: float = 0.85


class CapitalisedWords:
    """Stands in for Presidio: every run of capitalised words is a PERSON."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    def analyze(
        self, text: str, language: str, entities: list[str] | None = None
    ) -> Sequence[Finding]:
        del language, entities
        self.seen.append(text)
        pattern = r"[A-Z][a-z]+(?: [A-Z][a-z]+)*"
        return [Finding("PERSON", m.start(), m.end()) for m in re.finditer(pattern, text)]


class Fixed:
    """Stands in for Presidio: returns the same findings for any text."""

    def __init__(self, findings: list[Finding]) -> None:
        self.findings = findings

    def analyze(
        self, text: str, language: str, entities: list[str] | None = None
    ) -> Sequence[Finding]:
        del text, language, entities
        return self.findings


@pytest.fixture
def fake_model(tmp_path: Path) -> Path:
    path = tmp_path / "xx_fake-1.0.0"
    path.mkdir()
    (path / "config.cfg").write_text("[nlp]\n", encoding="utf-8")
    return path


def builder(analyzer: object, calls: list[tuple[str, Path]] | None = None) -> Callable:
    def build(lang: str, path: Path) -> object:
        if calls is not None:
            calls.append((lang, path))
        return analyzer

    return build


def found(text: str, spans: list[Span]) -> list[tuple[str, E]]:
    return [(text[s.start : s.end], s.entity_type) for s in sorted(spans, key=lambda s: s.start)]


# ------------------------------------------------------------------ label mapping


def test_presidio_entities_map_to_ours_and_the_rest_is_dropped(fake_model: Path) -> None:
    text = "Anna Corp Oslo 2024 Nordic 42 years"
    stub = Fixed(
        [
            Finding("PERSON", 0, 4, 0.85),
            Finding("ORGANIZATION", 5, 9, 0.8),
            Finding("LOCATION", 10, 14),
            Finding("DATE_TIME", 15, 19),
            Finding("NRP", 20, 26),  # nationality or religion: not personal data on its own
            Finding("AGE", 27, 29),  # not a label we asked for: dropped, never guessed
        ]
    )
    recognizer = SpacyNerRecognizer("en", fake_model, build=builder(stub))

    spans = recognizer.find(text)

    assert found(text, spans) == [
        ("Anna", E.PERSON),
        ("Corp", E.ORG),
        ("Oslo", E.LOCATION),
        ("2024", E.DATE),
    ]
    assert {s.recognizer for s in spans} == {"ner_en"}
    assert [s.score for s in spans] == [0.85, 0.8, 0.85, 0.85]


def test_spacy_labels_follow_adr_0012() -> None:
    assert SPACY_TO_PRESIDIO == {
        "PERSON": "PERSON",
        "PER": "PERSON",
        "ORG": "ORGANIZATION",
        "GPE": "LOCATION",
        "LOC": "LOCATION",
        "FAC": "LOCATION",  # Presidio's default mapping lacks FAC
        "DATE": "DATE_TIME",
    }
    # TIME is dropped inside Presidio, so its DATE_TIME means a spaCy DATE only.
    assert {"TIME", "NORP"} <= set(IGNORED_SPACY_LABELS)
    assert not set(IGNORED_SPACY_LABELS) & set(SPACY_TO_PRESIDIO)


def test_the_recognizer_is_named_after_its_language(fake_model: Path) -> None:
    assert SpacyNerRecognizer("ru", fake_model, build=builder(Fixed([]))).name == "ner_ru"


# ------------------------------------------------------------------ windows


def test_a_short_text_is_one_window() -> None:
    assert windows("Zorvath Quillin", 100, 10) == [(0, 15)]
    assert windows("", 100, 10) == []


def test_a_window_ends_after_a_paragraph_break_when_there_is_one() -> None:
    text = "one two three\n\nfour five six seven eight nine ten"
    first_end = windows(text, 24, 4)[0][1]
    assert text[:first_end] == "one two three\n\n"


def test_a_window_ends_after_whitespace_when_there_is_no_paragraph() -> None:
    text = "alpha beta gamma delta epsilon zeta eta theta"
    first_end = windows(text, 20, 4)[0][1]
    assert text[:first_end] == "alpha beta gamma "


def test_a_text_without_whitespace_is_cut_at_the_window_size() -> None:
    assert windows("x" * 25, 10, 2)[0] == (0, 10)


def test_the_next_window_starts_inside_the_overlap_on_a_word_boundary() -> None:
    text = "alpha beta gamma delta epsilon zeta eta theta"
    (_, first_end), (second_start, _) = windows(text, 20, 8)[:2]
    assert first_end - 8 <= second_start < first_end
    assert text[second_start].isspace()


@given(
    text=st.text(alphabet="ab \n", max_size=400),
    size=st.integers(min_value=10, max_value=80),
    data=st.data(),
)
def test_windows_cover_the_text_in_order_and_stay_within_the_size(
    text: str, size: int, data: st.DataObject
) -> None:
    overlap = data.draw(st.integers(min_value=0, max_value=size // 2 - 1))
    spans = windows(text, size, overlap)
    if not text:
        assert spans == []
        return
    assert spans[0][0] == 0
    assert spans[-1][1] == len(text)
    for start, end in spans:
        assert 0 < end - start <= size
    for (start, end), (next_start, next_end) in itertools.pairwise(spans):
        assert start < next_start <= end  # no gap, always progress
        assert end < next_end


def test_an_overlap_of_half_the_window_or_more_is_refused(fake_model: Path) -> None:
    with pytest.raises(ValueError, match="overlap"):
        windows("text", 10, 5)
    with pytest.raises(ValueError, match="overlap"):
        SpacyNerRecognizer("en", fake_model, window=100, overlap=60, build=builder(Fixed([])))


def test_a_window_larger_than_spacy_max_length_is_refused(fake_model: Path) -> None:
    assert WINDOW < SPACY_MAX_LENGTH
    assert 0 < OVERLAP < WINDOW // 2
    with pytest.raises(ValueError, match="max_length"):
        SpacyNerRecognizer("en", fake_model, window=SPACY_MAX_LENGTH + 1, build=builder(Fixed([])))


FILLER = "the quick brown fox jumps over the lazy dog and runs back home again. "


def test_names_on_both_sides_of_a_window_edge_are_found_at_exact_offsets(
    fake_model: Path,
) -> None:
    # A name ends the first window, a name straddles the cut, a name opens the next window.
    text = FILLER * 3 + "Zorvath Quillin met " + "Mirela Okonkwo" + " and Taviel Brandt. " + FILLER
    stub = CapitalisedWords()
    recognizer = SpacyNerRecognizer("en", fake_model, window=230, overlap=60, build=builder(stub))

    spans = recognizer.find(text)

    assert len(stub.seen) > 1, "the text must span several windows"
    names = {"Zorvath Quillin", "Mirela Okonkwo", "Taviel Brandt"}
    whole = {text[s.start : s.end] for s in spans} & names
    assert whole == names
    for span in spans:  # every offset maps back into the original text
        assert text[span.start : span.end] in {*names, "Zorvath", "Mirela"}


def test_a_name_found_in_two_overlapping_windows_is_reported_once(fake_model: Path) -> None:
    text = FILLER * 3 + "Mirela Okonkwo " + FILLER * 3
    recognizer = SpacyNerRecognizer(
        "en", fake_model, window=260, overlap=120, build=builder(CapitalisedWords())
    )

    spans = recognizer.find(text)

    assert [text[s.start : s.end] for s in spans].count("Mirela Okonkwo") == 1


def test_a_megabyte_text_reaches_presidio_only_in_windows_below_max_length(
    fake_model: Path,
) -> None:
    text = (FILLER * 21_000)[:1_400_000]
    stub = CapitalisedWords()

    SpacyNerRecognizer("en", fake_model, build=builder(stub)).find(text)

    assert len(stub.seen) >= len(text) // WINDOW
    assert max(map(len, stub.seen)) <= WINDOW < SPACY_MAX_LENGTH


# ------------------------------------------------------------------ lazy loading


def test_presidio_is_built_lazily_and_once(fake_model: Path) -> None:
    calls: list[tuple[str, Path]] = []
    recognizer = SpacyNerRecognizer("en", fake_model, build=builder(CapitalisedWords(), calls))
    assert calls == []

    assert recognizer.find("  \n ") == []
    assert calls == []

    recognizer.find("Zorvath Quillin")
    recognizer.find("Mirela Okonkwo")
    assert calls == [("en", fake_model)]


# ------------------------------------------------------------------ missing models never download


@pytest.fixture
def no_presidio_or_spacy(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record any attempt to build Presidio, load a spaCy model or download one."""
    import presidio_analyzer  # noqa: PLC0415  (only this fixture needs it)
    import presidio_analyzer.nlp_engine  # noqa: PLC0415
    import spacy  # noqa: PLC0415
    import spacy.cli  # noqa: PLC0415

    attempts: list[str] = []

    def refuse(label: str) -> Callable[..., None]:
        def record(*_args: object, **_kwargs: object) -> None:
            attempts.append(label)
            raise AssertionError(label)

        return record

    monkeypatch.setattr(presidio_analyzer.AnalyzerEngine, "__init__", refuse("AnalyzerEngine"))
    monkeypatch.setattr(
        presidio_analyzer.nlp_engine.SpacyNlpEngine, "__init__", refuse("SpacyNlpEngine")
    )
    monkeypatch.setattr(spacy, "load", refuse("spacy.load"))
    monkeypatch.setattr(spacy.cli, "download", refuse("spacy.cli.download"))
    return attempts


def test_model_dir_raises_model_missing_naming_the_pull_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SAFEGUARD_HOME", str(tmp_path))
    with pytest.raises(ModelMissing, match=r"safeguard models pull --only ru"):
        model_dir("ru")


def test_model_dir_returns_the_pulled_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SAFEGUARD_HOME", str(tmp_path))
    expected = tmp_path / "models" / "spacy" / SPACY_MODEL_DIRS["en"]
    expected.mkdir(parents=True)
    (expected / "config.cfg").write_text("[nlp]\n", encoding="utf-8")
    write_marker(expected, SPACY_MODEL_PINS["en"])
    assert model_dir("en") == expected


@pytest.mark.parametrize("marker", [None, "0" * 64])
def test_model_dir_refuses_an_unmarked_or_outdated_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, marker: str | None
) -> None:
    # What an interrupted pull leaves (M3): the model files, but no marker for the current pin.
    monkeypatch.setenv("SAFEGUARD_HOME", str(tmp_path))
    path = tmp_path / "models" / "spacy" / SPACY_MODEL_DIRS["en"]
    path.mkdir(parents=True)
    (path / "config.cfg").write_text("[nlp]\n", encoding="utf-8")
    if marker is not None:
        write_marker(path, Pin(SPACY_MODEL_DIRS["en"], marker))
    with pytest.raises(ModelMissing, match=r"safeguard models pull --only en"):
        model_dir("en")


def test_model_dir_refuses_a_language_without_a_model() -> None:
    with pytest.raises(ValueError, match="hy"):
        model_dir("hy")


def test_engine_and_models_pull_agree_on_where_models_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SAFEGUARD_HOME", str(tmp_path))
    assert set(SPACY_MODEL_DIRS) == set(SPACY_MODELS) == set(SPACY_MODEL_PINS)
    for lang, spec in SPACY_MODELS.items():
        assert SPACY_MODEL_DIRS[lang] == spec.directory_name
        # The engine accepts exactly the marker that `safeguard models pull` writes.
        assert SPACY_MODEL_PINS[lang] == spec.pin
        with pytest.raises(ModelMissing) as missing:
            model_dir(lang)
        assert str(install_dir(spec)) in str(missing.value)


def test_a_missing_directory_raises_before_presidio_or_spacy_is_touched(
    tmp_path: Path, no_presidio_or_spacy: list[str]
) -> None:
    # A path that does not exist would make Presidio call spacy.cli.download (ADR 0012).
    with pytest.raises(ModelMissing, match="safeguard models pull"):
        SpacyNerRecognizer("en", tmp_path / "en_core_web_lg-3.8.0")
    assert no_presidio_or_spacy == []


def test_a_directory_removed_after_construction_raises_before_presidio_is_built(
    fake_model: Path, no_presidio_or_spacy: list[str]
) -> None:
    recognizer = SpacyNerRecognizer("en", fake_model)
    (fake_model / "config.cfg").unlink()

    with pytest.raises(ModelMissing):
        recognizer.find("Zorvath Quillin")
    assert no_presidio_or_spacy == []


# ------------------------------------------------------------------ the real models


@pytest.fixture(scope="module")
def en_ner() -> SpacyNerRecognizer:
    try:
        return SpacyNerRecognizer("en", model_dir("en"))
    except ModelMissing as error:
        pytest.skip(f"English model absent: {error}")


@pytest.fixture(scope="module")
def ru_ner() -> SpacyNerRecognizer:
    try:
        return SpacyNerRecognizer("ru", model_dir("ru"))
    except ModelMissing as error:
        pytest.skip(f"Russian model absent: {error}")


def positions(text: str, spans: list[Span]) -> list[tuple[int, int, E, str]]:
    ordered = sorted(spans, key=lambda s: s.start)
    return [(s.start, s.end, s.entity_type, text[s.start : s.end]) for s in ordered]


@pytest.mark.models
def test_english_person_org_location_and_date(en_ner: SpacyNerRecognizer) -> None:
    text = "Zorvath Quillin joined Brightwater Analytics in Portland last March."
    assert positions(text, en_ner.find(text)) == [
        (0, 15, E.PERSON, "Zorvath Quillin"),
        (23, 44, E.ORG, "Brightwater Analytics"),
        (48, 56, E.LOCATION, "Portland"),
        (57, 67, E.DATE, "last March"),
    ]


@pytest.mark.models
def test_english_facility_is_a_location_and_time_is_dropped(en_ner: SpacyNerRecognizer) -> None:
    text = "Zorvath Quillin met Mirela Okonkwo at 10:30 am on 3 May 2024 near the Heathrow Airport."
    assert positions(text, en_ner.find(text)) == [
        (0, 15, E.PERSON, "Zorvath Quillin"),
        (20, 34, E.PERSON, "Mirela Okonkwo"),
        (50, 60, E.DATE, "3 May 2024"),
        (66, 86, E.LOCATION, "the Heathrow Airport"),
    ]


@pytest.mark.models
def test_russian_person_org_and_location(ru_ner: SpacyNerRecognizer) -> None:
    text = "Договор подписал Зорват Квиллин, директор ООО «Халверн Биотех», в Новосибирске."
    assert positions(text, ru_ner.find(text)) == [
        (17, 31, E.PERSON, "Зорват Квиллин"),
        (42, 62, E.ORG, "ООО «Халверн Биотех»"),
        (66, 78, E.LOCATION, "Новосибирске"),
    ]


@pytest.mark.models
def test_real_model_finds_names_on_both_sides_of_a_window_edge(
    en_ner: SpacyNerRecognizer,
) -> None:
    text = (
        "The board met on Tuesday to review the annual report in detail. " * 4
        + "Later that week Zorvath Quillin called Mirela Okonkwo about the contract. "
        + "The board met on Tuesday to review the annual report in detail. " * 4
    )
    # A second recognizer on the same directory reuses the loaded model (once per process).
    small = SpacyNerRecognizer("en", en_ner.model_dir, window=300, overlap=120)

    spans = small.find(text)

    assert len(windows(text, 300, 120)) > 2
    names = {(text[s.start : s.end], s.entity_type) for s in spans}
    assert {("Zorvath Quillin", E.PERSON), ("Mirela Okonkwo", E.PERSON)} <= names


@pytest.mark.models
@pytest.mark.parametrize("lang", ["en", "ru"])
def test_every_label_of_the_installed_model_is_mapped_or_ignored(lang: str) -> None:
    try:
        path = model_dir(lang)
    except ModelMissing as error:
        pytest.skip(f"model absent: {error}")
    labels = json.loads((path / "meta.json").read_text(encoding="utf-8"))["labels"]["ner"]
    assert set(labels) <= set(SPACY_TO_PRESIDIO) | set(IGNORED_SPACY_LABELS)
