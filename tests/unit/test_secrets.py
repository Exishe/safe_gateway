from collections.abc import Iterator

import keyring
import pytest
from keyring.backend import KeyringBackend
from keyring.errors import KeyringError, PasswordDeleteError

from gateway.secrets import (
    SERVICE,
    ApiKey,
    KeychainError,
    KeyringKeyProvider,
    api_key_user,
    read_api_key,
)

FAKE_KEY = "fake-key-for-tests-0123456789"  # pragma: allowlist secret


class MemoryKeyring(KeyringBackend):
    priority = 1  # type: ignore[assignment]  (keyring declares it as a class property)

    def __init__(self) -> None:
        super().__init__()
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if self.store.pop((service, username), None) is None:
            raise PasswordDeleteError(username)


class BrokenKeyring(MemoryKeyring):
    def get_password(self, service: str, username: str) -> str | None:
        msg = f"backend exploded while reading {service}/{username}"
        raise KeyringError(msg)


@pytest.fixture
def memory() -> Iterator[MemoryKeyring]:
    previous = keyring.get_keyring()
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(previous)


def test_user_name_per_provider() -> None:
    assert api_key_user("openai") == "api-key:openai"


def test_key_is_read_from_the_keychain(memory: MemoryKeyring) -> None:
    memory.store[(SERVICE, "api-key:openai")] = FAKE_KEY
    key = read_api_key("openai")
    assert key is not None
    assert key.reveal() == FAKE_KEY


@pytest.mark.usefixtures("memory")
def test_missing_key_is_none() -> None:
    assert read_api_key("anthropic") is None


def test_empty_key_is_none(memory: MemoryKeyring) -> None:
    memory.store[(SERVICE, "api-key:openai")] = ""
    assert read_api_key("openai") is None


def test_environment_is_never_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    previous = keyring.get_keyring()
    keyring.set_keyring(MemoryKeyring())
    try:
        assert read_api_key("openai") is None
    finally:
        keyring.set_keyring(previous)


def test_failing_backend_raises_keychain_error() -> None:
    previous = keyring.get_keyring()
    keyring.set_keyring(BrokenKeyring())
    try:
        with pytest.raises(KeychainError, match="keychain") as error:
            read_api_key("openai")
    finally:
        keyring.set_keyring(previous)
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__


def test_api_key_never_prints_its_value() -> None:
    key = ApiKey(FAKE_KEY)
    assert FAKE_KEY not in repr(key)
    assert FAKE_KEY not in str(key)
    assert FAKE_KEY not in f"{key}"


def test_empty_api_key_is_refused() -> None:
    with pytest.raises(ValueError, match="empty"):
        ApiKey("")


def test_keyring_key_provider_round_trip(memory: MemoryKeyring) -> None:
    provider = KeyringKeyProvider()
    assert provider.get("k") is None
    provider.set("k", b"value")
    assert memory.store[(SERVICE, "k")] == "value"
    assert provider.get("k") == b"value"
    assert provider.delete("k")
    assert not provider.delete("k")
