import hashlib
import hmac

import pytest

from gateway.audit.chain import (
    GENESIS_HASH,
    MAX_SAFE_INT,
    canonical_json,
    head_hmac,
    row_hash,
    row_hmac,
    tagged_hmac,
)

KEY = b"k" * 64


def test_canonical_json_sorts_keys_and_drops_whitespace() -> None:
    value = {"b": 1, "a": [True, None, "x"], "c": {"z": 0, "y": -2}}
    assert canonical_json(value) == b'{"a":[true,null,"x"],"b":1,"c":{"y":-2,"z":0}}'


def test_canonical_json_escapes_quotes_and_backslashes() -> None:
    assert canonical_json({"k": 'a"b\\c'}) == b'{"k":"a\\"b\\\\c"}'


@pytest.mark.parametrize(
    "value",
    [1.5, float("nan"), b"bytes", "café", MAX_SAFE_INT + 1, -MAX_SAFE_INT - 1, {1: 2}, {"k": (1,)}],
)
def test_canonical_json_refuses_values_outside_the_subset(value: object) -> None:
    with pytest.raises(ValueError, match="canonical JSON"):
        canonical_json(value)


def test_canonical_json_accepts_the_largest_safe_integers() -> None:
    assert canonical_json([MAX_SAFE_INT, -MAX_SAFE_INT]) == b"[9007199254740991,-9007199254740991]"


def test_row_hash_is_sha256_of_prev_hex_then_canonical_content() -> None:
    expected = hashlib.sha256(b"0" * 64 + b'{"id":1,"kind":"request"}').hexdigest()
    assert GENESIS_HASH == "0" * 64
    assert row_hash(GENESIS_HASH, {"kind": "request", "id": 1}) == expected


def test_row_hash_depends_on_prev_hash() -> None:
    assert row_hash("0" * 64, {"id": 1}) != row_hash("1" * 64, {"id": 1})


def test_row_hmac_is_tagged_hmac_sha256_of_the_row_hash() -> None:
    digest = "ab" * 32
    expected = hmac.new(KEY, b"safeguard-audit/row/v1\n" + digest.encode(), "sha256").hexdigest()
    assert row_hmac(KEY, digest) == expected


def test_head_hmac_covers_id_and_hash() -> None:
    digest = "cd" * 32
    message = b'safeguard-audit/head/v1\n{"head_hash":"' + digest.encode() + b'","head_id":7}'
    assert head_hmac(KEY, 7, digest) == hmac.new(KEY, message, "sha256").hexdigest()
    assert head_hmac(KEY, 8, digest) != head_hmac(KEY, 7, digest)


def test_tags_separate_the_uses_of_one_key() -> None:
    data = b"same input"
    assert tagged_hmac(KEY, "row", data) != tagged_hmac(KEY, "export", data)
    assert tagged_hmac(KEY, "row", data) != tagged_hmac(b"other-key", "row", data)
