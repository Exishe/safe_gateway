import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from gateway.audit.rows import COLUMNS, format_ts
from gateway.audit.store import AuditChainBroken, AuditLog, PurgeResult
from gateway.audit.verify import Reason
from gateway.engine.session_disk import InMemoryKeyProvider
from tests.audit_support import START, Clock, all_rows, payload_sha, record_request, tamper


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "audit.db"


@pytest.fixture
def log(db: Path, clock: Clock) -> AuditLog:
    return AuditLog(db, InMemoryKeyProvider(), clock=clock)


def record_on_days(log: AuditLog, clock: Clock, days: list[int]) -> None:
    for n, day in enumerate(days, start=1):
        clock.now = START + timedelta(days=day)
        record_request(log, n)


def purge_on_day(log: AuditLog, clock: Clock, day: int, older_than_days: int = 30) -> PurgeResult:
    clock.now = START + timedelta(days=day)
    return log.purge(older_than_days=older_than_days)


def test_old_rows_go_and_a_checkpoint_carries_the_last_hash(
    log: AuditLog, clock: Clock, db: Path
) -> None:
    record_on_days(log, clock, [0, 1, 2, 40, 41])
    third = all_rows(db)[2]["row_hash"]

    result = purge_on_day(log, clock, 45)

    assert result == PurgeResult(deleted=3, checkpoint_id=6, purged_through_id=3)
    rows = all_rows(db)
    assert [r["id"] for r in rows] == [4, 5, 6]
    checkpoint = rows[2]
    assert checkpoint["kind"] == "checkpoint"
    assert checkpoint["purged_through_id"] == 3
    assert checkpoint["purged_through_hash"] == third
    assert checkpoint["purged_count"] == 3
    assert checkpoint["purged_before_utc"] == format_ts(START + timedelta(days=15))
    assert checkpoint["prev_hash"] == rows[1]["row_hash"]
    assert checkpoint["session_id"] is None
    verified = log.verify()
    assert verified.ok, verified.detail
    assert verified.purged_through_id == 3
    assert verified.rows_checked == 3


def test_a_later_purge_removes_the_old_checkpoint_and_carries_the_anchor(
    log: AuditLog, clock: Clock, db: Path
) -> None:
    record_on_days(log, clock, [0, 1, 2, 40, 41])
    purge_on_day(log, clock, 45)
    clock.now = START + timedelta(days=99)
    record_request(log, 7)

    result = purge_on_day(log, clock, 100)

    assert result == PurgeResult(deleted=3, checkpoint_id=8, purged_through_id=6)
    assert [r["id"] for r in all_rows(db)] == [7, 8]
    verified = log.verify()
    assert verified.ok, verified.detail
    assert verified.purged_through_id == 6


def test_purging_everything_leaves_a_verifiable_checkpoint(
    log: AuditLog, clock: Clock, db: Path
) -> None:
    record_on_days(log, clock, [0, 1])
    assert purge_on_day(log, clock, 45).deleted == 2
    assert [r["kind"] for r in all_rows(db)] == ["checkpoint"]
    assert log.verify().ok
    assert record_request(log, 9) == 4
    assert log.verify().ok


def test_nothing_old_means_no_checkpoint(log: AuditLog, clock: Clock, db: Path) -> None:
    record_on_days(log, clock, [44])
    assert purge_on_day(log, clock, 45) == PurgeResult(0, None, None)
    assert [r["kind"] for r in all_rows(db)] == ["request"]


def test_an_empty_log_purges_nothing(log: AuditLog, clock: Clock) -> None:
    assert purge_on_day(log, clock, 45) == PurgeResult(0, None, None)


def test_retention_needs_at_least_one_day(log: AuditLog) -> None:
    with pytest.raises(ValueError, match="at least 1"):
        log.purge(older_than_days=0)


def test_only_a_prefix_goes_when_the_clock_stepped_back(
    log: AuditLog, clock: Clock, db: Path
) -> None:
    record_on_days(log, clock, [0, 1, 50, 2])
    assert purge_on_day(log, clock, 60).purged_through_id == 2
    assert [r["id"] for r in all_rows(db)] == [3, 4, 5]
    assert log.verify().ok


def test_purged_rows_are_overwritten_in_the_file(log: AuditLog, clock: Clock, db: Path) -> None:
    record_on_days(log, clock, [0, 1, 2, 40])
    purge_on_day(log, clock, 45)
    log.close()
    data = db.read_bytes()
    for n in (1, 2, 3):
        assert payload_sha(n).encode() not in data
    assert payload_sha(4).encode() in data


def test_a_tampered_log_is_not_purged(log: AuditLog, clock: Clock, db: Path) -> None:
    record_on_days(log, clock, [0, 1, 2, 40])
    tamper(db, "UPDATE audit_log SET bytes_out = 1 WHERE id = 2")
    with pytest.raises(AuditChainBroken, match="row 2"):
        purge_on_day(log, clock, 45)
    assert len(all_rows(db)) == 4


@pytest.fixture
def purged(log: AuditLog, clock: Clock, db: Path) -> dict[str, object]:
    """Rows on days 0, 1, 2, 40, 41 purged on day 45; returns the old row 3 as it was."""
    record_on_days(log, clock, [0, 1, 2, 40, 41])
    third = all_rows(db)[2]
    purge_on_day(log, clock, 45)
    return third


def test_deleting_the_checkpoint_is_detected(
    purged: dict[str, object], log: AuditLog, db: Path
) -> None:
    del purged
    tamper(db, "DELETE FROM audit_log WHERE kind = 'checkpoint'")
    assert not log.verify().ok


def test_editing_the_carried_hash_is_detected(
    purged: dict[str, object], log: AuditLog, db: Path
) -> None:
    del purged
    tamper(db, "UPDATE audit_log SET purged_through_hash = ? WHERE id = 6", ("e" * 64,))
    assert not log.verify().ok


def test_deleting_the_oldest_remaining_row_is_detected(
    purged: dict[str, object], log: AuditLog, db: Path
) -> None:
    del purged
    tamper(db, "DELETE FROM audit_log WHERE id = 4")
    result = log.verify()
    assert (result.reason, result.first_bad_id) == (Reason.DELETED, 4)


def test_a_purged_row_put_back_is_inserted(
    purged: dict[str, object], log: AuditLog, db: Path
) -> None:
    connection = sqlite3.connect(db)
    names = ", ".join(COLUMNS)
    marks = ", ".join("?" for _ in COLUMNS)
    connection.execute(
        f"INSERT INTO audit_log ({names}) VALUES ({marks})",  # noqa: S608  (fixed column list)
        [purged[c] for c in COLUMNS],
    )
    connection.commit()
    connection.close()
    result = log.verify()
    assert (result.reason, result.first_bad_id) == (Reason.INSERTED, 3)
