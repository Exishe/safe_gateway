"""`safeguard models pull` for GLiNER: pinned Hugging Face files, assembled for offline use.

Every test serves invented files through `httpx.MockTransport`; no test reaches
the network (pytest-socket is on).
"""

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from gateway.cloud.hub import (
    GLINER_MODEL,
    HubFile,
    HubModelSpec,
    hub_url,
    install_dir,
    installed,
    pull,
)
from gateway.cloud.models import ALLOWED_HOSTS, SIZE_MARGIN, ModelDownloadError
from gateway.engine.gliner_ner import GLINER_MODEL_DIR, GLINER_PIN, is_gliner_model
from gateway.engine.model_marker import MARKER, Pin, write_marker

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


@pytest.mark.parametrize("size", [-1, True])
def test_hub_file_rejects_a_size_that_is_not_a_byte_count(size: int) -> None:
    with pytest.raises(ValueError, match="size"):
        HubFile("org/repo", REVISION, "a.json", "0" * 64, size=size)


def test_every_pinned_file_has_a_size_and_an_https_url_on_the_allow_list() -> None:
    for file in GLINER_MODEL.files:
        url = httpx.URL(hub_url(file))
        assert url.scheme == "https", file.path
        assert url.host in ALLOWED_HOSTS, file.path
        assert file.size is not None, file.path
        assert file.size > 0, file.path


@pytest.mark.usefixtures("home")
def test_a_file_larger_than_its_recorded_size_installs_nothing() -> None:
    oversized = b"\x00" * (SIZE_MARGIN + 4096)
    payloads = {**PAYLOADS, ("example/gliner", "pytorch_model.bin"): oversized}
    files = tuple(
        replace(f, size=len(PAYLOADS[(f.repo, f.path)])) for f in spec_for(payloads).files
    )
    spec = replace(spec_for(payloads), files=files)  # digests of what is served, sizes of before

    with serving(payloads, []) as client, pytest.raises(ModelDownloadError, match="larger than"):
        pull(spec, client)

    assert not install_dir(spec).exists()
    assert not installed(spec)


@pytest.mark.usefixtures("home")
def test_a_redirect_to_the_hub_cdn_is_followed_and_one_elsewhere_refused() -> None:
    spec = spec_for(PAYLOADS)
    weights_url = f"https://huggingface.co/example/gliner/resolve/{REVISION}/pytorch_model.bin"
    cdn = "https://us.gcp.cdn.hf.co/xet-bridge-us/1/invented?Signature=x"
    elsewhere = "https://cdn.example.org/weights.bin"

    def via(location: str, calls: list[str]) -> httpx.Client:
        """Like `serving`, but the weights answer 302 to `location`, which serves them."""
        by_url = {
            f"https://huggingface.co/{repo}/resolve/{REVISION}/{path}": data
            for (repo, path), data in PAYLOADS.items()
        }
        weights = by_url.pop(weights_url)

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            calls.append(url)
            if url == weights_url:
                return httpx.Response(302, headers={"Location": location})
            data = weights if url == location else by_url.get(url)
            return httpx.Response(404) if data is None else httpx.Response(200, content=data)

        return httpx.Client(transport=httpx.MockTransport(handler))

    refused: list[str] = []
    with via(elsewhere, refused) as client, pytest.raises(ModelDownloadError, match="refused"):
        pull(spec, client)
    assert elsewhere not in refused
    assert not installed(spec)

    followed: list[str] = []
    with via(cdn, followed) as client:
        pull(spec, client)
    assert cdn in followed
    assert installed(spec)


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
    assert installed(spec)
    assert (target / MARKER).is_file()


def test_the_pin_covers_every_pinned_file() -> None:
    spec = spec_for(PAYLOADS)
    assert spec.pin.model_id == spec.directory_name
    # One digest over all files: a change to any one of them makes an old install outdated.
    for index, file in enumerate(spec.files):
        moved = HubFile(file.repo, file.revision, file.path, "0" * 64)
        other = HubModelSpec(
            spec.name,
            spec.revision,
            spec.backbone,
            (*spec.files[:index], moved, *spec.files[index + 1 :]),
        )
        assert other.pin.sha256 != spec.pin.sha256, file.path
    # The order the files are listed in is not part of what is pinned.
    shuffled = HubModelSpec(spec.name, spec.revision, spec.backbone, spec.files[::-1])
    assert shuffled.pin == spec.pin


@pytest.mark.usefixtures("home")
def test_a_verified_install_is_kept_without_a_download() -> None:
    spec = spec_for(PAYLOADS)
    with serving(PAYLOADS, []) as client:
        pull(spec, client)
    calls: list[str] = []
    with serving(PAYLOADS, calls) as client:
        assert pull(spec, client) == install_dir(spec)
    assert calls == []


@pytest.mark.parametrize("pin_sha256", [None, "0" * 64])
def test_an_unmarked_or_outdated_directory_is_missing_and_replaced(
    home: Path, pin_sha256: str | None
) -> None:
    spec = spec_for(PAYLOADS)
    target = install_dir(spec)
    target.mkdir(parents=True)
    (target / "pytorch_model.bin").write_bytes(b"\x00\x01wei")  # a download cut short
    if pin_sha256 is not None:
        write_marker(target, Pin(spec.directory_name, pin_sha256))
    assert not installed(spec)
    calls: list[str] = []

    with serving(PAYLOADS, calls) as client:
        assert pull(spec, client) == target

    assert len(calls) == len(PAYLOADS)
    assert installed(spec)
    assert (target / "pytorch_model.bin").read_bytes() == b"\x00\x01weights"
    assert [p.name for p in (home / "gliner").iterdir()] == [target.name]  # nothing staged


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
    # The engine accepts exactly the marker that `safeguard models pull` writes.
    assert GLINER_MODEL.pin == GLINER_PIN
    files = {(f.repo, f.path) for f in GLINER_MODEL.files}
    assert files == {
        ("urchade/gliner_multi_pii-v1", "gliner_config.json"),
        ("urchade/gliner_multi_pii-v1", "pytorch_model.bin"),
        ("microsoft/mdeberta-v3-base", "config.json"),
        ("microsoft/mdeberta-v3-base", "spm.model"),
        ("microsoft/mdeberta-v3-base", "tokenizer_config.json"),
    }
    assert GLINER_MODEL.backbone == "microsoft/mdeberta-v3-base"
