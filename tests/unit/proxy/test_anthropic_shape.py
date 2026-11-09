import json
from typing import Any

import pytest

from gateway.cloud.providers import ApiShape
from gateway.engine.mask import Mapping
from gateway.engine.types import EntityType
from gateway.proxy.anthropic_shape import ANTHROPIC
from gateway.proxy.errors import RequestRejected
from gateway.proxy.payload import PLACEHOLDER_INSTRUCTION
from gateway.proxy.sse import SSEEvent


def _mapping() -> Mapping:
    mapping = Mapping()
    mapping.placeholder_for("Alice Example", EntityType.PERSON)
    mapping.placeholder_for("alice@example.org", EntityType.EMAIL)
    return mapping


def _body(**extra: object) -> dict[str, Any]:
    return {
        "model": "m",
        "max_tokens": 10,
        "messages": [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": [{"type": "text", "text": "a1"}]},
            {"role": "user", "content": "u2"},
        ],
        **extra,
    }


def test_shape() -> None:
    assert ANTHROPIC.api_shape is ApiShape.ANTHROPIC


def test_texts_are_collected_in_order() -> None:
    body = ANTHROPIC.validate(_body(system="sys", stop_sequences=["s1"]))
    assert ANTHROPIC.collect_texts(body) == ["sys", "u1", "a1", "u2", "s1"]
    blocks = ANTHROPIC.validate(_body(system=[{"type": "text", "text": "b1"}]))
    assert ANTHROPIC.collect_texts(blocks) == ["b1", "u1", "a1", "u2"]


def test_with_texts_puts_them_back_in_place() -> None:
    body = ANTHROPIC.validate(_body(system="sys", stop_sequences=["s1"]))
    out = ANTHROPIC.with_texts(body, ["S", "A", "B", "C", "D"])
    assert out["system"] == "S"
    assert out["messages"][1]["content"] == [{"type": "text", "text": "B"}]
    assert out["stop_sequences"] == ["D"]
    assert body["system"] == "sys"


@pytest.mark.parametrize(
    ("system", "expected"),
    [
        (None, PLACEHOLDER_INSTRUCTION),
        ("sys", f"{PLACEHOLDER_INSTRUCTION}\n\nsys"),
        (
            [{"type": "text", "text": "b"}],
            [{"type": "text", "text": PLACEHOLDER_INSTRUCTION}, {"type": "text", "text": "b"}],
        ),
    ],
)
def test_instruction_goes_first_in_system(system: object, expected: object) -> None:
    raw = _body() if system is None else _body(system=system)
    assert ANTHROPIC.with_instruction(ANTHROPIC.validate(raw))["system"] == expected


def test_metadata_is_dropped_and_cache_control_kept() -> None:
    raw = _body(metadata={"user_id": "someone"}, temperature=0.1, stream=True)
    raw["messages"][1]["content"][0]["cache_control"] = {"type": "ephemeral"}
    raw["messages"][1]["content"][0]["citations"] = None
    body = ANTHROPIC.validate(raw)
    assert "metadata" not in body
    assert body["temperature"] == 0.1
    assert body["messages"][1]["content"][0] == {
        "type": "text",
        "text": "a1",
        "cache_control": {"type": "ephemeral"},
    }


def _rejected(raw: dict[str, Any]) -> RequestRejected:
    with pytest.raises(RequestRejected) as error:
        ANTHROPIC.validate(raw)
    return error.value


@pytest.mark.parametrize("block_type", ["image", "document"])
def test_media_blocks_are_refused_with_422(block_type: str) -> None:
    raw = _body()
    raw["messages"][0]["content"] = [{"type": block_type, "source": {}}]
    error = _rejected(raw)
    assert error.status == 422
    assert block_type in error.message
    assert "text only" in error.message


@pytest.mark.parametrize(
    "raw",
    [
        _body(tools=[{"name": "f", "input_schema": {}}]),
        _body(tool_choice={"type": "auto"}),
        {
            "model": "m",
            "messages": [
                {"role": "assistant", "content": [{"type": "tool_use", "id": "1", "input": {}}]}
            ],
        },
        {
            "model": "m",
            "messages": [
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1"}]}
            ],
        },
    ],
)
def test_tools_are_refused_with_422(raw: dict[str, Any]) -> None:
    error = _rejected(raw)
    assert error.status == 422
    assert "tool" in error.message


