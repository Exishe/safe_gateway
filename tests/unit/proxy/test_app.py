from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

from gateway import __version__
from gateway.cloud.providers import Provider
from gateway.config import ProxyConfig
from gateway.engine.mask import Mapping
from gateway.engine.rehydrate import rehydrate
from gateway.engine.session import SessionStore
from gateway.engine.session_disk import EncryptedMappingStore, InMemoryKeyProvider
from gateway.proxy.app import DEV_UI_ORIGINS, MAX_SESSIONS, create_app
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


def _rebuild(text: str, detections: list[dict[str, object]]) -> str:
    pieces, cursor = [], 0
    for found in detections:
        start, end = found["start"], found["end"]
        assert isinstance(start, int)
        assert isinstance(end, int)
        pieces += [text[cursor:start], str(found["placeholder"])]
        cursor = end
    return "".join([*pieces, text[cursor:]])


def test_mask_returns_each_detection_with_its_place_type_and_score() -> None:
    payload = _client().post("/mask", json={"text": DATA.prompt}).json()
    (detections,) = payload["detections"]
    (masked,) = payload["masked"]
    assert _rebuild(DATA.prompt, detections) == masked
    by_value = {DATA.prompt[d["start"] : d["end"]]: d for d in detections}
    assert set(by_value) == set(DATA.values)
    email = by_value[DATA.email]
    assert email["type"] == "EMAIL"
    assert payload["mapping"][email["placeholder"]] == DATA.email
    assert email["source"] == "detected"
    assert 0 < email["score"] <= 1
    assert by_value[DATA.name]["type"] == "PERSON"
    assert by_value[DATA.name]["score"] == 1.0  # the gazetteer's score


def test_mask_marks_repeats_and_literals_without_a_score() -> None:
    client = _client()
    first = client.post("/mask", json={"text": DATA.email}).json()
    text = f"Again {DATA.email.upper()} and {DATA.email}, see [PERSON_4]."
    second = client.post("/mask", json={"text": text, "session_id": first["session_id"]}).json()
    (detections,) = second["detections"]
    assert _rebuild(text, detections) == second["masked"][0]
    sources = {text[d["start"] : d["end"]]: (d["source"], d["score"]) for d in detections}
    assert sources["[PERSON_4]"] == ("literal", None)
    for value, (source, score) in sources.items():
        if value != "[PERSON_4]":
            assert source in {"detected", "repeat"}
            assert score is None or 0 < score <= 1


def test_mask_detections_are_listed_per_text() -> None:
    payload = _client().post("/mask", json={"texts": ["nothing here", DATA.email]}).json()
    assert payload["detections"][0] == []
    assert [d["type"] for d in payload["detections"][1]] == ["EMAIL"]


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


def test_mask_saves_the_session_after_adding_entries(tmp_path: Path) -> None:
    """Security review M11: /mask saved the session before masking into it, so the new
    entries never reached disk and a restart lost them."""
    disk = EncryptedMappingStore(tmp_path / "sessions", InMemoryKeyProvider())
    client = _client(sessions=SessionStore(persist=disk))
    first = client.post("/mask", json={"text": f"Mail {DATA.email}."}).json()
    session_id = first["session_id"]
    client.post("/mask", json={"text": f"Card {DATA.card}.", "session_id": session_id})
    stored = disk.load(session_id).to_dict()
    assert DATA.email in stored.values()
    assert DATA.card in stored.values()


# ---------------------------------------------------------------- the web UI (ADR 0024)

INDEX = "<!doctype html><title>SafeGuard</title>"


def _ui_dir(root: Path) -> Path:
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(INDEX, encoding="utf-8")
    (root / "assets" / "app.js").write_text("export {};", encoding="utf-8")
    return root


def _ui_client(ui_dir: Path | None) -> TestClient:
    fake = FakeUpstream(Provider.OLLAMA, _never)
    app = create_app(fake.upstream(), engine_config=DATA.engine_config(), ui_dir=ui_dir)
    return TestClient(app, base_url="http://127.0.0.1:8787")


def test_the_built_ui_is_served_at_the_root_with_strict_headers(tmp_path: Path) -> None:
    client = _ui_client(_ui_dir(tmp_path))
    page = client.get("/")
    assert page.status_code == 200
    assert page.text == INDEX
    csp = page.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "connect-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "unsafe-inline" not in csp
    assert page.headers["x-frame-options"] == "DENY"
    assert page.headers["x-content-type-options"] == "nosniff"
    assert page.headers["referrer-policy"] == "no-referrer"
    asset = client.get("/assets/app.js")
    assert asset.status_code == 200
    assert asset.headers["x-content-type-options"] == "nosniff"


def test_api_routes_win_over_static_files(tmp_path: Path) -> None:
    ui = _ui_dir(tmp_path)
    (ui / "health").write_text("not the route", encoding="utf-8")
    assert _ui_client(ui).get("/health").json()["status"] == "ok"


def test_static_files_keep_the_host_and_origin_checks(tmp_path: Path) -> None:
    client = _ui_client(_ui_dir(tmp_path))
    assert client.get("/", headers={"host": "rebind.example:8787"}).status_code == 400
    assert client.get("/", headers={"origin": "https://evil.example"}).status_code == 403


def test_the_served_ui_may_post_to_its_own_origin(tmp_path: Path) -> None:
    client = _ui_client(_ui_dir(tmp_path))
    own = {"origin": "http://127.0.0.1:8787"}
    assert client.post("/mask", json={"text": "x"}, headers=own).status_code == 200
    dev = {"origin": "http://127.0.0.1:5173"}
    assert client.post("/mask", json={"text": "x"}, headers=dev).status_code == 403


def test_without_a_build_there_is_no_ui_and_no_same_origin(tmp_path: Path) -> None:
    for ui_dir in (None, tmp_path / "missing", tmp_path):  # tmp_path has no index.html
        client = _ui_client(ui_dir)
        assert client.get("/").status_code == 404
        own = {"origin": "http://127.0.0.1:8787"}
        assert client.post("/mask", json={"text": "x"}, headers=own).status_code == 403


def test_traversal_out_of_the_ui_dir_is_refused(tmp_path: Path) -> None:
    ui = _ui_dir(tmp_path / "dist")
    (tmp_path / "secret.txt").write_text("outside", encoding="utf-8")
    client = _ui_client(ui)
    for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/assets/..%2f..%2fsecret.txt"):
        assert "outside" not in client.get(path).text


@pytest.mark.parametrize("origin", DEV_UI_ORIGINS)
def test_dev_ui_origins_need_the_flag(origin: str) -> None:
    headers = {"origin": origin}
    assert _client().post("/mask", json={"text": "x"}, headers=headers).status_code == 403
    client = _client(proxy=ProxyConfig(allow_dev_ui=True))
    response = client.post("/mask", json={"text": "x"}, headers=headers)
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
