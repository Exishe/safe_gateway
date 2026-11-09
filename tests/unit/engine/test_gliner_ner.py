"""GLiNER for Armenian and other text without a spaCy model, strictly offline (ADR 0014).

Most tests drive `GlinerRecognizer` with a fake model. Tests marked `models` load
the real model from `safeguard models pull` under the socket-disabled run, with
tripwires on every Hugging Face download function, and are skipped when it is absent.
"""

import itertools
import json
import os
import sys
import types
from collections.abc import Sequence
from pathlib import Path

import pytest

from gateway.engine import gliner_ner
from gateway.engine.gliner_ner import (
    GLINER_LABELS,
    GlinerRecognizer,
    gliner_model_dir,
    is_gliner_model,
    load_gliner,
    word_windows,
)
from gateway.engine.ner import ModelMissing
from gateway.engine.types import EntityType as E

# Invented people and places; any likeness to real ones is chance.
HY = "Երեկ Նարեկ Վարդանյանը այցելեց Արևաշեն գյուղ, որտեղ ապրում է նրա քույրը։"


def installed(path: Path) -> Path:
    """A directory shaped like a `safeguard models pull` install, with no real weights."""
    path.mkdir(parents=True)
    config = {"model_name": "microsoft/mdeberta-v3-base", "encoder_config": {"x": 1}}
    (path / "gliner_config.json").write_text(json.dumps(config), encoding="utf-8")
    for name in ("pytorch_model.bin", "tokenizer_config.json", "spm.model", "config.json"):
        (path / name).write_bytes(b"{}")
    return path


class FakeModel:
    """Finds each listed word in the text it is given, with the label next to it."""

    def __init__(self, words: dict[str, tuple[str, float]]) -> None:
        self.words = words
        self.calls: list[tuple[str, list[str], float]] = []

    def predict_entities(
        self, text: str, labels: Sequence[str], threshold: float = 0.5
    ) -> list[dict[str, object]]:
        self.calls.append((text, list(labels), threshold))
        found: list[dict[str, object]] = []
        for word, (label, score) in self.words.items():
            start = text.find(word)
            while start != -1:
                found.append({"start": start, "end": start + len(word), "label": label})
                found[-1]["score"] = score
                start = text.find(word, start + 1)
        return found


def recognizer(path: Path, model: FakeModel, **kwargs: float) -> GlinerRecognizer:
    return GlinerRecognizer(path, load=lambda _path: model, **kwargs)  # ty: ignore[invalid-argument-type]


# ------------------------------------------------------------------ the model directory


def test_a_missing_directory_raises_before_anything_is_loaded(tmp_path: Path) -> None:
    def tripwire(_path: Path) -> FakeModel:
        pytest.fail("the model loader ran for a missing model")

    with pytest.raises(ModelMissing, match="safeguard models pull --only gliner"):
        GlinerRecognizer(tmp_path / "absent", load=tripwire)


def test_a_bare_hub_snapshot_is_not_a_model(tmp_path: Path) -> None:
    # No tokenizer and no embedded encoder config: loading it would name the backbone on the Hub.
    path = tmp_path / "snapshot"
    path.mkdir()
    (path / "gliner_config.json").write_text('{"model_name": "x/y"}', encoding="utf-8")
    (path / "pytorch_model.bin").write_bytes(b"")
    assert not is_gliner_model(path)
    with pytest.raises(ModelMissing):
        GlinerRecognizer(path, load=lambda _p: FakeModel({}))


def test_load_gliner_refuses_a_missing_model_before_importing_gliner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "gliner", None)  # importing it now raises ImportError
    with pytest.raises(ModelMissing):
        load_gliner(tmp_path / "absent")


