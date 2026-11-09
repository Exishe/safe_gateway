"""Mappings kept between mask and rehydrate, in memory only (ADR 0002 point 2).

The opt-in encrypted disk store of ADR 0002 point 3 is not built yet.
"""

import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from gateway.engine.mask import Mapping

DEFAULT_TTL_SECONDS = 30 * 60
_ID_BYTES = 32  # token_urlsafe(32): 43 characters, 256 bits


class SessionNotFound(KeyError):  # noqa: N818  (reads as what happened, like KeyError)
    """No such session, or it was idle past its TTL. The id is not echoed back."""


@dataclass
class _Session:
    mapping: Mapping
    last_used: float


class SessionStore:
    """Session id -> Mapping, dropped after `ttl_seconds` without use or on delete."""

    def __init__(
        self,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            msg = f"session ttl must be positive, got {ttl_seconds}"
            raise ValueError(msg)
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._sessions: dict[str, _Session] = {}
        # The proxy serves requests from worker threads.
        self._lock = threading.Lock()

    def create(self, mapping: Mapping | None = None) -> str:
        session_id = secrets.token_urlsafe(_ID_BYTES)
        mapping = Mapping() if mapping is None else mapping  # an empty Mapping is falsy
        with self._lock:
            self._sessions[session_id] = _Session(mapping, self._clock())
        return session_id

    def get(self, session_id: str) -> Mapping:
        """The session's mapping; using it restarts the idle timer."""
        with self._lock:
            session = self._sessions.get(session_id)
            now = self._clock()
            if session is None or self._expired(session, now):
                self._drop(session_id)
                raise SessionNotFound
            session.last_used = now
            return session.mapping

    def delete(self, session_id: str) -> bool:
        with self._lock:
            return self._drop(session_id)

    def purge_expired(self) -> int:
        with self._lock:
            now = self._clock()
            expired = [sid for sid, s in self._sessions.items() if self._expired(s, now)]
            for session_id in expired:
                self._drop(session_id)
            return len(expired)

    def __contains__(self, session_id: object) -> bool:
        with self._lock:
            return session_id in self._sessions

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)

    def _expired(self, session: _Session, now: float) -> bool:
        return now - session.last_used > self.ttl_seconds

    def _drop(self, session_id: str) -> bool:
        session = self._sessions.pop(session_id, None)
        if session is None:
            return False
        # Drop the values now rather than whenever the last reference goes.
        session.mapping.clear()
        return True
