"""Start the proxy on 127.0.0.1, and nowhere else (ADR 0019, ADR 0020)."""

from collections.abc import Callable
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from gateway.cloud.providers import PROVIDERS
from gateway.cloud.upstream import Upstream, UpstreamSettings
from gateway.config import GatewayConfig, load_config
from gateway.engine.detect import EngineConfig
from gateway.engine.recognizers.gazetteer import load_gazetteer
from gateway.proxy.app import create_app
from gateway.secrets import ApiKey, KeychainError, read_api_key

LOOPBACK = "127.0.0.1"
DEFAULT_PORT = 8787
_MAX_PORT = 65535


class NotLoopback(ValueError):  # noqa: N818  (reads as what is wrong)
    """The requested host is not 127.0.0.1."""


class StartupError(RuntimeError):
    """The proxy cannot start: a key, the keychain or the gazetteer is missing."""


def check_host(host: str) -> None:
    # Exactly 127.0.0.1: "localhost" can be pointed elsewhere by a hosts file, and every
    # other address, 0.0.0.0 above all, could expose the gateway to the network.
    if host != LOOPBACK:
        msg = f"refusing to serve on {host!r}: the gateway binds to 127.0.0.1 only"
        raise NotLoopback(msg)


def _check_port(port: int) -> None:
    if not 1 <= port <= _MAX_PORT:
        msg = f"port must be from 1 to {_MAX_PORT}, not {port}"
        raise ValueError(msg)


def build_app(
    config: GatewayConfig, read_key: Callable[[str], ApiKey | None] | None = None
) -> FastAPI:
    """The app for `config`: the key from the keychain, the gazetteer from its file."""
    read_key = read_key or read_api_key
    provider = config.provider
    key = None
    if PROVIDERS[provider].needs_key:
        try:
            key = read_key(provider.value)
        except KeychainError as error:
            raise StartupError(str(error)) from None
    settings = UpstreamSettings(provider, config.base_url, config.model, config.api_version)
    try:
        upstream = Upstream(settings, key)
    except ValueError as error:
        raise StartupError(str(error)) from None
    gazetteer = None
    if config.gazetteer is not None:
        try:
            gazetteer = load_gazetteer(config.gazetteer)
        except (OSError, ValueError) as error:
            msg = f"cannot read the gazetteer {config.gazetteer}: {error}"
            raise StartupError(msg) from None
    engine = EngineConfig(thresholds=config.thresholds, gazetteer=gazetteer)
    return create_app(upstream, engine_config=engine, model=config.model, proxy=config.proxy)


def run(host: str = LOOPBACK, port: int = DEFAULT_PORT, config_path: Path | None = None) -> int:
    """Serve until stopped. Raises before binding when anything is wrong."""
    check_host(host)
    _check_port(port)
    app = build_app(load_config(config_path))
    uvicorn.run(
        app,
        host=LOOPBACK,  # the constant, not the argument: belt and braces
        port=port,
        access_log=False,  # request paths carry session ids
        proxy_headers=False,  # no X-Forwarded-* from anyone
        server_header=False,
        log_level="info",
    )
    return 0
