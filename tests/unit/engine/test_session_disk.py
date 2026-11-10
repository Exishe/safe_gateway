import hashlib
import os
import stat
import time
from pathlib import Path

import keyring
import pytest
from cryptography.fernet import Fernet, InvalidToken
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError

from gateway.engine.mask import Mapping
from gateway.engine.session import SessionNotFound, SessionStore
from gateway.engine.session_disk import (
    EncryptedMappingStore,
    InMemoryKeyProvider,
    StoredMappingNotFound,
    default_sessions_dir,
)
from gateway.engine.types import EntityType
from gateway.secrets import SERVICE, KeyringKeyProvider

SESSION_ID = "session-abc-123"


def make_mapping() -> Mapping:
    mapping = Mapping()
    mapping.placeholder_for("Anna Example", EntityType.PERSON)
    mapping.placeholder_for("anna@example.invalid", EntityType.EMAIL)
    return mapping


@pytest.fixture
def keys() -> InMemoryKeyProvider:
    return InMemoryKeyProvider()


@pytest.fixture
def disk(tmp_path: Path, keys: InMemoryKeyProvider) -> EncryptedMappingStore:
    return EncryptedMappingStore(tmp_path / "sessions", keys)


def files(disk: EncryptedMappingStore) -> list[Path]:
    return [p for p in disk.directory.iterdir() if p.is_file()]


def test_round_trip(disk: EncryptedMappingStore) -> None:
    mapping = make_mapping()
    disk.save(SESSION_ID, mapping)
    assert disk.load(SESSION_ID).to_dict() == mapping.to_dict()


def test_save_again_replaces_the_file(disk: EncryptedMappingStore) -> None:
    mapping = make_mapping()
    disk.save(SESSION_ID, mapping)
    mapping.placeholder_for("Bob Example", EntityType.PERSON)
    disk.save(SESSION_ID, mapping)
    assert len(files(disk)) == 1
    assert len(disk.load(SESSION_ID)) == 3


def test_the_plaintext_is_not_on_disk(disk: EncryptedMappingStore) -> None:
    disk.save(SESSION_ID, make_mapping())
    raw = files(disk)[0].read_bytes()
    assert b"Anna" not in raw
    assert b"PERSON" not in raw


def test_wrong_key_fails(disk: EncryptedMappingStore, keys: InMemoryKeyProvider) -> None:
    disk.save(SESSION_ID, make_mapping())
    keys.set(disk.key_id(SESSION_ID), Fernet.generate_key())
    with pytest.raises(InvalidToken):
        disk.load(SESSION_ID)


def test_tampered_file_fails(disk: EncryptedMappingStore) -> None:
    disk.save(SESSION_ID, make_mapping())
    path = files(disk)[0]
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 0x01
    path.write_bytes(bytes(data))
    with pytest.raises(InvalidToken):
        disk.load(SESSION_ID)


def test_truncated_file_fails(disk: EncryptedMappingStore) -> None:
    disk.save(SESSION_ID, make_mapping())
    path = files(disk)[0]
    path.write_bytes(path.read_bytes()[:-10])
    with pytest.raises(InvalidToken):
        disk.load(SESSION_ID)


def test_empty_file_fails(disk: EncryptedMappingStore) -> None:
    disk.save(SESSION_ID, make_mapping())
    files(disk)[0].write_bytes(b"")
    with pytest.raises(InvalidToken):
        disk.load(SESSION_ID)


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_file_mode_is_0600(disk: EncryptedMappingStore) -> None:
    disk.save(SESSION_ID, make_mapping())
    assert stat.S_IMODE(files(disk)[0].stat().st_mode) == 0o600
    assert stat.S_IMODE(disk.directory.stat().st_mode) == 0o700


def test_file_name_does_not_reveal_the_session_id(disk: EncryptedMappingStore) -> None:
    disk.save(SESSION_ID, make_mapping())
    name = files(disk)[0].name
    assert SESSION_ID not in name
    assert hashlib.sha256(SESSION_ID.encode()).hexdigest() in name


def test_no_temp_files_are_left(disk: EncryptedMappingStore) -> None:
    disk.save(SESSION_ID, make_mapping())
    assert [p.suffix for p in disk.directory.iterdir()] == [".enc"]


