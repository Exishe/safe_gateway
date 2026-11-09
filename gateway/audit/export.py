"""Signed JSON export of the audit log (ADR 0022).

The rows are exact copies of the columns, so anyone can recompute each row hash
and check the links; the export HMAC and the row HMACs need the device key.
"""

import hmac
import os
import sqlite3
import tempfile
from collections.abc import Mapping
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from gateway.audit.chain import MAX_SAFE_INT, canonical_json, tagged_hmac
from gateway.audit.db import iter_rows
from gateway.audit.rows import format_ts
from gateway.audit.verify import verify_connection

FORMAT = "safeguard-audit-export"
VERSION = 1


def day_range(start: date | None, end: date | None) -> tuple[str | None, str | None]:
    """Whole UTC days, both ends inclusive, as `ts_utc` bounds: start <= ts < end + 1 day."""
    if start is not None and end is not None and start > end:
        msg = f"--from {start} is after --to {end}"
        raise ValueError(msg)
    low = None if start is None else format_ts(datetime.combine(start, time(), UTC))
    high = None if end is None else format_ts(datetime.combine(end, time(), UTC) + timedelta(1))
    return low, high


def _exportable(value: object) -> object:
    """A stored value as canonical JSON can carry it. Only a tampered row needs this."""
    if value is None or type(value) in (bool, str, int):
        if isinstance(value, str) and not value.isascii():
            return value.encode("ascii", "backslashreplace").decode("ascii")
        if type(value) is int and abs(value) > MAX_SAFE_INT:
            return str(value)
        return value
    if isinstance(value, bytes):
        return f"hex:{value.hex()}"
    return repr(value)


def _export_row(row: Mapping[str, object]) -> dict[str, object]:
    exported = {column: _exportable(value) for column, value in row.items()}
    if exported.get("local") in (0, 1):
        exported["local"] = exported["local"] == 1
    return exported


def sign(key: bytes, body: Mapping[str, object]) -> str:
    return tagged_hmac(key, "export", canonical_json(dict(body)))


def build_export(
    connection: sqlite3.Connection,
    key: bytes,
    *,
    start: date | None,
    end: date | None,
    now: datetime,
) -> dict[str, object]:
    low, high = day_range(start, end)
    checked = verify_connection(connection, key)
    body: dict[str, object] = {
        "format": FORMAT,
        "version": VERSION,
        "generated_utc": format_ts(now),
        "range": {"from_utc": low, "to_utc_exclusive": high},
        "verification": {
            "ok": checked.ok,
            "rows_checked": checked.rows_checked,
            "first_bad_id": checked.first_bad_id,
            "reason": None if checked.reason is None else str(checked.reason),
            "detail": _exportable(checked.detail),
        },
        "chain": {
            "head_id": checked.head_id,
            "head_hash": checked.head_hash,
            "purged_through_id": checked.purged_through_id,
        },
        "rows": [_export_row(row) for row in iter_rows(connection, low, high)],
    }
    return {**body, "hmac": sign(key, body)}


def verify_export(document: Mapping[str, object], key: bytes) -> bool:
    """True when the export is exactly as this device's key signed it."""
    body = {name: value for name, value in document.items() if name != "hmac"}
    mac = document.get("hmac")
    try:
        expected = sign(key, body)
    except ValueError:  # something canonical JSON cannot hold: not what we wrote
        return False
    return isinstance(mac, str) and hmac.compare_digest(mac, expected)


def write_private(path: Path, data: bytes) -> None:
    """Write via a 0600 temporary file in the same directory, then rename over `path`."""
    descriptor, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    temp = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temp.replace(path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
