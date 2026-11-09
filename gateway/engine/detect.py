"""`detect(text)`: run every recognizer, then merge (ADR 0011, ADR 0012)."""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from gateway.engine.merge import merge
from gateway.engine.ner import SPACY_MODEL_DIRS, ModelMissing, SpacyNerRecognizer, model_dir
from gateway.engine.recognizers.credit_card import CREDIT_CARD
from gateway.engine.recognizers.email import EMAIL
from gateway.engine.recognizers.gazetteer import Gazetteer
from gateway.engine.recognizers.iban import IBAN
from gateway.engine.recognizers.ip_address import IP_ADDRESS
from gateway.engine.recognizers.phone import DEFAULT_REGIONS, PhoneRecognizer
from gateway.engine.recognizers.ru_inn import RU_INN
from gateway.engine.recognizers.ru_snils import RU_SNILS
from gateway.engine.recognizers.url_token import URL_WITH_TOKEN
from gateway.engine.recognizers.us_ssn import US_SSN
from gateway.engine.script import segments
from gateway.engine.textview import TextView, clean_view
from gateway.engine.types import EntityType, Recognizer, Span

_log = logging.getLogger(__name__)

# On a tie (same length, same score) the recognizer listed first wins. The
# customer's own list comes first; checksummed types before shape-only ones.
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
    "ner_en",
    "ner_ru",
)
# "auto" routes each paragraph by script (gateway/engine/script.py); the rest force one.
# Armenian ("hy") and undetermined ("und") text get no NER until Phase 1c.
LANGUAGES = ("auto", *SPACY_MODEL_DIRS, "hy", "und")


@dataclass(frozen=True)
class EngineConfig:
    thresholds: Mapping[EntityType, float] = field(default_factory=dict)
    phone_regions: Sequence[str] = DEFAULT_REGIONS
    gazetteer: Gazetteer | None = None
    ner: Literal["auto", "off"] = "auto"


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
            _log.warning("%s. Names, organisations, places and dates are not detected.", missing)
        return None
    # Cheap: the model itself is loaded once per process (ner.build_analyzer).
    return SpacyNerRecognizer(lang, path)


def _ner_spans(text: str, lang: str) -> list[Span]:
    pieces = segments(text) if lang == "auto" else [(0, len(text), lang)]
    spans = []
    for start, end, piece_lang in pieces:
        recognizer = _ner_for(piece_lang, forced=lang != "auto")
        if recognizer is None:
            continue
        spans.extend(
            Span(s.start + start, s.end + start, s.entity_type, s.score, s.recognizer)
            for s in recognizer.find(text[start:end])
        )
    return spans


def detect(text: str, lang: str = "auto", config: EngineConfig | None = None) -> list[Span]:
    """Every detected span in `text`, overlaps resolved.

    Recognizers, NER included, read a cleaned copy (unicode spaces and dashes unified,
    invisible characters removed); each span is mapped back to the original (ADR 0013).
    With `lang="auto"` each paragraph goes to the NER model of its script, and a model
    that is not installed is skipped with one warning per process. Naming a language
    sends the whole text to that model and raises ModelMissing if it is not installed.
    """
    if lang not in LANGUAGES:
        msg = f"unsupported language {lang!r}; expected one of {', '.join(LANGUAGES)}"
        raise ValueError(msg)
    config = config or EngineConfig()
    if not text.strip():
        return []
    view = clean_view(text)
    found = [span for recognizer in _recognizers(config) for span in recognizer.find(view.text)]
    if config.ner == "auto":
        found.extend(_ner_spans(view.text, lang))
    spans = [_as_written(span, view) for span in found]
    return merge(spans, config.thresholds, PRIORITY, text=text)
