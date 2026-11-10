"""Security review M3, end to end: no unmasked free text in fields beside the messages."""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from starlette.testclient import TestClient

from gateway.cloud.providers import Provider
from gateway.proxy.app import create_app
from tests.proxy_fakes import FakeUpstream, PersonalData, RecordingSink, string_leaves

DATA = PersonalData.generate(seed=23)


def _client(fake: FakeUpstream) -> TestClient:
    app = create_app(fake.upstream(), engine_config=DATA.engine_config(), model="test-model")
    return TestClient(app, base_url="http://127.0.0.1:8787")


def test_a_json_schema_property_name_is_masked_upstream_and_restored_in_the_answer() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        schema = json.loads(request.content)["response_format"]["json_schema"]["schema"]
        (name,) = schema["properties"]
        message = {"role": "assistant", "content": json.dumps({name: "yes"})}
        choice = {"index": 0, "message": message, "finish_reason": "stop"}
        return httpx.Response(
            200, json={"id": "c1", "object": "chat.completion", "choices": [choice]}
        )

    fake = FakeUpstream(Provider.OPENAI, respond)
    schema: dict[str, Any] = {
        "name": "answer",
        "schema": {
            "type": "object",
            "properties": {DATA.email: {"type": "string"}},
            "required": [DATA.email],
        },
    }
    body = {
        "messages": [{"role": "user", "content": "Fill it in."}],
        "response_format": {"type": "json_schema", "json_schema": schema},
    }
    response = _client(fake).post("/v1/chat/completions", json=body)

    assert response.status_code == 200
    assert not any(DATA.email in leaf for leaf in string_leaves(fake.sent))
    sent = fake.sent["response_format"]["json_schema"]["schema"]
    assert list(sent["properties"]) == sent["required"]  # masked alike: the schema holds
    answer = response.json()["choices"][0]["message"]["content"]
    assert json.loads(answer) == {DATA.email: "yes"}


def test_free_text_in_anthropic_cache_control_never_reaches_the_upstream() -> None:
    def never(_: httpx.Request) -> httpx.Response:
        msg = "a refused request must not be forwarded"
        raise AssertionError(msg)

    fake = FakeUpstream(Provider.ANTHROPIC, never)
    block = {"type": "text", "text": "hi", "cache_control": {"type": "ephemeral", "n": DATA.email}}
    body = {"max_tokens": 10, "messages": [{"role": "user", "content": [block]}]}
    response = _client(fake).post("/v1/messages", json=body)
    assert response.status_code == 400
    assert DATA.email not in response.text
    assert fake.requests == []


# ------------------------- security review 2, M-c: identifiers in model ids and short tokens


def _never(_: httpx.Request) -> httpx.Response:
    msg = "a refused request must not be forwarded"
    raise AssertionError(msg)


def _openai_body(**extra: object) -> dict[str, Any]:
    return {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}], **extra}


def _anthropic_body(**extra: object) -> dict[str, Any]:
    messages = [{"role": "user", "content": "hi"}]
    return {"model": "claude-x", "max_tokens": 10, "messages": messages, **extra}


@pytest.mark.parametrize("provider", [Provider.OPENAI, Provider.ANTHROPIC])
def test_the_bodies_here_pass_without_an_identifier(provider: Provider) -> None:
    # So a 400 below is the identifier's doing, not a missing or bad field.
    def respond(_: httpx.Request) -> httpx.Response:
        if provider is Provider.ANTHROPIC:
            return httpx.Response(200, json={"id": "m", "type": "message", "content": []})
        return httpx.Response(200, json={"id": "c1", "choices": []})

    fake = FakeUpstream(provider, respond)
    sink = RecordingSink()
    for field, (field_provider, body) in FIELDS.items():
        if field_provider is not provider:
            continue
        route = "/v1/messages" if provider is Provider.ANTHROPIC else "/v1/chat/completions"
        response = _unconfigured(fake, sink).post(route, json=body("0"))
        assert response.status_code == 200, (field, response.text)


