"""The completion marker of a model install (ADR 0006, amended 2026-11-09).

`safeguard models pull` writes the marker into a model directory as its very last
step, after every file was verified and the directory was renamed into place. An
install counts only when its marker names the model and the SHA-256 pinned for it
now, so an empty, half-unpacked or outdated directory counts as missing and the next
pull replaces it. The engine reads markers and gateway/cloud/ writes them; both use
this module, so the format is defined once. File I/O only.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

MARKER = ".safeguard-install.json"
_FORMAT = 1
_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class Pin:
    """What one install must be: its model id and the SHA-256 its download was pinned to."""

    model_id: str
    sha256: str

    def __post_init__(self) -> None:
        if not self.model_id:
            msg = "a pin needs a model id"
            raise ValueError(msg)
        if not _SHA256.fullmatch(self.sha256):
            msg = f"{self.model_id}: sha256 must be 64 lower-case hex digits"
            raise ValueError(msg)


def _record(pin: Pin) -> dict[str, object]:
    return {"format": _FORMAT, "model_id": pin.model_id, "sha256": pin.sha256}


def is_installed(directory: Path, pin: Pin) -> bool:
    """True only when `directory` holds a marker recording exactly `pin`."""
    try:
        recorded = json.loads((directory / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):  # absent, unreadable, not UTF-8, not JSON: not installed
        return False
    return recorded == _record(pin)


def write_marker(directory: Path, pin: Pin) -> None:
    """Record `directory` as a complete install of `pin`: the last step of a pull."""
    staged = directory / f"{MARKER}.tmp"
    staged.write_text(json.dumps(_record(pin), sort_keys=True), encoding="utf-8")
    # Renamed into place, so a marker is either whole or absent.
    staged.replace(directory / MARKER)
