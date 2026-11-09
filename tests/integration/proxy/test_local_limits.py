"""Security review M12: a local client or a hostile upstream cannot exhaust the gateway."""

import json
from typing import Any

import httpx
import pytest
from starlette.testclient import TestClient

from gateway.cloud.providers import Provider
from gateway.cloud.upstream import MAX_REPLY_BYTES
from gateway.engine.detect import detect
from gateway.engine.session import SessionStore
from gateway.proxy import app as app_module
from gateway.proxy.app import MAX_SESSION_ENTRIES, create_app
from gateway.proxy.sse import MAX_LINE_CHARS
from tests.proxy_fakes import FakeUpstream, PersonalData, RecordingSink, decode_sse

DATA = PersonalData.generate(seed=29)
CHAT = "/v1/chat/completions"
BODY = {"messages": [{"role": "user", "content": f"Write to {DATA.email}."}]}


def _client(
    fake: FakeUpstream, sink: RecordingSink | None = None, sessions: SessionStore | None = None
) -> TestClient:
    app = create_app(
        fake.upstream(),
        engine_config=DATA.engine_config(),
        model="test-model",
        audit=sink,
        sessions=sessions,
    )
    return TestClient(app, base_url="http://127.0.0.1:8787")


def _never(_: httpx.Request) -> httpx.Response:
    msg = "these requests must not reach the upstream"
    raise AssertionError(msg)


def test_a_stream_line_over_1_mib_ends_the_stream_with_an_error_event() -> None:
    line = "data: " + "x" * MAX_LINE_CHARS + "\n\n"

    def respond(_: httpx.Request) -> httpx.Response:
        headers = {"content-type": "text/event-stream"}
        return httpx.Response(200, headers=headers, content=line.encode())

    sink = RecordingSink()
    response = _client(FakeUpstream(Provider.OPENAI, respond), sink).post(
        CHAT, json={**BODY, "stream": True}
    )
    (event,) = decode_sse(response.content)
    assert "1 MiB" in json.loads(event.data)["error"]["message"]
    assert [e.outcome for e in sink.events] == ["upstream_error"]


def test_a_whole_answer_over_16_mib_is_a_502() -> None:
    def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b" " * (MAX_REPLY_BYTES + 1))

    response = _client(FakeUpstream(Provider.OPENAI, respond)).post(CHAT, json=BODY)
    assert response.status_code == 502
    assert "16 MiB" in response.json()["error"]["message"]


def test_detection_runs_outside_the_session_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(FakeUpstream(Provider.OLLAMA, _never))
    gateway: Any = client.app.state.gateway  # ty: ignore[unresolved-attribute]
    held: list[bool] = []

    def watching(text: str, **kwargs: Any) -> object:  # noqa: ANN401
        held.append(gateway.session_lock.locked())
        return detect(text, **kwargs)

    monkeypatch.setattr(app_module, "detect", watching)
    first = client.post("/mask", json={"text": f"Mail {DATA.email}."})
    assert first.status_code == 200
    session_id = first.json()["session_id"]
    second = client.post("/mask", json={"text": f"Card {DATA.card}.", "session_id": session_id})
    assert second.status_code == 200
    assert held == [False, False]


def test_the_session_entry_cap_is_50000() -> None:
    assert MAX_SESSION_ENTRIES == 50_000


def test_a_session_that_would_pass_its_entry_cap_is_refused_with_413(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_module, "MAX_SESSION_ENTRIES", 3)
    sessions = SessionStore()
    client = _client(FakeUpstream(Provider.OLLAMA, _never), sessions=sessions)
    first = client.post("/mask", json={"text": f"{DATA.email} and {DATA.ssn}"})
    assert first.status_code == 200
    session_id = first.json()["session_id"]

    refused = client.post(
        "/mask", json={"text": f"{DATA.card} and {DATA.iban}", "session_id": session_id}
    )
    assert refused.status_code == 413
    assert refused.json()["error"]["type"] == "session_full"
    assert "new session" in refused.json()["error"]["message"]
    assert DATA.card not in refused.text
    assert len(sessions.get(session_id)) == 2  # nothing was added

    room = client.post("/mask", json={"text": f"{DATA.card}.", "session_id": session_id})
    assert room.status_code == 200
    assert len(sessions.get(session_id)) == 3
