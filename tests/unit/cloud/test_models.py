"""`safeguard models pull`: pinned downloads, verified and unpacked safely (ADR 0012 point 3).

Every test serves a small wheel built here through `httpx.MockTransport`; no
test reaches the network (pytest-socket is on). Downloads follow redirects only over
https to the hosts in `ALLOWED_HOSTS`, and stop at a byte cap (ADR 0006, amended).
"""

import hashlib
import io
import re
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest

from gateway.cloud import models
from gateway.cloud.models import (
    ALLOWED_HOSTS,
    MAX_DOWNLOAD_BYTES,
    MAX_REDIRECTS,
    SIZE_MARGIN,
    SPACY_MODELS,
    ModelDownloadError,
    ModelSpec,
    byte_cap,
    download_verified,
    install_dir,
    installed,
    models_home,
    open_client,
    pull,
)
from gateway.engine.model_marker import MARKER, Pin, write_marker

NAME, VERSION = "xx_fake_model", "1.0.0"
INNER = f"{NAME}/{NAME}-{VERSION}/"
# Invented release paths on the real hosts: only the hosts are checked, and nothing is sent.
URL = f"https://github.com/example/models/releases/download/{NAME}-{VERSION}/{NAME}.whl"
ASSET = "https://release-assets.githubusercontent.com/production/1/asset?sig=invented"

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
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Path.home() reads this on Windows
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


# ------------------------------------------------------------------ redirects, M4


