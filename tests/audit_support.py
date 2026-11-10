"""Shared helpers for the audit log tests: a controllable clock, a request, raw tampering."""

import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from gateway.audit.store import AuditLog

START = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)


class Clock:
    """Returns `now` and then moves it on by `step`, like a real clock between requests."""

    def __init__(self, now: datetime = START, step: timedelta = timedelta(seconds=1)) -> None:
        self.now = now
        self.step = step

    def __call__(self) -> datetime:
        moment = self.now
        self.now += self.step
        return moment


def payload_sha(n: int) -> str:
    return hashlib.sha256(f"masked payload {n}".encode()).hexdigest()


def record_request(log: AuditLog, n: int = 1, **overrides: object) -> int:
    arguments: dict[str, object] = {
        "session_id": f"session-{n % 3}",
        "upstream_host": "api.example.com" if n % 2 else "127.0.0.1:11434",
        "model": "model-a",
        "entity_counts": {"PERSON": n % 4, "EMAIL": 1},
        "masked_payload_sha256": payload_sha(n),
        "bytes_out": 100 + n,
        "status": 200,
        "local": n % 2 == 0,
    }
    arguments.update(overrides)
    return log.record(**arguments)  # type: ignore[arg-type]


def fill(log: AuditLog, count: int) -> list[int]:
    return [record_request(log, n) for n in range(1, count + 1)]


def tamper(path: Path, sql: str, params: tuple[object, ...] = ()) -> None:
    """Change the file the way someone with a SQLite tool would, CHECKs switched off."""
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(sql, params)
        connection.commit()
    finally:
        connection.close()


def all_rows(path: Path) -> list[dict[str, object]]:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute("SELECT * FROM audit_log ORDER BY id")]
    finally:
        connection.close()
