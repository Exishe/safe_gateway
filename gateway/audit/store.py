"""The audit log writer: SQLite schema, appends and the head record (ADR 0021).

Every write is one `BEGIN IMMEDIATE` transaction, which is also the lock between
processes: check the head, chain the new row to it, insert, move the head.
"""

import hmac
import os
import sqlite3
import threading
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import Self

from gateway.audit.chain import GENESIS_HASH, head_hmac, row_hash, row_hmac
from gateway.audit.db import SCHEMA_VERSION, AuditLogError, check_schema, read_head
from gateway.audit.rows import checkpoint_content, format_ts, is_hex64, request_content
from gateway.audit.verify import VerifyResult, verify_connection
from gateway.engine.session_disk import KeyProvider
from gateway.secrets import device_key, load_device_key

_DIR_MODE = 0o700
_FILE_MODE = 0o600
_BUSY_TIMEOUT_SECONDS = 30

_CREATE_LOG = """
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('request', 'checkpoint')),
    ts_utc TEXT NOT NULL,
    session_id TEXT,
    upstream_host TEXT,
    model TEXT,
    entity_counts_json TEXT,
    masked_payload_sha256 TEXT,
    bytes_out INTEGER,
    status INTEGER,
    local INTEGER CHECK (local IN (0, 1)),
    purged_through_id INTEGER,
    purged_through_hash TEXT,
    purged_count INTEGER,
    purged_before_utc TEXT,
    prev_hash TEXT NOT NULL,
    row_hash TEXT NOT NULL,
    hmac TEXT NOT NULL
) STRICT
"""
_CREATE_HEAD = """
CREATE TABLE audit_head (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    head_id INTEGER NOT NULL,
    head_hash TEXT NOT NULL,
    hmac TEXT NOT NULL
) STRICT
"""
_INSERT_ROW = """
INSERT INTO audit_log (
    id, kind, ts_utc, session_id, upstream_host, model, entity_counts_json,
    masked_payload_sha256, bytes_out, status, local, purged_through_id,
    purged_through_hash, purged_count, purged_before_utc, prev_hash, row_hash, hmac
) VALUES (
    :id, :kind, :ts_utc, :session_id, :upstream_host, :model, :entity_counts_json,
    :masked_payload_sha256, :bytes_out, :status, :local, :purged_through_id,
    :purged_through_hash, :purged_count, :purged_before_utc, :prev_hash, :row_hash, :hmac
)
"""
_UPSERT_HEAD = """
INSERT INTO audit_head (singleton, head_id, head_hash, hmac) VALUES (1, ?, ?, ?)
ON CONFLICT (singleton) DO UPDATE SET
    head_id = excluded.head_id, head_hash = excluded.head_hash, hmac = excluded.hmac
"""


class AuditChainBroken(AuditLogError):  # noqa: N818  (reads as what happened)
    """The head record is missing, forged or behind: writing on would hide that."""


class DeviceKeyMissing(AuditLogError):  # noqa: N818
    """The log has rows but this keychain has no device key for it."""


@dataclass(frozen=True)
class PurgeResult:
    deleted: int
    checkpoint_id: int | None
    purged_through_id: int | None


def utc_now() -> datetime:
    return datetime.now(UTC)


def _prepare_file(path: Path) -> None:
    # Created 0600 before SQLite opens it: SQLite would use the umask, and the
    # journal files it makes copy the database file's mode.
    path.parent.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, _FILE_MODE)
    os.close(descriptor)
    path.chmod(_FILE_MODE)


