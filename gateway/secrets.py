"""OS keychain access, kept outside `gateway/engine/` and injected into it.

`KeyringKeyProvider` is the default `KeyProvider` for the encrypted session store
(ADR 0002 point 3) and holds the audit log's device key (ADR 0021 point 7).
Tests use `InMemoryKeyProvider`, never the real keychain.
"""

import os
import re

import keyring
from keyring.errors import PasswordDeleteError

from gateway.engine.session_disk import KeyProvider

SERVICE = "safeguard-gateway"
AUDIT_KEY_ID = "audit-device-key"
_DEVICE_KEY = re.compile(rb"[0-9a-f]{64}")


def load_device_key(provider: KeyProvider) -> bytes | None:
    """The audit HMAC key, or None if this device has none yet. Never creates one."""
    key = provider.get(AUDIT_KEY_ID)
    if key is not None and not _DEVICE_KEY.fullmatch(key):
        msg = f"the audit device key in the keychain ({AUDIT_KEY_ID}) is not 64 hex characters"
        raise ValueError(msg)
    return key


def device_key(provider: KeyProvider) -> bytes:
    """The audit HMAC key, created on first use: 32 random bytes as 64 hex characters.

    Hex, so the HMAC key is plain ASCII and `openssl dgst -hmac` can check a row.
    """
    key = load_device_key(provider)
    if key is None:
        key = os.urandom(32).hex().encode("ascii")
        provider.set(AUDIT_KEY_ID, key)
    return key


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
