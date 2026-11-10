"""Security review M12: a local client or a hostile upstream cannot exhaust the gateway."""

import asyncio
import json
from typing import Any

import httpx
import pytest
from starlette.testclient import TestClient

from gateway.cloud.providers import Provider
from gateway.cloud.upstream import MAX_REPLY_BYTES
from gateway.engine.detect import detect
from gateway.engine.mask import Mapping
from gateway.engine.session import SessionStore
from gateway.proxy import app as app_module
from gateway.proxy.app import MAX_SESSION_ENTRIES, SESSION_HEADER, create_app
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


class _WatchedStore(SessionStore):
    """Records, for each call, whether the session lock is held and the event loop runs it."""

    def __init__(self) -> None:
        super().__init__()
        self.lock: Any = None
        self.calls: list[tuple[str, bool, bool]] = []

    def _note(self, name: str) -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            on_loop = False
        else:
            on_loop = True
        self.calls.append((name, self.lock.locked(), on_loop))

    def create(self, mapping: Mapping | None = None) -> str:
        self._note("create")
        return super().create(mapping)

    def get(self, session_id: str) -> Mapping:
        self._note("get")
        return super().get(session_id)

    def save(self, session_id: str) -> None:
        self._note("save")
        super().save(session_id)

    def delete(self, session_id: str) -> bool:
        self._note("delete")
        return super().delete(session_id)

    def purge_expired(self) -> int:
        self._note("purge_expired")
        return super().purge_expired()

    def __len__(self) -> int:
        self._note("len")
        return super().__len__()


def test_session_store_work_holds_the_lock_and_leaves_the_event_loop() -> None:
    # Security review 2, M-d: a delete during masking must wait for the lock. M-e: the
    # store reads and writes disk and the keychain, which must not block the loop.
    def respond(_: httpx.Request) -> httpx.Response:
        choice = {"index": 0, "message": {"role": "assistant", "content": "ok"}}
        return httpx.Response(200, json={"id": "c1", "choices": [choice]})

    store = _WatchedStore()
    client = _client(FakeUpstream(Provider.OPENAI, respond), sessions=store)
    store.lock = client.app.state.gateway.session_lock  # ty: ignore[unresolved-attribute]
    session_id = client.post("/mask", json={"text": f"Mail {DATA.email}."}).json()["session_id"]
    again = client.post("/mask", json={"text": f"Card {DATA.card}.", "session_id": session_id})
    assert again.status_code == 200
    chat = client.post(CHAT, json=BODY, headers={SESSION_HEADER: session_id})
    assert chat.status_code == 200
    assert client.delete(f"/session/{session_id}").status_code == 204
    assert client.delete(f"/session/{session_id}").status_code == 404

    names = {name for name, _, _ in store.calls}
    assert {"create", "get", "save", "delete", "purge_expired", "len"} <= names
    assert [call for call in store.calls if not call[1] or call[2]] == []


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
