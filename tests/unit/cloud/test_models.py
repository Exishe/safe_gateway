"""`safeguard models pull`: pinned downloads, verified and unpacked safely (ADR 0012 point 3).

Every test serves a small wheel built here through `httpx.MockTransport`; no
test reaches the network (pytest-socket is on).
"""

import hashlib
import io
import re
import zipfile
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from gateway.cloud import models
from gateway.cloud.models import (
    SPACY_MODELS,
    ModelDownloadError,
    ModelSpec,
    install_dir,
    installed,
    models_home,
    pull,
)
from gateway.engine.model_marker import MARKER, Pin, write_marker

NAME, VERSION = "xx_fake_model", "1.0.0"
INNER = f"{NAME}/{NAME}-{VERSION}/"
URL = f"https://models.example.org/{NAME}-{VERSION}-py3-none-any.whl"

GOOD_MEMBERS = {
    f"{NAME}/__init__.py": b"# package shim, not extracted\n",
    f"{NAME}/meta.json": b"{}",
    f"{INNER}config.cfg": b"[nlp]\nlang = xx\n",
    f"{INNER}meta.json": b'{"name": "fake_model"}',
    f"{INNER}ner/": b"",  # a directory entry, as some zip writers add
    f"{INNER}ner/model": b"\x00\x01weights",
    f"{NAME}-{VERSION}.dist-info/METADATA": b"Name: xx_fake_model\n",
}


