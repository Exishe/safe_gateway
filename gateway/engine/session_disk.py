"""Opt-in encrypted disk store for placeholder mappings (ADR 0002 point 3).

Each session's mapping is serialised in memory, encrypted with Fernet under a key
made for that session, and only the ciphertext is written. The key lives in a
`KeyProvider` (the OS keychain in production), never next to the file. File and
key are named by sha256 of the session id, so a directory listing or a keychain
listing does not reveal ids, and a purge can find the key of an old file.
"""

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Protocol

from cryptography.fernet import Fernet, InvalidToken

from gateway.engine.mask import Mapping

_SUFFIX = ".enc"
_DIR_MODE = 0o700


class StoredMappingNotFound(KeyError):  # noqa: N818  (reads as what happened, like KeyError)
    """No stored mapping, or its key is gone. The session id is not echoed back."""


class KeyProvider(Protocol):
    """Holds one Fernet key per key id. `get` returns None when there is none."""

    def get(self, key_id: str) -> bytes | None: ...

    def set(self, key_id: str, key: bytes) -> None: ...

    def delete(self, key_id: str) -> bool: ...


class InMemoryKeyProvider:
    """Keys in a dict: for tests and for callers that bring their own key custody."""

    def __init__(self) -> None:
        self._keys: dict[str, bytes] = {}

    def get(self, key_id: str) -> bytes | None:
        return self._keys.get(key_id)

    def set(self, key_id: str, key: bytes) -> None:
        self._keys[key_id] = key

    def delete(self, key_id: str) -> bool:
        return self._keys.pop(key_id, None) is not None


def default_sessions_dir() -> Path:
    """`<SAFEGUARD_HOME>/sessions`, with SAFEGUARD_HOME defaulting to `~/.safeguard`."""
    home = os.environ.get("SAFEGUARD_HOME")
    base = Path(home) if home else Path.home() / ".safeguard"
    return base / "sessions"


class EncryptedMappingStore:
    """Session id -> encrypted Mapping file, with the key held by `key_provider`."""

    def __init__(self, directory: Path, key_provider: KeyProvider) -> None:
        self.directory = directory
        self._keys = key_provider

    @staticmethod
    def key_id(session_id: str) -> str:
        """Name of the session's file stem and key: sha256 hex, never the id itself."""
        return hashlib.sha256(session_id.encode()).hexdigest()

    def path_for(self, session_id: str) -> Path:
        return self.directory / f"{self.key_id(session_id)}{_SUFFIX}"

    def save(self, session_id: str, mapping: Mapping) -> None:
        key_id = self.key_id(session_id)
        key = self._keys.get(key_id)
        if key is None:
            key = Fernet.generate_key()
            self._keys.set(key_id, key)
        token = Fernet(key).encrypt(json.dumps(mapping.to_dict()).encode())
        self._write_atomically(self.path_for(session_id), token)

    def load(self, session_id: str) -> Mapping:
        """The stored mapping; raises InvalidToken if the file or key is not the one saved."""
        path = self.path_for(session_id)
        key = self._keys.get(self.key_id(session_id))
        if key is None or not path.is_file():
            raise StoredMappingNotFound
        plaintext = Fernet(key).decrypt(path.read_bytes())  # all-or-nothing: raises on any change
        data = json.loads(plaintext)
        if not isinstance(data, dict):
            msg = "stored mapping is not an object"
            raise InvalidToken(msg)
        return Mapping.from_dict(data)

    def delete(self, session_id: str) -> bool:
        """Remove the file and the key; True if either existed."""
        return self._remove(self.key_id(session_id))

    def purge_older_than(self, seconds: float) -> int:
        """Delete files (and their keys) not written for more than `seconds`."""
        if not self.directory.is_dir():
            return 0
        cutoff = time.time() - seconds
        purged = 0
        for path in self.directory.glob(f"*{_SUFFIX}"):
            try:
                old = path.stat().st_mtime < cutoff
            except FileNotFoundError:
                continue
            if old and self._remove(path.stem):
                purged += 1
        return purged

    def _remove(self, key_id: str) -> bool:
        path = self.directory / f"{key_id}{_SUFFIX}"
        had_file = path.exists()
        path.unlink(missing_ok=True)
        had_key = self._keys.delete(key_id)
        return had_file or had_key

    def _write_atomically(self, path: Path, data: bytes) -> None:
        self.directory.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)
        # mkstemp creates the file 0600; the rename is atomic on one filesystem.
        fd, temp_name = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            Path(temp_name).replace(path)
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise
