"""Pinned Hugging Face downloads for `safeguard models pull` (ADR 0006 point 4, ADR 0015).

Each file is fetched from `huggingface.co/<repo>/resolve/<commit>/<file>`, pinned by
commit and SHA-256, into a staging directory that becomes the install only when every
file has been verified. The GLiNER config names its backbone by Hub id, which GLiNER
would resolve over the network for the tokenizer and the encoder config. The install
therefore also holds the backbone's tokenizer files and config, and the
`gliner_config.json` that GLiNER reads embeds that config as `encoder_config`; the
pinned original is kept beside it, byte for byte, as `gliner_config.source.json`.
As for wheels, the install is renamed into place and its completion marker written last.
"""

import hashlib
import json
import re
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import httpx

from gateway.cloud.models import ModelDownloadError, download_verified, move_into_place
from gateway.engine.model_marker import Pin, is_installed
from gateway.engine.ner import models_home

_HUB = "https://huggingface.co"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_COMMIT = re.compile(r"[0-9a-f]{40}")
SOURCE_CONFIG = "gliner_config.source.json"


@dataclass(frozen=True)
class HubFile:
    repo: str
    revision: str
    path: str
    sha256: str

    def __post_init__(self) -> None:
        if not _SHA256.fullmatch(self.sha256):
            msg = f"{self.repo}/{self.path}: sha256 must be 64 lower-case hex digits"
            raise ValueError(msg)
        # A branch or tag can move; only a commit pins the bytes.
        if not _COMMIT.fullmatch(self.revision):
            msg = f"{self.repo}: revision must be a 40-digit commit id, not {self.revision!r}"
            raise ValueError(msg)
        if "/" in self.path or self.path in {"", ".", ".."}:
            msg = f"{self.repo}: only top-level files are installed, not {self.path!r}"
            raise ValueError(msg)


@dataclass(frozen=True)
class HubModelSpec:
    name: str
    revision: str
    backbone: str
    files: tuple[HubFile, ...]

    @property
    def directory_name(self) -> str:
        return f"{self.name}-{self.revision}"

    @property
    def pin(self) -> Pin:
        """What the install's marker records: the directory and a digest over every file."""
        return Pin(self.directory_name, files_sha256(self.files))


def files_sha256(files: Iterable[HubFile]) -> str:
    """One SHA-256 over every pinned file's source and digest, in any listed order."""
    lines = sorted(f"{f.repo}@{f.revision}/{f.path} {f.sha256}\n" for f in files)
    return hashlib.sha256("".join(lines).encode()).hexdigest()


def hub_url(file: HubFile) -> str:
    return f"{_HUB}/{file.repo}/resolve/{file.revision}/{PurePosixPath(file.path)}"


_GLINER_REPO = "urchade/gliner_multi_pii-v1"
_GLINER_REVISION = "1fcf13e85f4eef5394e1fcd406cf2ca9ea82351d"  # pragma: allowlist secret
_BACKBONE_REPO = "microsoft/mdeberta-v3-base"
_BACKBONE_REVISION = "a0484667b22365f84929a935b5e50a51f71f159d"  # pragma: allowlist secret

# Digests of the files at these commits, 2026-11-09; public, not secrets, hence the
# pragmas for detect-secrets. Model card licenses: GLiNER PII apache-2.0 (fine-tuned from
# urchade/gliner_multi-v2.1, apache-2.0); mdeberta-v3-base mit. The backbone's weights are
# not fetched: GLiNER's own pytorch_model.bin holds the fine-tuned encoder.
GLINER_MODEL = HubModelSpec(
    name="gliner_multi_pii-v1",
    revision=_GLINER_REVISION,
    backbone=_BACKBONE_REPO,
    files=(
        HubFile(
            _GLINER_REPO,
            _GLINER_REVISION,
            "gliner_config.json",
            # pragma: allowlist nextline secret
            "2088bee85f50849dea2ab4cf411ab5cf3aaaf7c8a45c23412035978b2bc02801",
        ),
        HubFile(
            _GLINER_REPO,
            _GLINER_REVISION,
            "pytorch_model.bin",
            # pragma: allowlist nextline secret
            "3003753fba99e40645cf088c7367a2c6211fc174897dc64f1f9c147c29d18d2d",
        ),
        HubFile(
            _BACKBONE_REPO,
            _BACKBONE_REVISION,
            "config.json",
            # pragma: allowlist nextline secret
            "bcffcd343dc5efa5ef2d5a58d2b405eed108f01cc45b48d0a907b333ec41801f",
        ),
        HubFile(
            _BACKBONE_REPO,
            _BACKBONE_REVISION,
            "spm.model",
            # pragma: allowlist nextline secret
            "13c8d666d62a7bc4ac8f040aab68e942c861f93303156cc28f5c7e885d86d6e3",
        ),
        HubFile(
            _BACKBONE_REPO,
            _BACKBONE_REVISION,
            "tokenizer_config.json",
            # pragma: allowlist nextline secret
            "3f3978e0c036f2c2588cac34a6047cbb0af0b0dc1814254e291028529805496d",
        ),
    ),
)


def install_dir(spec: HubModelSpec) -> Path:
    return models_home() / "gliner" / spec.directory_name


def installed(spec: HubModelSpec) -> bool:
    """Whether `spec` is installed: its directory carries a marker for the current pin."""
    return is_installed(install_dir(spec), spec.pin)


def pull(spec: HubModelSpec, client: httpx.Client) -> Path:
    """Download and verify every file of `spec`, assemble the install; a verified one is kept."""
    target = install_dir(spec)
    if installed(spec):
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".partial-{spec.directory_name}-", dir=target.parent
    ) as staging:
        assembled = Path(staging) / "model"
        assembled.mkdir()
        for file in spec.files:
            label = f"{file.repo}/{file.path}"
            download_verified(hub_url(file), file.sha256, label, client, assembled / file.path)
        _write_offline_config(spec, assembled)
        move_into_place(assembled, target, spec.pin)
    return target


def _write_offline_config(spec: HubModelSpec, directory: Path) -> None:
    source = directory / "gliner_config.json"
    config = json.loads(source.read_text(encoding="utf-8"))
    if config.get("model_name") != spec.backbone:
        msg = (
            f"{spec.name}: config names backbone {config.get('model_name')!r}, "
            f"expected {spec.backbone!r}; nothing was installed"
        )
        raise ModelDownloadError(msg)
    source.rename(directory / SOURCE_CONFIG)
    config["encoder_config"] = json.loads((directory / "config.json").read_text(encoding="utf-8"))
    source.write_text(json.dumps(config, indent=2, sort_keys=True), encoding="utf-8")
