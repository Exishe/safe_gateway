"""Pinned model downloads for `safeguard models pull` (ADR 0006 point 4, ADR 0012 point 3).

This is the only code that fetches model files. Each wheel is pinned by URL
and SHA-256, streamed to a temporary file, verified, and only its model data
directory (`<name>/<name>-<version>/`) is unpacked. The engine later loads that
directory by path and never by name, so spaCy's own downloader never runs.

An install is the unpacked directory renamed into place with a completion marker
written into it last (`gateway/engine/model_marker.py`). A directory without a marker
for the current pin, such as one left by an interrupted pull, is missing and replaced.
"""

import hashlib
import re
import shutil
import tempfile
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

import httpx

from gateway.engine.model_marker import Pin, is_installed, write_marker

# One answer to "where do models live", shared with the engine that reads them.
from gateway.engine.ner import models_home

_RELEASES = "https://github.com/explosion/spacy-models/releases/download"
_SHA256 = re.compile(r"[0-9a-f]{64}")

# Every host a model download may reach: the pinned origins and the storage hosts they
# redirect to. Exact names, no wildcards, https only. A host not listed fails the pull by
# name and is added here only after review (ADR 0006, amended 2026-11-09).
ALLOWED_HOSTS = frozenset(
    {
        # GitHub release downloads (the pinned wheels)...
        "github.com",
        # ...answer 302 to the release-asset store (seen for both wheels, 2026-11-09)...
        "release-assets.githubusercontent.com",
        # ...which replaced this earlier release-asset host, named in the security review.
        "objects.githubusercontent.com",
        # Hugging Face resolve URLs (the pinned files); small files are served from here.
        "huggingface.co",
        # Large files answer 302 to this CDN edge (seen for both weight files, 2026-11-09).
        "us.gcp.cdn.hf.co",
        # The other download CDN hosts Hugging Face lists for firewalls (docs, 2026-11-09).
        "us.aws.cdn.hf.co",
        "cdn-lfs-us-1.hf.co",
        "cdn-lfs-eu-1.hf.co",
    }
)
MAX_REDIRECTS = 5
# Per-file byte cap: the recorded size plus this margin, or the ceiling when no size is
# recorded. The pinned files are at most about 1.2 GB.
SIZE_MARGIN = 1024**2
MAX_DOWNLOAD_BYTES = 2 * 1024**3


def byte_cap(size: int | None) -> int:
    """The most bytes a download may stream: `size` plus a margin, or the 2 GiB ceiling."""
    return MAX_DOWNLOAD_BYTES if size is None else size + SIZE_MARGIN


def check_size(label: str, size: int | None) -> None:
    """ValueError unless `size` is absent or a whole number of bytes."""
    if size is not None and (isinstance(size, bool) or not isinstance(size, int) or size < 0):
        msg = f"{label}: size must be a byte count, not {size!r}"
        raise ValueError(msg)


class ModelDownloadError(Exception):
    """A model could not be fetched, verified or unpacked; nothing was installed."""


@dataclass(frozen=True)
class ModelSpec:
    name: str
    version: str
    url: str
    sha256: str
    size: int | None = None  # bytes, when recorded; caps the download (byte_cap)

    def __post_init__(self) -> None:
        if not _SHA256.fullmatch(self.sha256):
            msg = f"{self.name}: sha256 must be 64 lower-case hex digits"
            raise ValueError(msg)
        check_size(self.name, self.size)

    @property
    def directory_name(self) -> str:
        return f"{self.name}-{self.version}"

    @property
    def pin(self) -> Pin:
        """What the install's marker records: the directory and the wheel's digest."""
        return Pin(self.directory_name, self.sha256)


def _spacy_wheel(name: str, version: str, sha256: str) -> ModelSpec:
    url = f"{_RELEASES}/{name}-{version}/{name}-{version}-py3-none-any.whl"
    return ModelSpec(name, version, url, sha256)


