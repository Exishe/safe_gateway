"""The file-backed keyring the browser tests give `safeguard serve` (tests only)."""

import stat
from pathlib import Path

import keyring
import pytest
from keyring.errors import PasswordDeleteError

from tests.e2e.web.file_keyring import FileKeyring

STORED_VALUE = "e2e-device-key-not-a-real-one"  # pragma: allowlist secret


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "keyring.json"
    monkeypatch.setenv("SAFEGUARD_E2E_KEYRING", str(path))
    return path


def test_a_secret_survives_a_new_backend_like_a_restarted_server(store: Path) -> None:
    FileKeyring().set_password("safeguard-gateway", "audit-device-key", STORED_VALUE)
    assert FileKeyring().get_password("safeguard-gateway", "audit-device-key") == STORED_VALUE
    assert FileKeyring().get_password("safeguard-gateway", "other") is None
    assert stat.S_IMODE(store.stat().st_mode) == 0o600


def test_delete_removes_it_and_a_missing_one_raises(store: Path) -> None:
    backend = FileKeyring()
    backend.set_password("s", "u", STORED_VALUE)
    backend.delete_password("s", "u")
    assert backend.get_password("s", "u") is None
    with pytest.raises(PasswordDeleteError):
        backend.delete_password("s", "u")
    assert store.exists()


def test_without_its_variable_it_refuses_instead_of_writing_somewhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SAFEGUARD_E2E_KEYRING", raising=False)
    with pytest.raises(RuntimeError, match="SAFEGUARD_E2E_KEYRING"):
        FileKeyring().get_password("s", "u")


def test_keyring_selects_it_by_name_as_the_server_will(
    store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    previous = keyring.get_keyring()
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "tests.e2e.web.file_keyring.FileKeyring")
    try:
        keyring.core.init_backend()
        assert isinstance(keyring.get_keyring(), FileKeyring)
        keyring.set_password("safeguard-gateway", "k", STORED_VALUE)
        assert store.exists()
    finally:
        keyring.set_keyring(previous)
