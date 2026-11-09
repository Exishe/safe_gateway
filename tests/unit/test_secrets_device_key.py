import re

import pytest

from gateway.engine.session_disk import InMemoryKeyProvider
from gateway.secrets import AUDIT_KEY_ID, device_key, load_device_key

HEX64 = re.compile(rb"[0-9a-f]{64}")


def test_device_key_is_created_on_first_use_and_stored() -> None:
    keys = InMemoryKeyProvider()
    key = device_key(keys)
    assert HEX64.fullmatch(key)
    assert keys.get(AUDIT_KEY_ID) == key


def test_device_key_is_stable_once_created() -> None:
    keys = InMemoryKeyProvider()
    assert device_key(keys) == device_key(keys)


def test_two_devices_get_different_keys() -> None:
    assert device_key(InMemoryKeyProvider()) != device_key(InMemoryKeyProvider())


def test_load_device_key_never_creates_one() -> None:
    keys = InMemoryKeyProvider()
    assert load_device_key(keys) is None
    assert keys.get(AUDIT_KEY_ID) is None


def test_a_corrupt_stored_key_is_refused() -> None:
    keys = InMemoryKeyProvider()
    keys.set(AUDIT_KEY_ID, b"short")
    with pytest.raises(ValueError, match="device key"):
        load_device_key(keys)
    with pytest.raises(ValueError, match="device key"):
        device_key(keys)