# Digests of the wheels as first downloaded, 2026-11-09; public, not secrets, hence the
# pragmas for detect-secrets. Both models are MIT; their meta.json asks for spaCy >=3.8,<3.9.
SPACY_MODELS: Mapping[str, ModelSpec] = {
    "en": _spacy_wheel(
        "en_core_web_lg",
        "3.8.0",
        # pragma: allowlist nextline secret
        "293e9547a655b25499198ab15a525b05b9407a75f10255e405e8c3854329ab63",
    ),
    "ru": _spacy_wheel(
        "ru_core_news_lg",
        "3.8.0",
        # pragma: allowlist nextline secret
        "90bd584be86772b647e1b15cdc62e09d3a7182284ee23da95e19c2bf71aa0189",
    ),
}


def install_dir(spec: ModelSpec) -> Path:
    return models_home() / "spacy" / spec.directory_name


def installed(spec: ModelSpec) -> bool:
    """Whether `spec` is installed: its directory carries a marker for the current pin."""
    return is_installed(install_dir(spec), spec.pin)


def open_client() -> httpx.Client:
    # Redirects (to the storage hosts) are followed by download_verified, which checks each.
    return httpx.Client(follow_redirects=False, timeout=httpx.Timeout(60.0))


def pull(spec: ModelSpec, client: httpx.Client) -> Path:
    """Download, verify and unpack `spec`; return its directory. A verified install is kept."""
    target = install_dir(spec)
    if installed(spec):
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    # Staged next to the target, so the final rename stays on one file system.
    with tempfile.TemporaryDirectory(
        prefix=f".partial-{spec.directory_name}-", dir=target.parent
    ) as staging:
        wheel = Path(staging) / "model.whl"
        _download(spec, client, wheel)
        unpacked = Path(staging) / "model"
        _unpack(spec, wheel, unpacked)
        move_into_place(unpacked, target, spec.pin)
    return target


def move_into_place(staged: Path, target: Path, pin: Pin) -> None:
    """Make the verified directory `staged` the install at `target`, then mark it.

    `staged` lies in a staging directory next to `target`. Whatever is at `target`
    (an unmarked or outdated directory, or a link, never followed) is moved into that
    staging directory and deleted with it. The marker comes last, so an install cut
    short at any point before it counts as missing.
    """
    if target.is_symlink() or target.exists():
        target.replace(staged.parent / "replaced")
    staged.replace(target)
    write_marker(target, pin)


def _download(spec: ModelSpec, client: httpx.Client, destination: Path) -> None:
    download_verified(
        spec.url, spec.sha256, spec.name, client, destination, max_bytes=byte_cap(spec.size)
    )


def download_verified(  # noqa: PLR0913  (the cap is keyword-only; the rest name one download)
    url: str,
    sha256: str,
    label: str,
    client: httpx.Client,
    destination: Path,
    *,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
) -> None:
    """Stream `url` to `destination`; ModelDownloadError unless its SHA-256 is `sha256`.

    Redirects are followed here, hop by hop, only over https to `ALLOWED_HOSTS`, never by
    the client. The body may not exceed `max_bytes`, whatever its Content-Length says.
    On any failure, an interrupt included, `destination` is deleted.
    """
    complete = False
    try:
        actual = _fetch(url, label, client, destination, max_bytes)
        if actual != sha256:
            msg = (
                f"{label}: SHA-256 mismatch, expected {sha256}, got {actual}; nothing was installed"
            )
            raise ModelDownloadError(msg)
        complete = True
    finally:
        if not complete:
            destination.unlink(missing_ok=True)


def _fetch(url: str, label: str, client: httpx.Client, destination: Path, max_bytes: int) -> str:
    """Write the body at the end of `url`'s redirects to `destination`; return its SHA-256."""
    try:
        response = _follow(httpx.URL(url), label, client)
        try:
            response.raise_for_status()
            declared = response.headers.get("Content-Length", "")
            if declared.isdigit() and int(declared) > max_bytes:
                raise ModelDownloadError(_too_large(label, max_bytes))
            return _write_capped(response, label, destination, max_bytes)
        finally:
            response.close()
    except (httpx.HTTPError, httpx.InvalidURL) as error:
        msg = f"{label}: download failed: {error}"
        raise ModelDownloadError(msg) from error


