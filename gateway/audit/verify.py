"""Walk the audit chain and name the first broken row and why (ADR 0021 point 10).

One row is held at a time. The extra lookups that tell a move from an edit, or a
deletion from a reordering, run only once something has already failed.
"""

import hmac
import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from gateway.audit.chain import GENESIS_HASH, head_hmac, row_hash, row_hmac
from gateway.audit.db import iter_rows, open_readonly, read_head
from gateway.audit.rows import content_of, is_hex64, stored_problem


class Reason(StrEnum):
    EDITED = "edited"
    DELETED = "deleted"
    REORDERED = "reordered"
    INSERTED = "inserted"
    BAD_HMAC = "bad HMAC"


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    rows_checked: int
    first_bad_id: int | None
    reason: Reason | None
    detail: str
    head_id: int | None
    head_hash: str | None
    purged_through_id: int


@dataclass(frozen=True)
class _Problem:
    row_id: int
    reason: Reason
    detail: str


@dataclass
class _Walk:
    connection: sqlite3.Connection
    key: bytes
    anchor_id: int
    expected_id: int
    expected_prev: str


def _anchor(connection: sqlite3.Connection) -> tuple[int, str]:
    """The newest checkpoint's carried id and hash, or the start of a never-purged chain."""
    found = connection.execute(
        "SELECT purged_through_id, purged_through_hash FROM audit_log "
        "WHERE kind = 'checkpoint' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if found and type(found[0]) is int and found[0] > 0 and is_hex64(found[1]):
        return found[0], found[1]
    return 0, GENESIS_HASH  # a malformed checkpoint is reported where the walk reaches it


def _written_as(walk: _Walk, row: dict[str, object], content: dict[str, object]) -> int | None:
    """The id this row hashes correctly under, if another: the row was moved there from it."""
    (count,) = walk.connection.execute("SELECT COUNT(*) FROM audit_log").fetchone()
    ids = {i for (i,) in walk.connection.execute("SELECT id FROM audit_log") if type(i) is int}
    candidates = ids | set(range(walk.anchor_id + 1, walk.anchor_id + count + 2))
    candidates |= {walk.expected_id}
    candidates.discard(row["id"])
    prev = str(row["prev_hash"])
    for candidate in sorted(candidates):
        if row_hash(prev, {**content, "id": candidate}) == row["row_hash"]:
            return candidate
    return None


def _content_problem(walk: _Walk, row: dict[str, object]) -> _Problem | None:
    row_id = row["id"] if type(row["id"]) is int else walk.expected_id
    shape = stored_problem(row)
    if shape:
        return _Problem(row_id, Reason.EDITED, shape)
    content = content_of(row)
    if row_hash(str(row["prev_hash"]), content) == row["row_hash"]:
        return None
    original = _written_as(walk, row, content)
    if original is not None:
        return _Problem(row_id, Reason.REORDERED, f"row {row_id} was written as row {original}")
    return _Problem(row_id, Reason.EDITED, "its stored values do not match its row hash")


def _link_problem(walk: _Walk, row: dict[str, object]) -> _Problem | None:
    row_id = int(str(row["id"]))
    prev = str(row["prev_hash"])
    if row_id <= walk.anchor_id:
        detail = (
            f"a checkpoint says rows up to {walk.anchor_id} were purged, yet row {row_id} is here"
        )
        return _Problem(row_id, Reason.INSERTED, detail)
    if prev == walk.expected_prev and row_id == walk.expected_id:
        return None
    holder = walk.connection.execute(
        "SELECT id FROM audit_log WHERE row_hash = ? LIMIT 1", (prev,)
    ).fetchone()
    if holder is not None:
        detail = f"row {row_id} was chained after the row that now has id {holder[0]}"
        return _Problem(row_id, Reason.REORDERED, detail)
    first_missing = min(walk.expected_id, row_id)
    last_missing = max(walk.expected_id, row_id) - 1
    detail = f"rows {first_missing} to {last_missing} are missing"
    if first_missing >= last_missing:
        detail = f"the row before row {row_id} is missing"
    return _Problem(first_missing, Reason.DELETED, detail)


def _row_problem(walk: _Walk, row: dict[str, object]) -> _Problem | None:
    problem = _content_problem(walk, row)
    if problem:
        return problem
    expected_mac = row_hmac(walk.key, str(row["row_hash"]))
    if not hmac.compare_digest(str(row["hmac"]), expected_mac):
        detail = "its HMAC was not made with this device's key"
        return _Problem(int(str(row["id"])), Reason.BAD_HMAC, detail)
    return _link_problem(walk, row)


def _valid_head(
    connection: sqlite3.Connection,
) -> tuple[int, str, str] | _Problem | None:
    head = read_head(connection)
    if head is None:
        return None
    head_id, head_hash, mac = head
    if type(head_id) is not int or not is_hex64(head_hash) or not is_hex64(mac):
        return _Problem(1, Reason.EDITED, "the head record holds invalid values")
    return head_id, str(head_hash), str(mac)


def _head_problem(walk: _Walk, last: tuple[int, str] | None) -> _Problem | None:
    head = _valid_head(walk.connection)
    if isinstance(head, _Problem):
        return head
    if head is None:
        if last is None:
            return None
        detail = f"the head record is missing, so rows after row {last[0]} may have been deleted"
        return _Problem(last[0] + 1, Reason.DELETED, detail)
    head_id, head_hash, mac = head
    if not hmac.compare_digest(mac, head_hmac(walk.key, head_id, head_hash)):
        return _Problem(head_id, Reason.BAD_HMAC, "the head record's HMAC does not match")
    if last is None:
        return _Problem(head_id, Reason.DELETED, f"no rows remain but the head names row {head_id}")
    return _head_mismatch(head_id, head_hash, last)


def _head_mismatch(head_id: int, head_hash: str, last: tuple[int, str]) -> _Problem | None:
    last_id, last_hash = last
    if head_id > last_id:
        detail = f"rows {last_id + 1} to {head_id} were deleted from the end"
        return _Problem(last_id + 1, Reason.DELETED, detail)
    if head_id < last_id:
        detail = f"rows after row {head_id} were added outside the gateway's writer"
        return _Problem(head_id + 1, Reason.INSERTED, detail)
    if head_hash != last_hash:
        return _Problem(last_id, Reason.EDITED, "the newest row is not the one the head names")
    return None


def verify_connection(connection: sqlite3.Connection, key: bytes) -> VerifyResult:
    anchor_id, anchor_hash = _anchor(connection)
    walk = _Walk(connection, key, anchor_id, anchor_id + 1, anchor_hash)
    checked = 0
    last: tuple[int, str] | None = None
    problem: _Problem | None = None
    for row in iter_rows(connection):
        problem = _row_problem(walk, row)
        if problem:
            break
        checked += 1
        last = (int(str(row["id"])), str(row["row_hash"]))
        walk.expected_id, walk.expected_prev = last[0] + 1, last[1]
    else:
        problem = _head_problem(walk, last)

    head = _valid_head(connection)
    head_id, head_hash = (head[0], head[1]) if isinstance(head, tuple) else (None, None)
    return VerifyResult(
        ok=problem is None,
        rows_checked=checked,
        first_bad_id=None if problem is None else problem.row_id,
        reason=None if problem is None else problem.reason,
        detail=f"{checked} rows verified" if problem is None else problem.detail,
        head_id=head_id,
        head_hash=head_hash,
        purged_through_id=anchor_id,
    )


def verify_file(path: Path, key: bytes) -> VerifyResult:
    """Verify the log at `path`, opened read-only. Raises FileNotFoundError, NotAnAuditLog."""
    connection = open_readonly(path)
    try:
        return verify_connection(connection, key)
    finally:
        connection.close()
