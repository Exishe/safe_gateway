"""Reading request bodies and choosing what goes upstream (ADR 0019)."""

import json
from typing import Any

from gateway.cloud.providers import MODEL_NAME
from gateway.proxy.errors import RequestRejected, invalid

PLACEHOLDER_INSTRUCTION = (
    "Some values in this conversation were replaced with placeholders such as [PERSON_1] "
    "or [EMAIL_2]. Whenever you refer to one of them, write the placeholder exactly as it "
    "appears, square brackets included. Do not translate, reformat or explain the "
    "placeholders, and do not guess the values behind them."
)


def _no_constant(name: str) -> object:
    msg = f"{name} is not valid JSON"
    raise invalid(msg)


def encode_json(value: object) -> bytes:
    """Compact UTF-8 JSON: the exact bytes that are sent and hashed."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def parse_json_object(body: bytes) -> dict[str, Any]:
    """A JSON object, or a 400 that says why without repeating the body."""
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        msg = "the body is not UTF-8 JSON"
        raise invalid(msg) from None
    try:
        data = json.loads(text, parse_constant=_no_constant)
    except RequestRejected:
        raise
    except RecursionError:
        msg = "the JSON body is nested too deeply"
        raise invalid(msg) from None
    except json.JSONDecodeError as error:
        msg = f"the body is not valid JSON (line {error.lineno}, column {error.colno})"
        raise invalid(msg) from None
    if not isinstance(data, dict):
        msg = "the body must be a JSON object"
        raise invalid(msg)
    try:
        encode_json(data)
    except UnicodeEncodeError:
        msg = "the body holds a lone surrogate (\\ud800-\\udfff), which is not text"
        raise invalid(msg) from None
    return data


def choose_model(configured: str | None, requested: object) -> str:
    """The configured model if there is one, otherwise the client's (ADR 0019)."""
    if configured is not None:
        return configured
    if not isinstance(requested, str) or not MODEL_NAME.fullmatch(requested):
        msg = (
            "'model' is missing or not a model id (letters, digits and ._:/@+- only), "
            "and the gateway has no model configured"
        )
        raise invalid(msg)
    return requested
