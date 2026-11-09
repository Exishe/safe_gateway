import dataclasses
import typing
from datetime import UTC, datetime

from gateway.engine.mask import Mapping
from gateway.engine.types import EntityType
from gateway.proxy.audit import AuditEvent, AuditSink, NullAuditSink, entity_counts

# The only string fields an event may have; none of them can hold request text.
ALLOWED_STRING_FIELDS = {
    "route",
    "outcome",
    "provider",
    "upstream_host",
    "model",
    "request_sha256",
    "response_sha256",
}


def test_event_has_no_text_field() -> None:
    hints = typing.get_type_hints(AuditEvent)
    string_fields = {
        f.name for f in dataclasses.fields(AuditEvent) if "str" in str(hints[f.name]).split("[")[0]
    }
    assert string_fields <= ALLOWED_STRING_FIELDS


def test_entity_counts_are_distinct_values_per_type() -> None:
    mapping = Mapping()
    mapping.placeholder_for("a@example.org", EntityType.EMAIL)
    mapping.placeholder_for("b@example.org", EntityType.EMAIL)
    mapping.placeholder_for("a@example.org", EntityType.EMAIL)
    mapping.placeholder_for("[PERSON_1]", EntityType.LITERAL)
    assert entity_counts(mapping) == {"EMAIL": 2, "LITERAL": 1}


def test_null_sink_accepts_events() -> None:
    sink: AuditSink = NullAuditSink()
    event = AuditEvent(
        route="/mask",
        outcome="masked",
        status_code=200,
        started_at=datetime.now(UTC),
        duration_ms=1,
    )
    sink.record(event)
    assert event.entity_counts == {}
    assert event.stream is False
