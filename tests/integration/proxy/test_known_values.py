"""A value detected in one message is masked in every message of the request.

The proxy masks a request's texts with `mask_messages` (ADR 0002 point 1, ADR 0014
amendment 1): every text is detected before any is masked, so a value found only
in message 2 is also replaced where message 1 holds it in a form detection misses.
"""

import json

import httpx
import pytest
from starlette.testclient import TestClient

from gateway.cloud.providers import Provider
from gateway.engine.detect import EngineConfig, detect
from gateway.proxy.app import create_app
from tests.proxy_fakes import FakeUpstream

SSN = "123-45-6780"  # pragma: allowlist secret
HIDDEN = f"Scan saved as ref{SSN}.pdf"  # glued to letters: not detected on its own
FOUND = f"Her SSN is {SSN}."
NO_NER = EngineConfig(ner="off", gliner="off")


def _answer(provider: Provider) -> httpx.Response:
    if provider is Provider.ANTHROPIC:
        return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]})
    message = {"role": "assistant", "content": "ok"}
    return httpx.Response(200, json={"choices": [{"index": 0, "message": message}]})


def test_the_hidden_form_is_missed_by_detection_alone() -> None:
    assert detect(HIDDEN, config=NO_NER) == []
    assert detect(FOUND, config=NO_NER) != []


@pytest.mark.parametrize("provider", [Provider.OPENAI, Provider.ANTHROPIC])
def test_a_value_found_in_message_2_is_masked_in_message_1(provider: Provider) -> None:
    fake = FakeUpstream(provider, lambda _: _answer(provider))
    app = create_app(fake.upstream(), engine_config=NO_NER, model="m")
    messages = [
        {"role": "user", "content": HIDDEN},
        {"role": "assistant", "content": "Noted."},
        {"role": "user", "content": FOUND},
    ]
    if provider is Provider.ANTHROPIC:
        route, body = "/v1/messages", {"max_tokens": 10, "messages": messages}
    else:
        route, body = "/v1/chat/completions", {"messages": messages}
    client = TestClient(app, base_url="http://127.0.0.1:8787")
    assert client.post(route, json=body).status_code == 200

    (request,) = fake.requests
    assert SSN.encode() not in request.content
    sent = json.loads(request.content)["messages"]
    first_user = next(m for m in sent if m["role"] == "user")
    text = first_user["content"]
    text = text if isinstance(text, str) else "".join(p["text"] for p in text)
    assert "[SSN_1]" in text
