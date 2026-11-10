"""Signed JSON export of the audit log (ADR 0022).

The rows are exact copies of the columns, so anyone can recompute each row hash
and check the links; the export HMAC and the row HMACs need the device key.
"""

import hmac
import json
import os
import sqlite3
import tempfile
from collections import Counter
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


_LABEL = 20
_ITEM = 18


def _line(label: str, value: object) -> str:
    return f"{label + ':':<{_LABEL}}{value}"


def _items(counter: Counter[str]) -> list[str]:
    return [f"  {name:<{_ITEM}}{count}" for name, count in counter.most_common()]


def _verification_text(verification: Mapping[str, object]) -> str:
    if verification.get("ok"):
        return f"OK, {verification.get('rows_checked')} rows checked"
    return (
        f"BROKEN at row {verification.get('first_bad_id')}: {verification.get('reason')} "
        f"({verification.get('detail')})"
    )


def _totals(rows: list[Mapping[str, object]]) -> list[str]:
    requests = [r for r in rows if r.get("kind") == "request"]
    hosts: Counter[str] = Counter(str(r.get("upstream_host")) for r in requests)
    statuses: Counter[str] = Counter(str(r.get("status")) for r in requests)
    entities: Counter[str] = Counter()
    for row in requests:
        text = row.get("entity_counts_json")
        data = json.loads(text) if isinstance(text, str) else {}
        if isinstance(data, dict):
            entities.update({str(k): v for k, v in data.items() if type(v) is int})
    local = sum(1 for r in requests if r.get("local") is True)
    sent = sum(v for r in requests if type(v := r.get("bytes_out")) is int)
    return [
        _line("Requests", len(requests)),
        _line("Checkpoints", len(rows) - len(requests)),
        _line("Bytes sent", sent),
        _line("Local / cloud", f"{local} / {len(requests) - local}"),
        "By upstream host:",
        *_items(hosts),
        "By upstream status (0 = no response):",
        *_items(statuses),
        "Values masked before sending, by type:",
        *_items(entities),
    ]


def summary_lines(document: Mapping[str, object], json_name: str) -> list[str]:
    """The human-readable summary of an export, for the PDF (ADR 0022 point 7)."""
    verification = document.get("verification")
    chain = document.get("chain")
    span = document.get("range")
    rows = document.get("rows")
    if not (isinstance(verification, dict) and isinstance(chain, dict) and isinstance(span, dict)):
        msg = "not an audit export document"
        raise TypeError(msg)
    rows = rows if isinstance(rows, list) else []
    purged = chain.get("purged_through_id")
    return [
        "SafeGuard Gateway - audit log export summary",
        "",
        _line("Generated (UTC)", document.get("generated_utc")),
        _line("From (UTC)", span.get("from_utc") or "the oldest row"),
        _line("Before (UTC)", span.get("to_utc_exclusive") or "the newest row"),
        _line("Export file", json_name),
        _line("Export HMAC", document.get("hmac")),
        "",
        _line("Verification", _verification_text(verification)),
        _line("Chain head", f"row {chain.get('head_id')}, {chain.get('head_hash')}"),
        _line("Purged", f"rows 1 to {purged}" if purged else "nothing"),
        "",
        *_totals(rows),
        "",
        "This summary is not signed. The JSON export is the evidence: its HMAC is",
        "checked with this device's key. The log is tamper-evident, not",
        "tamper-proof: see docs/SECURITY.md.",
    ]


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
