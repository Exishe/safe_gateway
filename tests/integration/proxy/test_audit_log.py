"""The proxy writes the hash-chained audit log: one row per upstream request, no text.

The app runs in process with a mocked upstream and a real `AuditLogSink` on a temp
file, keyed from an in-memory provider (no keychain, no sockets).
"""

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from faker import Faker
from starlette.testclient import TestClient
from starlette.types import Message

from gateway.audit import AuditLog, AuditLogSink, verify_file
from gateway.cloud.providers import Provider
from gateway.engine.session_disk import InMemoryKeyProvider
from gateway.proxy.app import create_app
from gateway.proxy.audit import AuditEvent
from gateway.secrets import AUDIT_KEY_ID
from tests.audit_support import all_rows
from tests.proxy_fakes import (
    FakeUpstream,
    PersonalData,
    anthropic_events,
    last_user_text,
    openai_events,
    sse_body,
    stream_response,
)

CHAT = "/v1/chat/completions"
MESSAGES = "/v1/messages"
EXPECTED_COUNTS = '{"CREDIT_CARD":1,"EMAIL":1,"IBAN":1,"PERSON":1,"SSN":1}'


class Log:
    """A real audit log on disk and the sink the proxy writes it through."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.keys = InMemoryKeyProvider()
        self.sink = AuditLogSink(AuditLog(path, self.keys))

    def rows(self) -> list[dict[str, object]]:
        return all_rows(self.path)

    def verifies(self) -> bool:
        key = self.keys.get(AUDIT_KEY_ID)
        assert key is not None
        return verify_file(self.path, key).ok


@pytest.fixture
def log(tmp_path: Path) -> Log:
    return Log(tmp_path / "audit.db")


def echo(provider: Provider, *, stream: bool) -> FakeUpstream:
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
            message = {"role": "assistant", "content": answer}
            whole = {"choices": [{"index": 0, "message": message, "finish_reason": "stop"}]}
        if stream:
            return stream_response(sse_body(events))
        return httpx.Response(200, json=whole)

    return FakeUpstream(provider, respond)


def request_for(
    provider: Provider, data: PersonalData, *, stream: bool
) -> tuple[str, dict[str, Any]]:
    messages: list[dict[str, Any]] = [{"role": "user", "content": data.prompt}]
    if provider is Provider.ANTHROPIC:
        body: dict[str, Any] = {"max_tokens": 50, "system": f"File of {data.name}."}
        route = MESSAGES
    else:
        messages.insert(0, {"role": "system", "content": f"File of {data.name}."})
        body = {}
        route = CHAT
    body.update(model="client-model", messages=messages)
    if stream:
        body["stream"] = True
    return route, body


def client(fake: FakeUpstream, log: Log, data: PersonalData) -> TestClient:
    app = create_app(
        fake.upstream(), engine_config=data.engine_config(), model="test-model", audit=log.sink
    )
    return TestClient(app, base_url="http://127.0.0.1:8787")


CASES = [
    (Provider.OPENAI, False),
    (Provider.OPENAI, True),
    (Provider.ANTHROPIC, False),
    (Provider.ANTHROPIC, True),
    (Provider.OLLAMA, False),
    (Provider.OLLAMA, True),
]


@pytest.mark.parametrize(("provider", "stream"), CASES)
def test_each_request_writes_one_row_of_counts_and_hashes(
    log: Log, provider: Provider, *, stream: bool
) -> None:
    data = PersonalData.generate(seed=4)
    fake = echo(provider, stream=stream)
    route, body = request_for(provider, data, stream=stream)
    response = client(fake, log, data).post(route, json=body)
    assert response.status_code == 200

    (sent,) = fake.requests
    (row,) = log.rows()
    assert row["kind"] == "request"
    assert row["upstream_host"] == sent.url.host
    assert row["model"] == "test-model"
    assert row["entity_counts_json"] == EXPECTED_COUNTS
    assert row["masked_payload_sha256"] == hashlib.sha256(sent.content).hexdigest()
    assert row["bytes_out"] == len(sent.content)
    assert row["status"] == 200
    assert row["local"] == (1 if provider is Provider.OLLAMA else 0)
    assert log.verifies()


def test_two_requests_write_two_chained_rows(log: Log) -> None:
    data = PersonalData.generate(seed=22)
    http = client(echo(Provider.OPENAI, stream=False), log, data)
    route, body = request_for(Provider.OPENAI, data, stream=False)
    http.post(route, json=body)
    http.post(route, json=body)
    rows = log.rows()
    assert [r["id"] for r in rows] == [1, 2]
    assert rows[1]["prev_hash"] == rows[0]["row_hash"]
    assert log.verifies()


def failing(error: Exception) -> FakeUpstream:
    def respond(_: httpx.Request) -> httpx.Response:
        raise error

    return FakeUpstream(Provider.OPENAI, respond)


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("error", [httpx.ConnectError("refused"), httpx.ReadTimeout("slow")])
def test_no_answer_from_upstream_is_a_row_with_status_0(
    log: Log, error: Exception, *, stream: bool
) -> None:
    data = PersonalData.generate(seed=4)
    route, body = request_for(Provider.OPENAI, data, stream=stream)
    response = client(failing(error), log, data).post(route, json=body)
    assert response.status_code in {502, 504}
    (row,) = log.rows()
    assert row["status"] == 0
    assert row["entity_counts_json"] == EXPECTED_COUNTS


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize(
    ("answer", "status"),
    [
        (httpx.Response(429, json={"error": {"message": "slow down"}}), 429),
        (httpx.Response(500, text="<html>oops</html>"), 500),
        (httpx.Response(200, text="not json"), 200),
    ],
)
def test_upstream_error_answers_record_the_upstream_status(
    log: Log, answer: httpx.Response, status: int, *, stream: bool
) -> None:
    data = PersonalData.generate(seed=24)
    fake = FakeUpstream(Provider.OPENAI, lambda _: answer)
    route, body = request_for(Provider.OPENAI, data, stream=stream)
    client(fake, log, data).post(route, json=body)
    (row,) = log.rows()
    assert row["status"] == status


def test_a_stream_that_fails_midway_writes_one_row(log: Log) -> None:
    data = PersonalData.generate(seed=25)

    async def broken() -> Any:  # noqa: ANN401
        yield b'data: {"choices":[{"index":0,"delta":{"content":"Hi"}}]}\n\n'
        raise httpx.ReadError("cut")  # noqa: EM101

    def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=broken())

    route, body = request_for(Provider.OPENAI, data, stream=True)
    client(FakeUpstream(Provider.OPENAI, respond), log, data).post(route, json=body)
    (row,) = log.rows()
    assert row["status"] == 200  # the upstream answered; the stream broke later


def test_requests_that_never_go_upstream_write_no_row(log: Log) -> None:
    data = PersonalData.generate(seed=26)
    http = client(echo(Provider.OPENAI, stream=False), log, data)
    route, body = request_for(Provider.OPENAI, data, stream=False)
    body["tools"] = []
    assert http.post(route, json=body).status_code == 422
    assert http.post("/mask", json={"text": data.prompt}).status_code == 200
    assert log.rows() == []


def test_client_gone_before_the_stream_starts_still_writes_its_row(log: Log) -> None:
    data = PersonalData.generate(seed=27)
    fake = echo(Provider.OPENAI, stream=True)
    app = create_app(fake.upstream(), engine_config=data.engine_config(), audit=log.sink)
    _, body = request_for(Provider.OPENAI, data, stream=True)
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": CHAT,
        "raw_path": CHAT.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"127.0.0.1:8787"), (b"content-type", b"application/json")],
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", 8787),
    }
    messages: list[Message] = [
        {"type": "http.request", "body": json.dumps(body).encode(), "more_body": False}
    ]

    async def receive() -> Message:
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        if message["type"] == "http.response.start":
            msg = "client went away"
            raise OSError(msg)

    with pytest.raises(OSError, match="went away"):
        asyncio.run(app(scope, receive, send))
    (row,) = log.rows()
    assert row["masked_payload_sha256"] == hashlib.sha256(fake.requests[0].content).hexdigest()
    assert log.verifies()


class BrokenSink:
    def record(self, event: AuditEvent) -> None:
        if event.request_sha256 is not None:
            msg = "disk full"
            raise OSError(msg)


def test_a_failed_audit_write_stops_further_forwarding() -> None:
    data = PersonalData.generate(seed=28)
    fake = echo(Provider.OPENAI, stream=False)
    app = create_app(
        fake.upstream(), engine_config=data.engine_config(), model="m", audit=BrokenSink()
    )
    http = TestClient(app, base_url="http://127.0.0.1:8787")
    route, body = request_for(Provider.OPENAI, data, stream=False)
    assert http.post(route, json=body).status_code == 200  # already answered when it failed
    refused = http.post(route, json=body)
    assert refused.status_code == 503
    assert "audit" in refused.json()["error"]["message"]
    assert len(fake.requests) == 1  # the second request never left the machine
    assert http.post("/mask", json={"text": "hello"}).status_code == 200  # nothing leaves


# ------------------------------------------------------------------ no text in the file

NEEDLE_MIN = 6  # shorter strings occur by chance in binary data and say nothing


@pytest.mark.parametrize("seed", [31, 32, 33, 34, 35])
@pytest.mark.parametrize(("provider", "stream"), CASES[:4])
def test_no_byte_of_the_audit_file_holds_request_or_answer_text(
    tmp_path: Path, seed: int, provider: Provider, *, stream: bool
) -> None:
    fake_data = Faker(["en_US", "de_DE"])
    fake_data.seed_instance(seed)
    data = PersonalData.generate(seed=seed)
    extra = [fake_data.street_address(), fake_data.company(), fake_data.sentence(nb_words=9)]
    log = Log(tmp_path / "audit.db")
    fake = echo(provider, stream=stream)
    route, body = request_for(provider, data, stream=stream)
    body["messages"][-1]["content"] += " " + " ".join(extra)
    http = client(fake, log, data)
    answer = http.post(route, json=body)
    assert answer.status_code == 200
    log.sink.close()

    (sent,) = fake.requests
    masked = last_user_text(json.loads(sent.content))
    # Values are also searched word by word (a surname alone is still personal data);
    # whole texts only as a whole, since a lorem word may equal a column name.
    values = [*data.values, extra[0], extra[1]]
    texts = [data.prompt, masked, extra[2], answer.text]
    pieces = [p for v in values for p in (v, *v.split())] + texts
    raw = log.path.read_bytes()
    for piece in pieces:
        if len(piece) >= NEEDLE_MIN:
            assert piece.encode() not in raw, piece
            assert piece.encode("utf-16-le") not in raw, piece
    assert len(log.rows()) == 1
