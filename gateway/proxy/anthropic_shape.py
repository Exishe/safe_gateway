"""The Anthropic Messages shape: what is forwarded, masked and restored (ADR 0019)."""

import json
import re
from collections.abc import Callable, Sequence
from typing import Any

from gateway.cloud.providers import ApiShape
from gateway.engine.mask import Mapping
from gateway.proxy.errors import invalid, unsupported
from gateway.proxy.payload import (
    PLACEHOLDER_INSTRUCTION,
    Check,
    check_fields,
    encode_json,
    integer,
    number,
    token,
)
from gateway.proxy.shape import (
    Body,
    Rehydrators,
    collect,
    rehydrate_message,
    replace,
    stream_key,
)
from gateway.proxy.sse import SSEEvent

_FORWARDED = frozenset(
    {
        "model",
        "messages",
        "system",
        "max_tokens",
        "stream",
        "temperature",
        "top_p",
        "top_k",
        "stop_sequences",
        "thinking",
        "service_tier",
    }
)
_DROPPED = frozenset({"metadata"})  # holds the end user's id
_TOOL_FIELDS = frozenset({"tools", "tool_choice", "mcp_servers", "container"})
_TOOL_BLOCKS = frozenset(
    {"tool_use", "tool_result", "server_tool_use", "web_search_tool_result", "mcp_tool_use"}
)
_MEDIA_BLOCKS = frozenset({"image", "document"})
_ROLES = frozenset({"user", "assistant"})
# Anthropic's documented cache control, and nothing else: any other key or value
# would be free text sent upstream unmasked (security review M3).
_CACHE_TTL = re.compile(r"[0-9]{1,4}[smh]")
CACHE_CONTROL_RULE = "{'type': 'ephemeral'} with an optional 'ttl' such as '5m' or '1h'"


def _cache_control(value: object) -> dict[str, str]:
    ttl = value.get("ttl", "5m") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or value.get("type") != "ephemeral"
        or not set(value) <= {"type", "ttl"}
        or not isinstance(ttl, str)
        or not _CACHE_TTL.fullmatch(ttl)
    ):
        msg = f"'cache_control' must be {CACHE_CONTROL_RULE}"
        raise invalid(msg)
    return dict(value)


def _thinking(name: str, value: object) -> None:
    if not isinstance(value, dict) or not set(value) <= {"type", "budget_tokens"}:
        msg = f"{name!r} may hold only type and budget_tokens"
        raise invalid(msg)
    if value.get("type") is not None:
        token(f"{name}.type", value["type"])
    if value.get("budget_tokens") is not None:
        integer(f"{name}.budget_tokens", value["budget_tokens"])


_CHECKS: dict[str, Check] = {
    "max_tokens": integer,
    "temperature": number,
    "top_p": number,
    "top_k": integer,
    "service_tier": token,
    "thinking": _thinking,
}

TOOLS_REFUSED = (
    "tool use is not supported by the gateway yet: tool inputs and results cannot be "
    "masked and restored safely (ADR 0019)"
)


def _block(block: object) -> Body:
    if not isinstance(block, dict):
        msg = "every content block must be an object"
        raise invalid(msg)
    kind = block.get("type")
    if kind in _MEDIA_BLOCKS:
        msg = (
            f"{kind!r} blocks are not supported: the gateway masks personal data in text "
            "only, so send text only"
        )
        raise unsupported(msg)
    if kind in _TOOL_BLOCKS:
        raise unsupported(TOOLS_REFUSED)
    if kind != "text":
        msg = f"content block type {kind!r} is not supported; send text blocks only"
        raise unsupported(msg)
    text = block.get("text")
    if not isinstance(text, str):
        msg = "a text block needs a string 'text'"
        raise invalid(msg)
    out: Body = {"type": "text", "text": text}
    for key, value in block.items():
        if key in {"type", "text"} or value is None:
            continue
        if key == "cache_control":
            out[key] = _cache_control(value)
            continue
        msg = f"text block field {key!r} is not forwarded by the gateway"
        raise unsupported(msg)
    return out


def _content(content: object, field: str) -> str | list[Body]:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        msg = f"{field!r} must be a string or a list of text blocks"
        raise invalid(msg)
    return [_block(block) for block in content]


def _message(message: object) -> Body:
    if not isinstance(message, dict):
        msg = "every message must be an object"
        raise invalid(msg)
    role = message.get("role")
    if role not in _ROLES:
        msg = "message role must be user or assistant"
        raise invalid(msg)
    for key, value in message.items():
        if key not in {"role", "content"} and value is not None:
            msg = f"message field {key!r} is not forwarded by the gateway"
            raise unsupported(msg)
    if message.get("content") is None:
        msg = "every message needs 'content'"
        raise invalid(msg)
    return {"role": role, "content": _content(message["content"], "content")}


def _rewrite_content(content: str | list[Body], fn: Callable[[str], str]) -> str | list[Body]:
    if isinstance(content, str):
        return fn(content)
    return [{**block, "text": fn(block["text"])} for block in content]


def _rewrite(body: Body, fn: Callable[[str], str]) -> Body:
    out = dict(body)
    if "system" in body:
        out["system"] = _rewrite_content(body["system"], fn)
    out["messages"] = [
        {**message, "content": _rewrite_content(message["content"], fn)}
        for message in body["messages"]
    ]
    if "stop_sequences" in body:
        out["stop_sequences"] = [fn(s) for s in body["stop_sequences"]]
    return out


