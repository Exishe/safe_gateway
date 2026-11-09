"""Pinned model downloads for `safeguard models pull` (ADR 0006 point 4, ADR 0012 point 3).

This is the only code that fetches model files. Each wheel is pinned by URL
and SHA-256, streamed to a temporary file, verified, and only its model data
directory (`<name>/<name>-<version>/`) is unpacked. The engine later loads that
directory by path and never by name, so spaCy's own downloader never runs.
"""

import hashlib
import os
import re
import shutil
import tempfile
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

import httpx

_RELEASES = "https://github.com/explosion/spacy-models/releases/download"
_SHA256 = re.compile(r"[0-9a-f]{64}")


class ModelDownloadError(Exception):
    """A model could not be fetched, verified or unpacked; nothing was installed."""


@dataclass(frozen=True)
class ModelSpec:
    name: str
    version: str
    url: str
    sha256: str

    def __post_init__(self) -> None:
        if not _SHA256.fullmatch(self.sha256):
            msg = f"{self.name}: sha256 must be 64 lower-case hex digits"
            raise ValueError(msg)

    @property
    def directory_name(self) -> str:
        return f"{self.name}-{self.version}"


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


def models_home() -> Path:
    """`$SAFEGUARD_HOME/models`, or `~/.safeguard/models`."""
    base = os.environ.get("SAFEGUARD_HOME")
    return (Path(base) if base else Path.home() / ".safeguard") / "models"


def install_dir(spec: ModelSpec) -> Path:
    return models_home() / "spacy" / spec.directory_name


def open_client() -> httpx.Client:
    # Redirects: GitHub serves release assets from its storage host.
    return httpx.Client(follow_redirects=True, timeout=httpx.Timeout(60.0))


def pull(spec: ModelSpec, client: httpx.Client) -> Path:
    """Download, verify and unpack `spec`; return its directory. A present install is kept."""
    target = install_dir(spec)
    if target.is_dir():
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
        unpacked.rename(target)
    return target


def _download(spec: ModelSpec, client: httpx.Client, destination: Path) -> None:
    digest = hashlib.sha256()
    try:
        with client.stream("GET", spec.url) as response, destination.open("wb") as out:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                digest.update(chunk)
                out.write(chunk)
    except httpx.HTTPError as error:
        msg = f"{spec.name}: download failed: {error}"
        raise ModelDownloadError(msg) from error
    if digest.hexdigest() != spec.sha256:
        msg = (
            f"{spec.name}: SHA-256 mismatch, expected {spec.sha256}, got {digest.hexdigest()}; "
            "nothing was installed"
        )
        raise ModelDownloadError(msg)


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
