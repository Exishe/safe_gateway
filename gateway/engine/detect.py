"""`detect(text)`: run every recognizer, then merge (ADR 0011, ADR 0012)."""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from gateway.engine.gliner_ner import DEFAULT_THRESHOLD, GlinerRecognizer, gliner_model_dir
from gateway.engine.merge import defer, merge
from gateway.engine.ner import SPACY_MODEL_DIRS, ModelMissing, SpacyNerRecognizer, model_dir
from gateway.engine.recognizers.credit_card import CREDIT_CARD, CREDIT_CARD_SHAPE
from gateway.engine.recognizers.dates import DATE
from gateway.engine.recognizers.email import EMAIL
from gateway.engine.recognizers.gazetteer import Gazetteer
from gateway.engine.recognizers.iban import IBAN
from gateway.engine.recognizers.id_documents import (
    ID_CARD_SHAPE,
    ID_DOCUMENT_CONTEXT,
    PASSPORT_SHAPE,
)
from gateway.engine.recognizers.ip_address import IP_ADDRESS
from gateway.engine.recognizers.phone import DEFAULT_REGIONS, PhoneRecognizer
from gateway.engine.recognizers.phone_shape import PHONE_CONTEXT, PHONE_SHAPE
from gateway.engine.recognizers.ru_inn import RU_INN
from gateway.engine.recognizers.ru_snils import RU_SNILS
from gateway.engine.recognizers.url_token import URL_WITH_TOKEN
from gateway.engine.recognizers.us_ssn import US_SSN
from gateway.engine.script import lines_in_other_scripts, lines_with_words_of, segments
from gateway.engine.textview import TextView, clean_view, ner_view
from gateway.engine.types import EntityType, Recognizer, Span

_log = logging.getLogger(__name__)

# On a tie (same length, same score) the recognizer listed first wins. The
# customer's own list comes first; checksummed types before shape-only ones, and
# the shape and keyword rules of ADR 0023 after every validating recognizer.
PRIORITY = (
    "gazetteer",
    "iban",
    "credit_card",
    "ru_inn",
    "ru_snils",
    "email",
    "url_token",
    "phone",
    "ip_address",
    "us_ssn",
    "date",
    "phone_shape",
    "phone_context",
    "id_document_context",
    "passport_shape",
    "id_card_shape",
    "credit_card_shape",
    "ner_en",
    "ner_ru",
    "gliner",
)
# Shape rules never stretch a value the validating recognizer found (ADR 0023).
DEFERS_TO = {"phone_shape": "phone", "phone_context": "phone"}
# "auto" routes each paragraph by script (gateway/engine/script.py); the rest force one.
# Armenian ("hy") and undetermined ("und") text go to GLiNER (ADR 0015).
LANGUAGES = ("auto", *SPACY_MODEL_DIRS, "hy", "und")


@dataclass(frozen=True)
class EngineConfig:
    thresholds: Mapping[EntityType, float] = field(default_factory=dict)
    phone_regions: Sequence[str] = DEFAULT_REGIONS
    gazetteer: Gazetteer | None = None
    # "off" turns every model off, GLiNER included.
    ner: Literal["auto", "off"] = "auto"
    # "auto": GLiNER reads text with no spaCy model (hy, und). "all": also en and ru, beside
    # spaCy, for ADDRESS and DATE_OF_BIRTH, which spaCy does not label.
    gliner: Literal["auto", "all", "off"] = "auto"
    gliner_threshold: float = DEFAULT_THRESHOLD


def _recognizers(config: EngineConfig) -> list[Recognizer]:
    recognizers: list[Recognizer] = [
        IBAN,
        CREDIT_CARD,
        RU_INN,
        RU_SNILS,
        US_SSN,
        EMAIL,
        URL_WITH_TOKEN,
        IP_ADDRESS,
        PhoneRecognizer(config.phone_regions),
        DATE,
        PHONE_SHAPE,
        PHONE_CONTEXT,
        ID_DOCUMENT_CONTEXT,
        PASSPORT_SHAPE,
        ID_CARD_SHAPE,
        CREDIT_CARD_SHAPE,
    ]
    if config.gazetteer is not None:
        recognizers.insert(0, config.gazetteer)
    return recognizers


def _as_written(span: Span, view: TextView) -> Span:
    start, end = view.original_span(span.start, span.end)
    return Span(start, end, span.entity_type, span.score, span.recognizer)


# Languages whose missing model was already reported in this process.
_WARNED: set[str] = set()


def _ner_for(lang: str, *, forced: bool) -> SpacyNerRecognizer | None:
    """The recognizer for `lang`; None if it has no model, or (auto) its model is absent."""
    if lang not in SPACY_MODEL_DIRS:
        return None
    try:
        path = model_dir(lang)
    except ModelMissing as missing:
        if forced:
            raise
        if lang not in _WARNED:
            _WARNED.add(lang)
            _log.warning("%s. Names, organisations and places are not detected.", missing)
        return None
    # Cheap: the model itself is loaded once per process (ner.build_analyzer).
    return SpacyNerRecognizer(lang, path)


