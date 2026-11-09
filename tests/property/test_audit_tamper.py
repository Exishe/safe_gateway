"""Every kind of tampering is reported, wherever Hypothesis puts it (ADR 0021 point 10)."""

import sqlite3
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from gateway.audit.rows import COLUMNS
from gateway.audit.store import AuditLog
from gateway.audit.verify import Reason, VerifyResult, verify_file
from gateway.engine.session_disk import InMemoryKeyProvider
from gateway.secrets import AUDIT_KEY_ID
from tests.audit_support import Clock, all_rows, fill, record_request, tamper

ROWS = 6
INT_COLUMNS = {"id", "bytes_out", "status", "local", "purged_through_id", "purged_count"}
NOT_NULL = {"id", "kind", "ts_utc", "prev_hash", "row_hash", "hmac"}
TEXT = st.text(max_size=80)  # no surrogates by default, so SQLite can store it


@contextmanager
def written_log(count: int = ROWS) -> Generator[tuple[Path, bytes]]:
    with tempfile.TemporaryDirectory() as directory:
        db = Path(directory) / "audit.db"
        keys = InMemoryKeyProvider()
        with AuditLog(db, keys, clock=Clock()) as log:
            fill(log, count)
        key = keys.get(AUDIT_KEY_ID)
        assert key is not None
        yield db, key


def check(db: Path, key: bytes) -> VerifyResult:
    result = verify_file(db, key)
    assert not result.ok, "tampering went unnoticed"
    return result


@st.composite
def edits(draw: st.DrawFn) -> tuple[int, str, object]:
    row_id = draw(st.integers(1, ROWS))
    column = draw(st.sampled_from(COLUMNS))
    if column == "id":
        value: object = draw(st.integers(-1000, 0) | st.integers(ROWS + 1, 1000))
    elif column in INT_COLUMNS:
        value = draw(st.integers(-(2**63), 2**63 - 1))
    else:
        value = draw(TEXT)
    if column not in NOT_NULL and draw(st.booleans()):
        value = None
    return row_id, column, value


@settings(deadline=None)  # each example writes SQLite files with an fsync per commit
@given(edits())
def test_any_change_to_any_column_of_any_row_is_reported(edit: tuple[int, str, object]) -> None:
    row_id, column, value = edit
    with written_log() as (db, key):
        if all_rows(db)[row_id - 1][column] == value:
            return  # not a change
        tamper(db, f"UPDATE audit_log SET {column} = ? WHERE id = ?", (value, row_id))  # noqa: S608  (column from COLUMNS)
        result = check(db, key)
        if column == "id":
            assert result.reason is Reason.REORDERED
        else:
            assert result.first_bad_id == row_id
            assert result.reason in {Reason.EDITED, Reason.BAD_HMAC}


@settings(deadline=None)  # each example writes SQLite files with an fsync per commit
@given(st.integers(1, ROWS))
def test_deleting_any_row_is_reported_with_its_id(row_id: int) -> None:
    with written_log() as (db, key):
        tamper(db, "DELETE FROM audit_log WHERE id = ?", (row_id,))
        result = check(db, key)
        assert (result.reason, result.first_bad_id) == (Reason.DELETED, row_id)


@settings(deadline=None)  # each example writes SQLite files with an fsync per commit
@given(st.lists(st.integers(1, ROWS), min_size=2, max_size=2, unique=True))
def test_swapping_any_two_rows_is_reported(pair: list[int]) -> None:
    low, high = sorted(pair)
    with written_log() as (db, key):
        tamper(db, "UPDATE audit_log SET id = -1 WHERE id = ?", (low,))
        tamper(db, "UPDATE audit_log SET id = ? WHERE id = ?", (low, high))
        tamper(db, "UPDATE audit_log SET id = ? WHERE id = -1", (high,))
        result = check(db, key)
        assert (result.reason, result.first_bad_id) == (Reason.REORDERED, low)


@settings(deadline=None)  # each example writes SQLite files with an fsync per commit
@given(st.integers(1, ROWS))
def test_a_row_from_another_device_is_a_bad_hmac(row_id: int) -> None:
    with written_log() as (db, key), tempfile.TemporaryDirectory() as directory:
        other = Path(directory) / "other.db"
        with AuditLog(other, InMemoryKeyProvider(), clock=Clock()) as log:
            for n in range(1, ROWS + 1):
                record_request(log, n + 100)
        foreign = all_rows(other)[row_id - 1]
        connection = sqlite3.connect(db)
        connection.execute("DELETE FROM audit_log WHERE id = ?", (row_id,))
        names = ", ".join(COLUMNS)
        marks = ", ".join("?" for _ in COLUMNS)
        connection.execute(
            f"INSERT INTO audit_log ({names}) VALUES ({marks})",  # noqa: S608  (fixed column list)
            [foreign[c] for c in COLUMNS],
        )
        connection.commit()
        connection.close()
        result = check(db, key)
        assert (result.reason, result.first_bad_id) == (Reason.BAD_HMAC, row_id)
