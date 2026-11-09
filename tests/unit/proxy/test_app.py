import httpx
import pytest
from starlette.testclient import TestClient

from gateway import __version__
from gateway.cloud.providers import Provider
from gateway.config import ProxyConfig
from gateway.engine.mask import Mapping
from gateway.engine.rehydrate import rehydrate
from gateway.engine.session import SessionStore
from gateway.proxy.app import MAX_SESSIONS, create_app
from tests.proxy_fakes import FakeUpstream, PersonalData, RecordingSink

DATA = PersonalData.generate(seed=11)


def _never(_: httpx.Request) -> httpx.Response:
    msg = "these routes must not call the upstream"
    raise AssertionError(msg)


def _client(
    *,
    model: str | None = "test-model",
    provider: Provider = Provider.OLLAMA,
    sink: RecordingSink | None = None,
    sessions: SessionStore | None = None,
    proxy: ProxyConfig | None = None,
) -> TestClient:
    fake = FakeUpstream(provider, _never)
    app = create_app(
        fake.upstream(),
        engine_config=DATA.engine_config(),
        model=model,
        audit=sink,
        sessions=sessions,
        proxy=proxy,
    )
    return TestClient(app, base_url="http://127.0.0.1:8787")


def test_health() -> None:
    assert _client().get("/health").json() == {
        "status": "ok",
        "version": __version__,
        "provider": "ollama",
        "local": True,
        "audit": False,  # no sink given: nothing is recorded
    }
    assert _client(sink=RecordingSink()).get("/health").json()["audit"] is True


def test_audit_is_not_implemented_yet() -> None:
    response = _client().get("/audit")
    assert response.status_code == 501
    assert "Phase 5" in response.json()["error"]["message"]


def test_models_lists_the_configured_model() -> None:
    payload = _client().get("/v1/models").json()
    assert payload["object"] == "list"
    assert [m["id"] for m in payload["data"]] == ["test-model"]
    assert _client(model=None).get("/v1/models").json()["data"] == []


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_no_documentation_pages(path: str) -> None:
    assert _client().get(path).status_code == 404


def test_mask_returns_a_session_and_its_map() -> None:
    sink = RecordingSink()
    client = _client(sink=sink)
    response = client.post("/mask", json={"text": DATA.prompt})
    assert response.status_code == 200
    payload = response.json()
    assert response.headers["cache-control"] == "no-store"
    (masked,) = payload["masked"]
    for value in DATA.values:
        assert value not in masked
    mapping = Mapping.from_dict(payload["mapping"])
    assert rehydrate(masked, mapping) == (DATA.prompt, [])
    assert payload["entities"]["EMAIL"] == 1
    assert len(payload["session_id"]) >= 43
    (event,) = sink.events
    assert event.route == "/mask"
    assert event.outcome == "masked"
    assert event.entity_counts["PERSON"] == 1
    for value in DATA.values:
        assert value not in repr(event)


def test_mask_session_continues_the_numbering() -> None:
    client = _client()
    first = client.post("/mask", json={"texts": [DATA.email]}).json()
    other = PersonalData.generate(seed=12).email
    second = client.post(
        "/mask", json={"texts": [other, DATA.email], "session_id": first["session_id"]}
    ).json()
    assert second["session_id"] == first["session_id"]
    assert second["masked"] == ["[EMAIL_2]", "[EMAIL_1]"]
    assert set(second["mapping"]) == {"[EMAIL_1]", "[EMAIL_2]"}


def test_unknown_session_is_404_and_creates_nothing() -> None:
    sessions = SessionStore()
    client = _client(sessions=sessions)
    response = client.post("/mask", json={"text": DATA.email, "session_id": "planted-id"})
    assert response.status_code == 404
    assert "planted-id" not in response.text
    assert len(sessions) == 0
    assert "planted-id" not in sessions


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"text": 5},
        {"texts": "x"},
        {"texts": [1]},
        {"text": "a", "texts": ["b"]},
        {"text": "a", "session_id": 3},
        {"text": "a", "lang": "en"},
    ],
)
def test_bad_mask_requests_are_400(body: dict[str, object]) -> None:
    response = _client().post("/mask", json=body)
    assert response.status_code == 400
    assert response.json()["error"]["type"] == "invalid_request_error"


def test_session_store_is_bounded() -> None:
    sessions = SessionStore()
    for _ in range(MAX_SESSIONS):
        sessions.create()
    response = _client(sessions=sessions).post("/mask", json={"text": "x"})
    assert response.status_code == 503


def test_delete_session() -> None:
    sink = RecordingSink()
    client = _client(sink=sink)
    session_id = client.post("/mask", json={"text": DATA.email}).json()["session_id"]
    assert client.delete(f"/session/{session_id}").status_code == 204
    assert client.delete(f"/session/{session_id}").status_code == 404
    assert [e.outcome for e in sink.events] == ["masked", "session_deleted", "session_not_found"]


@pytest.mark.parametrize("path", ["/mask", "/v1/chat/completions", "/v1/messages"])
def test_post_routes_need_a_json_content_type(path: str) -> None:
    response = _client().post(
        path, content=b'{"text": "x"}', headers={"content-type": "text/plain"}
    )
    assert response.status_code == 415


def test_body_cap_applies_to_routes() -> None:
    client = _client(proxy=ProxyConfig(max_body_bytes=32))
    response = client.post("/mask", json={"text": "x" * 100})
    assert response.status_code == 413


def test_foreign_origin_is_refused_and_listed_origin_gets_cors() -> None:
    assert (
        _client()
        .post("/mask", json={"text": "x"}, headers={"origin": "https://evil.example"})
        .status_code
        == 403
    )
    origin = "chrome-extension://abcdefghijklmnop"
    client = _client(proxy=ProxyConfig(cors_allow_origins=(origin,)))
    preflight = client.options(
        "/mask",
        headers={
            "origin": origin,
            "access-control-request-method": "POST",
            "access-control-request-headers": "content-type",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == origin
    response = client.post("/mask", json={"text": "x"}, headers={"origin": origin})
    assert response.headers["access-control-allow-origin"] == origin


def test_foreign_host_header_is_refused() -> None:
    response = _client().get("/health", headers={"host": "rebind.example"})
    assert response.status_code == 400