def _configure(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA journal_mode = DELETE")
    connection.execute("PRAGMA synchronous = FULL")
    connection.execute("PRAGMA secure_delete = ON")  # purged rows are overwritten


def head_problem(
    key: bytes, head: tuple[object, object, object] | None, newest: tuple[object, object] | None
) -> str | None:
    """Why the writer may not chain onto this head, or None."""
    if head is None:
        return None if newest is None else "the head record is missing"
    head_id, head_hash, mac = head
    if not (isinstance(head_id, int) and is_hex64(head_hash) and is_hex64(mac)):
        return "the head record holds invalid values"
    if not hmac.compare_digest(str(mac), head_hmac(key, head_id, str(head_hash))):
        return "the head record fails its HMAC"
    if newest != (head_id, head_hash):
        return "the newest row is not the one the head record names"
    return None


class AuditLog:
    """The writer. One connection, a lock for threads, `BEGIN IMMEDIATE` for processes."""

    def __init__(
        self, path: Path, key_provider: KeyProvider, clock: Callable[[], datetime] = utc_now
    ) -> None:
        self.path = path
        self._clock = clock
        self._lock = threading.Lock()
        _prepare_file(path)
        self._connection = sqlite3.connect(
            path, timeout=_BUSY_TIMEOUT_SECONDS, isolation_level=None, check_same_thread=False
        )
        try:
            _configure(self._connection)
            self._key = self._initialise(key_provider)
        except BaseException:
            self._connection.close()
            raise

    def _initialise(self, key_provider: KeyProvider) -> bytes:
        with self._transaction():
            if not check_schema(self._connection):
                self._connection.execute(_CREATE_LOG)
                self._connection.execute(_CREATE_HEAD)
                self._connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            key = load_device_key(key_provider)
            if key is not None:
                return key
            if read_head(self._connection) is not None or self._newest() is not None:
                msg = (
                    f"{self.path} has rows but this keychain has no audit device key: the key "
                    "is lost or the file comes from another device"
                )
                raise DeviceKeyMissing(msg)
            # Created inside the write lock, so two processes starting at once agree on one key.
            return device_key(key_provider)

    @contextmanager
    def _transaction(self) -> Generator[None]:
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise
        self._connection.execute("COMMIT")

    def _newest(self) -> tuple[object, object] | None:
        found = self._connection.execute(
            "SELECT id, row_hash FROM audit_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return None if found is None else (found[0], found[1])

    def _checked_head(self) -> tuple[int, str]:
        head = read_head(self._connection)
        problem = head_problem(self._key, head, self._newest())
        if problem:
            msg = f"{problem}; run `safeguard audit verify` (ADR 0021 point 6)"
            raise AuditChainBroken(msg)
        if head is None:
            return 0, GENESIS_HASH
        return int(str(head[0])), str(head[1])

    def _append(self, content: Mapping[str, object], prev_hash: str) -> None:
        digest = row_hash(prev_hash, content)
        values = dict(content)
        if isinstance(values["local"], bool):
            values["local"] = int(values["local"])
        values.update(prev_hash=prev_hash, row_hash=digest, hmac=row_hmac(self._key, digest))
        self._connection.execute(_INSERT_ROW, values)
        row_id = int(str(content["id"]))
        self._connection.execute(
            _UPSERT_HEAD, (row_id, digest, head_hmac(self._key, row_id, digest))
        )

    def record(  # noqa: PLR0913  (one keyword per column: the proxy's contract)
        self,
        *,
        session_id: str,
        upstream_host: str,
        model: str,
        entity_counts: Mapping[str, int],
        masked_payload_sha256: str,
        bytes_out: int,
        status: int,
        local: bool,
    ) -> int:
        """Append one request row; returns its id. Raises ValueError for a bad value."""
        with self._lock, self._transaction():
            head_id, head_hash = self._checked_head()
            content = request_content(
                row_id=head_id + 1,
                ts_utc=format_ts(self._clock()),
                session_id=session_id,
                upstream_host=upstream_host,
                model=model,
                entity_counts=entity_counts,
                masked_payload_sha256=masked_payload_sha256,
                bytes_out=bytes_out,
                status=status,
                local=local,
            )
            self._append(content, head_hash)
            return head_id + 1

    def purge(self, *, older_than_days: int) -> PurgeResult:
        """Delete the oldest rows older than the cutoff behind a checkpoint (ADR 0021 point 9)."""
        if type(older_than_days) is not int or older_than_days < 1:
            msg = "the retention period must be a whole number of days, at least 1"
            raise ValueError(msg)
        with self._lock, self._transaction():
            now = self._clock()
            cutoff = format_ts(now - timedelta(days=older_than_days))
            # Purging a broken log would delete the evidence of what broke it.
            checked = verify_connection(self._connection, self._key)
            if not checked.ok:
                msg = (
                    f"row {checked.first_bad_id} is {checked.reason} ({checked.detail}); "
                    "not purging a log that fails verification"
                )
                raise AuditChainBroken(msg)
            head_id, head_hash = self._checked_head()
            through = self._purge_limit(cutoff)
            if through is None:
                return PurgeResult(0, None, None)
            (through_hash,) = self._connection.execute(
                "SELECT row_hash FROM audit_log WHERE id = ?", (through,)
            ).fetchone()
            (count,) = self._connection.execute(
                "SELECT COUNT(*) FROM audit_log WHERE id <= ?", (through,)
            ).fetchone()
            content = checkpoint_content(
                row_id=head_id + 1,
                ts_utc=format_ts(now),
                purged_through_id=through,
                purged_through_hash=through_hash,
                purged_count=count,
                purged_before_utc=cutoff,
            )
            self._append(content, head_hash)
            self._connection.execute("DELETE FROM audit_log WHERE id <= ?", (through,))
            return PurgeResult(count, head_id + 1, through)

    def _purge_limit(self, cutoff: str) -> int | None:
        """The last id of the run of old rows at the start, so only a prefix is ever deleted."""
        (oldest, newest) = self._connection.execute(
            "SELECT MIN(id), MAX(id) FROM audit_log"
        ).fetchone()
        if oldest is None:
            return None
        (first_young,) = self._connection.execute(
            "SELECT MIN(id) FROM audit_log WHERE ts_utc >= ?", (cutoff,)
        ).fetchone()
        through = newest if first_young is None else first_young - 1
        return through if through >= oldest else None

    def verify(self) -> VerifyResult:
        with self._lock:
            return verify_connection(self._connection, self._key)

    def newest(self, limit: int, before_id: int | None = None) -> list[dict[str, object]]:
        """Up to `limit` rows, newest first, with ids below `before_id` when given."""
        with self._lock:
            cursor = self._connection.execute(
                "SELECT * FROM audit_log WHERE (:before IS NULL OR id < :before) "
                "ORDER BY id DESC LIMIT :limit",
                {"before": before_id, "limit": limit},
            )
            names = [column[0] for column in cursor.description]
            return [dict(zip(names, values, strict=True)) for values in cursor]

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
