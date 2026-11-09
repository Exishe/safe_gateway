"""A recording, in-process upstream and Faker personal data for the proxy tests."""

import json
from collections.abc import AsyncIterator, Callable, Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx
import keyring
from faker import Faker
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError

from gateway.cloud.providers import Provider
from gateway.cloud.upstream import Upstream, UpstreamSettings
from gateway.engine.detect import EngineConfig
from gateway.engine.recognizers.gazetteer import Gazetteer
from gateway.engine.types import EntityType
from gateway.proxy.audit import AuditEvent
from gateway.proxy.sse import SSEDecoder, SSEEvent, encode
from gateway.secrets import ApiKey

FAKE_KEY = "fake-keychain-key-0123456789"  # pragma: allowlist secret
BASE_URLS = {
    Provider.OPENAI: "https://api.openai.test/v1",
    Provider.ANTHROPIC: "https://api.anthropic.test",
    Provider.OLLAMA: "http://127.0.0.1:11434/v1",
}

Responder = Callable[[httpx.Request], httpx.Response]


@dataclass(frozen=True)
class PersonalData:
    name: str
    email: str
    ssn: str
    card: str
    iban: str

    @classmethod
    def generate(cls, seed: int = 4) -> "PersonalData":
        fake = Faker()
        fake.seed_instance(seed)
        return cls(
            name=fake.name(),
            email=fake.email(),
            ssn=fake.ssn(),
            card=fake.credit_card_number(card_type="visa16"),
            iban=fake.iban(),
        )

    @property
    def values(self) -> list[str]:
        return [self.name, self.email, self.ssn, self.card, self.iban]

    @property
    def prompt(self) -> str:
        return (
            f"Write to {self.name} at {self.email}. "
            f"SSN {self.ssn}, card {self.card}, IBAN {self.iban}."
        )

    def engine_config(self) -> EngineConfig:
        return EngineConfig(gazetteer=Gazetteer({self.name: EntityType.PERSON}), ner="off")


def string_leaves(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from string_leaves(item)
    elif isinstance(value, list):
        for item in value:
            yield from string_leaves(item)


@dataclass
class FakeUpstream:
    """Answers through `responder`; keeps every request the gateway sent."""

    provider: Provider
    responder: Responder
    requests: list[httpx.Request] = field(default_factory=list)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responder(request)

    def upstream(self, settings: UpstreamSettings | None = None) -> Upstream:
        settings = settings or UpstreamSettings(self.provider, BASE_URLS[self.provider])
        key = None if self.provider is Provider.OLLAMA else ApiKey(FAKE_KEY)
        return Upstream(settings, key, transport=httpx.MockTransport(self._handle))

    @property
    def sent(self) -> dict[str, Any]:
        (request,) = self.requests
        return json.loads(request.content)


def last_user_text(body: dict[str, Any]) -> str:
    """The masked text of the last user message, as the upstream received it."""
    message = [m for m in body["messages"] if m["role"] == "user"][-1]
    content = message["content"]
    return content if isinstance(content, str) else "".join(p["text"] for p in content)


def pieces(text: str, size: int = 4) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


def openai_events(text: str) -> list[SSEEvent]:
    def chunk(delta: dict[str, str], finish: str | None = None) -> SSEEvent:
        payload = {
            "id": "chatcmpl-1",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "m",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        return SSEEvent(json.dumps(payload))

    return [
        chunk({"role": "assistant", "content": ""}),
        *(chunk({"content": piece}) for piece in pieces(text)),
        chunk({}, "stop"),
        SSEEvent("[DONE]"),
    ]


def anthropic_events(text: str) -> list[SSEEvent]:
    def event(name: str, **data: object) -> SSEEvent:
        return SSEEvent(json.dumps({"type": name, **data}), event=name)

    return [
        event("message_start", message={"id": "msg_1", "content": [], "role": "assistant"}),
        event("content_block_start", index=0, content_block={"type": "text", "text": ""}),
        event("ping"),
        *(
            event("content_block_delta", index=0, delta={"type": "text_delta", "text": piece})
            for piece in pieces(text)
        ),
        event("content_block_stop", index=0),
        event("message_delta", delta={"stop_reason": "end_turn"}, usage={"output_tokens": 9}),
        event("message_stop"),
    ]


def sse_body(events: list[SSEEvent]) -> bytes:
    return b"".join(encode(event) for event in events)


async def byte_by_byte(body: bytes) -> AsyncIterator[bytes]:
    for index in range(len(body)):
        yield body[index : index + 1]


def stream_response(body: bytes, *, one_byte_at_a_time: bool = False) -> httpx.Response:
    content = byte_by_byte(body) if one_byte_at_a_time else body
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=content)


def decode_sse(raw: bytes) -> list[SSEEvent]:
    decoder = SSEDecoder()
    return decoder.feed(raw) + decoder.close()


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        self.events.append(event)


class MemoryKeyring(KeyringBackend):
    """A keyring backend in a dict, standing in for the OS keychain in `serve` tests."""

    priority = 1  # type: ignore[assignment]  (keyring declares it as a class property)

    def __init__(self) -> None:
        super().__init__()
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if self.store.pop((service, username), None) is None:
            raise PasswordDeleteError(username)


@contextmanager
def keyring_backend(backend: KeyringBackend) -> Generator[KeyringBackend]:
    previous = keyring.get_keyring()
    keyring.set_keyring(backend)
    try:
        yield backend
    finally:
        keyring.set_keyring(previous)