def _text_holder(payload: Body) -> Body | None:
    """The object whose "text" is answer text: a text block's start, or a text delta."""
    kind = payload.get("type")
    if kind == "content_block_start":
        holder, wanted = payload.get("content_block"), "text"
    elif kind == "content_block_delta":
        holder, wanted = payload.get("delta"), "text_delta"
    else:
        return None
    if isinstance(holder, dict) and holder.get("type") == wanted:
        return holder if isinstance(holder.get("text"), str) else None
    return None


class AnthropicStreamRewriter:
    """Rehydrates text deltas per content block; flushes when a block stops.

    The blocks are one message, read in one link context (security review 2, I-2):
    a block is closed by its stop, or by text for another block, and stays closed.
    """

    def __init__(self, mapping: Mapping) -> None:
        self._rehydrators = Rehydrators(mapping, sequential=True)

    @property
    def unresolved(self) -> list[str]:
        return self._rehydrators.unresolved

    def on_event(self, event: SSEEvent) -> list[SSEEvent]:
        try:
            payload = json.loads(event.data)
        except ValueError:
            return [event]
        kind = payload.get("type") if isinstance(payload, dict) else None
        if kind == "message_stop":
            return [*self.finish(), event]
        index = stream_key(payload.get("index")) if isinstance(payload, dict) else None
        if index is None:
            return [event]  # not a block we can track: passed on as it came
        if kind == "content_block_stop":
            tail = self._rehydrators.close(index)
            return [*([self._delta(index, tail)] if tail else []), event]
        holder = _text_holder(payload)
        if holder is None:
            return [event]
        before = [
            self._delta(other, tail)
            for other, tail in self._rehydrators.close_others(index)
            if tail
        ]
        holder["text"] = self._rehydrators.feed(index, holder["text"])
        return [*before, self._encode(payload, event)]

    @staticmethod
    def _encode(payload: Body, event: SSEEvent) -> SSEEvent:
        return SSEEvent(encode_json(payload).decode(), event.event, event.id)

    @staticmethod
    def _delta(index: object, text: str) -> SSEEvent:
        payload = {
            "type": "content_block_delta",
            "index": index,
            "delta": {"type": "text_delta", "text": text},
        }
        return SSEEvent(encode_json(payload).decode(), event="content_block_delta")

    def finish(self) -> list[SSEEvent]:
        return [self._delta(index, tail) for index, tail in self._rehydrators.close_all() if tail]


class AnthropicShape:
    api_shape = ApiShape.ANTHROPIC

    def validate(self, body: Body) -> Body:
        out: Body = {}
        for name, value in body.items():
            if name in _TOOL_FIELDS:
                raise unsupported(TOOLS_REFUSED)
            if name in _DROPPED:
                continue
            if name not in _FORWARDED:
                msg = f"field {name!r} is not forwarded by the gateway"
                raise unsupported(msg)
            out[name] = value
        messages = out.get("messages")
        if not isinstance(messages, list) or not messages:
            msg = "'messages' must be a non-empty list"
            raise invalid(msg)
        out["messages"] = [_message(message) for message in messages]
        if "system" in out:
            out["system"] = _content(out["system"], "system")
        stops = out.get("stop_sequences", [])
        if not isinstance(stops, list) or not all(isinstance(s, str) for s in stops):
            msg = "'stop_sequences' must be a list of strings"
            raise invalid(msg)
        if "stream" in out and not isinstance(out["stream"], bool):
            msg = "'stream' must be true or false"
            raise invalid(msg)
        check_fields(out, _CHECKS)
        return out

    def collect_texts(self, body: Body) -> list[str]:
        return collect(_rewrite, body)

    def short_fields(self, body: Body) -> list[tuple[str, str]]:
        thinking = body.get("thinking")
        fields = [
            ("service_tier", body.get("service_tier")),
            ("thinking.type", thinking.get("type") if isinstance(thinking, dict) else None),
        ]
        return [(name, value) for name, value in fields if isinstance(value, str)]

    def with_texts(self, body: Body, texts: Sequence[str]) -> Body:
        return replace(_rewrite, body, texts)

    def with_instruction(self, body: Body) -> Body:
        system = body.get("system")
        if isinstance(system, list):
            instruction: object = [{"type": "text", "text": PLACEHOLDER_INSTRUCTION}, *system]
        elif isinstance(system, str):
            instruction = f"{PLACEHOLDER_INSTRUCTION}\n\n{system}"
        else:
            instruction = PLACEHOLDER_INSTRUCTION
        return {**body, "system": instruction}

    def rehydrate_reply(self, payload: Body, mapping: Mapping) -> list[str]:
        content = payload.get("content")
        blocks = [
            block
            for block in (content if isinstance(content, list) else [])
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        ]
        restored, unresolved = rehydrate_message([block["text"] for block in blocks], mapping)
        for block, text in zip(blocks, restored, strict=True):
            block["text"] = text
        return unresolved

    def stream_rewriter(self, mapping: Mapping) -> AnthropicStreamRewriter:
        return AnthropicStreamRewriter(mapping)

    def error_body(self, error_type: str, message: str) -> dict[str, Any]:
        return {"type": "error", "error": {"type": error_type, "message": message}}

    def stream_error(self, message: str) -> SSEEvent:
        body = self.error_body("api_error", message)
        return SSEEvent(encode_json(body).decode(), event="error")


ANTHROPIC = AnthropicShape()
