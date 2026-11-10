"""`config.toml`: which upstream to use and how the gateway behaves. Never keys (ADR 0020)."""

import os
import re
import tomllib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from gateway.cloud.providers import (
    MODEL_NAME,
    MODEL_NAME_CHARS,
    PROVIDERS,
    Provider,
    check_base_url,
)
from gateway.engine.types import EntityType

DEFAULT_MAX_BODY_BYTES = 4 * 1024 * 1024
# Audit purges below this need `--force` (security review M10); config refuses them.
MIN_RETENTION_DAYS = 7
DEFAULT_AZURE_API_VERSION = "2024-10-21"

_TOP_FIELDS = frozenset(
    {
        "provider",
        "base_url",
        "model",
        "api_version",
        "gazetteer",
        "retention_days",
        "thresholds",
        "proxy",
    }
)
_PROXY_FIELDS = frozenset({"max_body_bytes", "cors_allow_origins", "allow_dev_ui", "extension_ids"})
_KEY_LIKE_NAME = re.compile(r"key|token|secret|passw|credential|auth|bearer", re.IGNORECASE)
_KEY_LIKE_VALUE = re.compile(r"\s*sk-", re.IGNORECASE)
_API_VERSION = re.compile(r"\d{4}-\d{2}-\d{2}(-preview)?")
# scheme://host[:port], no path, no wildcard: "chrome-extension://<id>", "http://127.0.0.1:5173"
_ORIGIN = re.compile(r"[a-z][a-z0-9+.-]*://[A-Za-z0-9.-]+(:\d{1,5})?")
# The browser extension (ADR 0026). Chrome, Edge and Brave derive a 32-letter id from
# the extension's key, in the letters a to p; Firefox gives each install an internal
# UUID, shown in lower case in about:debugging. Either names exactly one origin.
_CHROME_EXTENSION_ID = re.compile(r"[a-p]{32}")
_FIREFOX_INTERNAL_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_EXTENSION_IDS_HINT = (
    "'extension_ids' must be a list of extension ids: the 32 letters shown in "
    "chrome://extensions (Chrome, Edge, Brave) or the Internal UUID shown in "
    "about:debugging (Firefox)"
)
_KEYCHAIN_HINT = (
    "API keys live in the OS keychain, never in config.toml: "
    "uv run keyring set safeguard-gateway api-key:<provider>"
)


class ConfigError(ValueError):
    """config.toml cannot be used. Messages name fields, never their values."""


@dataclass(frozen=True)
class ProxyConfig:
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES
    # Origins allowed to call the proxy from a browser; empty means none (ADR 0019).
    cors_allow_origins: tuple[str, ...] = ()
    # Also allow the Vite dev server's origins, for working on apps/web (ADR 0024).
    allow_dev_ui: bool = False
    # The browser extension's ids; each allows its own origin only (ADR 0026).
    extension_ids: tuple[str, ...] = ()


def extension_origins(ids: tuple[str, ...]) -> tuple[str, ...]:
    """The origin a browser gives each extension id: Chrome-family or Firefox."""
    return tuple(
        f"chrome-extension://{i}" if _CHROME_EXTENSION_ID.fullmatch(i) else f"moz-extension://{i}"
        for i in ids
    )


@dataclass(frozen=True)
class GatewayConfig:
    provider: Provider = Provider.OLLAMA
    base_url: str = "http://127.0.0.1:11434/v1"
    model: str | None = None
    api_version: str | None = None
    gazetteer: Path | None = None
    # Audit rows older than this are purged; None keeps every row (ADR 0021 point 12).
    retention_days: int | None = None
    thresholds: Mapping[EntityType, float] = field(default_factory=dict)
    proxy: ProxyConfig = field(default_factory=ProxyConfig)


def safeguard_home() -> Path:
    home = os.environ.get("SAFEGUARD_HOME")
    return Path(home) if home else Path.home() / ".safeguard"


def config_path() -> Path:
    return safeguard_home() / "config.toml"


def _walk(data: object, prefix: str) -> Iterator[tuple[str, object]]:
    """(dotted name, value) for every field and list item, depth first."""
    if isinstance(data, Mapping):
        for name, value in data.items():
            path = f"{prefix}{name}"
            yield path, value
            if path != "thresholds":  # entity type names such as URL_WITH_TOKEN
                yield from _walk(value, f"{path}.")
    elif isinstance(data, list):
        for index, value in enumerate(data):
            yield f"{prefix[:-1]}[{index}]", value
            yield from _walk(value, prefix)


def _refuse_keys(data: Mapping[str, object]) -> None:
    for path, value in _walk(data, ""):
        name = path.rsplit(".", 1)[-1]
        if _KEY_LIKE_NAME.search(name):
            msg = f"field {path!r} looks like a credential. {_KEYCHAIN_HINT}"
            raise ConfigError(msg)
        if isinstance(value, str) and _KEY_LIKE_VALUE.match(value):
            msg = f"the value of {path!r} looks like an API key. {_KEYCHAIN_HINT}"
            raise ConfigError(msg)


def _table(data: Mapping[str, object], name: str) -> Mapping[str, object]:
    value = data.get(name, {})
    if not isinstance(value, Mapping):
        msg = f"{name!r} must be a table"
        raise ConfigError(msg)
    return value