@pytest.mark.parametrize(
    ("raw", "status", "word"),
    [
        (_body(top_kk=1), 422, "top_kk"),
        ({"model": "m", "messages": []}, 400, "messages"),
        ({"model": "m", "messages": [{"role": "system", "content": "x"}]}, 400, "role"),
        ({"model": "m", "messages": [{"role": "user"}]}, 400, "content"),
        ({"model": "m", "messages": [{"role": "user", "content": None}]}, 400, "content"),
        (
            {"model": "m", "messages": [{"role": "user", "content": [{"type": "text"}]}]},
            400,
            "text",
        ),
        (
            {"model": "m", "messages": [{"role": "user", "content": [{"type": "thinking"}]}]},
            422,
            "thinking",
        ),
        (
            {
                "model": "m",
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": "x", "citations": [{"a": 1}]}],
                    }
                ],
            },
            422,
            "citations",
        ),
        ({"model": "m", "messages": [{"role": "user", "content": "x", "x": 1}]}, 422, "x"),
        (_body(system=5), 400, "system"),
        (_body(system=[{"type": "image"}]), 422, "image"),
        (_body(stop_sequences="s"), 400, "stop_sequences"),
        (_body(stream=1), 400, "stream"),
    ],
)
def test_invalid_requests(raw: dict[str, Any], status: int, word: str) -> None:
    error = _rejected(raw)
    assert error.status == status
    assert word in error.message


def test_reply_text_blocks_are_rehydrated() -> None:
    payload = {
        "type": "message",
        "content": [
            {"type": "text", "text": "Hi [PERSON_1], [EMAIL_3]"},
            {"type": "thinking", "thinking": "[PERSON_1]"},
        ],
    }
    unresolved = ANTHROPIC.rehydrate_reply(payload, _mapping())
    assert payload["content"][0]["text"] == "Hi Alice Example, [EMAIL_3]"
    assert payload["content"][1]["thinking"] == "[PERSON_1]"
    assert unresolved == ["[EMAIL_3]"]


def _event(name: str, **data: object) -> SSEEvent:
    return SSEEvent(json.dumps({"type": name, **data}), event=name)


def _delta(index: int, text: str) -> SSEEvent:
    return _event("content_block_delta", index=index, delta={"type": "text_delta", "text": text})


def _texts(events: list[SSEEvent]) -> dict[int, str]:
    out: dict[int, str] = {}
    for event in events:
        payload = json.loads(event.data)
        if payload["type"] == "content_block_delta" and payload["delta"]["type"] == "text_delta":
            out[payload["index"]] = out.get(payload["index"], "") + payload["delta"]["text"]
    return out


def test_stream_restores_a_placeholder_split_across_events() -> None:
    rewriter = ANTHROPIC.stream_rewriter(_mapping())
    events = rewriter.on_event(
        _event("content_block_start", index=0, content_block={"type": "text", "text": ""})
    )
    first = rewriter.on_event(_delta(0, "Dear [PER"))
    second = rewriter.on_event(_delta(0, "SON_1], hi"))
    assert _texts(first) == {0: "Dear "}
    assert _texts(second) == {0: "Alice Example, hi"}
    assert json.loads(events[0].data)["type"] == "content_block_start"


def test_flush_is_a_delta_just_before_block_stop() -> None:
    rewriter = ANTHROPIC.stream_rewriter(_mapping())
    events = rewriter.on_event(_delta(0, "mail EMAIL_1"))
    stop = rewriter.on_event(_event("content_block_stop", index=0))
    assert [e.event for e in stop] == ["content_block_delta", "content_block_stop"]
    assert _texts(events + stop) == {0: "mail alice@example.org"}


def test_two_blocks_are_kept_apart() -> None:
    rewriter = ANTHROPIC.stream_rewriter(_mapping())
    events = rewriter.on_event(_delta(0, "[PER"))
    events += rewriter.on_event(_delta(1, "[EMA"))
    events += rewriter.on_event(_delta(0, "SON_1]"))
    events += rewriter.on_event(_delta(1, "IL_1]"))
    events += rewriter.on_event(_event("message_stop"))
    assert _texts(events) == {0: "Alice Example", 1: "alice@example.org"}
    assert events[-1].event == "message_stop"


