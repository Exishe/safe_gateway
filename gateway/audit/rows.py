"""The shape of an audit row: what `record()` accepts and what a stored row must be.

A value outside its shape is refused, never trimmed or cleaned: a cleaned value
would still be a piece of the text (ADR 0021 point 3).
"""

import hashlib
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import NoReturn

from gateway.audit.chain import MAX_SAFE_INT, canonical_json

CONTENT_COLUMNS = (
    "id",
    "kind",
    "ts_utc",
    "session_id",
    "upstream_host",
    "model",
    "entity_counts_json",
    "masked_payload_sha256",
    "bytes_out",
    "status",
    "local",
    "purged_through_id",
    "purged_through_hash",
    "purged_count",
    "purged_before_utc",
)
COLUMNS = (*CONTENT_COLUMNS, "prev_hash", "row_hash", "hmac")
KINDS = frozenset({"request", "checkpoint"})

HEX64 = re.compile(r"[0-9a-f]{64}")
_TS = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z")
_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
# A host name or IPv4 address, or a bracketed IPv6 address; then an optional port.
_HOST = re.compile(rf"(?:{_LABEL}(?:\.{_LABEL})*|\[[0-9A-Fa-f:.]+\])(?::\d{{1,5}})?")
_MAX_HOST = 253
_MODEL = re.compile(r"[A-Za-z0-9._:/@+-]{1,200}")
_ENTITY = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
_MAX_ENTITY_TYPES = 64
_MAX_SESSION_ID = 512
_NO_RESPONSE = 0
_HTTP_STATUS = range(100, 600)


def format_ts(moment: datetime) -> str:
    """Fixed-width UTC with microseconds, so the strings sort as times."""
    if moment.tzinfo is None:
        msg = "audit timestamps need a timezone-aware datetime"
        raise ValueError(msg)
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _refuse(field: str, why: str) -> NoReturn:
    msg = f"audit {field}: {why}"
    raise ValueError(msg)


def _safe_int(field: str, value: object, low: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _refuse(field, "must be an integer")
    if not low <= value <= MAX_SAFE_INT:
        _refuse(field, f"must be between {low} and 2**53 - 1")
    return value


def _hex64(field: str, value: object) -> str:
    if not isinstance(value, str) or not HEX64.fullmatch(value):
        _refuse(field, "must be 64 lowercase hex characters")
    return value


def _timestamp(field: str, value: str) -> str:
    if not _TS.fullmatch(value):
        _refuse(field, "must look like 2026-11-09T08:05:03.000007Z")
    return value


def _host(value: str) -> str:
    if len(value) > _MAX_HOST or not _HOST.fullmatch(value):
        _refuse("upstream_host", "must be a host with an optional port, not a URL")
    return value


def _model(value: str) -> str:
    if not _MODEL.fullmatch(value):
        _refuse("model", "must be 1 to 200 of A-Z a-z 0-9 . _ : / @ + -")
    return value


def _session(value: str) -> str:
    # A session id is a bearer handle for a mapping (ADR 0002); keep only its hash.
    if len(value) > _MAX_SESSION_ID:
        _refuse("session_id", f"longer than {_MAX_SESSION_ID} characters")
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else ""


def _counts(counts: Mapping[str, int]) -> str:
    if len(counts) > _MAX_ENTITY_TYPES:
        _refuse("entity_counts", f"more than {_MAX_ENTITY_TYPES} entity types")
    clean: dict[str, int] = {}
    for name, count in counts.items():
        if not isinstance(name, str) or not _ENTITY.fullmatch(name):
            _refuse("entity_counts", "keys must be entity type names such as PERSON")
        clean[str(name)] = _safe_int("entity_counts", count)
    return canonical_json(clean).decode("ascii")


def _status(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _refuse("status", "must be an integer")
    if value != _NO_RESPONSE and value not in _HTTP_STATUS:
        _refuse("status", "must be an HTTP status (100 to 599), or 0 for no response")
    return value


def request_content(  # noqa: PLR0913  (one keyword per column: the proxy's contract)
    *,
    row_id: int,
    ts_utc: str,
    session_id: str,
    upstream_host: str,
    model: str,
    entity_counts: Mapping[str, int],
    masked_payload_sha256: str,
    bytes_out: int,
    status: int,
    local: bool,
) -> dict[str, object]:
    if not isinstance(local, bool):
        _refuse("local", "must be True or False")
    return {
        "id": _safe_int("id", row_id, low=1),
        "kind": "request",
        "ts_utc": _timestamp("ts_utc", ts_utc),
        "session_id": _session(session_id),
        "upstream_host": _host(upstream_host),
        "model": _model(model),
        "entity_counts_json": _counts(entity_counts),
        "masked_payload_sha256": _hex64("masked_payload_sha256", masked_payload_sha256),
        "bytes_out": _safe_int("bytes_out", bytes_out),
        "status": _status(status),
        "local": local,
        "purged_through_id": None,
        "purged_through_hash": None,
        "purged_count": None,
        "purged_before_utc": None,
    }


def checkpoint_content(  # noqa: PLR0913  (one keyword per column)
    *,
    row_id: int,
    ts_utc: str,
    purged_through_id: int,
    purged_through_hash: str,
    purged_count: int,
    purged_before_utc: str,
) -> dict[str, object]:
    content: dict[str, object] = dict.fromkeys(CONTENT_COLUMNS)
    content.update(
        id=_safe_int("id", row_id, low=1),
        kind="checkpoint",
        ts_utc=_timestamp("ts_utc", ts_utc),
        purged_through_id=_safe_int("purged_through_id", purged_through_id, low=1),
        purged_through_hash=_hex64("purged_through_hash", purged_through_hash),
        purged_count=_safe_int("purged_count", purged_count, low=1),
        purged_before_utc=_timestamp("purged_before_utc", purged_before_utc),
    )
    return content


def _type_problem(row: Mapping[str, object]) -> str | None:
    for column in COLUMNS:
        value = row.get(column)
        if value is not None and type(value) not in (int, str):
            return f"column {column} holds a {type(value).__name__}"
        if isinstance(value, str) and not value.isascii():
            return f"column {column} holds non-ASCII text"
    return None


def stored_problem(row: Mapping[str, object]) -> str | None:
    """Why a stored row cannot be hashed as written, or None (ADR 0021 point 10.1)."""
    problem = _type_problem(row)
    if problem:
        return problem
    if not isinstance(row.get("id"), int):
        return "column id is not an integer"
    if row.get("kind") not in KINDS:
        return "column kind is neither request nor checkpoint"
    if row.get("local") not in (0, 1, None):
        return "column local is neither 0 nor 1"
    bad = [c for c in ("prev_hash", "row_hash", "hmac") if not _is_hex64(row.get(c))]
    return f"column {bad[0]} is not 64 hex characters" if bad else None


def _is_hex64(value: object) -> bool:
    return isinstance(value, str) and HEX64.fullmatch(value) is not None


def content_of(row: Mapping[str, object]) -> dict[str, object]:
    """The hashed content of a stored row: SQLite's 0/1 for `local` read back as a bool."""
    content = {column: row.get(column) for column in CONTENT_COLUMNS}
    if content["local"] is not None:
        content["local"] = content["local"] == 1
    return content
