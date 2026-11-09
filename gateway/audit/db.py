"""Reading the audit log file: where it lives, its schema check, rows and head (ADR 0021).

Shared by the writer (`store.py`) and the checker (`verify.py`); nothing here writes.
"""

import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

SCHEMA_VERSION = 1
TABLES = frozenset({"audit_log", "audit_head"})

_SELECT_ROWS = """
SELECT * FROM audit_log
WHERE (:start IS NULL OR ts_utc >= :start) AND (:end IS NULL OR ts_utc < :end)
ORDER BY id
"""


class AuditLogError(Exception):
    """The audit log cannot be used as it is."""


class NotAnAuditLog(AuditLogError):  # noqa: N818  (reads as what happened)
    """The file is not an audit log this version can read."""


def default_audit_path() -> Path:
    """`<SAFEGUARD_HOME>/audit.db`, with SAFEGUARD_HOME defaulting to `~/.safeguard`."""
    home = os.environ.get("SAFEGUARD_HOME")
    return (Path(home) if home else Path.home() / ".safeguard") / "audit.db"


def check_schema(connection: sqlite3.Connection) -> bool:
    """True when the file holds our tables, False when it is empty; raises otherwise."""
    (version,) = connection.execute("PRAGMA user_version").fetchone()
    rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    tables = {name for (name,) in rows}
    if version == SCHEMA_VERSION and tables >= TABLES:
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
        has_tables = check_schema(connection)
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
    """Rows in id order as stored, optionally only those with start <= ts_utc < end."""
    cursor = connection.execute(_SELECT_ROWS, {"start": start, "end": end})
    names = [column[0] for column in cursor.description]
    for values in cursor:
        yield dict(zip(names, values, strict=True))