def _follow(url: httpx.URL, label: str, client: httpx.Client) -> httpx.Response:
    """The open, streamed response for `url`, after at most MAX_REDIRECTS checked hops."""
    for _ in range(MAX_REDIRECTS + 1):
        _check_url(url, label)
        # follow_redirects=False here overrides the client: every hop comes back to us.
        request = client.build_request("GET", url)
        response = client.send(request, stream=True, follow_redirects=False)
        if not response.has_redirect_location:
            return response
        response.close()
        url = url.join(response.headers["Location"])  # a relative Location stays on its host
    msg = f"{label}: more than {MAX_REDIRECTS} redirects; nothing was installed"
    raise ModelDownloadError(msg)


def _check_url(url: httpx.URL, label: str) -> None:
    # The query is left out of the message: storage URLs carry signed tokens.
    if url.scheme != "https" or url.host not in ALLOWED_HOSTS or url.port not in {None, 443}:
        port = f":{url.port}" if url.port is not None else ""
        msg = (
            f"{label}: refused to fetch from {url.scheme}://{url.host}{port}: models come only "
            "over https from the hosts in ALLOWED_HOSTS (gateway/cloud/models.py); "
            "nothing was installed"
        )
        raise ModelDownloadError(msg)


def _write_capped(response: httpx.Response, label: str, destination: Path, max_bytes: int) -> str:
    digest = hashlib.sha256()
    written = 0
    with destination.open("wb") as out:
        for chunk in response.iter_bytes():
            written += len(chunk)
            if written > max_bytes:  # stop reading at once: the rest is never fetched
                raise ModelDownloadError(_too_large(label, max_bytes))
            digest.update(chunk)
            out.write(chunk)
    return digest.hexdigest()


def _too_large(label: str, max_bytes: int) -> str:
    return f"{label}: larger than {max_bytes} bytes; download aborted, nothing was installed"


def _is_unsafe(member: str) -> bool:
    """A name that could land outside the directory it is unpacked into."""
    posix = PurePosixPath(member)
    return (
        "\\" in member
        or posix.is_absolute()
        or ".." in posix.parts
        or bool(PureWindowsPath(member).drive)
    )


def _unpack(spec: ModelSpec, wheel: Path, destination: Path) -> None:
    prefix = f"{spec.name}/{spec.directory_name}/"
    try:
        with zipfile.ZipFile(wheel) as archive:
            members = archive.infolist()
            # The whole archive is refused, not just the entry: a pinned wheel never has one.
            unsafe = [m.filename for m in members if _is_unsafe(m.filename)]
            if unsafe:
                msg = f"{spec.name}: archive member with an unsafe path: {unsafe[0]!r}"
                raise ModelDownloadError(msg)
            model = [m for m in members if m.filename.startswith(prefix) and m.filename != prefix]
            if not any(m.filename == f"{prefix}config.cfg" for m in model):
                msg = f"{spec.name}: no model directory {prefix} in the archive"
                raise ModelDownloadError(msg)
            destination.mkdir()
            for member in model:
                _extract(
                    archive, member, PurePosixPath(member.filename[len(prefix) :]), destination
                )
    except zipfile.BadZipFile as error:
        msg = f"{spec.name}: not a wheel (zip) file: {error}"
        raise ModelDownloadError(msg) from error


def _extract(
    archive: zipfile.ZipFile, member: zipfile.ZipInfo, relative: PurePosixPath, destination: Path
) -> None:
    target = destination.joinpath(*relative.parts)
    # Checked again on the joined path: belt and braces over _is_unsafe.
    if not target.resolve().is_relative_to(destination.resolve()):
        msg = f"archive member with an unsafe path: {member.filename!r}"
        raise ModelDownloadError(msg)
    if member.is_dir():
        target.mkdir(parents=True, exist_ok=True)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    # Written by hand, not ZipFile.extract: no symlinks, no permission bits from the archive.
    with archive.open(member) as source, target.open("xb") as out:
        shutil.copyfileobj(source, out)
