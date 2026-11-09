import hashlib
import hmac
import json
import sqlite3
import stat
import threading
from itertools import pairwise
from pathlib import Path

import pytest

from gateway.audit.rows import COLUMNS
from gateway.audit.store import (
    AuditChainBroken,
    AuditLog,
    DeviceKeyMissing,
    NotAnAuditLog,
    default_audit_path,
)
from gateway.engine.session_disk import InMemoryKeyProvider
from gateway.secrets import AUDIT_KEY_ID
from tests.audit_support import Clock, all_rows, fill, record_request, tamper

ZEROS = "0" * 64


@pytest.fixture
def keys() -> InMemoryKeyProvider:
    return InMemoryKeyProvider()


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "home" / "audit.db"


@pytest.fixture
def log(db: Path, keys: InMemoryKeyProvider) -> AuditLog:
    return AuditLog(db, keys, clock=Clock())


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_default_path_follows_safeguard_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SAFEGUARD_HOME", str(tmp_path / "sg"))
    assert default_audit_path() == tmp_path / "sg" / "audit.db"
    monkeypatch.delenv("SAFEGUARD_HOME")
    assert default_audit_path() == Path.home() / ".safeguard" / "audit.db"


def test_a_new_log_is_0600_in_a_0700_directory(log: AuditLog, db: Path) -> None:
    record_request(log)
    log.close()
    assert mode(db.parent) == 0o700
    assert mode(db) == 0o600
    assert [p.name for p in db.parent.iterdir()] == ["audit.db"]  # no journal left behind


def test_an_existing_file_is_tightened_to_0600(db: Path, keys: InMemoryKeyProvider) -> None:
    db.parent.mkdir(mode=0o700)
    db.touch(mode=0o644)
    db.chmod(0o644)
    AuditLog(db, keys).close()
    assert mode(db) == 0o600


def test_the_table_has_the_documented_columns(log: AuditLog, db: Path) -> None:
    log.close()
    connection = sqlite3.connect(db)
    names = tuple(row[1] for row in connection.execute("PRAGMA table_info(audit_log)"))
    connection.close()
    assert names == COLUMNS


def test_rows_are_numbered_and_chained(log: AuditLog, db: Path) -> None:
    assert fill(log, 3) == [1, 2, 3]
    rows = all_rows(db)
    assert rows[0]["prev_hash"] == ZEROS
    assert rows[1]["prev_hash"] == rows[0]["row_hash"]
    assert rows[2]["prev_hash"] == rows[1]["row_hash"]
    assert [r["ts_utc"] for r in rows] == [
        "2026-11-01T09:00:00.000000Z",
        "2026-11-01T09:00:01.000000Z",
        "2026-11-01T09:00:02.000000Z",
    ]


def test_hash_and_hmac_recompute_from_the_stored_columns(
    log: AuditLog, db: Path, keys: InMemoryKeyProvider
) -> None:
    record_request(log, 1, entity_counts={"PERSON": 2}, bytes_out=42, local=True)
    row = all_rows(db)[0]
    content = {k: v for k, v in row.items() if k not in {"prev_hash", "row_hash", "hmac"}}
    content["local"] = row["local"] == 1
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
    expected = hashlib.sha256(ZEROS.encode() + canonical).hexdigest()
    assert row["row_hash"] == expected
    key = keys.get(AUDIT_KEY_ID)
    assert key is not None
    tag = b"safeguard-audit/row/v1\n"
    assert row["hmac"] == hmac.new(key, tag + expected.encode(), "sha256").hexdigest()
    assert row["entity_counts_json"] == '{"PERSON":2}'
    assert row["bytes_out"] == 42


def test_the_head_names_the_newest_row(log: AuditLog, db: Path) -> None:
    fill(log, 2)
    connection = sqlite3.connect(db)
    head = connection.execute("SELECT head_id, head_hash FROM audit_head").fetchall()
    connection.close()
    assert head == [(2, all_rows(db)[1]["row_hash"])]


def test_reopening_continues_the_chain(db: Path, keys: InMemoryKeyProvider) -> None:
    with AuditLog(db, keys) as first:
        fill(first, 2)
    with AuditLog(db, keys) as second:
        assert record_request(second, 3) == 3
    rows = all_rows(db)
    assert rows[2]["prev_hash"] == rows[1]["row_hash"]


def test_a_refused_value_writes_nothing(log: AuditLog, db: Path) -> None:
    record_request(log, 1)
    with pytest.raises(ValueError, match="upstream_host"):
        record_request(log, 2, upstream_host="https://api.example.com/v1/chat")
    with pytest.raises(ValueError, match="model"):
        record_request(log, 2, model="what did Anna say")
    assert record_request(log, 2) == 2
    assert len(all_rows(db)) == 2


def test_deleting_the_newest_row_blocks_the_next_write(log: AuditLog, db: Path) -> None:
    fill(log, 3)
    tamper(db, "DELETE FROM audit_log WHERE id = 3")
    with pytest.raises(AuditChainBroken):
        record_request(log, 4)
    assert len(all_rows(db)) == 2


def test_deleting_the_head_blocks_the_next_write(log: AuditLog, db: Path) -> None:
    fill(log, 2)
    tamper(db, "DELETE FROM audit_head")
    with pytest.raises(AuditChainBroken):
        record_request(log, 3)


def test_a_forged_head_blocks_the_next_write(log: AuditLog, db: Path) -> None:
    fill(log, 3)
    tamper(db, "DELETE FROM audit_log WHERE id = 3")
    previous = all_rows(db)[1]["row_hash"]
    tamper(db, "UPDATE audit_head SET head_id = 2, head_hash = ?", (previous,))
    with pytest.raises(AuditChainBroken):
        record_request(log, 4)


def test_a_log_with_rows_and_no_key_is_refused(db: Path, keys: InMemoryKeyProvider) -> None:
    with AuditLog(db, keys) as log:
        fill(log, 1)
    other_device = InMemoryKeyProvider()
    with pytest.raises(DeviceKeyMissing):
        AuditLog(db, other_device)
    assert other_device.get(AUDIT_KEY_ID) is None


def test_a_newer_schema_is_refused(db: Path, keys: InMemoryKeyProvider) -> None:
    AuditLog(db, keys).close()
    tamper(db, "PRAGMA user_version = 2")
    with pytest.raises(NotAnAuditLog):
        AuditLog(db, keys)


def test_threads_sharing_one_log_get_consecutive_ids(log: AuditLog, db: Path) -> None:
    def work(offset: int) -> None:
        for n in range(25):
            record_request(log, offset * 100 + n)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    rows = all_rows(db)
    assert [r["id"] for r in rows] == list(range(1, 201))
    assert all(b["prev_hash"] == a["row_hash"] for a, b in pairwise(rows))


def test_separate_writers_on_one_file_keep_one_chain(db: Path, keys: InMemoryKeyProvider) -> None:
    # Four AuditLog objects are four connections, as four processes would have.
    logs = [AuditLog(db, keys) for _ in range(4)]

    def work(log: AuditLog, offset: int) -> None:
        for n in range(15):
            record_request(log, offset * 100 + n)

    threads = [threading.Thread(target=work, args=(log, i)) for i, log in enumerate(logs)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    for log in logs:
        log.close()
    rows = all_rows(db)
    assert [r["id"] for r in rows] == list(range(1, 61))
    assert all(b["prev_hash"] == a["row_hash"] for a, b in pairwise(rows))
