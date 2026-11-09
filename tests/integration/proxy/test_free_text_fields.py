"""Security review M3, end to end: no unmasked free text in fields beside the messages."""

import json
from typing import Any

import httpx
from starlette.testclient import TestClient

from gateway.cloud.providers import Provider
from gateway.proxy.app import create_app
from tests.proxy_fakes import FakeUpstream, PersonalData, string_leaves

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