def test_load_gliner_goes_offline_and_loads_only_the_local_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = installed(tmp_path / "model")
    seen: dict[str, object] = {}

    class FakeGliner:
        @staticmethod
        def from_pretrained(model_id: str, **kwargs: object) -> str:
            seen["model_id"] = model_id
            seen["kwargs"] = kwargs
            seen["env"] = (os.environ.get("HF_HUB_OFFLINE"), os.environ.get("TRANSFORMERS_OFFLINE"))
            return "loaded"

    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
    monkeypatch.setitem(sys.modules, "gliner", types.SimpleNamespace(GLiNER=FakeGliner))
    load_gliner.cache_clear()

    assert load_gliner(path) == "loaded"
    assert seen["model_id"] == str(path)
    assert seen["env"] == ("1", "1")
    kwargs = seen["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["local_files_only"] is True
    load_gliner.cache_clear()


def test_gliner_model_dir_names_the_pull_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SAFEGUARD_HOME", str(tmp_path))
    with pytest.raises(ModelMissing, match="safeguard models pull --only gliner"):
        gliner_model_dir()
    path = installed(tmp_path / "models" / "gliner" / gliner_ner.GLINER_MODEL_DIR)
    assert gliner_model_dir() == path


# ------------------------------------------------------------------ windows


def test_word_windows_cover_every_word_within_the_limit() -> None:
    text = " ".join(f"w{i}" for i in range(50))
    found = word_windows(text, max_words=10, overlap=3)
    assert found[0][0] == 0
    assert found[-1][1] == len(text)
    for (start, end), (next_start, _) in itertools.pairwise(found):
        assert next_start < end  # overlap
        assert len(text[start:end].split()) <= 10
        assert len(text[next_start:end].split()) == 3


def test_word_windows_count_punctuation_as_gliner_does() -> None:
    # GLiNER splits "a,b" into three words; a window must never exceed its word limit.
    text = ",".join("a" * 20)
    for start, end in word_windows(text, max_words=8, overlap=2):
        assert len(gliner_ner.WORD.findall(text[start:end])) <= 8


def test_word_windows_of_blank_text_are_empty() -> None:
    assert word_windows("   \n ", max_words=8, overlap=2) == []


def test_word_windows_refuse_an_overlap_of_half_the_window() -> None:
    with pytest.raises(ValueError, match="overlap"):
        word_windows("a b c", max_words=8, overlap=4)


# ------------------------------------------------------------------ finding spans


def test_labels_map_to_entity_types(tmp_path: Path) -> None:
    model = FakeModel(
        {
            "Նարեկ Վարդանյանը": ("person", 0.9),
            "Արևաշեն": ("location", 0.8),
            "Ծիրան ՍՊԸ": ("organization", 0.7),
            "Մաշտոցի 99": ("address", 0.75),
            "1990-01-01": ("date of birth", 0.66),
            "քույրը": ("unexpected label", 0.99),
        }
    )
    text = f"{HY} Ծիրան ՍՊԸ, Մաշտոցի 99, ծնված 1990-01-01։"
    found = recognizer(installed(tmp_path / "m"), model).find(text)

    assert [(text[s.start : s.end], s.entity_type, s.recognizer) for s in found] == [
        ("Նարեկ Վարդանյանը", E.PERSON, "gliner"),
        ("Արևաշեն", E.LOCATION, "gliner"),
        ("Ծիրան ՍՊԸ", E.ORG, "gliner"),
        ("Մաշտոցի 99", E.ADDRESS, "gliner"),
        ("1990-01-01", E.DATE_OF_BIRTH, "gliner"),
    ]
    assert model.calls[0][1] == list(GLINER_LABELS)


def test_threshold_reaches_the_model(tmp_path: Path) -> None:
    model = FakeModel({})
    recognizer(installed(tmp_path / "m"), model, threshold=0.3).find(HY)
    assert model.calls[0][2] == 0.3


def test_offsets_map_back_across_windows_and_overlaps_report_once(tmp_path: Path) -> None:
    filler = " ".join(f"բառ{i}" for i in range(40))
    text = f"{filler} Նարեկ {filler} Նարեկ {filler}"
    model = FakeModel({"Նարեկ": ("person", 0.9)})
    found = recognizer(installed(tmp_path / "m"), model, max_words=16, overlap=4).find(text)

    assert len(model.calls) > 3
    assert [text[s.start : s.end] for s in found] == ["Նարեկ", "Նարեկ"]
    assert found[0].start == text.index("Նարեկ")


def test_blank_text_loads_nothing(tmp_path: Path) -> None:
    def tripwire(_path: Path) -> FakeModel:
        pytest.fail("the model was loaded for blank text")

    assert GlinerRecognizer(installed(tmp_path / "m"), load=tripwire).find("  \n") == []


def test_the_model_loads_once(tmp_path: Path) -> None:
    loads: list[Path] = []
    model = FakeModel({})

    def load(path: Path) -> FakeModel:
        loads.append(path)
        return model

    found = GlinerRecognizer(installed(tmp_path / "m"), load=load)
    found.find(HY)
    found.find(HY)
    assert len(loads) == 1


# ------------------------------------------------------------------ the real model


@pytest.fixture(scope="module")
def real_model_dir() -> Path:
    try:
        return gliner_model_dir()
    except ModelMissing as error:
        pytest.skip(f"GLiNER model absent: {error}")


def _tripwire(*_args: object, **_kwargs: object) -> None:
    pytest.fail("a Hugging Face download function was called")


@pytest.mark.models
def test_real_model_loads_offline_and_finds_armenian_names(
    real_model_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import huggingface_hub  # noqa: PLC0415
    import huggingface_hub.file_download  # noqa: PLC0415
    import transformers.utils.hub  # noqa: PLC0415

    for module, name in [
        (huggingface_hub, "hf_hub_download"),
        (huggingface_hub, "snapshot_download"),
        (huggingface_hub.file_download, "hf_hub_download"),
        (huggingface_hub.file_download, "http_get"),
        (huggingface_hub.file_download, "get_hf_file_metadata"),
        (transformers.utils.hub, "hf_hub_download"),
        (transformers.utils.hub, "snapshot_download"),
    ]:
        monkeypatch.setattr(module, name, _tripwire)
    load_gliner.cache_clear()

    found = GlinerRecognizer(real_model_dir).find(HY)

    kinds = {(HY[s.start : s.end], s.entity_type) for s in found}
    assert any(kind == E.PERSON and "Նարեկ" in value for value, kind in kinds), kinds
    assert any(kind == E.LOCATION and "Արևաշեն" in value for value, kind in kinds), kinds
    assert os.environ["HF_HUB_OFFLINE"] == "1"
