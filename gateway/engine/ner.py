"""Names, organisations, places and dates through Presidio and spaCy, strictly offline (ADR 0012).

Presidio is never given a model name, only a directory that `safeguard models
pull` unpacked: given a name, or a path that does not exist, its
SpacyNlpEngine calls spacy.cli.download. A missing directory therefore raises
ModelMissing before Presidio or spaCy is touched. The model is loaded here,
from the directory, and handed to Presidio already loaded, so Presidio's own
load(), which holds the download branch, never runs at all.
"""

import os
import re
from collections.abc import Callable, Sequence
from functools import cache
from pathlib import Path
from typing import Protocol

from gateway.engine.model_marker import Pin, is_installed
from gateway.engine.types import EntityType, Span

# Where `safeguard models pull` (gateway/cloud/models.py) unpacks each model, and the
# wheel digest its completion marker must record (ADR 0006, amended 2026-11-09). The
# engine only reads them; a test keeps both in step with the pinned downloads. Public
# digests, not secrets, hence the pragmas for detect-secrets.
SPACY_MODEL_PINS = {
    "en": Pin(
        "en_core_web_lg-3.8.0",
        # pragma: allowlist nextline secret
        "293e9547a655b25499198ab15a525b05b9407a75f10255e405e8c3854329ab63",
    ),
    "ru": Pin(
        "ru_core_news_lg-3.8.0",
        # pragma: allowlist nextline secret
        "90bd584be86772b647e1b15cdc62e09d3a7182284ee23da95e19c2bf71aa0189",
    ),
}
SPACY_MODEL_DIRS = {lang: pin.model_id for lang, pin in SPACY_MODEL_PINS.items()}

# spaCy label -> Presidio entity (ADR 0012 point 5). Presidio's default mapping lacks FAC.
SPACY_TO_PRESIDIO = {
    "PERSON": "PERSON",
    "PER": "PERSON",
    "ORG": "ORGANIZATION",
    "GPE": "LOCATION",
    "LOC": "LOCATION",
    "FAC": "LOCATION",
    "DATE": "DATE_TIME",
}
# Every other label of the en and ru models, dropped inside Presidio. TIME is dropped too,
# so Presidio's DATE_TIME below only ever means a spaCy DATE.
IGNORED_SPACY_LABELS = (
    "CARDINAL",
    "EVENT",
    "LANGUAGE",
    "LAW",
    "MONEY",
    "NORP",
    "ORDINAL",
    "PERCENT",
    "PRODUCT",
    "QUANTITY",
    "TIME",
    "WORK_OF_ART",
)
# Presidio entity -> ours. Anything else Presidio returns (NRP, say) is dropped.
PRESIDIO_TO_ENTITY = {
    "PERSON": EntityType.PERSON,
    "ORGANIZATION": EntityType.ORG,
    "LOCATION": EntityType.LOCATION,
    "DATE_TIME": EntityType.DATE,
}

SPACY_MAX_LENGTH = 1_000_000  # spaCy's default nlp.max_length
WINDOW = 100_000  # characters per model call: far below max_length, and bounded memory
OVERLAP = 200  # ADR 0004's starting value: a name cut by one window is whole in the next


class ModelMissing(Exception):  # noqa: N818  (name fixed by the Phase 1b plan)
    """The model directory is not installed; `safeguard models pull` installs it."""


def models_home() -> Path:
    """`$SAFEGUARD_HOME/models`, or `~/.safeguard/models`."""
    base = os.environ.get("SAFEGUARD_HOME")
    return (Path(base) if base else Path.home() / ".safeguard") / "models"


def _is_model(path: Path) -> bool:
    return (path / "config.cfg").is_file()


def _missing(lang: str, path: Path) -> ModelMissing:
    return ModelMissing(
        f"no complete, verified {lang} NER model at {path}; "
        f"run `safeguard models pull --only {lang}`"
    )


def model_dir(lang: str) -> Path:
    """The installed model directory for `lang`; ModelMissing if it is not installed.

    Installed means the completion marker of a verified pull for the current pin, the
    same test `safeguard models pull` and `models list` use; a bare or half-unpacked
    directory is missing.
    """
    if lang not in SPACY_MODEL_PINS:
        msg = f"no NER model is defined for language {lang!r}"
        raise ValueError(msg)
    pin = SPACY_MODEL_PINS[lang]
    path = models_home() / "spacy" / pin.model_id
    if not is_installed(path, pin):
        raise _missing(lang, path)
    return path


class Finding(Protocol):
    """One Presidio RecognizerResult, as far as we read it."""

    @property
    def entity_type(self) -> str: ...
    @property
    def start(self) -> int: ...
    @property
    def end(self) -> int: ...
    @property
    def score(self) -> float: ...


class Analyzer(Protocol):
    def analyze(
        self, text: str, language: str, entities: list[str] | None = None
    ) -> Sequence[Finding]: ...


AnalyzerFactory = Callable[[str, Path], Analyzer]


