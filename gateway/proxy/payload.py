"""Reading request bodies and choosing what goes upstream (ADR 0019)."""

import dataclasses
import json
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from gateway.cloud.providers import MODEL_NAME, MODEL_NAME_CHARS
from gateway.engine.detect import EngineConfig, detect
from gateway.engine.types import EntityType
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
    except ValueError:  # an integer past Python's digit limit, for one
        msg = "the body is not valid JSON"
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


# A forwarded parameter is a number, a flag or a short token: never free text, which
# would reach the upstream unmasked (ADR 0019). `Check` raises a 400 naming the field.
Check = Callable[[str, object], None]
_TOKEN = re.compile(r"[A-Za-z0-9_.:-]{1,64}")


def number(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        msg = f"{name!r} must be a number"
        raise invalid(msg)


def integer(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"{name!r} must be a whole number"
        raise invalid(msg)


def boolean(name: str, value: object) -> None:
    if not isinstance(value, bool):
        msg = f"{name!r} must be true or false"
        raise invalid(msg)


def token(name: str, value: object) -> None:
    if not isinstance(value, str) or not _TOKEN.fullmatch(value):
        msg = f"{name!r} must be a short word such as 'auto'"
        raise invalid(msg)


def flags_object(allowed: frozenset[str]) -> Check:
    """An object whose only keys are `allowed`, each true or false."""

    def check(name: str, value: object) -> None:
        if not isinstance(value, dict) or not set(value) <= allowed:
            msg = f"{name!r} may hold only {', '.join(sorted(allowed))}"
            raise invalid(msg)
        for key, flag in value.items():
            boolean(f"{name}.{key}", flag)

    return check


def check_fields(body: Mapping[str, object], checks: Mapping[str, Check]) -> None:
    for name, check in checks.items():
        if body.get(name) is not None:
            check(name, body[name])


# A calendar date alone is no identifier, and model snapshots carry one
# (`gpt-4o-2024-08-06`); every other kind the structured recognizers find is.
_HARMLESS_IN_TOKENS = frozenset({EntityType.DATE})


def refuse_identifiers(fields: Sequence[tuple[str, str]], config: EngineConfig) -> None:
    """A 400 if a short field, sent upstream as written, holds a structured identifier.

    A model id or a token such as `service_tier` passes its character check with an
    SSN-shaped number inside (security review 2, M-c). The structured recognizers and
    the gazetteer read each one; no model runs on a few characters.
    """
    structured = dataclasses.replace(config, ner="off", gliner="off")
    for name, value in fields:
        found = {
            span.entity_type.value
            for span in detect(value, config=structured)
            if span.entity_type not in _HARMLESS_IN_TOKENS
        }
        if found:
            msg = (
                f"{name!r} looks like it holds personal data ({', '.join(sorted(found))}); "
                "it would be sent upstream unmasked, so the request is refused"
            )
            raise invalid(msg)


def choose_model(configured: str | None, requested: object) -> str:
    """The configured model if there is one, otherwise the client's (ADR 0019)."""
    if configured is not None:
        return configured
    if not isinstance(requested, str) or not MODEL_NAME.fullmatch(requested):
        msg = (
            f"'model' is missing or not a model id ({MODEL_NAME_CHARS}), "
            "and the gateway has no model configured"
        )
        raise invalid(msg)
    return requested
