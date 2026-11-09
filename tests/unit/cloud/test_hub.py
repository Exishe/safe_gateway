"""`safeguard models pull` for GLiNER: pinned Hugging Face files, assembled for offline use.

Every test serves invented files through `httpx.MockTransport`; no test reaches
the network (pytest-socket is on).
"""

import hashlib
import json
from pathlib import Path

import httpx
import pytest

from gateway.cloud.hub import (
    GLINER_MODEL,
    HubFile,
    HubModelSpec,
    hub_url,
    install_dir,
    pull,
)
from gateway.cloud.models import ModelDownloadError
from gateway.engine.gliner_ner import GLINER_MODEL_DIR, is_gliner_model

GLINER_CONFIG = json.dumps({"model_name": "example/backbone", "max_len": 384}).encode()
BACKBONE_CONFIG = json.dumps({"model_type": "deberta-v2", "hidden_size": 8}).encode()
PAYLOADS = {
    ("example/gliner", "gliner_config.json"): GLINER_CONFIG,
    ("example/gliner", "pytorch_model.bin"): b"\x00\x01weights",
    ("example/backbone", "config.json"): BACKBONE_CONFIG,
    ("example/backbone", "spm.model"): b"\x0asentencepiece",
    ("example/backbone", "tokenizer_config.json"): b'{"do_lower_case": false}',
}
REVISION = "0123456789abcdef0123456789abcdef01234567"  # pragma: allowlist secret


def spec_for(payloads: dict[tuple[str, str], bytes]) -> HubModelSpec:
    files = tuple(
        HubFile(repo, REVISION, path, hashlib.sha256(data).hexdigest())
        for (repo, path), data in payloads.items()
    )
    return HubModelSpec("fake_gliner", REVISION, "example/backbone", files)


def serving(payloads: dict[tuple[str, str], bytes], calls: list[str]) -> httpx.Client:
    by_url = {
        f"https://huggingface.co/{repo}/resolve/{REVISION}/{path}": data
        for (repo, path), data in payloads.items()
    }

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        data = by_url.get(str(request.url))
        return httpx.Response(404) if data is None else httpx.Response(200, content=data)

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SAFEGUARD_HOME", str(tmp_path / "sg"))
    return tmp_path / "sg" / "models"


def test_hub_url_names_repo_revision_and_file() -> None:
    file = HubFile("org/repo", REVISION, "a.json", "0" * 64)
    assert hub_url(file) == f"https://huggingface.co/org/repo/resolve/{REVISION}/a.json"


@pytest.mark.parametrize("path", ["dir/a.json", "../a.json", "..", ""])
def test_hub_file_installs_top_level_files_only(path: str) -> None:
    with pytest.raises(ValueError, match="top-level"):
        HubFile("org/repo", REVISION, path, "0" * 64)


def test_hub_file_rejects_a_bad_digest_or_a_branch_name() -> None:
    with pytest.raises(ValueError, match="sha256"):
        HubFile("org/repo", REVISION, "a.json", "abc")
    with pytest.raises(ValueError, match="revision"):
        HubFile("org/repo", "main", "a.json", "0" * 64)


def test_pull_installs_every_file_and_the_offline_config(home: Path) -> None:
    calls: list[str] = []
    spec = spec_for(PAYLOADS)
    with serving(PAYLOADS, calls) as client:
        target = pull(spec, client)
    assert target == home / "gliner" / f"fake_gliner-{REVISION}"
    assert (target / "pytorch_model.bin").read_bytes() == b"\x00\x01weights"
    assert (target / "spm.model").read_bytes() == b"\x0asentencepiece"
    assert (target / "config.json").read_bytes() == BACKBONE_CONFIG
    # The pinned config is kept byte for byte; the one GLiNER reads embeds the backbone's.
    assert (target / "gliner_config.source.json").read_bytes() == GLINER_CONFIG
    loaded = json.loads((target / "gliner_config.json").read_text())
    assert loaded["encoder_config"] == json.loads(BACKBONE_CONFIG)
    assert loaded["max_len"] == 384
    assert is_gliner_model(target)
    assert len(calls) == len(PAYLOADS)


@pytest.mark.usefixtures("home")
def test_a_present_install_is_kept_without_a_download() -> None:
    spec = spec_for(PAYLOADS)
    install_dir(spec).mkdir(parents=True)
    calls: list[str] = []
    with serving(PAYLOADS, calls) as client:
        assert pull(spec, client) == install_dir(spec)
    assert calls == []


def test_a_wrong_digest_installs_nothing(home: Path) -> None:
    spec = spec_for(PAYLOADS)
    tampered = {**PAYLOADS, ("example/backbone", "spm.model"): b"\x0aother"}
    with serving(tampered, []) as client, pytest.raises(ModelDownloadError, match="SHA-256"):
        pull(spec, client)
    assert not install_dir(spec).exists()
    assert list((home / "gliner").iterdir()) == []


@pytest.mark.usefixtures("home")
def test_a_missing_file_installs_nothing() -> None:
    spec = spec_for(PAYLOADS)
    partial = {k: v for k, v in PAYLOADS.items() if k[1] != "pytorch_model.bin"}
    with serving(partial, []) as client, pytest.raises(ModelDownloadError, match="download"):
        pull(spec, client)
    assert not install_dir(spec).exists()


@pytest.mark.usefixtures("home")
def test_a_config_naming_another_backbone_installs_nothing() -> None:
    other = json.dumps({"model_name": "example/other-backbone"}).encode()
    payloads = {**PAYLOADS, ("example/gliner", "gliner_config.json"): other}
    spec = spec_for(payloads)
    with serving(payloads, []) as client, pytest.raises(ModelDownloadError, match="backbone"):
        pull(spec, client)
    assert not install_dir(spec).exists()


def test_the_pinned_model_is_the_one_the_engine_loads() -> None:
    assert GLINER_MODEL.directory_name == GLINER_MODEL_DIR
    files = {(f.repo, f.path) for f in GLINER_MODEL.files}
    assert files == {
        ("urchade/gliner_multi_pii-v1", "gliner_config.json"),
        ("urchade/gliner_multi_pii-v1", "pytorch_model.bin"),
        ("microsoft/mdeberta-v3-base", "config.json"),
        ("microsoft/mdeberta-v3-base", "spm.model"),
        ("microsoft/mdeberta-v3-base", "tokenizer_config.json"),
    }
    assert GLINER_MODEL.backbone == "microsoft/mdeberta-v3-base"
