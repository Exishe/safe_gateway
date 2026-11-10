import hashlib
from datetime import UTC, datetime
from typing import Any

import pytest

from gateway.audit.rows import (
    COLUMNS,
    CONTENT_COLUMNS,
    checkpoint_content,
    content_of,
    format_ts,
    request_content,
    stored_problem,
)
from gateway.engine.types import EntityType

PAYLOAD_SHA = "a" * 64


def valid(**overrides: object) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "row_id": 1,
        "ts_utc": "2026-11-09T08:05:03.000007Z",
        "session_id": "sess-1",
        "upstream_host": "api.example.com",
        "model": "model-a",
        "entity_counts": {"PERSON": 1, "EMAIL": 2},
        "masked_payload_sha256": PAYLOAD_SHA,
        "bytes_out": 512,
        "status": 200,
        "local": False,
    }
    arguments.update(overrides)
    return arguments


def test_format_ts_is_fixed_width_utc_with_microseconds() -> None:
    moment = datetime(2026, 11, 9, 8, 5, 3, 7, tzinfo=UTC)
    assert format_ts(moment) == "2026-11-09T08:05:03.000007Z"


def test_format_ts_refuses_naive_datetimes() -> None:
    with pytest.raises(ValueError, match="timezone"):
        format_ts(datetime(2026, 11, 9))  # noqa: DTZ001  (naive on purpose)


def test_request_content_has_every_content_column() -> None:
    content = request_content(**valid())
    assert list(content) == list(CONTENT_COLUMNS)
    assert (*CONTENT_COLUMNS, "prev_hash", "row_hash", "hmac") == COLUMNS
    assert content["kind"] == "request"
    assert content["purged_through_id"] is None
    assert content["local"] is False


def test_request_content_stores_a_hash_of_the_session_id() -> None:
    content = request_content(**valid(session_id="sess-1"))
    assert content["session_id"] == hashlib.sha256(b"sess-1").hexdigest()
    assert request_content(**valid(session_id=""))["session_id"] == ""


def test_request_content_writes_counts_as_canonical_json() -> None:
    content = request_content(**valid(entity_counts={"PERSON": 1, "EMAIL": 2}))
    assert content["entity_counts_json"] == '{"EMAIL":2,"PERSON":1}'


def test_every_entity_type_name_is_accepted() -> None:
    counts = {str(entity_type): 1 for entity_type in EntityType}
    request_content(**valid(entity_counts=counts))


@pytest.mark.parametrize("host", ["127.0.0.1:11434", "[::1]:8787", "api.example.com", "localhost"])
def test_hosts_with_optional_port_are_accepted(host: str) -> None:
    assert request_content(**valid(upstream_host=host))["upstream_host"] == host


@pytest.mark.parametrize(
    "model", ["qwen2.5:3b", "library/llama3:latest", "gpt-4o", "hf.co/org/m:Q4_K_M"]
)
def test_model_names_are_accepted(model: str) -> None:
    assert request_content(**valid(model=model))["model"] == model


@pytest.mark.parametrize(
    "overrides",
    [
        {"upstream_host": "https://api.example.com/v1"},
        {"upstream_host": "user@api.example.com"},
        {"upstream_host": "api.example.com/v1"},
        {"upstream_host": "api example.com"},
        {"upstream_host": ""},
        {"model": "gpt 4"},
        {"model": ""},
        {"model": "m" * 129},
        {"model": "jane.doe@example.org"},  # review M1
        {"model": "John Smith asked about his card"},
        {"entity_counts": {"person": 1}},
        {"entity_counts": {"PERSON": -1}},
        {"entity_counts": {"PERSON": True}},
        {"entity_counts": {"PERSON": 1.0}},
        {"entity_counts": {f"T{i}": 1 for i in range(65)}},
        {"masked_payload_sha256": "A" * 64},
        {"masked_payload_sha256": "a" * 63},
        {"masked_payload_sha256": "g" * 64},
        {"bytes_out": -1},
        {"bytes_out": 2**53},
        {"bytes_out": True},
        {"status": 99},
        {"status": 600},
        {"status": 1000},
        {"status": -1},
        {"local": 1},
        {"session_id": "s" * 513},
        {"row_id": 0},
    ],
)
def test_values_outside_their_shape_are_refused(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError):  # noqa: PT011  (each case has its own message)
        request_content(**valid(**overrides))


def test_status_zero_means_no_response() -> None:
    assert request_content(**valid(status=0))["status"] == 0


def test_checkpoint_content_leaves_request_columns_null() -> None:
    content = checkpoint_content(
        row_id=9,
        ts_utc="2026-11-09T08:05:03.000007Z",
        purged_through_id=4,
        purged_through_hash="b" * 64,
        purged_count=4,
        purged_before_utc="2026-10-10T08:05:03.000007Z",
    )
    assert list(content) == list(CONTENT_COLUMNS)
    assert content["kind"] == "checkpoint"
    assert content["session_id"] is None
    assert content["local"] is None
    assert content["purged_through_hash"] == "b" * 64


def stored(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = dict(request_content(**valid()))
    row["local"] = 0
    row.update(prev_hash="0" * 64, row_hash="1" * 64, hmac="2" * 64)
    row.update(overrides)
    return row


def test_a_well_formed_stored_row_has_no_problem() -> None:
    assert stored_problem(stored()) is None
    assert content_of(stored(local=1))["local"] is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"local": 2},
        {"bytes_out": 2**53},
        {"status": -(2**63)},
        {"kind": "deleted"},
        {"bytes_out": 1.5},
        {"model": b"bytes"},
        {"model": "café"},
        {"row_hash": "1" * 63},
        {"hmac": None},
        {"id": "1"},
    ],
)
def test_stored_problem_flags_values_that_cannot_be_hashed(overrides: dict[str, object]) -> None:
    assert stored_problem(stored(**overrides)) is not None
