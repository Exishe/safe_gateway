"""OS keychain access, kept outside `gateway/engine/` and injected into it.

`KeyringKeyProvider` is the default `KeyProvider` for the encrypted session store
(ADR 0002 point 3). Tests use `InMemoryKeyProvider`, never the real keychain.
"""

import keyring
from keyring.errors import PasswordDeleteError

SERVICE = "safeguard-gateway"


class KeyringKeyProvider:
    """Fernet keys in the OS keychain under service "safeguard-gateway"."""

    def __init__(self, service: str = SERVICE) -> None:
        self._service = service

    def get(self, key_id: str) -> bytes | None:
        value = keyring.get_password(self._service, key_id)
        return None if value is None else value.encode()

    def set(self, key_id: str, key: bytes) -> None:
        keyring.set_password(self._service, key_id, key.decode())

    def delete(self, key_id: str) -> bool:
        try:
            keyring.delete_password(self._service, key_id)
        except PasswordDeleteError:  # no such entry
            return False
        return True
