import json
from typing import Any

import pytest

from gateway.cloud.providers import ApiShape
from gateway.engine.mask import Mapping
from gateway.engine.types import EntityType
from gateway.proxy.errors import RequestRejected
from gateway.proxy.openai_shape import OPENAI
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
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": [{"type": "text", "text": "u1"}]},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
        ],
        **extra,
    }


def test_shape() -> None:
    assert OPENAI.api_shape is ApiShape.OPENAI


def test_texts_are_collected_in_order() -> None:
    body = OPENAI.validate(_body(stop=["s1", "s2"]))
    assert OPENAI.collect_texts(body) == ["sys", "u1", "a1", "u2", "s1", "s2"]
    assert OPENAI.collect_texts(OPENAI.validate(_body(stop="s"))) == [
        "sys",
        "u1",
        "a1",
        "u2",
        "s",
    ]


def test_with_texts_puts_them_back_in_place() -> None:
    body = OPENAI.validate(_body(stop=["s1"]))
    out = OPENAI.with_texts(body, ["A", "B", "C", "D", "E"])
    assert [m["content"] for m in out["messages"]] == [
        "A",
        [{"type": "text", "text": "B"}],
        "C",
        "D",
    ]
    assert out["stop"] == ["E"]
    assert body["messages"][0]["content"] == "sys"  # the input is not changed


def test_with_texts_needs_exactly_one_text_per_slot() -> None:
    body = OPENAI.validate(_body())
    with pytest.raises(ValueError, match="texts"):
        OPENAI.with_texts(body, ["only one"])


def test_instruction_is_the_first_message() -> None:
    out = OPENAI.with_instruction(OPENAI.validate(_body()))
    assert out["messages"][0] == {"role": "system", "content": PLACEHOLDER_INSTRUCTION}
    assert len(out["messages"]) == 5


def test_end_user_identifiers_are_dropped() -> None:
    raw = _body(user="someone@example.org", metadata={"k": "v"}, store=True)
    raw["messages"][3]["name"] = "Alice"
    raw["messages"][2]["refusal"] = None
    raw["messages"][2]["tool_calls"] = []
    body = OPENAI.validate(raw)
    assert set(body) == {"model", "messages"}
    assert body["messages"][3] == {"role": "user", "content": "u2"}
    assert body["messages"][2] == {"role": "assistant", "content": "a1"}


def test_forwarded_parameters_are_kept() -> None:
    raw = _body(temperature=0.2, max_tokens=5, stream=True, stream_options={"include_usage": True})
    assert OPENAI.validate(raw) == raw


def _rejected(raw: dict[str, Any]) -> RequestRejected:
    with pytest.raises(RequestRejected) as error:
        OPENAI.validate(raw)
    return error.value


@pytest.mark.parametrize(
    "raw",
    [
        _body(tools=[{"type": "function", "function": {"name": "f"}}]),
        _body(functions=[{"name": "f"}]),
        _body(tool_choice="auto"),
        {"model": "m", "messages": [{"role": "tool", "tool_call_id": "1", "content": "r"}]},
        {
            "model": "m",
            "messages": [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{"id": "1", "function": {"name": "f", "arguments": "{}"}}],
                }
            ],
        },
    ],
)
def test_tools_are_refused_with_422(raw: dict[str, Any]) -> None:
    error = _rejected(raw)
    assert error.status == 422
    assert "tool" in error.message


@pytest.mark.parametrize("part_type", ["image_url", "input_audio", "file"])
def test_non_text_parts_are_refused_with_422(part_type: str) -> None:
    raw = {"model": "m", "messages": [{"role": "user", "content": [{"type": part_type}]}]}
    error = _rejected(raw)
    assert error.status == 422
    assert part_type in error.message


def test_image_message_explains_text_only() -> None:
    raw = {
        "model": "m",
        "messages": [
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:"}}]}
        ],
    }
    assert "text only" in _rejected(raw).message


@pytest.mark.parametrize(
    ("raw", "status", "word"),
    [
        (_body(logit_bias_typo=1), 422, "logit_bias_typo"),
        (_body(audio={"voice": "x"}), 422, "audio"),
        ({"model": "m"}, 400, "messages"),
        ({"model": "m", "messages": []}, 400, "messages"),
        ({"model": "m", "messages": "hi"}, 400, "messages"),
        ({"model": "m", "messages": ["hi"]}, 400, "message"),
        ({"model": "m", "messages": [{"role": "boss", "content": "x"}]}, 400, "role"),
        ({"model": "m", "messages": [{"role": "user", "content": 5}]}, 400, "content"),
        ({"model": "m", "messages": [{"role": "user", "content": [5]}]}, 400, "content"),
        (
            {"model": "m", "messages": [{"role": "user", "content": [{"type": "text"}]}]},
            400,
            "text",
        ),
        ({"model": "m", "messages": [{"role": "user", "content": None}]}, 400, "content"),
        ({"model": "m", "messages": [{"role": "user", "content": "x", "x": 1}]}, 422, "x"),
        (_body(stream="yes"), 400, "stream"),
        (_body(stop=5), 400, "stop"),
        (_body(stop=[1]), 400, "stop"),
    ],
)
def test_invalid_requests(raw: dict[str, Any], status: int, word: str) -> None:
    error = _rejected(raw)
    assert error.status == status
    assert word in error.message