def redirecting(
    payload: bytes, hops: dict[str, str], calls: list[str], *, follow: bool = False
) -> httpx.Client:
    """Serves `payload` at ASSET; a URL in `hops` answers 302 to its value; anything else 404."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        if url in hops:
            return httpx.Response(302, headers={"Location": hops[url]})
        return httpx.Response(200, content=payload) if url == ASSET else httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=follow)


def test_a_redirect_to_the_release_storage_host_is_followed(home: Path) -> None:
    payload = wheel(GOOD_MEMBERS)
    calls: list[str] = []

    with redirecting(payload, {URL: ASSET}, calls) as client:
        pull(spec_for(payload), client)

    assert calls == [URL, ASSET]
    assert installed(spec_for(payload))
    assert home.exists()


@pytest.mark.parametrize(
    "location",
    [
        "http://release-assets.githubusercontent.com/production/1/asset",  # downgrade to http
        "https://downloads.example.org/model.whl",  # a host not on the list
        "//downloads.example.org/model.whl",  # the same, scheme-relative
        "https://release-assets.githubusercontent.com.example.org/asset",  # a look-alike
        "https://release-assets.githubusercontent.com:8443/asset",  # another port
        "ftp://release-assets.githubusercontent.com/asset",
        "file:///etc/passwd",
    ],
)
@pytest.mark.parametrize("follow", [False, True])
def test_a_redirect_off_the_allow_list_is_refused_before_it_is_requested(
    home: Path, location: str, *, follow: bool
) -> None:
    # `follow`: even a client built to follow redirects itself gets every hop checked.
    payload = wheel(GOOD_MEMBERS)
    calls: list[str] = []

    with (
        redirecting(payload, {URL: location}, calls, follow=follow) as client,
        pytest.raises(ModelDownloadError, match="refused"),
    ):
        pull(spec_for(payload), client)

    assert calls == [URL]  # the target was never contacted
    assert [p for p in leftovers(home) if p.is_file()] == []


def test_a_relative_redirect_stays_on_its_host_and_is_followed(tmp_path: Path) -> None:
    # huggingface.co answers some resolve URLs with a same-host relative redirect.
    url = "https://huggingface.co/example/model/resolve/0123abc/config.json"
    cached = "https://huggingface.co/api/resolve-cache/models/example/model/0123abc/config.json"
    payload = b'{"model_type": "fake"}'
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if str(request.url) == url:
            location = cached.removeprefix("https://huggingface.co")  # "/api/resolve-cache/..."
            return httpx.Response(307, headers={"Location": location})
        return httpx.Response(200, content=payload)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        download_verified(
            url, hashlib.sha256(payload).hexdigest(), "config", client, tmp_path / "f"
        )

    assert calls == [url, cached]
    assert (tmp_path / "f").read_bytes() == payload


def test_a_redirect_loop_is_cut_off(home: Path) -> None:
    payload = wheel(GOOD_MEMBERS)
    calls: list[str] = []

    with (
        redirecting(payload, {URL: URL}, calls) as client,
        pytest.raises(ModelDownloadError, match="redirects"),
    ):
        pull(spec_for(payload), client)

    assert len(calls) == MAX_REDIRECTS + 1
    assert [p for p in leftovers(home) if p.is_file()] == []


@pytest.mark.parametrize(
    "pinned",
    [
        URL.replace("https://", "http://"),
        f"https://downloads.example.org/{NAME}.whl",
    ],
)
def test_a_pinned_url_off_the_allow_list_is_refused_without_a_request(
    home: Path, pinned: str
) -> None:
    payload = wheel(GOOD_MEMBERS)
    spec = ModelSpec(NAME, VERSION, pinned, spec_for(payload).sha256)
    calls: list[str] = []

    with serving(payload, calls) as client, pytest.raises(ModelDownloadError, match="refused"):
        pull(spec, client)

    assert calls == []
    assert [p for p in leftovers(home) if p.is_file()] == []


def test_the_client_never_follows_redirects_by_itself() -> None:
    with open_client() as client:
        assert client.follow_redirects is False


def test_the_allow_list_is_the_pinned_hosts_and_their_storage_hosts() -> None:
    # Widening it is a reviewed change to this test, not a side effect.
    assert (
        frozenset(
            {
                "github.com",
                "release-assets.githubusercontent.com",
                "objects.githubusercontent.com",
                "huggingface.co",
                "us.gcp.cdn.hf.co",
                "us.aws.cdn.hf.co",
                "cdn-lfs-us-1.hf.co",
                "cdn-lfs-eu-1.hf.co",
            }
        )
        == ALLOWED_HOSTS
    )


def test_every_pinned_wheel_is_https_on_an_allowed_host() -> None:
    for spec in SPACY_MODELS.values():
        url = httpx.URL(spec.url)
        assert url.scheme == "https", spec.name
        assert url.host in ALLOWED_HOSTS, spec.name


# ------------------------------------------------------------------ size cap, M4


def chunks(count: int, size: int, served: list[int]) -> Iterator[bytes]:
    for _ in range(count):
        served.append(size)
        yield b"\x00" * size


def streaming(body: Iterator[bytes], headers: dict[str, str] | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, headers=headers, content=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_byte_cap_is_the_pinned_size_plus_a_margin_or_a_ceiling() -> None:
    assert byte_cap(None) == MAX_DOWNLOAD_BYTES == 2 * 1024**3
    assert byte_cap(1000) == 1000 + SIZE_MARGIN
    assert 0 < SIZE_MARGIN <= 1024**2


@pytest.mark.parametrize("size", [-1, True])
def test_a_recorded_size_must_be_a_byte_count(size: int) -> None:
    with pytest.raises(ValueError, match="size"):
        ModelSpec(NAME, VERSION, URL, "0" * 64, size=size)


@pytest.mark.parametrize("headers", [None, {"Content-Length": "100"}])
def test_a_stream_past_the_cap_is_aborted_even_if_its_length_claims_less(
    tmp_path: Path, headers: dict[str, str] | None
) -> None:
    served: list[int] = []
    destination = tmp_path / "model.whl"

    with (
        streaming(chunks(10_000, 1024, served), headers) as client,
        pytest.raises(ModelDownloadError, match="larger than 4096 bytes"),
    ):
        download_verified(URL, "0" * 64, NAME, client, destination, max_bytes=4096)

    assert len(served) <= 5  # stopped at the first chunk past the cap, not at 10 MB
    assert not destination.exists()


def test_a_declared_length_past_the_cap_is_refused_before_the_body(tmp_path: Path) -> None:
    served: list[int] = []

    with (
        streaming(chunks(8, 1024, served), {"Content-Length": "8192"}) as client,
        pytest.raises(ModelDownloadError, match="larger than 4096 bytes"),
    ):
        download_verified(URL, "0" * 64, NAME, client, tmp_path / "m", max_bytes=4096)

    assert served == []
    assert not (tmp_path / "m").exists()


def test_a_file_of_exactly_the_cap_is_accepted(tmp_path: Path) -> None:
    served: list[int] = []
    expected = hashlib.sha256(b"\x00" * 4096).hexdigest()

    with streaming(chunks(4, 1024, served)) as client:
        download_verified(URL, expected, NAME, client, tmp_path / "m", max_bytes=4096)

    assert (tmp_path / "m").stat().st_size == 4096


def test_a_wheel_larger_than_its_recorded_size_is_not_installed(home: Path) -> None:
    payload = wheel({**GOOD_MEMBERS, f"{INNER}ner/big": b"\x00" * (SIZE_MARGIN + 4096)})
    spec = ModelSpec(NAME, VERSION, URL, spec_for(payload).sha256, size=1024)

    with serving(payload) as client, pytest.raises(ModelDownloadError, match="larger than"):
        pull(spec, client)

    assert not installed(spec)
    assert [p for p in leftovers(home) if p.is_file()] == []


# ------------------------------------------------------------------ partial downloads, M4


def failing_after(count: int, error: BaseException, served: list[int]) -> Iterator[bytes]:
    yield from chunks(count, 1024, served)
    raise error


def test_a_digest_mismatch_deletes_the_download(tmp_path: Path) -> None:
    destination = tmp_path / "model.whl"

    with (
        serving(b"pinned bytes") as client,
        pytest.raises(ModelDownloadError, match="SHA-256"),
    ):
        download_verified(URL, "0" * 64, NAME, client, destination)

    assert not destination.exists()


@pytest.mark.parametrize(
    ("error", "raised"),
    [
        (httpx.ReadError("connection reset"), ModelDownloadError),
        (KeyboardInterrupt(), KeyboardInterrupt),
        (OSError("disk full"), OSError),
    ],
)
def test_a_download_cut_short_deletes_the_partial_file(
    tmp_path: Path, error: BaseException, raised: type[BaseException]
) -> None:
    served: list[int] = []
    destination = tmp_path / "model.whl"

    with streaming(failing_after(3, error, served)) as client, pytest.raises(raised):
        download_verified(URL, "0" * 64, NAME, client, destination)

    assert served == [1024] * 3  # some bytes were written before it failed
    assert not destination.exists()


def test_an_http_error_leaves_no_file(tmp_path: Path) -> None:
    destination = tmp_path / "model.whl"

    with serving(b"") as client, pytest.raises(ModelDownloadError, match="404"):
        download_verified(URL + "?gone", "0" * 64, NAME, client, destination)

    assert not destination.exists()