def _refuse_unknown(data: Mapping[str, object], allowed: frozenset[str], where: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        msg = f"unknown field {where}{unknown[0]!r}; expected one of {', '.join(sorted(allowed))}"
        raise ConfigError(msg)


def _string(data: Mapping[str, object], name: str) -> str | None:
    value = data.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        msg = f"{name!r} must be a non-empty string"
        raise ConfigError(msg)
    return value


def _positive_int(data: Mapping[str, object], name: str, default: int) -> int:
    value = data.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        msg = f"{name!r} must be a whole number of at least 1"
        raise ConfigError(msg)
    return value


def _retention_days(data: Mapping[str, object]) -> int | None:
    value = data.get("retention_days")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < MIN_RETENTION_DAYS:
        # No override here: a shorter period is a one-off `audit purge --force`.
        msg = f"'retention_days' must be a whole number of at least {MIN_RETENTION_DAYS}"
        raise ConfigError(msg)
    return value


def _provider(data: Mapping[str, object]) -> Provider:
    name = data.get("provider", Provider.OLLAMA.value)
    try:
        return Provider(name)
    except ValueError:
        msg = f"'provider' must be one of {', '.join(p.value for p in Provider)}"
        raise ConfigError(msg) from None


def _base_url(data: Mapping[str, object], provider: Provider) -> str:
    url = _string(data, "base_url") or PROVIDERS[provider].default_base_url
    if url is None:
        msg = f"'base_url' is required for provider {provider.value}"
        raise ConfigError(msg)
    try:
        return check_base_url(url)
    except ValueError as error:
        msg = f"'base_url': {error}"
        raise ConfigError(msg) from None


def _model(data: Mapping[str, object], provider: Provider) -> str | None:
    model = _string(data, "model")
    if model is not None and not MODEL_NAME.fullmatch(model):
        msg = f"'model' may hold {MODEL_NAME_CHARS}"
        raise ConfigError(msg)
    if model is None and provider is Provider.AZURE_OPENAI:
        msg = "'model' (the deployment name) is required for provider azure_openai"
        raise ConfigError(msg)
    return model


def _api_version(data: Mapping[str, object], provider: Provider) -> str | None:
    version = _string(data, "api_version")
    if provider is not Provider.AZURE_OPENAI:
        if version is not None:
            msg = "'api_version' applies to provider azure_openai only"
            raise ConfigError(msg)
        return None
    version = version or DEFAULT_AZURE_API_VERSION
    if not _API_VERSION.fullmatch(version):
        msg = "'api_version' must look like 2024-10-21"
        raise ConfigError(msg)
    return version


def _thresholds(data: Mapping[str, object]) -> dict[EntityType, float]:
    thresholds: dict[EntityType, float] = {}
    for name, value in _table(data, "thresholds").items():
        try:
            entity_type = EntityType(name)
        except ValueError:
            msg = f"thresholds: {name!r} is not an entity type"
            raise ConfigError(msg) from None
        if isinstance(value, bool) or not isinstance(value, int | float) or not 0 <= value <= 1:
            msg = f"thresholds: {name!r} must be a number from 0 to 1"
            raise ConfigError(msg)
        thresholds[entity_type] = float(value)
    return thresholds


def _gazetteer(data: Mapping[str, object]) -> Path | None:
    value = data.get("gazetteer")
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        msg = "'gazetteer' must be a file path"
        raise ConfigError(msg)
    return Path(value).expanduser()


def _proxy(data: Mapping[str, object]) -> ProxyConfig:
    table = _table(data, "proxy")
    _refuse_unknown(table, _PROXY_FIELDS, "proxy.")
    origins = table.get("cors_allow_origins", [])
    if not isinstance(origins, list) or not all(
        isinstance(o, str) and _ORIGIN.fullmatch(o) for o in origins
    ):
        msg = "'cors_allow_origins' must be a list of origins such as chrome-extension://<id>"
        raise ConfigError(msg)
    allow_dev_ui = table.get("allow_dev_ui", False)
    if not isinstance(allow_dev_ui, bool):
        msg = "'allow_dev_ui' must be true or false"
        raise ConfigError(msg)
    return ProxyConfig(
        max_body_bytes=_positive_int(table, "max_body_bytes", DEFAULT_MAX_BODY_BYTES),
        cors_allow_origins=tuple(origins),
        allow_dev_ui=allow_dev_ui,
        extension_ids=_extension_ids(table),
    )


def _extension_ids(table: Mapping[str, object]) -> tuple[str, ...]:
    ids = table.get("extension_ids", [])
    if not isinstance(ids, list) or not all(
        isinstance(i, str)
        and (_CHROME_EXTENSION_ID.fullmatch(i) or _FIREFOX_INTERNAL_UUID.fullmatch(i))
        for i in ids
    ):
        raise ConfigError(_EXTENSION_IDS_HINT)
    return tuple(ids)


def parse_config(data: Mapping[str, object]) -> GatewayConfig:
    _refuse_keys(data)
    _refuse_unknown(data, _TOP_FIELDS, "")
    provider = _provider(data)
    return GatewayConfig(
        provider=provider,
        base_url=_base_url(data, provider),
        model=_model(data, provider),
        api_version=_api_version(data, provider),
        gazetteer=_gazetteer(data),
        retention_days=_retention_days(data),
        thresholds=_thresholds(data),
        proxy=_proxy(data),
    )


def load_config(path: Path | None = None) -> GatewayConfig:
    """The configuration at `path` (default `config_path()`); defaults when there is no file."""
    path = config_path() if path is None else path
    if not path.exists():
        return GatewayConfig()
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        msg = f"cannot read {path}: {error}"
        raise ConfigError(msg) from None
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        msg = f"{path} is not valid TOML: {error}"
        raise ConfigError(msg) from None
    try:
        return parse_config(data)
    except ConfigError as error:
        msg = f"{path}: {error}"
        raise ConfigError(msg) from None
