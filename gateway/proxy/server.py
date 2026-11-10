"""Start the proxy on 127.0.0.1, and nowhere else (ADR 0019, ADR 0020, ADR 0021).

By default every forwarded request is written to the hash-chained audit log. The
log is opened, verified and purged under `retention_days` before anything binds;
when that fails, `serve` does not start unless the user chose `--no-audit`.
"""

import logging
import sqlite3
from collections.abc import Callable
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from keyring.errors import KeyringError

from gateway.audit import AuditChainBroken, AuditLogError, AuditLogSink, default_audit_path
from gateway.cloud.providers import PROVIDERS
from gateway.cloud.upstream import Upstream, UpstreamSettings
from gateway.config import GatewayConfig, load_config
from gateway.engine.detect import EngineConfig
from gateway.engine.recognizers.gazetteer import load_gazetteer
from gateway.engine.session_disk import KeyProvider
from gateway.proxy.app import create_app
from gateway.proxy.audit import AuditSink
from gateway.secrets import ApiKey, KeychainError, read_api_key

LOOPBACK = "127.0.0.1"
DEFAULT_PORT = 8787
_MAX_PORT = 65535
_FIX_OR_SKIP = (
    "Fix that, or start with `safeguard serve --no-audit` to serve without an audit log "
    "(then nothing records what leaves this machine)."
)


DEV_UI_WARNING = (
    "[proxy] allow_dev_ui is on: any local web app served on port 5173 "
    "(http://127.0.0.1:5173 or http://localhost:5173) can call this gateway from a browser, "
    "including POST /mask, whose answers hold session maps of original values. "
    "Turn it off unless you are developing the web UI."
)

_log = logging.getLogger(__name__)


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


def open_audit(retention_days: int | None, key_provider: KeyProvider | None = None) -> AuditLogSink:
    """The audit log `serve` writes: opened, verified and purged before anything is forwarded."""
    path = default_audit_path()
    try:
        sink = AuditLogSink.open_default(key_provider)
    except KeyringError as error:
        msg = (
            f"cannot open the audit log: the OS keychain is unavailable ({type(error).__name__}), "
            f"and the log's device key is kept there. {_FIX_OR_SKIP}"
        )
        raise StartupError(msg) from None
    except (AuditLogError, sqlite3.DatabaseError, OSError, ValueError) as error:
        msg = f"cannot open the audit log {path}: {error}. {_FIX_OR_SKIP}"
        raise StartupError(msg) from None
    try:
        _check_and_purge(sink, path, retention_days)
    except BaseException:
        sink.close()
        raise
    return sink


def _check_and_purge(sink: AuditLogSink, path: Path, retention_days: int | None) -> None:
    # Fail closed (ADR 0021 point 6): writing on after a broken row would bury it.
    hint = (
        "Run `safeguard audit verify`, move the file aside to keep it as evidence, "
        "and start again for a new log."
    )
    if retention_days is not None:
        try:
            sink.log.purge(older_than_days=retention_days)
        except AuditChainBroken as error:
            msg = f"the audit log {path} fails verification: {error}. {hint}"
            raise StartupError(msg) from None
        return
    result = sink.verify()
    if not result.ok:
        msg = (
            f"the audit log {path} fails verification at row {result.first_bad_id}: "
            f"{result.reason} ({result.detail}). {hint}"
        )
        raise StartupError(msg)


def default_ui_dir() -> Path:
    """The web UI as `npm run build` writes it in a source checkout (ADR 0024)."""
    return Path(__file__).resolve().parents[2] / "apps" / "web" / "dist"


def build_app(
    config: GatewayConfig,
    read_key: Callable[[str], ApiKey | None] | None = None,
    audit: AuditSink | None = None,
    ui_dir: Path | None = None,
) -> FastAPI:
    """The app for `config`: the key from the keychain, the gazetteer from its file.

    The UI in `ui_dir` (default: the repository's build) is served when it exists.
    """
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
    if config.proxy.allow_dev_ui:
        # Security review 2, M-g: said once, at start-up, where the user sees it.
        _log.warning(DEV_UI_WARNING)
    return create_app(
        upstream,
        engine_config=engine,
        model=config.model,
        proxy=config.proxy,
        ui_dir=ui_dir if ui_dir is not None else default_ui_dir(),
        audit=audit,
    )


def run(
    host: str = LOOPBACK,
    port: int = DEFAULT_PORT,
    config_path: Path | None = None,
    *,
    audit: bool = True,
) -> int:
    """Serve until stopped. Raises before binding when anything is wrong.

    `audit=False` is `--no-audit`: no log is opened and the keychain is not asked for
    its device key.
    """
    check_host(host)
    _check_port(port)
    config = load_config(config_path)
    sink = open_audit(config.retention_days) if audit else None
    try:
        app = build_app(config, audit=sink)
        uvicorn.run(
            app,
            host=LOOPBACK,  # the constant, not the argument: belt and braces
            port=port,
            access_log=False,  # request paths carry session ids
            proxy_headers=False,  # no X-Forwarded-* from anyone
            server_header=False,
            log_level="info",
        )
    finally:
        if sink is not None:
            sink.close()
    return 0