def wheel(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def spec_for(payload: bytes) -> ModelSpec:
    return ModelSpec(NAME, VERSION, URL, hashlib.sha256(payload).hexdigest())


def serving(payload: bytes, calls: list[str] | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(request.url))
        if str(request.url) != URL:
            return httpx.Response(404)
        return httpx.Response(200, content=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SAFEGUARD_HOME", str(tmp_path / "sg"))
    return tmp_path / "sg" / "models"


def leftovers(home: Path) -> list[Path]:
    return sorted(home.rglob("*")) if home.exists() else []


# ------------------------------------------------------------------ where models live


def test_models_home_follows_safeguard_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SAFEGUARD_HOME", str(tmp_path))
    assert models_home() == tmp_path / "models"


def test_models_home_defaults_to_dot_safeguard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SAFEGUARD_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert models_home() == tmp_path / ".safeguard" / "models"


def test_install_dir_is_spacy_name_dash_version(home: Path) -> None:
    spec = spec_for(b"")
    assert install_dir(spec) == home / "spacy" / f"{NAME}-{VERSION}"


def test_spacy_models_are_pinned_wheels_from_the_spacy_models_releases() -> None:
    assert set(SPACY_MODELS) == {"en", "ru"}
    assert SPACY_MODELS["en"].name == "en_core_web_lg"
    assert SPACY_MODELS["ru"].name == "ru_core_news_lg"
    for spec in SPACY_MODELS.values():
        assert spec.version == "3.8.0"
        assert spec.url == (
            "https://github.com/explosion/spacy-models/releases/download/"
            f"{spec.name}-{spec.version}/{spec.name}-{spec.version}-py3-none-any.whl"
        )
        assert re.fullmatch(r"[0-9a-f]{64}", spec.sha256), spec.name


def test_a_spec_without_a_sha256_is_rejected() -> None:
    with pytest.raises(ValueError, match="sha256"):
        ModelSpec(NAME, VERSION, URL, "not-a-digest")


# ------------------------------------------------------------------ pull


def test_a_good_wheel_unpacks_only_the_model_directory(home: Path) -> None:
    payload = wheel(GOOD_MEMBERS)

    with serving(payload) as client:
        path = pull(spec_for(payload), client)

    assert path == home / "spacy" / f"{NAME}-{VERSION}"
    assert (path / "config.cfg").read_bytes() == b"[nlp]\nlang = xx\n"
    assert (path / "ner" / "model").read_bytes() == b"\x00\x01weights"
    # The package shim and the dist-info stay in the wheel: only the model data is needed.
    assert sorted(p.relative_to(home) for p in leftovers(home)) == [
        Path("spacy"),
        Path("spacy", f"{NAME}-{VERSION}"),
        Path("spacy", f"{NAME}-{VERSION}", MARKER),
        Path("spacy", f"{NAME}-{VERSION}", "config.cfg"),
        Path("spacy", f"{NAME}-{VERSION}", "meta.json"),
        Path("spacy", f"{NAME}-{VERSION}", "ner"),
        Path("spacy", f"{NAME}-{VERSION}", "ner", "model"),
    ]
    assert installed(spec_for(payload))


def test_the_pin_of_a_wheel_is_its_directory_and_digest() -> None:
    spec = spec_for(b"wheel bytes")
    assert spec.pin == Pin(f"{NAME}-{VERSION}", spec.sha256)


def test_a_wrong_sha256_raises_and_leaves_nothing_behind(home: Path) -> None:
    payload = wheel(GOOD_MEMBERS)
    wrong = ModelSpec(NAME, VERSION, URL, "0" * 64)

    with serving(payload) as client, pytest.raises(ModelDownloadError, match="SHA-256"):
        pull(wrong, client)

    assert not install_dir(wrong).exists()
    assert [p for p in leftovers(home) if p.is_file()] == []


@pytest.mark.parametrize(
    "hostile",
    [
        f"{INNER}../../../escaped.txt",
        f"{INNER}ner/../../../../escaped.txt",
        "../escaped.txt",
        "/tmp/escaped.txt",  # noqa: S108  (an archive member name, never written)
        f"{INNER}..\\..\\escaped.txt",
        "C:/escaped.txt",
    ],
)
def test_an_archive_member_that_would_escape_is_rejected(
    home: Path, tmp_path: Path, hostile: str
) -> None:
    # The digest matches, so only the path check stands between the archive and the disk.
    payload = wheel({**GOOD_MEMBERS, hostile: b"escaped"})

    with serving(payload) as client, pytest.raises(ModelDownloadError, match="unsafe path"):
        pull(spec_for(payload), client)

    assert not any(p.name.endswith("escaped.txt") for p in tmp_path.rglob("*"))
    assert [p for p in leftovers(home) if p.is_file()] == []


def test_a_verified_install_is_not_downloaded_again(home: Path) -> None:
    payload = wheel(GOOD_MEMBERS)
    spec = spec_for(payload)
    with serving(payload) as client:
        pull(spec, client)
    calls: list[str] = []

    with serving(payload, calls) as client:
        assert pull(spec, client) == home / "spacy" / f"{NAME}-{VERSION}"

    assert calls == []


@pytest.mark.parametrize(
    "leftover",
    [
        {},  # an empty directory
        {"config.cfg": b"[nlp]\n", "ner/model": b"\x00\x01wei"},  # unpacking was cut short
    ],
)
def test_a_directory_without_a_marker_is_missing_and_replaced(
    home: Path, leftover: dict[str, bytes]
) -> None:
    payload = wheel(GOOD_MEMBERS)
    spec = spec_for(payload)
    target = install_dir(spec)
    target.mkdir(parents=True)
    for name, data in leftover.items():
        (target / name).parent.mkdir(parents=True, exist_ok=True)
        (target / name).write_bytes(data)
    assert not installed(spec)
    calls: list[str] = []

    with serving(payload, calls) as client:
        assert pull(spec, client) == target

    assert calls == [URL]
    assert installed(spec)
    assert (target / "ner" / "model").read_bytes() == b"\x00\x01weights"
    assert (target / "meta.json").is_file()
    assert [p.name for p in home.joinpath("spacy").iterdir()] == [target.name]  # nothing staged


def test_an_install_pinned_to_another_digest_is_replaced(home: Path) -> None:
    payload = wheel(GOOD_MEMBERS)
    spec = spec_for(payload)
    target = install_dir(spec)
    target.mkdir(parents=True)
    (target / "stale.bin").write_bytes(b"old model")
    write_marker(target, Pin(spec.directory_name, "0" * 64))
    assert not installed(spec)

    with serving(payload) as client:
        pull(spec, client)

    assert installed(spec)
    assert not (target / "stale.bin").exists()
    assert home.joinpath("spacy").exists()


def test_a_symlink_in_place_of_the_install_is_replaced_not_followed(
    home: Path, tmp_path: Path
) -> None:
    payload = wheel(GOOD_MEMBERS)
    spec = spec_for(payload)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "keep.txt").write_bytes(b"not ours")
    install_dir(spec).parent.mkdir(parents=True)
    install_dir(spec).symlink_to(elsewhere, target_is_directory=True)

    with serving(payload) as client:
        pull(spec, client)

    assert not install_dir(spec).is_symlink()
    assert installed(spec)
    assert (elsewhere / "keep.txt").read_bytes() == b"not ours"
    assert home.exists()


def test_the_marker_is_written_last_and_a_pull_without_it_counts_as_missing(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = wheel(GOOD_MEMBERS)
    spec = spec_for(payload)
    target = install_dir(spec)
    seen: list[list[str]] = []

    def interrupted(directory: Path, pin: Pin) -> None:
        # By now the verified model is complete and in place; only the marker is missing.
        assert directory == target
        assert pin == spec.pin
        seen.append(sorted(p.name for p in directory.iterdir()))
        raise KeyboardInterrupt

    monkeypatch.setattr(models, "write_marker", interrupted)
    with serving(payload) as client, pytest.raises(KeyboardInterrupt):
        pull(spec, client)

    assert seen == [["config.cfg", "meta.json", "ner"]]
    assert not installed(spec)
    assert [p.name for p in home.joinpath("spacy").iterdir()] == [target.name]
    monkeypatch.undo()
    with serving(payload) as client:
        pull(spec, client)
    assert installed(spec)


def test_an_http_error_raises_and_leaves_nothing_behind(home: Path) -> None:
    payload = wheel(GOOD_MEMBERS)
    elsewhere = ModelSpec(NAME, VERSION, URL + "?moved", spec_for(payload).sha256)

    with serving(payload) as client, pytest.raises(ModelDownloadError, match="404"):
        pull(elsewhere, client)

    assert [p for p in leftovers(home) if p.is_file()] == []


@pytest.mark.parametrize(
    ("payload_of", "message"),
    [
        (lambda: wheel({f"{NAME}/__init__.py": b""}), "no model directory"),
        (lambda: b"this is not a zip file", "not a wheel"),
    ],
)
def test_an_archive_without_a_model_raises(
    home: Path, payload_of: Callable[[], bytes], message: str
) -> None:
    payload = payload_of()

    with serving(payload) as client, pytest.raises(ModelDownloadError, match=message):
        pull(spec_for(payload), client)

    assert [p for p in leftovers(home) if p.is_file()] == []
