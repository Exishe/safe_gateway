"""Mappings kept between mask and rehydrate (ADR 0002 points 2 and 3).

Memory only by default. Given an `EncryptedMappingStore`, each mapping is also kept
encrypted on disk so it survives a restart, and is removed with the session.
"""

import math
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from gateway.engine.mask import Mapping
from gateway.engine.session_disk import EncryptedMappingStore, StoredMappingNotFound

DEFAULT_TTL_SECONDS = 30 * 60
_ID_BYTES = 32  # token_urlsafe(32): 43 characters, 256 bits


class SessionNotFound(KeyError):  # noqa: N818  (reads as what happened, like KeyError)
    """No such session, or it was idle past its TTL. The id is not echoed back."""


@dataclass
class _Session:
    mapping: Mapping
    last_used: float
    saved_size: int = 0  # entries on disk at the last save; growth means a re-save


class SessionStore:
    """Session id -> Mapping, dropped after `ttl_seconds` without use or on delete."""

    def __init__(
        self,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        persist: EncryptedMappingStore | None = None,
    ) -> None:
        # NaN compares false with everything, so `<= 0` alone would accept it and never expire.
        if not math.isfinite(ttl_seconds) or ttl_seconds <= 0:
            msg = f"session ttl must be a positive, finite number of seconds, got {ttl_seconds}"
            raise ValueError(msg)
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._persist = persist
        self._sessions: dict[str, _Session] = {}
        # The proxy serves requests from worker threads.
        self._lock = threading.Lock()

    def create(self, mapping: Mapping | None = None) -> str:
        session_id = secrets.token_urlsafe(_ID_BYTES)
        mapping = Mapping() if mapping is None else mapping  # an empty Mapping is falsy
        with self._lock:
            self._purge(self._clock())
            session = _Session(mapping, self._clock())
            self._sessions[session_id] = session
            self._save(session_id, session)
        return session_id

    def get(self, session_id: str) -> Mapping:
        """The session's mapping; using it restarts the idle timer."""
        with self._lock:
            now = self._clock()
            self._purge(now)
            session = self._sessions.get(session_id) or self._restore(session_id, now)
            if session is None or self._expired(session, now):
                self._drop(session_id)
                raise SessionNotFound
            session.last_used = now
            if len(session.mapping) != session.saved_size:  # the caller added entries
                self._save(session_id, session)
            return session.mapping

    def save(self, session_id: str) -> None:
        """Write the session's current mapping to disk (no-op when memory-only)."""
        with self._lock:
            self._purge(self._clock())
            session = self._sessions.get(session_id)
            if session is None:
                raise SessionNotFound
            self._save(session_id, session)

    def delete(self, session_id: str) -> bool:
        with self._lock:
            return self._drop(session_id)

    def purge_expired(self) -> int:
        with self._lock:
            return self._purge(self._clock())

    def __contains__(self, session_id: object) -> bool:
        with self._lock:
            self._purge(self._clock())
            return session_id in self._sessions

    def __len__(self) -> int:
        with self._lock:
            self._purge(self._clock())
            return len(self._sessions)

    def _purge(self, now: float) -> int:
        """Drop every expired session, so no answer counts or keeps one (call under the lock).

        Every public method runs this first: expiry must not wait for a get() on that id.
        """
        expired = [sid for sid, s in self._sessions.items() if self._expired(s, now)]
        for session_id in expired:
            self._drop(session_id)
        return len(expired)

    def _expired(self, session: _Session, now: float) -> bool:
        return now - session.last_used > self.ttl_seconds

    def _save(self, session_id: str, session: _Session) -> None:
        if self._persist is not None:
            self._persist.save(session_id, session.mapping)
            session.saved_size = len(session.mapping)

    def _restore(self, session_id: str, now: float) -> _Session | None:
        """A session saved by an earlier process; its idle timer starts now."""
        if self._persist is None:
            return None
        try:
            mapping = self._persist.load(session_id)
        except StoredMappingNotFound:
            return None
        session = _Session(mapping, now, saved_size=len(mapping))
        self._sessions[session_id] = session
        return session

    def _drop(self, session_id: str) -> bool:
        if self._persist is not None:
            self._persist.delete(session_id)
        session = self._sessions.pop(session_id, None)
        if session is None:
            return False
        # Drop the values now rather than whenever the last reference goes.
        session.mapping.clear()
        return True