def _gliner(config: EngineConfig) -> GlinerRecognizer | None:
    """GLiNER, or None (with one warning per process) when its model is not installed."""
    try:
        path = gliner_model_dir()
    except ModelMissing as missing:
        # Never raised, even for a forced language: without GLiNER the text still gets
        # every structured recognizer and the gazetteer.
        if "gliner" not in _WARNED:
            _WARNED.add("gliner")
            _log.warning(
                "%s. Names, organisations, places and addresses in Armenian and other text "
                "without a spaCy model are not detected.",
                missing,
            )
        return None
    # Cheap: the model itself is loaded once per process (gliner_ner.load_gliner).
    return GlinerRecognizer(path, threshold=config.gliner_threshold)


def _wants_gliner(lang: str, config: EngineConfig) -> bool:
    if config.gliner == "all":
        return True
    return config.gliner == "auto" and lang not in SPACY_MODEL_DIRS


def _model_spans(recognizers: Sequence[Recognizer], text: str, start: int, end: int) -> list[Span]:
    """What the models find in text[start:end], mapped back to `text`."""
    # Accents composed and capitals title-cased for the models; spans map back (ADR 0016).
    piece = ner_view(text[start:end])
    spans = []
    for recognizer in recognizers:
        for s in recognizer.find(piece.text):
            piece_start, piece_end = piece.original_span(s.start, s.end)
            spans.append(
                Span(piece_start + start, piece_end + start, s.entity_type, s.score, s.recognizer)
            )
    return spans


def _ner_spans(text: str, lang: str, config: EngineConfig) -> list[Span]:
    pieces = segments(text) if lang == "auto" else [(0, len(text), lang)]
    spans = []
    for start, end, piece_lang in pieces:
        recognizers: list[Recognizer] = []
        spacy = _ner_for(piece_lang, forced=lang != "auto")
        if spacy is not None:
            recognizers.append(spacy)
        wants_gliner = _wants_gliner(piece_lang, config)
        gliner = _gliner(config) if wants_gliner else None
        if gliner is not None:
            recognizers.append(gliner)
        if recognizers:
            spans.extend(_model_spans(recognizers, text, start, end))
        if lang == "auto" and not wants_gliner and config.gliner == "auto":
            spans.extend(_other_script_spans(text, start, end, config))
        if lang == "auto":
            spans.extend(_other_model_spans(text, start, end, piece_lang, config))
    return spans


# The spaCy model for the other script of each spaCy language.
_OTHER_MODEL = {"en": "ru", "ru": "en"}


def _other_model_spans(
    text: str, start: int, end: int, lang: str, config: EngineConfig
) -> list[Span]:
    """The other spaCy model on each line of an `en` or `ru` segment that holds a word in
    its script: a Russian name in an English sentence, a Latin one in a Russian line
    (ADR 0016 amended, 1d). Only the line goes, as for GLiNER.

    The Russian model misses a Cyrillic name in an English line as often as it finds
    it, so such a line also goes to GLiNER (with gliner="auto"), which reads it whole.
    """
    other = _OTHER_MODEL.get(lang)
    lines = lines_with_words_of(text, start, end, other) if other else []
    if not other or not lines:
        return []
    recognizers: list[Recognizer] = []
    spacy = _ner_for(other, forced=False)
    if spacy is not None:
        recognizers.append(spacy)
    gliner = _gliner(config) if other == "ru" and config.gliner == "auto" else None
    if gliner is not None:
        recognizers.append(gliner)
    return [span for s, e in lines for span in _model_spans(recognizers, text, s, e)]


def _other_script_spans(text: str, start: int, end: int, config: EngineConfig) -> list[Span]:
    """GLiNER on each line of a spaCy segment that holds letters of another script: an
    Armenian clause after an English instruction (ADR 0016 amended)."""
    lines = lines_in_other_scripts(text, start, end)
    gliner = _gliner(config) if lines else None
    if gliner is None:
        return []
    return [span for s, e in lines for span in _model_spans([gliner], text, s, e)]


def detect(text: str, lang: str = "auto", config: EngineConfig | None = None) -> list[Span]:
    """Every detected span in `text`, overlaps resolved.

    Recognizers, NER included, read a cleaned copy (unicode spaces and dashes unified,
    invisible characters removed); each span is mapped back to the original (ADR 0013).
    With `lang="auto"` each paragraph goes to the NER model of its script (GLiNER for
    Armenian and other scripts), and a model that is not installed is skipped with one
    warning per process. Naming a language sends the whole text to that model and raises
    ModelMissing if its spaCy model is not installed; a missing GLiNER only warns.
    """
    if lang not in LANGUAGES:
        msg = f"unsupported language {lang!r}; expected one of {', '.join(LANGUAGES)}"
        raise ValueError(msg)
    config = config or EngineConfig()
    if not text.strip():
        return []
    view = clean_view(text)
    found = [span for recognizer in _recognizers(config) for span in recognizer.find(view.text)]
    found = defer(found, DEFERS_TO, view.text)
    if config.ner == "auto":
        found.extend(_ner_spans(view.text, lang, config))
    spans = [_as_written(span, view) for span in found]
    return merge(spans, config.thresholds, PRIORITY, text=text)
