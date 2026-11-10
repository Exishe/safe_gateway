"""Canonical JSON and the digests of the audit chain (ADR 0021 points 4 to 6).

Canonical JSON here is RFC 8785 (JCS) restricted to objects, arrays, ASCII
strings, safe integers, booleans and null. For that subset Python's sorted,
compact, ASCII-only `json.dumps` produces exactly the JCS bytes, so any JCS
library in any language reproduces our hashes.
"""

import hashlib
import hmac
import json
from collections.abc import Mapping

GENESIS_HASH = "0" * 64
MAX_SAFE_INT = 2**53 - 1  # the largest integer every JSON parser reads exactly


def _scalar_problem(value: object) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return None if abs(value) <= MAX_SAFE_INT else f"integer {value} is outside ±(2**53 - 1)"
    if isinstance(value, str):
        return None if value.isascii() else "strings must be ASCII"
    return f"{type(value).__name__} is not allowed"


def _check(value: object) -> None:
    # One error type for every refusal, so callers catch a single thing.
    if isinstance(value, list):
        for item in value:
            _check(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                msg = f"canonical JSON: object keys must be strings, not {type(key).__name__}"
                raise ValueError(msg)  # noqa: TRY004
            _check(key)
            _check(item)
        return
    problem = _scalar_problem(value)
    if problem:
        msg = f"canonical JSON: {problem}"
        raise ValueError(msg)


def canonical_json(value: object) -> bytes:
    _check(value)
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return text.encode("ascii")


def row_hash(prev_hash: str, content: Mapping[str, object]) -> str:
    """sha256 of the previous row hash (64 ASCII hex characters) then the content."""
    return hashlib.sha256(prev_hash.encode("ascii") + canonical_json(dict(content))).hexdigest()


def tagged_hmac(key: bytes, tag: str, data: bytes) -> str:
    # One device key signs rows, the head and exports; the tag keeps those apart.
    message = f"safeguard-audit/{tag}/v1\n".encode("ascii") + data
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def row_hmac(key: bytes, digest: str) -> str:
    return tagged_hmac(key, "row", digest.encode("ascii"))


def head_hmac(key: bytes, head_id: int, head_hash: str) -> str:
    return tagged_hmac(key, "head", canonical_json({"head_hash": head_hash, "head_id": head_id}))
