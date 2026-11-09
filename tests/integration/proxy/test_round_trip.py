"""End to end through the app with an in-process upstream: no sockets, no internet."""

import hashlib
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from starlette.testclient import TestClient

from gateway.cloud.providers import Provider
from gateway.proxy.app import create_app
from gateway.proxy.payload import PLACEHOLDER_INSTRUCTION
from gateway.proxy.sse import SSEEvent
from tests.proxy_fakes import (
    FAKE_KEY,
    FakeUpstream,
    PersonalData,
    RecordingSink,
    anthropic_events,
    decode_sse,
    last_user_text,
    openai_events,
    sse_body,
    stream_response,
    string_leaves,
)

DATA = PersonalData.generate()
CHAT = "/v1/chat/completions"
MESSAGES = "/v1/messages"


def _echo(provider: Provider, *, stream: bool, one_byte: bool = False) -> FakeUpstream:
    """An upstream that answers "Noted: <the masked user text>"."""

    def respond(request: httpx.Request) -> httpx.Response:
        answer = "Noted: " + last_user_text(json.loads(request.content))
        if provider is Provider.ANTHROPIC:
            events = anthropic_events(answer)
            whole: dict[str, Any] = {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": answer}],
            }
        else:
            events = openai_events(answer)
            whole = {
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": answer},
                        "finish_reason": "stop",
                    }
                ],
            }
        if stream:
            return stream_response(sse_body(events), one_byte_at_a_time=one_byte)
        return httpx.Response(200, json=whole)

    return FakeUpstream(provider, respond)


def _client(fake: FakeUpstream, sink: RecordingSink | None = None) -> TestClient:
    app = create_app(
        fake.upstream(), engine_config=DATA.engine_config(), model="test-model", audit=sink
    )
    return TestClient(app, base_url="http://127.0.0.1:8787")


def _request(provider: Provider, *, stream: bool) -> tuple[str, dict[str, Any]]:
    if provider is Provider.ANTHROPIC:
        body: dict[str, Any] = {
            "model": "client-model",
            "max_tokens": 50,
            "system": f"Client file for {DATA.name}.",
            "messages": [{"role": "user", "content": DATA.prompt}],
        }
        route = MESSAGES
    else:
        body = {
            "model": "client-model",
            "messages": [
                {"role": "system", "content": f"Client file for {DATA.name}."},
                {"role": "user", "content": DATA.prompt},
            ],
        }
        route = CHAT
    if stream:
        body["stream"] = True
    return route, body


def _answer_text(provider: Provider, content: bytes, *, stream: bool) -> str:
    if not stream:
        payload = json.loads(content)
        if provider is Provider.ANTHROPIC:
            return "".join(b["text"] for b in payload["content"] if b["type"] == "text")
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


def _assert_no_personal_data_upstream(fake: FakeUpstream) -> None:
    (request,) = fake.requests
    leaves = list(string_leaves(json.loads(request.content)))
    for value in DATA.values:
        assert value.encode() not in request.content
        assert not any(value in leaf for leaf in leaves)


CASES = [
    (Provider.OPENAI, False),
    (Provider.OPENAI, True),
    (Provider.ANTHROPIC, False),
    (Provider.ANTHROPIC, True),
    (Provider.OLLAMA, False),
    (Provider.OLLAMA, True),
]


@pytest.mark.parametrize(("provider", "stream"), CASES)
def test_round_trip_restores_values_and_sends_none_upstream(
    provider: Provider, *, stream: bool
) -> None:
    fake = _echo(provider, stream=stream)
    route, body = _request(provider, stream=stream)
    response = _client(fake).post(route, json=body, headers={"authorization": "Bearer dummy"})
    assert response.status_code == 200, response.text
    assert _answer_text(provider, response.content, stream=stream) == "Noted: " + DATA.prompt
    _assert_no_personal_data_upstream(fake)
    sent = fake.sent
    assert sent["model"] == "test-model"
    if provider is Provider.ANTHROPIC:
        assert sent["system"].startswith(PLACEHOLDER_INSTRUCTION)
        assert "[PERSON_1]" in sent["system"]
    else:
        assert sent["messages"][0] == {"role": "system", "content": PLACEHOLDER_INSTRUCTION}
    assert "[EMAIL_1]" in last_user_text(sent)


