"""The upstream providers the gateway can forward to, and the rules for their URLs (ADR 0020).

No network code here: the configuration reads this table too.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from urllib.parse import urlsplit


class Provider(StrEnum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    AZURE_OPENAI = "azure_openai"
    OLLAMA = "ollama"


class ApiShape(StrEnum):
    """The request and response format a provider speaks (ADR 0019: no translation)."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"


@dataclass(frozen=True)
class ProviderInfo:
    shape: ApiShape
    default_base_url: str | None
    needs_key: bool
    local: bool


PROVIDERS: Mapping[Provider, ProviderInfo] = MappingProxyType(
    {
        Provider.ANTHROPIC: ProviderInfo(
            ApiShape.ANTHROPIC, "https://api.anthropic.com", needs_key=True, local=False
        ),
        Provider.OPENAI: ProviderInfo(
            ApiShape.OPENAI, "https://api.openai.com/v1", needs_key=True, local=False
        ),
        # Every Azure resource has its own host, so there is no default.
        Provider.AZURE_OPENAI: ProviderInfo(ApiShape.OPENAI, None, needs_key=True, local=False),
        Provider.OLLAMA: ProviderInfo(
            ApiShape.OPENAI, "http://127.0.0.1:11434/v1", needs_key=False, local=True
        ),
    }
)

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

# A model id is forwarded as written and stored in the audit log, so it may not carry
# free text: `gpt-4o-mini`, `claude-x`, `qwen2.5:3b`, `hf.co/org/model:Q4_K_M` and Azure
# deployment names fit. No `@` or whitespace, so not an email or a sentence (review M1).
MODEL_NAME_MAX = 128
MODEL_NAME_CHARS = "letters, digits and ._:/- only, at most 128 characters"
MODEL_NAME = re.compile(rf"[A-Za-z0-9._:/-]{{1,{MODEL_NAME_MAX}}}")


def is_loopback_host(host: str) -> bool:
    return host.lower() in LOOPBACK_HOSTS


def check_base_url(url: str) -> str:
    """`url` without a trailing slash; ValueError unless it is safe to send a key to.

    https only, except plain http to this machine (a local Ollama). No user info, query
    or fragment, so a credential cannot ride along in the URL.
    """
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        msg = "base_url must start with https:// (or http:// for this machine)"
        raise ValueError(msg)
    host = parts.hostname or ""
    if not host:
        msg = "base_url has no host"
        raise ValueError(msg)
    if parts.username is not None or parts.password is not None:
        msg = "base_url must not hold a user name or password; keys live in the OS keychain"
        raise ValueError(msg)
    if parts.query:
        msg = "base_url must not have a query string"
        raise ValueError(msg)
    if parts.fragment:
        msg = "base_url must not have a fragment"
        raise ValueError(msg)
    if parts.scheme == "http" and not is_loopback_host(host):
        msg = "base_url must use https:// unless it points at this machine"
        raise ValueError(msg)
    _ = parts.port  # raises ValueError for a port that is not a number in range
    return url.rstrip("/")
