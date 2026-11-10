from pathlib import Path

import pytest

from gateway.audit.chain import head_hmac
from gateway.audit.store import AuditLog
from gateway.audit.verify import Reason, verify_file
from gateway.engine.session_disk import InMemoryKeyProvider
from gateway.secrets import AUDIT_KEY_ID
from tests.audit_support import Clock, all_rows, fill, tamper


@pytest.fixture
def keys() -> InMemoryKeyProvider:
    return InMemoryKeyProvider()


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "audit.db"


def key_of(keys: InMemoryKeyProvider) -> bytes:
    key = keys.get(AUDIT_KEY_ID)
    assert key is not None
    return key


def five_rows(db: Path, keys: InMemoryKeyProvider) -> None:
    with AuditLog(db, keys, clock=Clock()) as log:
        fill(log, 5)


def test_an_empty_log_verifies(db: Path, keys: InMemoryKeyProvider) -> None:
    AuditLog(db, keys).close()
    result = verify_file(db, key_of(keys))
    assert result.ok
    assert result.rows_checked == 0
    assert result.head_id is None
    assert result.first_bad_id is None


def test_an_untouched_log_verifies_and_reports_its_head(
    db: Path, keys: InMemoryKeyProvider
) -> None:
    five_rows(db, keys)
    result = verify_file(db, key_of(keys))
    assert result.ok
    assert result.rows_checked == 5
    assert result.head_id == 5
    assert result.head_hash == all_rows(db)[4]["row_hash"]
    assert result.purged_through_id == 0
    assert result.reason is None


def test_audit_log_verify_matches_verify_file(db: Path, keys: InMemoryKeyProvider) -> None:
    with AuditLog(db, keys, clock=Clock()) as log:
        fill(log, 2)
        assert log.verify().ok
        tamper(db, "UPDATE audit_log SET bytes_out = 1 WHERE id = 2")
        assert log.verify().first_bad_id == 2


@pytest.mark.parametrize("row_id", [1, 3, 5])
def test_a_deleted_row_is_named(db: Path, keys: InMemoryKeyProvider, row_id: int) -> None:
    five_rows(db, keys)
    tamper(db, "DELETE FROM audit_log WHERE id = ?", (row_id,))
    result = verify_file(db, key_of(keys))
    assert not result.ok
    assert result.reason is Reason.DELETED
    assert result.first_bad_id == row_id


def test_two_deleted_rows_report_the_first(db: Path, keys: InMemoryKeyProvider) -> None:
    five_rows(db, keys)
    tamper(db, "DELETE FROM audit_log WHERE id IN (2, 3)")
    result = verify_file(db, key_of(keys))
    assert (result.reason, result.first_bad_id) == (Reason.DELETED, 2)


def test_a_deleted_head_record_is_reported(db: Path, keys: InMemoryKeyProvider) -> None:
    five_rows(db, keys)
    tamper(db, "DELETE FROM audit_head")
    result = verify_file(db, key_of(keys))
    assert (result.reason, result.first_bad_id) == (Reason.DELETED, 6)


def test_deleting_every_row_is_reported(db: Path, keys: InMemoryKeyProvider) -> None:
    five_rows(db, keys)
    tamper(db, "DELETE FROM audit_log")
    result = verify_file(db, key_of(keys))
    assert not result.ok
    assert result.reason is Reason.DELETED


def test_a_head_without_the_key_is_a_bad_hmac(db: Path, keys: InMemoryKeyProvider) -> None:
    five_rows(db, keys)
    tamper(db, "UPDATE audit_head SET hmac = ?", ("0" * 64,))
    result = verify_file(db, key_of(keys))
    assert (result.reason, result.first_bad_id) == (Reason.BAD_HMAC, 5)


def test_rows_after_the_head_are_inserted(db: Path, keys: InMemoryKeyProvider) -> None:
    five_rows(db, keys)
    fourth = str(all_rows(db)[3]["row_hash"])
    mac = head_hmac(key_of(keys), 4, fourth)
    tamper(db, "UPDATE audit_head SET head_id = 4, head_hash = ?, hmac = ?", (fourth, mac))
    result = verify_file(db, key_of(keys))
    assert (result.reason, result.first_bad_id) == (Reason.INSERTED, 5)


def test_a_wrong_key_fails_on_the_first_row(db: Path, keys: InMemoryKeyProvider) -> None:
    five_rows(db, keys)
    result = verify_file(db, b"f" * 64)
    assert (result.reason, result.first_bad_id) == (Reason.BAD_HMAC, 1)


def test_verify_changes_nothing_and_creates_nothing(
    db: Path, keys: InMemoryKeyProvider, tmp_path: Path
) -> None:
    five_rows(db, keys)
    before = db.read_bytes()
    verify_file(db, key_of(keys))
    assert db.read_bytes() == before
    missing = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError):
        verify_file(missing, key_of(keys))
    assert not missing.exists()
