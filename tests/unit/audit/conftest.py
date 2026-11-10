from collections.abc import Iterator

import keyring
import pytest
from keyring.backend import KeyringBackend


class RefusingKeyring(KeyringBackend):
    """Any use fails: an audit test that forgets to inject a key provider fails loudly
    instead of reading or writing the developer's real OS keychain."""

    priority = 1  # type: ignore[assignment]

    def get_password(self, service: str, username: str) -> str | None:
        msg = f"test touched the keychain ({service}/{username}); inject InMemoryKeyProvider"
        raise AssertionError(msg)

    def set_password(self, service: str, username: str, password: str) -> None:
        self.get_password(service, username)
        del password

    def delete_password(self, service: str, username: str) -> None:
        self.get_password(service, username)


@pytest.fixture(autouse=True)
def _no_real_keychain() -> Iterator[None]:
    previous = keyring.get_keyring()
    keyring.set_keyring(RefusingKeyring())
    try:
        yield
    finally:
        keyring.set_keyring(previous)