@pytest.mark.parametrize("provider", [Provider.OPENAI, Provider.ANTHROPIC])
def test_upstream_bytes_arriving_one_at_a_time(provider: Provider) -> None:
    fake = _echo(provider, stream=True, one_byte=True)
    route, body = _request(provider, stream=True)
    response = _client(fake).post(route, json=body)
    assert _answer_text(provider, response.content, stream=True) == "Noted: " + DATA.prompt


def test_client_authorization_is_never_forwarded() -> None:
    fake = _echo(Provider.OPENAI, stream=False)
    route, body = _request(Provider.OPENAI, stream=False)
    _client(fake).post(route, json=body, headers={"authorization": "Bearer client-secret"})
    (request,) = fake.requests
    assert request.headers["authorization"] == f"Bearer {FAKE_KEY}"
    assert b"client-secret" not in request.content


def test_streamed_response_has_sse_headers() -> None:
    fake = _echo(Provider.OPENAI, stream=True)
    route, body = _request(Provider.OPENAI, stream=True)
    response = _client(fake).post(route, json=body)
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(("provider", "stream"), CASES[:4])
def test_audit_gets_counts_and_hashes_only(provider: Provider, *, stream: bool) -> None:
    fake = _echo(provider, stream=stream)
    sink = RecordingSink()
    route, body = _request(provider, stream=stream)
    response = _client(fake, sink).post(route, json=body)
    (event,) = sink.events
    (request,) = fake.requests
    assert event.route == route
    assert event.outcome == "forwarded"
    assert event.status_code == 200
    assert event.stream is stream
    assert event.provider == provider.value
    assert event.upstream_host == request.url.host
    assert event.model == "test-model"
    assert event.request_sha256 == hashlib.sha256(request.content).hexdigest()
    upstream_answer = fake.responder(request)
    assert event.response_sha256 == hashlib.sha256(upstream_answer.read()).hexdigest()
    assert event.entity_counts == {
        "PERSON": 1,
        "EMAIL": 1,
        "SSN": 1,
        "CREDIT_CARD": 1,
        "IBAN": 1,
    }
    assert event.unresolved == 0
    for value in DATA.values:
        assert value not in repr(event)
    assert response.status_code == 200


def test_unresolved_placeholders_are_counted() -> None:
    def respond(_: httpx.Request) -> httpx.Response:
        message = {"role": "assistant", "content": "see [PERSON_8]"}
        return httpx.Response(200, json={"choices": [{"index": 0, "message": message}]})

    sink = RecordingSink()
    route, body = _request(Provider.OPENAI, stream=False)
    response = _client(FakeUpstream(Provider.OPENAI, respond), sink).post(route, json=body)
    assert response.json()["choices"][0]["message"]["content"] == "see [PERSON_8]"
    assert sink.events[0].unresolved == 1


def _failing(error: Exception) -> FakeUpstream:
    def respond(_: httpx.Request) -> httpx.Response:
        raise error

    return FakeUpstream(Provider.OPENAI, respond)


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize(
    ("error", "status"),
    [(httpx.ConnectError("refused"), 502), (httpx.ReadTimeout("slow"), 504)],
)
def test_network_failures_become_502_or_504(error: Exception, status: int, *, stream: bool) -> None:
    sink = RecordingSink()
    route, body = _request(Provider.OPENAI, stream=stream)
    response = _client(_failing(error), sink).post(route, json=body)
    assert response.status_code == status
    assert response.json()["error"]["type"] == "upstream_error"
    assert sink.events[0].outcome == "upstream_error"
    assert sink.events[0].status_code == status


@pytest.mark.parametrize("stream", [False, True])
def test_redirect_is_not_followed(*, stream: bool) -> None:
    fake = FakeUpstream(
        Provider.OPENAI,
        lambda _: httpx.Response(302, headers={"location": "https://elsewhere.test/"}),
    )
    route, body = _request(Provider.OPENAI, stream=stream)
    response = _client(fake).post(route, json=body)
    assert response.status_code == 502
    assert "redirect" in response.json()["error"]["message"]
    assert len(fake.requests) == 1


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("provider", [Provider.OPENAI, Provider.ANTHROPIC])
def test_upstream_json_error_passes_through(provider: Provider, *, stream: bool) -> None:
    error = {"error": {"message": "rate limited", "type": "rate_limit_error"}}
    fake = FakeUpstream(provider, lambda _: httpx.Response(429, json=error))
    route, body = _request(provider, stream=stream)
    response = _client(fake).post(route, json=body)
    assert response.status_code == 429
    assert response.json() == error


