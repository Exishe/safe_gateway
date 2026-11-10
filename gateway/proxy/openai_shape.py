"""The OpenAI Chat Completions shape: what is forwarded, masked and restored (ADR 0019)."""

import json
from collections.abc import Callable, Sequence
from typing import Any

from gateway.cloud.providers import ApiShape
from gateway.engine.mask import Mapping
from gateway.proxy.errors import invalid, unsupported
from gateway.proxy.payload import (
    PLACEHOLDER_INSTRUCTION,
    Check,
    boolean,
    check_fields,
    encode_json,
    flags_object,
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
    rewrite_values,
    stream_key,
)
from gateway.proxy.sse import SSEEvent

# Parameters that carry no text of the user's, or whose text is masked (stop).
_FORWARDED = frozenset(
    {
        "model",
        "messages",
        "stream",
        "stream_options",
        "temperature",
        "top_p",
        "n",
        "max_tokens",
        "max_completion_tokens",
        "presence_penalty",
        "frequency_penalty",
        "logit_bias",
        "logprobs",
        "top_logprobs",
        "seed",
        "stop",
        "response_format",
        "reasoning_effort",
        "service_tier",
        "verbosity",
    }
)
# They identify the end user and do not change the answer, so they are not sent.
_DROPPED = frozenset({"user", "safety_identifier", "metadata", "store", "prompt_cache_key"})
_TOOL_FIELDS = frozenset(
    {"tools", "tool_choice", "functions", "function_call", "parallel_tool_calls"}
)
_ROLES = frozenset({"system", "developer", "user", "assistant"})
_TOOL_ROLES = frozenset({"tool", "function"})
_TOOL_MESSAGE_KEYS = frozenset({"tool_calls", "function_call", "tool_call_id"})

TOOLS_REFUSED = (
    "tool and function calls are not supported by the gateway yet: their arguments "
    "cannot be masked and restored safely (ADR 0019)"
)
MEDIA_REFUSED = (
    "{kind} ({part!r} parts) are not supported: the gateway masks personal data in text "
    "only, so send text only"
)
_MEDIA_PARTS = {"image_url": "images", "input_audio": "audio inputs", "file": "files"}


def _content(content: object, role: str) -> object:
    if isinstance(content, str):
        return content
    if content is None and role == "assistant":
        return None
    if not isinstance(content, list):
        msg = "message 'content' must be a string or a list of text parts"
        raise invalid(msg)
    return [_part(part) for part in content]


def _part(part: object) -> dict[str, str]:
    if not isinstance(part, dict):
        msg = "every 'content' part must be an object"
        raise invalid(msg)
    kind = part.get("type")
    if kind in _MEDIA_PARTS:
        raise unsupported(MEDIA_REFUSED.format(kind=_MEDIA_PARTS[kind], part=kind))
    if kind != "text":
        msg = f"content part type {kind!r} is not supported; send text parts only"
        raise unsupported(msg)
    text = part.get("text")
    if not isinstance(text, str):
        msg = "a text part needs a string 'text'"
        raise invalid(msg)
    return {"type": "text", "text": text}


def _message(message: object) -> Body:
    if not isinstance(message, dict):
        msg = "every message must be an object"
        raise invalid(msg)
    role = message.get("role")
    if role in _TOOL_ROLES:
        raise unsupported(TOOLS_REFUSED)
    if role not in _ROLES:
        msg = f"message role must be one of {', '.join(sorted(_ROLES))}"
        raise invalid(msg)
    out: Body = {"role": role}
    for key, value in message.items():
        if key in {"role", "content", "name"} or value in (None, "", [], {}):
            continue  # a participant's name identifies someone and is not needed
        if key in _TOOL_MESSAGE_KEYS:
            raise unsupported(TOOLS_REFUSED)
        msg = f"message field {key!r} is not forwarded by the gateway"
        raise unsupported(msg)
    if "content" not in message and role != "assistant":
        msg = "every message needs 'content'"
        raise invalid(msg)
    out["content"] = _content(message.get("content"), role)
    return out


