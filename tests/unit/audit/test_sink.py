import hashlib
from collections import Counter
from pathlib import Path

import pytest

from gateway.audit import AuditLog, AuditLogSink
from gateway.engine.session_disk import InMemoryKeyProvider
from gateway.engine.types import EntityType
from tests.audit_support import all_rows

PAYLOAD_SHA = hashlib.sha256(b"[PERSON_1] asked about [EMAIL_1]").hexdigest()


def send(sink: AuditLogSink) -> int:
    return sink.record_request(
        session_id="",
        upstream_host="api.example.com",
        model="model-a",
        entity_counts=Counter({EntityType.PERSON: 2, EntityType.EMAIL: 1}),
        masked_payload_sha256=PAYLOAD_SHA,
        bytes_out=321,
        status=200,
        local=False,
    )


def test_a_counter_of_entity_types_is_stored_as_plain_names(tmp_path: Path) -> None:
    db = tmp_path / "audit.db"
    sink = AuditLogSink(AuditLog(db, InMemoryKeyProvider()))
    assert send(sink) == 1
    assert sink.verify().ok
    sink.close()
    assert all_rows(db)[0]["entity_counts_json"] == '{"EMAIL":1,"PERSON":2}'


def test_open_default_writes_under_safeguard_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SAFEGUARD_HOME", str(tmp_path / "sg"))
    sink = AuditLogSink.open_default(InMemoryKeyProvider())
    send(sink)
    sink.close()
    assert len(all_rows(tmp_path / "sg" / "audit.db")) == 1
