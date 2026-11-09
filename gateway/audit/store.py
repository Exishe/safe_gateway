"""The audit log on disk: SQLite schema, the writer, and the read-only opener (ADR 0021).

Every write is one `BEGIN IMMEDIATE` transaction, which is also the lock between
processes: check the head, chain the new row to it, insert, move the head.
"""

import hmac
import os
import sqlite3
import threading
from collections.abc import Callable, Generator, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Self

from gateway.audit.chain import GENESIS_HASH, head_hmac, row_hash, row_hmac
from gateway.audit.rows import format_ts, is_hex64, request_content
from gateway.engine.session_disk import KeyProvider
from gateway.secrets import device_key, load_device_key

SCHEMA_VERSION = 1
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
_SELECT_ROWS = """
SELECT * FROM audit_log
WHERE (:start IS NULL OR ts_utc >= :start) AND (:end IS NULL OR ts_utc < :end)
ORDER BY id
"""


class AuditLogError(Exception):
    """The audit log cannot be used as it is."""


class AuditChainBroken(AuditLogError):  # noqa: N818  (reads as what happened)
    """The head record is missing, forged or behind: writing on would hide that."""


class DeviceKeyMissing(AuditLogError):  # noqa: N818
    """The log has rows but this keychain has no device key for it."""


class NotAnAuditLog(AuditLogError):  # noqa: N818
    """The file is not an audit log this version can read."""


def default_audit_path() -> Path:
    """`<SAFEGUARD_HOME>/audit.db`, with SAFEGUARD_HOME defaulting to `~/.safeguard`."""
    home = os.environ.get("SAFEGUARD_HOME")
    return (Path(home) if home else Path.home() / ".safeguard") / "audit.db"


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


def _tables(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    return {name for (name,) in rows}


def _check_schema(connection: sqlite3.Connection) -> bool:
    """True when the file already holds our tables; raises when it holds anything else."""
    (version,) = connection.execute("PRAGMA user_version").fetchone()
    tables = _tables(connection)
    if version == SCHEMA_VERSION and {"audit_log", "audit_head"} <= tables:
        return True
    if version == 0 and not tables:
        return False
    msg = f"not a SafeGuard audit log of schema version {SCHEMA_VERSION}"
    raise NotAnAuditLog(msg)


def open_readonly(path: Path) -> sqlite3.Connection:
    """Open an existing log without the power to change or create it."""
    if not path.is_file():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        has_tables = _check_schema(connection)
    except sqlite3.DatabaseError as error:  # not a SQLite file at all
        connection.close()
        raise NotAnAuditLog(str(error)) from error
    except NotAnAuditLog:
        connection.close()
        raise
    if not has_tables:
        connection.close()
        msg = "empty file, not an audit log"
        raise NotAnAuditLog(msg)
    return connection


def read_head(connection: sqlite3.Connection) -> tuple[object, object, object] | None:
    found = connection.execute("SELECT head_id, head_hash, hmac FROM audit_head").fetchone()
    return None if found is None else (found[0], found[1], found[2])


def iter_rows(
    connection: sqlite3.Connection, start: str | None = None, end: str | None = None
) -> Iterator[dict[str, object]]:
    """Rows in id order as stored, optionally with start <= ts_utc < end."""
    cursor = connection.execute(_SELECT_ROWS, {"start": start, "end": end})
    names = [column[0] for column in cursor.description]
    for values in cursor:
        yield dict(zip(names, values, strict=True))


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
            if not _check_schema(self._connection):
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