def _logit_bias(name: str, value: object) -> None:
    if not isinstance(value, dict) or not all(isinstance(k, str) and k.isdigit() for k in value):
        msg = f"{name!r} must map token ids to numbers"
        raise invalid(msg)
    for bias in value.values():
        number(name, bias)


def _json_schema(name: str, value: object) -> None:
    if not isinstance(value, dict) or not set(value) <= _JSON_SCHEMA_FIELDS:
        msg = f"{name!r} may hold only {', '.join(sorted(_JSON_SCHEMA_FIELDS))}"
        raise invalid(msg)
    check_fields(value, {"name": token, "strict": boolean})
    if not isinstance(value.get("schema", {}), dict):
        msg = f"{name}.schema must be an object"
        raise invalid(msg)


def _response_format(name: str, value: object) -> None:
    if (
        not isinstance(value, dict)
        or value.get("type") not in _RESPONSE_FORMATS
        or not set(value) <= {"type", "json_schema"}
    ):
        msg = f"{name!r} must be {{'type': one of {', '.join(sorted(_RESPONSE_FORMATS))}}}"
        raise invalid(msg)
    if value["type"] == "json_schema":
        _json_schema("json_schema", value.get("json_schema"))


_RESPONSE_FORMATS = frozenset({"text", "json_object", "json_schema"})
_JSON_SCHEMA_FIELDS = frozenset({"name", "description", "schema", "strict"})
_CHECKS: dict[str, Check] = {
    "temperature": number,
    "top_p": number,
    "presence_penalty": number,
    "frequency_penalty": number,
    "n": integer,
    "max_tokens": integer,
    "max_completion_tokens": integer,
    "top_logprobs": integer,
    "seed": integer,
    "logprobs": boolean,
    "reasoning_effort": token,
    "service_tier": token,
    "verbosity": token,
    "stream_options": flags_object(frozenset({"include_usage", "include_obfuscation"})),
    "logit_bias": _logit_bias,
    "response_format": _response_format,
}


def _stop(stop: object) -> object:
    if stop is None or isinstance(stop, str):
        return stop
    if isinstance(stop, list) and all(isinstance(s, str) for s in stop):
        return stop
    msg = "'stop' must be a string or a list of strings"
    raise invalid(msg)


def _rewrite(body: Body, fn: Callable[[str], str]) -> Body:
    out = dict(body)
    out["messages"] = [_rewrite_message(message, fn) for message in body["messages"]]
    stop = body.get("stop")
    if isinstance(stop, str):
        out["stop"] = fn(stop)
    elif isinstance(stop, list):
        out["stop"] = [fn(s) for s in stop]
    response_format = body.get("response_format")
    if isinstance(response_format, dict) and isinstance(response_format.get("json_schema"), dict):
        # Descriptions, enums, examples and property names in a schema can hold
        # personal data (review M3); its name is a short token and is kept.
        schema = response_format["json_schema"]
        rewritten = {
            key: value if key in {"name", "strict"} else rewrite_values(value, fn)
            for key, value in schema.items()
        }
        out["response_format"] = {**response_format, "json_schema": rewritten}
    return out


def _rewrite_message(message: Body, fn: Callable[[str], str]) -> Body:
    content = message.get("content")
    if isinstance(content, str):
        return {**message, "content": fn(content)}
    if isinstance(content, list):
        return {**message, "content": [{**part, "text": fn(part["text"])} for part in content]}
    return dict(message)