def test_each_session_gets_its_own_key(
    disk: EncryptedMappingStore, keys: InMemoryKeyProvider
) -> None:
    disk.save("one", make_mapping())
    disk.save("two", make_mapping())
    assert keys.get(disk.key_id("one")) != keys.get(disk.key_id("two"))


def test_delete_removes_file_and_key(
    disk: EncryptedMappingStore, keys: InMemoryKeyProvider
) -> None:
    disk.save(SESSION_ID, make_mapping())
    assert disk.delete(SESSION_ID) is True
    assert files(disk) == []
    assert keys.get(disk.key_id(SESSION_ID)) is None
    assert disk.delete(SESSION_ID) is False
    with pytest.raises(StoredMappingNotFound):
        disk.load(SESSION_ID)


def test_load_unknown_session_does_not_echo_the_id(disk: EncryptedMappingStore) -> None:
    with pytest.raises(StoredMappingNotFound) as error:
        disk.load(SESSION_ID)
    assert SESSION_ID not in str(error.value)


def test_purge_older_than(disk: EncryptedMappingStore, keys: InMemoryKeyProvider) -> None:
    disk.save("old", make_mapping())
    disk.save("fresh", make_mapping())
    old_path = disk.path_for("old")
    stats = old_path.stat()
    os.utime(old_path, (stats.st_atime, stats.st_mtime - 1000))
    assert disk.purge_older_than(500) == 1
    assert not old_path.exists()
    assert keys.get(disk.key_id("old")) is None
    assert len(disk.load("fresh")) == 2


def test_purge_on_a_missing_directory_is_a_no_op(tmp_path: Path, keys: InMemoryKeyProvider) -> None:
    assert EncryptedMappingStore(tmp_path / "none", keys).purge_older_than(1) == 0


def test_default_directory_follows_safeguard_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SAFEGUARD_HOME", str(tmp_path))
    assert default_sessions_dir() == tmp_path / "sessions"
    monkeypatch.delenv("SAFEGUARD_HOME")
    assert default_sessions_dir() == Path.home() / ".safeguard" / "sessions"


# --- SessionStore wiring ---


def test_memory_only_default_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SAFEGUARD_HOME", str(tmp_path))
    store = SessionStore()
    session_id = store.create(make_mapping())
    store.get(session_id)
    store.delete(session_id)
    assert not (tmp_path / "sessions").exists()
    assert list(tmp_path.iterdir()) == []


def test_persisting_store_saves_on_create_and_removes_on_delete(
    disk: EncryptedMappingStore,
) -> None:
    store = SessionStore(persist=disk)
    session_id = store.create(make_mapping())
    assert disk.load(session_id).to_dict() == store.get(session_id).to_dict()
    store.delete(session_id)
    assert files(disk) == []


def test_changes_are_saved(disk: EncryptedMappingStore) -> None:
    store = SessionStore(persist=disk)
    session_id = store.create()
    store.get(session_id).placeholder_for("Anna Example", EntityType.PERSON)
    store.save(session_id)
    assert len(disk.load(session_id)) == 1


def test_get_saves_growth_since_the_last_save(disk: EncryptedMappingStore) -> None:
    store = SessionStore(persist=disk)
    session_id = store.create()
    store.get(session_id).placeholder_for("Anna Example", EntityType.PERSON)
    store.get(session_id)
    assert len(disk.load(session_id)) == 1


def test_expiry_removes_the_stored_file_and_key(
    disk: EncryptedMappingStore, keys: InMemoryKeyProvider
) -> None:
    now = [0.0]
    store = SessionStore(ttl_seconds=60, clock=lambda: now[0], persist=disk)
    session_id = store.create(make_mapping())
    now[0] += 61
    assert store.purge_expired() == 1
    assert files(disk) == []
    assert keys.get(disk.key_id(session_id)) is None


def test_a_new_store_restores_a_persisted_session(disk: EncryptedMappingStore) -> None:
    session_id = SessionStore(persist=disk).create(make_mapping())
    restored = SessionStore(persist=disk).get(session_id)
    assert len(restored) == 2