def test_other_events_pass_and_unresolved_is_reported() -> None:
    rewriter = ANTHROPIC.stream_rewriter(_mapping())
    ping = _event("ping")
    assert rewriter.on_event(ping) == [ping]
    thinking = _event(
        "content_block_delta", index=0, delta={"type": "thinking_delta", "thinking": "[PER"}
    )
    assert json.loads(rewriter.on_event(thinking)[0].data)["delta"]["thinking"] == "[PER"
    events = rewriter.on_event(_delta(1, "[PERSON_5]"))
    events += rewriter.finish()
    assert _texts(events) == {1: "[PERSON_5]"}
    assert rewriter.unresolved == ["[PERSON_5]"]
    odd = SSEEvent("not json")
    assert rewriter.on_event(odd) == [odd]


def test_error_shapes() -> None:
    assert ANTHROPIC.error_body("invalid_request_error", "bad") == {
        "type": "error",
        "error": {"type": "invalid_request_error", "message": "bad"},
    }
    event = ANTHROPIC.stream_error("upstream broke")
    assert event.event == "error"
    assert json.loads(event.data)["error"]["message"] == "upstream broke"


@pytest.mark.parametrize(
    ("extra", "word"),
    [
        ({"max_tokens": "ten"}, "max_tokens"),
        ({"top_k": 1.5}, "top_k"),
        ({"temperature": "Jane Roe"}, "temperature"),
        ({"service_tier": "call Jane Roe"}, "service_tier"),
        ({"thinking": {"type": "enabled", "budget_tokens": 1024, "note": "Jane"}}, "thinking"),
        ({"thinking": {"type": "enabled", "budget_tokens": "lots"}}, "thinking"),
        ({"thinking": "on"}, "thinking"),
    ],
)
def test_forwarded_parameters_are_type_checked(extra: dict[str, object], word: str) -> None:
    error = _rejected(_body(**extra))
    assert error.status == 400
    assert word in error.message


def test_valid_parameters_pass() -> None:
    raw = _body(
        top_k=5, top_p=0.9, service_tier="auto", thinking={"type": "enabled", "budget_tokens": 1024}
    )
    assert ANTHROPIC.validate(raw) == raw


def test_unhashable_block_index_from_upstream_passes_untouched() -> None:
    rewriter = ANTHROPIC.stream_rewriter(_mapping())
    event = _event("content_block_delta", index=[0], delta={"type": "text_delta", "text": "x"})
    assert rewriter.on_event(event) == [event]
    assert rewriter.on_event(_event("content_block_stop", index={"a": 1}))[-1].event == (
        "content_block_stop"
    )


# ------------------------------------------------------------------ cache_control (review M3)


def _with_cache_control(value: object, *, system: bool = False) -> dict[str, Any]:
    block = {"type": "text", "text": "x", "cache_control": value}
    if system:
        return _body(system=[block])
    raw = _body()
    raw["messages"][1]["content"] = [block]
    return raw


@pytest.mark.parametrize(
    "value",
    [{"type": "ephemeral"}, {"type": "ephemeral", "ttl": "5m"}, {"type": "ephemeral", "ttl": "1h"}],
)
@pytest.mark.parametrize("system", [False, True])
def test_documented_cache_control_passes(value: dict[str, str], *, system: bool) -> None:
    body = ANTHROPIC.validate(_with_cache_control(value, system=system))
    block = body["system"][0] if system else body["messages"][1]["content"][0]
    assert block["cache_control"] == value


@pytest.mark.parametrize(
    "value",
    [
        {"type": "ephemeral", "note": "Alice Example, alice@example.org"},  # free text upstream
        {"type": "Alice Example"},
        {"type": "ephemeral", "ttl": "five minutes"},
        {"type": "ephemeral", "ttl": "5d"},
        {"type": "ephemeral", "ttl": "٥m"},  # an Arabic-Indic five is not a digit here
        {"type": "ephemeral", "ttl": 5},
        {"ttl": "5m"},
        {},
        "ephemeral",
        ["ephemeral"],
    ],
)
@pytest.mark.parametrize("system", [False, True])
def test_any_other_cache_control_is_refused(value: object, *, system: bool) -> None:
    error = _rejected(_with_cache_control(value, system=system))
    assert error.status == 400
    assert "cache_control" in error.message
    assert "Alice" not in error.message