class OpenAIStreamRewriter:
    """Rehydrates `delta.content` per choice; flushes when a choice finishes.

    The choices are read in one link context as well as each in its own (security
    review 2, I-2); content for a choice after its finish_reason passes as written.
    """

    def __init__(self, mapping: Mapping) -> None:
        self._rehydrators = Rehydrators(mapping)
        self._template: Body = {}

    @property
    def unresolved(self) -> list[str]:
        return self._rehydrators.unresolved

    def on_event(self, event: SSEEvent) -> list[SSEEvent]:
        if event.data.strip() == "[DONE]":
            return [*self.finish(), event]
        try:
            payload = json.loads(event.data)
        except ValueError:
            return [event]
        if not isinstance(payload, dict) or not isinstance(payload.get("choices"), list):
            return [event]
        self._template = {
            key: payload[key]
            for key in ("id", "object", "created", "model", "system_fingerprint")
            if key in payload
        }
        before = [
            self._chunk(index, tail)
            for index, tail in (self._choice(c) for c in payload["choices"])
            if tail
        ]
        return [*before, SSEEvent(encode_json(payload).decode(), event.event, event.id)]

    def _choice(self, choice: object) -> tuple[int, str]:
        """Rehydrate one choice in place; the text that must go out before it, if any."""
        index = stream_key(choice.get("index", 0)) if isinstance(choice, dict) else None
        if not isinstance(choice, dict) or index is None:
            return 0, ""  # not a choice we can track: passed on as it came
        delta = choice.get("delta")
        has_content = isinstance(delta, dict) and isinstance(delta.get("content"), str)
        text = self._rehydrators.feed(index, delta["content"]) if has_content else ""
        tail = ""
        if choice.get("finish_reason") is not None:
            tail = self._rehydrators.close(index)
        if has_content:
            delta["content"] = text + tail  # the held tail follows this chunk's own text
            return index, ""
        return index, tail

    def _chunk(self, index: object, text: str) -> SSEEvent:
        payload = {
            **self._template,
            "choices": [{"index": index, "delta": {"content": text}, "finish_reason": None}],
        }
        return SSEEvent(encode_json(payload).decode())

    def finish(self) -> list[SSEEvent]:
        return [self._chunk(index, tail) for index, tail in self._rehydrators.close_all() if tail]


class OpenAIShape:
    api_shape = ApiShape.OPENAI

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
        if "stream" in out and not isinstance(out["stream"], bool):
            msg = "'stream' must be true or false"
            raise invalid(msg)
        if "stop" in out:
            out["stop"] = _stop(out["stop"])
        check_fields(out, _CHECKS)
        return out

    def collect_texts(self, body: Body) -> list[str]:
        return collect(_rewrite, body)

    def short_fields(self, body: Body) -> list[tuple[str, str]]:
        response_format = body.get("response_format")
        schema = response_format.get("json_schema") if isinstance(response_format, dict) else None
        fields = [
            (name, body.get(name)) for name in ("reasoning_effort", "service_tier", "verbosity")
        ]
        fields.append(
            ("json_schema.name", schema.get("name") if isinstance(schema, dict) else None)
        )
        return [(name, value) for name, value in fields if isinstance(value, str)]

    def with_texts(self, body: Body, texts: Sequence[str]) -> Body:
        return replace(_rewrite, body, texts)

    def with_instruction(self, body: Body) -> Body:
        instruction = {"role": "system", "content": PLACEHOLDER_INSTRUCTION}
        return {**body, "messages": [instruction, *body["messages"]]}

    def rehydrate_reply(self, payload: Body, mapping: Mapping) -> list[str]:
        choices = payload.get("choices")
        messages = [
            message
            for message in (
                choice.get("message") if isinstance(choice, dict) else None
                for choice in (choices if isinstance(choices, list) else [])
            )
            if isinstance(message, dict) and isinstance(message.get("content"), str)
        ]
        restored, unresolved = rehydrate_message([m["content"] for m in messages], mapping)
        for message, text in zip(messages, restored, strict=True):
            message["content"] = text
        return unresolved

    def stream_rewriter(self, mapping: Mapping) -> OpenAIStreamRewriter:
        return OpenAIStreamRewriter(mapping)

    def error_body(self, error_type: str, message: str) -> dict[str, Any]:
        return {"error": {"message": message, "type": error_type, "param": None, "code": None}}

    def stream_error(self, message: str) -> SSEEvent:
        return SSEEvent(encode_json(self.error_body("upstream_error", message)).decode())


OPENAI = OpenAIShape()
