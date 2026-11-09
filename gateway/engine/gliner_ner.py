"""Names, organisations, places, addresses and birth dates through GLiNER, offline (ADR 0015).

For Armenian and any text with no spaCy model. GLiNER's `from_pretrained` downloads
from the Hugging Face Hub whenever a name does not resolve locally, and the model's
config names its backbone (`microsoft/mdeberta-v3-base`), which GLiNER resolves for
the tokenizer and the encoder config. `safeguard models pull` therefore installs one
directory holding the backbone's tokenizer files and a config that embeds the
backbone's own, so the backbone name is never looked up. Here the directory is
checked for exactly that before `gliner` is imported, the offline switches are set
before the import, and only that local path is loaded, with `local_files_only`.
"""

import json
import os
import re
from collections.abc import Callable, Sequence
from functools import cache
from pathlib import Path
from typing import Protocol

from gateway.engine.model_marker import Pin, is_installed
from gateway.engine.ner import ModelMissing, models_home
from gateway.engine.types import EntityType, Span

# Where `safeguard models pull` (gateway/cloud/hub.py) installs the model, and the digest
# over its pinned files that the completion marker must record (ADR 0006, amended
# 2026-11-09). A test keeps both in step with the pinned download.
GLINER_MODEL_DIR = "gliner_multi_pii-v1-1fcf13e85f4eef5394e1fcd406cf2ca9ea82351d"
GLINER_PIN = Pin(
    GLINER_MODEL_DIR,
    # pragma: allowlist nextline secret
    "5f3128bd424c6a546fb24aa9bb6cfadb9417d5384ef0ec676503a0b3ef879a80",
)

# GLiNER label -> ours. GLiNER is prompted with exactly these labels.
GLINER_LABELS = {
    "person": EntityType.PERSON,
    "organization": EntityType.ORG,
    "location": EntityType.LOCATION,
    "address": EntityType.ADDRESS,
    "date of birth": EntityType.DATE_OF_BIRTH,
}
DEFAULT_THRESHOLD = 0.5

# GLiNER's own word splitter: its window (`max_len`, 384 in this model) counts these.
WORD = re.compile(r"\w+(?:[-_]\w+)*|\S")
# The binding limit is the backbone's 512 positions, not max_len: Armenian averages
# about 2.4 sub-word tokens a word, and 192 words of dense Faker hy_AM text reached 502
# with the label prompt. 128 words reach 344, leaving room for denser text (a test checks).
MAX_WORDS = 128
OVERLAP_WORDS = 32  # a name cut at one window's edge is whole in the next (ADR 0004)

_REQUIRED_FILES = (
    "gliner_config.json",
    "pytorch_model.bin",
    "tokenizer_config.json",
    "config.json",
)


class GlinerModel(Protocol):
    def predict_entities(
        self, text: str, labels: Sequence[str], threshold: float = ...
    ) -> list[dict[str, object]]: ...


GlinerLoader = Callable[[Path], GlinerModel]


def _missing(path: Path) -> ModelMissing:
    return ModelMissing(
        f"no complete, verified GLiNER model at {path}; run `safeguard models pull --only gliner`"
    )


def is_gliner_model(path: Path) -> bool:
    """A complete install: weights, local tokenizer files, and the backbone config embedded."""
    if not all((path / name).is_file() for name in _REQUIRED_FILES):
        return False
    try:
        config = json.loads((path / "gliner_config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(config, dict) and isinstance(config.get("encoder_config"), dict)


def gliner_model_dir() -> Path:
    """The installed GLiNER directory; ModelMissing if it is not installed.

    Installed means the completion marker of a verified pull for the current pin, the
    same test `safeguard models pull` and `models list` use. The recognizer and the
    loader still check the directory's shape before anything reads it.
    """
    path = models_home() / "gliner" / GLINER_PIN.model_id
    if not is_installed(path, GLINER_PIN):
        raise _missing(path)
    return path


@cache
def load_gliner(path: Path) -> GlinerModel:
    """The model in `path`, loaded once per process, never from the network."""
    if not is_gliner_model(path):
        raise _missing(path)
    # Read by huggingface_hub and transformers when they are imported, hence before.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    from gliner import GLiNER  # noqa: PLC0415  (seconds to import; only once a model exists)

    return GLiNER.from_pretrained(str(path), local_files_only=True, map_location="cpu")


def word_windows(text: str, max_words: int, overlap: int) -> list[tuple[int, int]]:
    """(start, end) windows of at most `max_words` GLiNER words, each overlapping the next."""
    if not 0 <= overlap < max_words // 2:
        msg = f"overlap must be from 0 to below half the window ({max_words // 2}), not {overlap}"
        raise ValueError(msg)
    words = [match.span() for match in WORD.finditer(text)]
    found: list[tuple[int, int]] = []
    first = 0
    while first < len(words):
        last = min(first + max_words, len(words))
        found.append((words[first][0], words[last - 1][1]))
        if last == len(words):
            break
        first = last - overlap
    return found


class GlinerRecognizer:
    """PERSON, ORG, LOCATION, ADDRESS and DATE_OF_BIRTH from the multilingual GLiNER PII model."""

    name = "gliner"

    def __init__(
        self,
        model_dir: Path,
        *,
        threshold: float = DEFAULT_THRESHOLD,
        max_words: int = MAX_WORDS,
        overlap: int = OVERLAP_WORDS,
        load: GlinerLoader = load_gliner,
    ) -> None:
        if not is_gliner_model(model_dir):
            raise _missing(model_dir)
        self.model_dir = model_dir
        self.threshold = threshold
        self.max_words = max_words
        self.overlap = overlap
        self._load = load
        self._model: GlinerModel | None = None

    def find(self, text: str) -> list[Span]:
        windows = word_windows(text, self.max_words, self.overlap)
        if not windows:
            return []
        model = self._loaded()
        # A span seen in two overlapping windows is reported once, at its best score.
        best: dict[tuple[int, int, EntityType], float] = {}
        for start, end in windows:
            for found in model.predict_entities(
                text[start:end], list(GLINER_LABELS), threshold=self.threshold
            ):
                span = _span_of(found, start)
                if span is not None:
                    key, score = span
                    best[key] = max(score, best.get(key, 0.0))
        return [
            Span(s, e, entity, score, self.name) for (s, e, entity), score in sorted(best.items())
        ]

    def _loaded(self) -> GlinerModel:
        if self._model is None:
            self._model = self._load(self.model_dir)
        return self._model


def _span_of(
    found: dict[str, object], offset: int
) -> tuple[tuple[int, int, EntityType], float] | None:
    entity = GLINER_LABELS.get(str(found.get("label")))
    start, end, score = found.get("start"), found.get("end"), found.get("score")
    if entity is None or not isinstance(start, int) or not isinstance(end, int) or end <= start:
        return None
    if not isinstance(score, float | int):
        return None
    return (offset + start, offset + end, entity), min(max(float(score), 0.0), 1.0)
