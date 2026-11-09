import copy
import hashlib
import json
import stat
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from gateway.audit.db import open_readonly
from gateway.audit.export import (
    build_export,
    day_range,
    summary_lines,
    verify_export,
    write_private,
)
from gateway.audit.store import AuditLog
from gateway.engine.session_disk import InMemoryKeyProvider
from gateway.secrets import AUDIT_KEY_ID
from tests.audit_support import Clock, fill, tamper

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


@pytest.fixture
def keys() -> InMemoryKeyProvider:
    return InMemoryKeyProvider()


@pytest.fixture
def db(tmp_path: Path, keys: InMemoryKeyProvider) -> Path:
    """Three requests, on 1, 2 and 3 November 2026 at 09:00 UTC."""
    path = tmp_path / "audit.db"
    with AuditLog(path, keys, clock=Clock(step=timedelta(days=1))) as log:
        fill(log, 3)
    return path


def key_of(keys: InMemoryKeyProvider) -> bytes:
    key = keys.get(AUDIT_KEY_ID)
    assert key is not None
    return key


def export(db: Path, key: bytes, start: date | None = None, end: date | None = None) -> dict:
    connection = open_readonly(db)
    try:
        return build_export(connection, key, start=start, end=end, now=NOW)
    finally:
        connection.close()


def test_day_range_covers_whole_utc_days() -> None:
    assert day_range(date(2026, 11, 2), date(2026, 11, 2)) == (
        "2026-11-02T00:00:00.000000Z",
        "2026-11-03T00:00:00.000000Z",
    )
    assert day_range(None, None) == (None, None)
    with pytest.raises(ValueError, match="after"):
        day_range(date(2026, 11, 3), date(2026, 11, 2))


def test_a_one_day_range_exports_that_day_only(db: Path, keys: InMemoryKeyProvider) -> None:
    document = export(db, key_of(keys), date(2026, 11, 2), date(2026, 11, 2))
    assert [row["id"] for row in document["rows"]] == [2]
    assert document["range"] == {
        "from_utc": "2026-11-02T00:00:00.000000Z",
        "to_utc_exclusive": "2026-11-03T00:00:00.000000Z",
    }


def test_open_bounds_export_everything(db: Path, keys: InMemoryKeyProvider) -> None:
    document = export(db, key_of(keys))
    assert [row["id"] for row in document["rows"]] == [1, 2, 3]
    assert document["format"] == "safeguard-audit-export"
    assert document["version"] == 1
    assert document["generated_utc"] == "2026-11-09T12:00:00.000000Z"
    assert document["verification"]["ok"] is True
    assert document["chain"]["head_id"] == 3
    assert document["chain"]["head_hash"] == document["rows"][2]["row_hash"]
    assert document["chain"]["purged_through_id"] == 0
    assert isinstance(document["rows"][0]["local"], bool)


def test_the_export_hmac_verifies_with_the_key_only(db: Path, keys: InMemoryKeyProvider) -> None:
    document = export(db, key_of(keys))
    assert verify_export(document, key_of(keys))
    assert not verify_export(document, b"f" * 64)


@pytest.mark.parametrize(
    "path",
    [
        ("rows", 1, "bytes_out"),
        ("rows", 0, "upstream_host"),
        ("chain", "head_id"),
        ("verification", "ok"),
        ("generated_utc",),
    ],
)
def test_changing_any_field_breaks_the_export_hmac(
    db: Path, keys: InMemoryKeyProvider, path: tuple[object, ...]
) -> None:
    document = copy.deepcopy(export(db, key_of(keys)))
    target = document
    for step in path[:-1]:
        target = target[step]  # type: ignore[index]
    old = target[path[-1]]  # type: ignore[index]
    target[path[-1]] = (
        (not old) if isinstance(old, bool) else (old + 1 if isinstance(old, int) else f"{old}x")
    )  # type: ignore[index]
    assert not verify_export(document, key_of(keys))


def test_others_can_recompute_each_row_hash_without_the_key(
    db: Path, keys: InMemoryKeyProvider
) -> None:
    rows = export(db, key_of(keys))["rows"]
    for row in rows:
        content = {k: v for k, v in row.items() if k not in {"prev_hash", "row_hash", "hmac"}}
        canonical = json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
        digest = hashlib.sha256(row["prev_hash"].encode() + canonical).hexdigest()
        assert digest == row["row_hash"]
    assert rows[1]["prev_hash"] == rows[0]["row_hash"]


def test_a_tampered_log_still_exports_and_says_so(db: Path, keys: InMemoryKeyProvider) -> None:
    tamper(db, "UPDATE audit_log SET model = ? WHERE id = 2", ("modèle",))
    document = export(db, key_of(keys))
    assert document["verification"]["ok"] is False
    assert document["verification"]["first_bad_id"] == 2
    assert document["verification"]["reason"] == "edited"
    assert document["rows"][1]["model"] == "mod\\xe8le"  # kept, escaped to ASCII
    assert verify_export(document, key_of(keys))


def test_write_private_is_0600_and_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "export.json"
    write_private(target, b"{}\n")
    assert target.read_bytes() == b"{}\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600

    def fail(self: Path, other: Path) -> Path:
        del self, other
        msg = "disk full"
        raise OSError(msg)

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError, match="disk full"):
        write_private(target, b"new")
    assert target.read_bytes() == b"{}\n"
    assert [p.name for p in tmp_path.iterdir()] == ["export.json"]


def test_the_summary_shows_verification_head_totals_and_the_json_hmac(
    db: Path, keys: InMemoryKeyProvider
) -> None:
    document = export(db, key_of(keys))
    text = "\n".join(summary_lines(document, "export.json"))
    head = document["chain"]["head_hash"]
    assert "Verification:       OK, 3 rows checked" in text
    assert f"Chain head:         row 3, {head}" in text
    assert "Requests:           3" in text
    assert "Bytes sent:         306" in text  # 101 + 102 + 103
    assert "Local / cloud:      1 / 2" in text
    assert "  api.example.com   2" in text
    assert "  127.0.0.1:11434   1" in text
    assert "  200               3" in text
    assert "  EMAIL             3" in text
    assert "  PERSON            6" in text  # 1 + 2 + 3
    assert "export.json" in text
    assert document["hmac"] in text


def test_the_summary_names_a_broken_row(db: Path, keys: InMemoryKeyProvider) -> None:
    tamper(db, "UPDATE audit_log SET bytes_out = 1 WHERE id = 2")
    text = "\n".join(summary_lines(export(db, key_of(keys)), "export.json"))
    assert "Verification:       BROKEN at row 2: edited" in text