SNEAKED = {
    "ssn": DATA.ssn,
    "reviewer's ssn": "078-05-1120",  # pragma: allowlist secret
    "card": DATA.card,
    "iban": DATA.iban,
}
FIELDS: dict[str, tuple[Provider, Callable[[str], dict[str, Any]]]] = {
    "openai model": (Provider.OPENAI, lambda v: _openai_body(model=f"gpt-4o-{v}")),
    "openai service_tier": (Provider.OPENAI, lambda v: _openai_body(service_tier=f"t-{v}")),
    "openai reasoning_effort": (
        Provider.OPENAI,
        lambda v: _openai_body(reasoning_effort=f"e-{v}"),
    ),
    "openai verbosity": (Provider.OPENAI, lambda v: _openai_body(verbosity=f"v-{v}")),
    "openai json_schema.name": (
        Provider.OPENAI,
        lambda v: _openai_body(
            response_format={"type": "json_schema", "json_schema": {"name": f"s-{v}"}}
        ),
    ),
    "anthropic model": (Provider.ANTHROPIC, lambda v: _anthropic_body(model=f"claude-{v}")),
    "anthropic service_tier": (
        Provider.ANTHROPIC,
        lambda v: _anthropic_body(service_tier=f"t-{v}"),
    ),
    "anthropic thinking.type": (
        Provider.ANTHROPIC,
        lambda v: _anthropic_body(thinking={"type": f"k-{v}", "budget_tokens": 1024}),
    ),
}


def _unconfigured(fake: FakeUpstream, sink: RecordingSink) -> TestClient:
    # No configured model, so the client's model id is the one forwarded and audited.
    app = create_app(fake.upstream(), engine_config=DATA.engine_config(), audit=sink)
    return TestClient(app, base_url="http://127.0.0.1:8787")


@pytest.mark.parametrize("value", list(SNEAKED))
@pytest.mark.parametrize("field", list(FIELDS))
def test_an_identifier_in_a_short_field_is_refused(field: str, value: str) -> None:
    provider, body = FIELDS[field]
    fake = FakeUpstream(provider, _never)
    sink = RecordingSink()
    route = "/v1/messages" if provider is Provider.ANTHROPIC else "/v1/chat/completions"
    response = _unconfigured(fake, sink).post(route, json=body(SNEAKED[value]))
    assert response.status_code == 400, response.text
    assert SNEAKED[value] not in response.text
    assert fake.requests == []
    (event,) = sink.events
    assert event.outcome == "rejected"
    assert event.model is None  # the audit log never holds an unchecked model id


@pytest.mark.parametrize(
    "model",
    ["gpt-4o-2024-08-06", "o3-mini-2025-01-31", "claude-3-5-sonnet-20241022", "qwen2.5:3b"],
)
def test_real_model_ids_pass_and_are_audited(model: str) -> None:
    def respond(_: httpx.Request) -> httpx.Response:
        choice = {"index": 0, "message": {"role": "assistant", "content": "ok"}}
        return httpx.Response(200, json={"id": "c1", "choices": [choice]})

    fake = FakeUpstream(Provider.OPENAI, respond)
    sink = RecordingSink()
    response = _unconfigured(fake, sink).post(
        "/v1/chat/completions", json=_openai_body(model=model)
    )
    assert response.status_code == 200
    assert fake.sent["model"] == model
    (event,) = sink.events
    assert event.model == model


def test_a_configured_model_id_is_checked_too() -> None:
    fake = FakeUpstream(Provider.OPENAI, _never)
    sink = RecordingSink()
    app = create_app(
        fake.upstream(), engine_config=DATA.engine_config(), model=f"m-{DATA.ssn}", audit=sink
    )
    client = TestClient(app, base_url="http://127.0.0.1:8787")
    response = client.post("/v1/chat/completions", json=_openai_body())
    assert response.status_code == 400
    assert DATA.ssn not in response.text
    assert fake.requests == []
    (event,) = sink.events
    assert event.model is None
