import dataclasses
import hashlib
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from gateway.audit import AuditLog, AuditLogSink
from gateway.engine.session_disk import InMemoryKeyProvider
from gateway.engine.types import EntityType
from gateway.proxy.audit import AuditEvent, AuditSink
from tests.audit_support import all_rows

PAYLOAD_SHA = hashlib.sha256(b"[PERSON_1] asked about [EMAIL_1]").hexdigest()
STARTED = datetime(2026, 11, 9, 8, 0, tzinfo=UTC)


FORWARDED = AuditEvent(
    route="/v1/chat/completions",
    outcome="forwarded",
    status_code=200,
    started_at=STARTED,
    duration_ms=12,
    provider="openai",
    upstream_host="api.example.com",
    model="model-a",
    entity_counts=Counter({EntityType.PERSON: 2, EntityType.EMAIL: 1}),
    request_sha256=PAYLOAD_SHA,
    bytes_out=321,
    upstream_status=200,
    local=False,
)


def forwarded(**overrides: Any) -> AuditEvent:  # noqa: ANN401  (any AuditEvent field)
    return dataclasses.replace(FORWARDED, **overrides)


@pytest.fixture
def sink(tmp_path: Path) -> AuditLogSink:
    return AuditLogSink(AuditLog(tmp_path / "audit.db", InMemoryKeyProvider()))


def test_the_sink_implements_the_proxy_protocol(sink: AuditLogSink, tmp_path: Path) -> None:
    hook: AuditSink = sink
    hook.record(forwarded())
    assert sink.verify().ok
    sink.close()
    (row,) = all_rows(tmp_path / "audit.db")
    assert row["kind"] == "request"
    assert row["upstream_host"] == "api.example.com"
    assert row["model"] == "model-a"
    assert row["entity_counts_json"] == '{"EMAIL":1,"PERSON":2}'
    assert row["masked_payload_sha256"] == PAYLOAD_SHA
    assert row["bytes_out"] == 321
    assert row["status"] == 200
    assert row["local"] == 0
    assert row["session_id"] == ""  # a chat request without a session


def test_a_session_request_stores_the_hash_of_its_session(
    sink: AuditLogSink, tmp_path: Path
) -> None:
    session_id = "s" * 43  # the shape of a token_urlsafe(32) id
    sink.record(forwarded(session_id=session_id))
    assert sink.verify().ok
    shown = sink.recent(1, None)
    sink.close()
    (row,) = all_rows(tmp_path / "audit.db")
    assert row["session_id"] == hashlib.sha256(session_id.encode()).hexdigest()
    assert session_id not in repr(row)
    assert session_id not in repr(shown)
    assert "session_id" not in shown[0]  # GET /audit never shows it


def test_local_upstream_and_no_status(sink: AuditLogSink, tmp_path: Path) -> None:
    sink.record(forwarded(outcome="upstream_error", status_code=502, upstream_status=None))
    sink.record(forwarded(upstream_host="127.0.0.1", local=True))
    sink.close()
    rows = all_rows(tmp_path / "audit.db")
    assert [(r["status"], r["local"]) for r in rows] == [(0, 0), (200, 1)]


def test_an_ipv6_host_is_stored_in_brackets(sink: AuditLogSink, tmp_path: Path) -> None:
    sink.record(forwarded(upstream_host="::1", local=True))
    sink.close()
    assert all_rows(tmp_path / "audit.db")[0]["upstream_host"] == "[::1]"


@pytest.mark.parametrize(
    "event",
    [
        AuditEvent("/v1/chat/completions", "rejected", 422, STARTED, 1, provider="openai"),
        AuditEvent("/mask", "masked", 200, STARTED, 1, entity_counts={"EMAIL": 1}),
        AuditEvent("/session", "session_deleted", 204, STARTED, 1),
    ],
)
def test_events_that_sent_nothing_upstream_write_no_row(
    sink: AuditLogSink, tmp_path: Path, event: AuditEvent
) -> None:
    sink.record(event)
    sink.close()
    assert all_rows(tmp_path / "audit.db") == []


def test_open_default_writes_under_safeguard_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SAFEGUARD_HOME", str(tmp_path / "sg"))
    sink = AuditLogSink.open_default(InMemoryKeyProvider())
    sink.record(forwarded())
    sink.close()
    assert len(all_rows(tmp_path / "sg" / "audit.db")) == 1
