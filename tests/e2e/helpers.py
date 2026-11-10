"""What the browser tests share: finding programs and the gateway's environment."""

import os
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def program(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        pytest.skip(f"{name} is not installed")
    return path


def server_env(tmp_path: Path, home: Path) -> dict[str, str]:
    """A temporary SAFEGUARD_HOME and a test-only keyring in a file.

    `safeguard serve` refuses to start without a keychain for the audit log's device
    key (ADR 0021 point 12). The test machine has none, so the server gets the file
    keyring in tests/e2e/web, selected by name. No `--no-audit`: the real audit
    path, device key included, is what the tests cover.
    """
    return {
        **os.environ,
        "SAFEGUARD_HOME": str(home),
        "PYTHON_KEYRING_BACKEND": "tests.e2e.web.file_keyring.FileKeyring",
        "SAFEGUARD_E2E_KEYRING": str(tmp_path / "keyring.json"),
        "PYTHONPATH": str(ROOT),  # so keyring can import the backend from tests/
    }