def test_upstream_non_json_error_is_502() -> None:
    fake = FakeUpstream(Provider.OPENAI, lambda _: httpx.Response(500, text="<html>oops</html>"))
    route, body = _request(Provider.OPENAI, stream=False)
    response = _client(fake).post(route, json=body)
    assert response.status_code == 502
    assert "oops" not in response.text


def test_upstream_non_json_success_is_502() -> None:
    fake = FakeUpstream(Provider.OPENAI, lambda _: httpx.Response(200, text="plain"))
    route, body = _request(Provider.OPENAI, stream=False)
    assert _client(fake).post(route, json=body).status_code == 502


def test_upstream_that_does_not_stream_is_502() -> None:
    fake = FakeUpstream(Provider.OPENAI, lambda _: httpx.Response(200, json={"choices": []}))
    route, body = _request(Provider.OPENAI, stream=True)
    assert _client(fake).post(route, json=body).status_code == 502


async def _breaks_after(first: bytes) -> AsyncIterator[bytes]:
    yield first
    msg = "connection reset"
    raise httpx.ReadError(msg)


@pytest.mark.parametrize("provider", [Provider.OPENAI, Provider.ANTHROPIC])
def test_failure_mid_stream_ends_with_an_error_event(provider: Provider) -> None:
    first = sse_body(
        openai_events("Hi [EMAIL_1]")[:4]  # role chunk, then "Hi [", "EMAI", "L_1]"
        if provider is Provider.OPENAI
        else anthropic_events("Hi [EMAIL_1]")[:6]
    )

    def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=_breaks_after(first)
        )

    sink = RecordingSink()
    route, body = _request(provider, stream=True)
    response = _client(FakeUpstream(provider, respond), sink).post(route, json=body)
    events = decode_sse(response.content)
    last = json.loads(events[-1].data)
    assert "error" in last
    assert DATA.email in "".join(e.data for e in events)  # text before the break is restored
    assert sink.events[0].outcome == "upstream_error"


def test_shape_mismatch_is_400_in_the_route_shape() -> None:
    fake = _echo(Provider.OPENAI, stream=False)
    route, body = _request(Provider.ANTHROPIC, stream=False)
    response = _client(fake).post(route, json=body)
    assert response.status_code == 400
    assert response.json()["type"] == "error"
    assert "/v1/chat/completions" in response.json()["error"]["message"]
    assert fake.requests == []


@pytest.mark.parametrize(
    ("route", "body", "status"),
    [
        (
            CHAT,
            {"model": "m", "messages": [{"role": "user", "content": [{"type": "image_url"}]}]},
            422,
        ),
        (CHAT, {"model": "m", "messages": [], "tools": []}, 422),
        (CHAT, {"messages": "x"}, 400),
    ],
)
def test_refused_requests_never_reach_upstream(
    route: str, body: dict[str, Any], status: int
) -> None:
    fake = _echo(Provider.OPENAI, stream=False)
    sink = RecordingSink()
    response = _client(fake, sink).post(route, json=body)
    assert response.status_code == status
    assert response.json()["error"]["message"]
    assert fake.requests == []
    assert sink.events[0].outcome == "rejected"


def test_model_passes_through_when_none_is_configured() -> None:
    fake = _echo(Provider.OLLAMA, stream=False)
    app = create_app(fake.upstream(), engine_config=DATA.engine_config())
    route, body = _request(Provider.OLLAMA, stream=False)
    body["model"] = "qwen2.5:3b"
    TestClient(app, base_url="http://127.0.0.1").post(route, json=body)
    assert fake.sent["model"] == "qwen2.5:3b"


def test_unparsable_sse_data_is_passed_on() -> None:
    raw = sse_body([SSEEvent("not json"), SSEEvent("[DONE]")])
    fake = FakeUpstream(Provider.OPENAI, lambda _: stream_response(raw))
    route, body = _request(Provider.OPENAI, stream=True)
    response = _client(fake).post(route, json=body)
    assert [e.data for e in decode_sse(response.content)] == ["not json", "[DONE]"]