@cache
def build_analyzer(lang: str, path: Path) -> Analyzer:
    """Presidio holding only its spaCy recognizer, over the model in `path`; once per process."""
    # Imported here: they take seconds to load and matter only once a model is installed.
    import spacy  # noqa: PLC0415
    from presidio_analyzer import AnalyzerEngine, RecognizerRegistry  # noqa: PLC0415
    from presidio_analyzer.nlp_engine import NerModelConfiguration, SpacyNlpEngine  # noqa: PLC0415
    from presidio_analyzer.predefined_recognizers import SpacyRecognizer  # noqa: PLC0415

    # Only `ner` is loaded: in both models it has its own tok2vec, so its output is the same,
    # it runs about twice as fast, and the Russian lemmatizer (which needs pymorphy3) stays out.
    pipeline = spacy.util.load_config(path / "config.cfg")["nlp"]["pipeline"]
    nlp = spacy.load(path, exclude=[name for name in pipeline if name != "ner"])
    nlp_engine = SpacyNlpEngine(
        models=[{"lang_code": lang, "model_name": str(path)}],
        ner_model_configuration=NerModelConfiguration(
            model_to_presidio_entity_mapping=dict(SPACY_TO_PRESIDIO),
            labels_to_ignore=list(IGNORED_SPACY_LABELS),
        ),
    )
    nlp_engine.nlp = {lang: nlp}  # already loaded, so Presidio's load() never runs
    # A registry that is never empty: an empty one makes Presidio load all its own recognizers.
    recognizer = SpacyRecognizer(supported_language=lang, supported_entities=[*PRESIDIO_TO_ENTITY])
    registry = RecognizerRegistry(recognizers=[recognizer], supported_languages=[lang])
    return AnalyzerEngine(nlp_engine=nlp_engine, registry=registry, supported_languages=[lang])


_PARAGRAPH_BREAK = re.compile(r"\n[^\S\n]*\n")


def _check_window(size: int, overlap: int) -> None:
    if not 0 <= overlap < size // 2:
        msg = f"overlap must be from 0 to below half the window ({size // 2}), not {overlap}"
        raise ValueError(msg)


def windows(text: str, size: int, overlap: int) -> list[tuple[int, int]]:
    """(start, end) windows of at most `size` characters covering `text`, each overlapping the next.

    A window ends after the last paragraph break in its second half, else after the last
    whitespace there, else at `size`. The next one starts at the first whitespace within
    `overlap` characters before that end, so a name cut by one window is whole in the next.
    """
    _check_window(size, overlap)
    found: list[tuple[int, int]] = []
    start = 0
    while start < len(text):
        if len(text) - start <= size:
            found.append((start, len(text)))
            break
        end = _window_end(text, start + size // 2, start + size)
        found.append((start, end))
        start = _next_start(text, end - overlap, end)
    return found


def _window_end(text: str, earliest: int, latest: int) -> int:
    breaks = list(_PARAGRAPH_BREAK.finditer(text, earliest, latest))
    if breaks:
        return breaks[-1].end()
    for index in range(latest - 1, earliest - 1, -1):
        if text[index].isspace():
            return index + 1
    return latest


def _next_start(text: str, earliest: int, end: int) -> int:
    for index in range(earliest, end):
        if text[index].isspace():
            return index
    return earliest


class SpacyNerRecognizer:
    """PERSON, ORG, LOCATION and DATE from one language's spaCy model, through Presidio."""

    def __init__(
        self,
        lang: str,
        model_dir: Path,
        *,
        window: int = WINDOW,
        overlap: int = OVERLAP,
        build: AnalyzerFactory = build_analyzer,
    ) -> None:
        _check_window(window, overlap)
        if window > SPACY_MAX_LENGTH:
            msg = f"window {window} exceeds spaCy's max_length {SPACY_MAX_LENGTH}"
            raise ValueError(msg)
        if not _is_model(model_dir):
            raise _missing(lang, model_dir)
        self.lang = lang
        self.name = f"ner_{lang}"
        self.model_dir = model_dir
        self.window = window
        self.overlap = overlap
        self._build = build
        self._analyzer: Analyzer | None = None

    def find(self, text: str) -> list[Span]:
        if not text.strip():
            return []
        analyzer = self._loaded()
        # A span seen in two overlapping windows is reported once, at its best score.
        best: dict[tuple[int, int, EntityType], float] = {}
        for start, end in windows(text, self.window, self.overlap):
            findings = analyzer.analyze(
                text=text[start:end], language=self.lang, entities=[*PRESIDIO_TO_ENTITY]
            )
            for finding in findings:
                entity = PRESIDIO_TO_ENTITY.get(finding.entity_type)
                if entity is None or finding.end <= finding.start:
                    continue
                key = (start + finding.start, start + finding.end, entity)
                best[key] = max(finding.score, best.get(key, 0.0))
        return [
            Span(s, e, entity, score, self.name) for (s, e, entity), score in sorted(best.items())
        ]

    def _loaded(self) -> Analyzer:
        if self._analyzer is None:
            # Checked again: a directory removed since construction must never reach Presidio.
            if not _is_model(self.model_dir):
                raise _missing(self.lang, self.model_dir)
            self._analyzer = self._build(self.lang, self.model_dir)
        return self._analyzer
