import json

import pytest

from gateway.proxy.errors import RequestRejected
from gateway.proxy.payload import (
    PLACEHOLDER_INSTRUCTION,
    choose_model,
    encode_json,
    parse_json_object,
)


def test_parses_an_object() -> None:
    assert parse_json_object(b'{"a": [1, "\\u0436"]}') == {"a": [1, "ж"]}


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (b"", "JSON"),
        (b"{", "JSON"),
        (b"[1, 2]", "object"),
        (b'"text"', "object"),
        (b'{"a": NaN}', "NaN"),
        (b'{"a": Infinity}', "Infinity"),
        (b'{"a": "\\ud800"}', "surrogate"),
        (b'{"\\udfff": 1}', "surrogate"),
        (b"\xff\xfe{}", "UTF-8"),
        (b"[" * 100_000 + b"]" * 100_000, "nested"),
    ],
)
def test_bad_bodies_are_400(body: bytes, reason: str) -> None:
    with pytest.raises(RequestRejected, match=reason) as error:
        parse_json_object(body)
    assert error.value.status == 400
    assert error.value.error_type == "invalid_request_error"


def test_error_message_never_echoes_the_body() -> None:
    with pytest.raises(RequestRejected) as error:
        parse_json_object(b'{"note": "secret-value", ')
    assert "secret-value" not in str(error.value)


def test_encode_json_keeps_non_ascii_and_is_compact() -> None:
    body = encode_json({"a": "Ереван", "b": [1]})
    assert body == '{"a":"Ереван","b":[1]}'.encode()
    assert json.loads(body) == {"a": "Ереван", "b": [1]}


def test_instruction_names_the_bracket_form() -> None:
    assert "[PERSON_1]" in PLACEHOLDER_INSTRUCTION
    assert "exactly" in PLACEHOLDER_INSTRUCTION


def test_configured_model_wins() -> None:
    assert choose_model("qwen2.5:3b", "gpt-4o") == "qwen2.5:3b"


@pytest.mark.parametrize("model", ["qwen2.5:3b", "gpt-4o-mini", "hf.co/org/model:Q4_K_M"])
def test_requested_model_passes_when_none_is_configured(model: str) -> None:
    assert choose_model(None, model) == model


@pytest.mark.parametrize("model", [None, "", "two words", 3, "x" * 201, "a\nb"])
def test_bad_or_missing_model_is_400(model: object) -> None:
    with pytest.raises(RequestRejected, match="model") as error:
        choose_model(None, model)
    assert error.value.status == 400