class FakeBackend(KeyringBackend):
    """An in-process keyring, so no test touches the real OS keychain."""

    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self.items: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.items.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.items[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if self.items.pop((service, username), None) is None:
            raise PasswordDeleteError


def test_keyring_provider_round_trip_on_a_fake_backend() -> None:
    backend = FakeBackend()
    previous = keyring.get_keyring()
    keyring.set_keyring(backend)
    try:
        provider = KeyringKeyProvider()
        assert provider.get("k") is None
        provider.set("k", b"fake-key")
        assert provider.get("k") == b"fake-key"
        assert (SERVICE, "k") in backend.items
        assert provider.delete("k") is True
        assert provider.delete("k") is False
    finally:
        keyring.set_keyring(previous)


def test_a_non_object_payload_is_refused(
    disk: EncryptedMappingStore, keys: InMemoryKeyProvider
) -> None:
    disk.save(SESSION_ID, make_mapping())
    key = keys.get(disk.key_id(SESSION_ID))
    assert key is not None
    disk.path_for(SESSION_ID).write_bytes(Fernet(key).encrypt(b"[]"))
    with pytest.raises(InvalidToken):
        disk.load(SESSION_ID)


def test_a_failed_write_leaves_no_temp_file(
    disk: EncryptedMappingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: object) -> Path:
        msg = "disk full"
        raise OSError(msg)

    monkeypatch.setattr(Path, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        disk.save(SESSION_ID, make_mapping())
    assert list(disk.directory.iterdir()) == []


def test_expiry_seen_by_contains_removes_the_stored_file_and_key(
    disk: EncryptedMappingStore, keys: InMemoryKeyProvider
) -> None:
    now = [0.0]
    store = SessionStore(ttl_seconds=60, clock=lambda: now[0], persist=disk)
    session_id = store.create(make_mapping())
    now[0] += 61
    assert session_id not in store
    assert files(disk) == []
    assert keys.get(disk.key_id(session_id)) is None


# ------------------------------------------------------------------ security review M11


class WallClock:
    """time.time() shifted by `offset` seconds, so a test can step into the future."""

    def __init__(self) -> None:
        self.offset = 0.0

    def __call__(self) -> float:
        return time.time() + self.offset


def test_a_persisting_store_purges_stale_files_when_it_starts(
    disk: EncryptedMappingStore, keys: InMemoryKeyProvider
) -> None:
    """purge_older_than was never called: a session file outlived its TTL on disk."""
    stale = SessionStore(persist=disk).create(make_mapping())
    fresh = SessionStore(persist=disk).create(make_mapping())
    old = time.time() - 3600
    os.utime(disk.path_for(stale), (old, old))
    SessionStore(ttl_seconds=60, persist=disk)
    assert not disk.path_for(stale).exists()
    assert keys.get(EncryptedMappingStore.key_id(stale)) is None
    assert disk.path_for(fresh).exists()


def test_a_restored_session_keeps_its_last_use(disk: EncryptedMappingStore) -> None:
    """_restore gave a stale session a fresh TTL: restarting revived it."""
    wall = WallClock()
    session_id = SessionStore(ttl_seconds=60, persist=disk, wall_clock=wall).create(make_mapping())
    restarted = SessionStore(ttl_seconds=60, persist=disk, wall_clock=wall)
    wall.offset = 70  # idle for longer than the TTL while the gateway was down
    with pytest.raises(SessionNotFound):
        restarted.get(session_id)
    assert not disk.path_for(session_id).exists()


def test_a_restored_session_inside_its_ttl_is_served(disk: EncryptedMappingStore) -> None:
    wall = WallClock()
    session_id = SessionStore(ttl_seconds=60, persist=disk, wall_clock=wall).create(make_mapping())
    wall.offset = 50
    restarted = SessionStore(ttl_seconds=60, persist=disk, wall_clock=wall)
    assert restarted.get(session_id).to_dict() == make_mapping().to_dict()


def test_use_without_changes_is_recorded_on_disk(disk: EncryptedMappingStore) -> None:
    """The file's time is the session's last use, so a restart keeps a busy session."""
    wall = WallClock()
    store = SessionStore(ttl_seconds=60, persist=disk, wall_clock=wall)
    session_id = store.create(make_mapping())
    wall.offset = 40
    store.get(session_id)
    assert disk.path_for(session_id).stat().st_mtime == pytest.approx(wall(), abs=1)
