"""`detect(text)`: run every recognizer, then merge (ADR 0011)."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from gateway.engine.merge import merge
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
from gateway.engine.textview import TextView, clean_view
from gateway.engine.types import EntityType, Recognizer, Span

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
)


@dataclass(frozen=True)
class EngineConfig:
    thresholds: Mapping[EntityType, float] = field(default_factory=dict)
    phone_regions: Sequence[str] = DEFAULT_REGIONS
    gazetteer: Gazetteer | None = None


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


def detect(text: str, lang: str = "auto", config: EngineConfig | None = None) -> list[Span]:
    """Every detected span in `text`, overlaps resolved. `lang` is used from Phase 1b.

    Recognizers read a cleaned copy (unicode spaces and dashes unified, invisible
    characters removed); each span is mapped back to the original (ADR 0013).
    """
    del lang  # accepted now so callers do not change when NER arrives
    config = config or EngineConfig()
    if not text.strip():
        return []
    view = clean_view(text)
    spans = [
        _as_written(span, view)
        for recognizer in _recognizers(config)
        for span in recognizer.find(view.text)
    ]
    return merge(spans, config.thresholds, PRIORITY, text=text)
