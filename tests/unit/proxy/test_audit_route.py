"""GET /audit: recent rows (metadata only) and the verify result, paginated."""

from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

from gateway.audit import AuditLog, AuditLogSink
from gateway.cloud.providers import Provider
from gateway.engine.session_disk import InMemoryKeyProvider
from gateway.proxy.app import AUDIT_MAX_LIMIT, create_app
from tests.audit_support import fill, tamper
from tests.proxy_fakes import FakeUpstream, PersonalData

DATA = PersonalData.generate(seed=12)
ROW_FIELDS = {
    "id",
    "kind",
    "ts_utc",
    "upstream_host",
    "model",
    "entity_counts",
    "masked_payload_sha256",
    "bytes_out",
    "status",
    "local",
    "row_hash",
}


def _never(_: httpx.Request) -> httpx.Response:
    msg = "GET /audit must not call the upstream"
    raise AssertionError(msg)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "audit.db"


def _client(db: Path, rows: int) -> TestClient:
    log = AuditLog(db, InMemoryKeyProvider())
    fill(log, rows)
    app = create_app(
        FakeUpstream(Provider.OLLAMA, _never).upstream(),
        engine_config=DATA.engine_config(),
        audit=AuditLogSink(log),
    )
    return TestClient(app, base_url="http://127.0.0.1:8787")


def test_recent_rows_newest_first_with_the_verify_result(db: Path) -> None:
    response = _client(db, 3).get("/audit")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert [row["id"] for row in payload["rows"]] == [3, 2, 1]
    assert set(payload["rows"][0]) == ROW_FIELDS
    assert payload["rows"][0]["entity_counts"] == {"EMAIL": 1, "PERSON": 3}
    assert payload["rows"][0]["local"] is False
    assert payload["next_before_id"] is None
    verification = payload["verification"]
    assert verification["ok"] is True
    assert verification["rows_checked"] == 3
    assert verification["head_id"] == 3


def test_pages_follow_next_before_id(db: Path) -> None:
    client = _client(db, 5)
    first = client.get("/audit", params={"limit": 2}).json()
    assert [row["id"] for row in first["rows"]] == [5, 4]
    assert first["next_before_id"] == 4
    second = client.get("/audit", params={"limit": 2, "before_id": 4}).json()
    assert [row["id"] for row in second["rows"]] == [3, 2]
    third = client.get("/audit", params={"limit": 2, "before_id": 2}).json()
    assert [row["id"] for row in third["rows"]] == [1]
    assert third["next_before_id"] is None


@pytest.mark.parametrize(
    "params",
    [
        {"limit": "0"},
        {"limit": str(AUDIT_MAX_LIMIT + 1)},
        {"limit": "ten"},
        {"before_id": "-1"},
        {"before_id": "x"},
        {"verbose": "1"},
    ],
)
def test_bad_parameters_are_refused(db: Path, params: dict[str, str]) -> None:
    response = _client(db, 1).get("/audit", params=params)
    assert response.status_code == 400
    assert response.json()["error"]["type"] == "invalid_request_error"


def test_the_limit_cap_is_allowed(db: Path) -> None:
    response = _client(db, 1).get("/audit", params={"limit": AUDIT_MAX_LIMIT})
    assert response.status_code == 200


def test_a_broken_log_is_reported_not_hidden(db: Path) -> None:
    client = _client(db, 3)
    tamper(db, "UPDATE audit_log SET bytes_out = 7 WHERE id = 2")
    verification = client.get("/audit").json()["verification"]
    assert verification["ok"] is False
    assert verification["first_bad_id"] == 2
    assert verification["reason"] == "edited"


def test_no_text_or_secret_columns_are_served(db: Path) -> None:
    rows = _client(db, 2).get("/audit").json()["rows"]
    for row in rows:
        assert "hmac" not in row
        assert "session_id" not in row


def test_loopback_guards_apply(db: Path) -> None:
    client = _client(db, 1)
    assert client.get("/audit", headers={"host": "evil.example"}).status_code == 400
    assert client.get("/audit", headers={"origin": "https://evil.example"}).status_code == 403


def test_without_an_audit_log_the_route_says_so() -> None:
    app = create_app(FakeUpstream(Provider.OLLAMA, _never).upstream())
    response = TestClient(app, base_url="http://127.0.0.1:8787").get("/audit")
    assert response.status_code == 404
    assert "--no-audit" in response.json()["error"]["message"]