def test_assistant_with_null_content_and_no_tools_has_no_text() -> None:
    raw = {"model": "m", "messages": [{"role": "assistant", "content": None}]}
    body = OPENAI.validate(raw)
    assert OPENAI.collect_texts(body) == []


def test_reply_is_rehydrated() -> None:
    payload = {
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": "Hi [PERSON_1] [PERSON_9]"}},
            {"index": 1, "message": {"role": "assistant", "content": "mail EMAIL_1"}},
            {"index": 2, "message": {"role": "assistant", "content": None}},
        ]
    }
    unresolved = OPENAI.rehydrate_reply(payload, _mapping())
    assert payload["choices"][0]["message"]["content"] == "Hi Alice Example [PERSON_9]"
    assert payload["choices"][1]["message"]["content"] == "mail alice@example.org"
    assert unresolved == ["[PERSON_9]"]


def _chunk(index: int, content: str | None, finish: str | None = None) -> SSEEvent:
    delta = {} if content is None else {"content": content}
    payload = {
        "id": "c1",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "m",
        "choices": [{"index": index, "delta": delta, "finish_reason": finish}],
    }
    return SSEEvent(json.dumps(payload))


def _contents(events: list[SSEEvent]) -> dict[int, str]:
    out: dict[int, str] = {}
    for event in events:
        if event.data == "[DONE]":
            continue
        for choice in json.loads(event.data)["choices"]:
            out[choice["index"]] = out.get(choice["index"], "") + choice["delta"].get("content", "")
    return out


def test_stream_restores_a_placeholder_split_across_events() -> None:
    rewriter = OPENAI.stream_rewriter(_mapping())
    first = rewriter.on_event(_chunk(0, "Dear [PER"))
    second = rewriter.on_event(_chunk(0, "SON_1], hi"))
    assert _contents(first) == {0: "Dear "}
    assert _contents(second) == {0: "Alice Example, hi"}


def test_stream_flush_comes_before_the_finish_chunk() -> None:
    rewriter = OPENAI.stream_rewriter(_mapping())
    events = rewriter.on_event(_chunk(0, "see EMAIL_1"))
    events += rewriter.on_event(_chunk(0, None, "stop"))
    events += rewriter.on_event(SSEEvent("[DONE]"))
    assert _contents(events) == {0: "see alice@example.org"}
    finish = [json.loads(e.data) for e in events if e.data != "[DONE]"][-1]
    assert finish["choices"][0]["finish_reason"] == "stop"
    assert finish["choices"][0]["delta"] == {}
    flush = [json.loads(e.data) for e in events if e.data != "[DONE]"][-2]
    assert flush["choices"][0]["delta"] == {"content": "alice@example.org"}
    assert flush["id"] == "c1"
    assert events[-1].data == "[DONE]"


def test_finish_chunk_with_content_keeps_the_order() -> None:
    rewriter = OPENAI.stream_rewriter(_mapping())
    events = rewriter.on_event(_chunk(0, "to [PER"))
    events += rewriter.on_event(_chunk(0, "SON_1] bye", "stop"))
    assert _contents(events) == {0: "to Alice Example bye"}


def test_two_choices_are_kept_apart() -> None:
    rewriter = OPENAI.stream_rewriter(_mapping())
    events = rewriter.on_event(_chunk(0, "[PER"))
    events += rewriter.on_event(_chunk(1, "[EMA"))
    events += rewriter.on_event(_chunk(0, "SON_1]"))
    events += rewriter.on_event(_chunk(1, "IL_1]"))
    events += rewriter.on_event(SSEEvent("[DONE]"))
    assert _contents(events) == {0: "Alice Example", 1: "alice@example.org"}


def test_done_flushes_and_unresolved_is_reported() -> None:
    rewriter = OPENAI.stream_rewriter(_mapping())
    events = rewriter.on_event(_chunk(0, "x [PERSON_7] PERSON_1"))
    events += rewriter.on_event(SSEEvent("[DONE]"))
    assert _contents(events) == {0: "x [PERSON_7] Alice Example"}
    assert rewriter.unresolved == ["[PERSON_7]"]


def test_finish_without_done_flushes_the_rest() -> None:
    rewriter = OPENAI.stream_rewriter(_mapping())
    events = rewriter.on_event(_chunk(0, "PERSON_1"))
    events += rewriter.finish()
    assert _contents(events) == {0: "Alice Example"}


def test_other_events_pass_through() -> None:
    rewriter = OPENAI.stream_rewriter(_mapping())
    usage = SSEEvent(json.dumps({"id": "c1", "choices": [], "usage": {"total_tokens": 3}}))
    assert rewriter.on_event(usage)[0].data == usage.data.replace(" ", "")
    odd = SSEEvent("not json")
    assert rewriter.on_event(odd) == [odd]
    error = SSEEvent(json.dumps({"error": {"message": "x"}}))
    assert json.loads(rewriter.on_event(error)[0].data) == {"error": {"message": "x"}}


def test_error_shapes() -> None:
    assert OPENAI.error_body("invalid_request_error", "bad") == {
        "error": {"message": "bad", "type": "invalid_request_error", "param": None, "code": None}
    }
    event = OPENAI.stream_error("upstream broke")
    assert json.loads(event.data)["error"]["message"] == "upstream broke"
