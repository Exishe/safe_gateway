import logging
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from keyring.backends.fail import Keyring as NoKeyring
from starlette.testclient import TestClient

from gateway.audit import AuditLog
from gateway.cloud.providers import Provider
from gateway.config import ConfigError, GatewayConfig, ProxyConfig
from gateway.engine.session_disk import InMemoryKeyProvider
from gateway.proxy import server
from gateway.proxy.server import (
    DEFAULT_PORT,
    LOOPBACK,
    NotLoopback,
    StartupError,
    build_app,
    check_host,
    run,
)
from gateway.secrets import AUDIT_KEY_ID, SERVICE, ApiKey, KeychainError, KeyringKeyProvider
from tests.audit_support import Clock, all_rows, fill, tamper
from tests.proxy_fakes import MemoryKeyring, keyring_backend

LONG_AGO = datetime(2020, 1, 1, tzinfo=UTC)
FAKE_KEY = "fake-key-for-serve-0123456789"  # pragma: allowlist secret


class Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[FastAPI, dict[str, Any]]] = []

    def __call__(self, app: FastAPI, **kwargs: Any) -> None:  # noqa: ANN401
        self.calls.append((app, kwargs))


@pytest.fixture
def uvicorn_run(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    recorder = Recorder()
    monkeypatch.setattr(server.uvicorn, "run", recorder)
    return recorder


@pytest.mark.parametrize("host", ["0.0.0.0", "localhost", "::1", "127.0.0.2", "", "192.168.1.5"])
def test_non_loopback_hosts_are_refused(host: str, uvicorn_run: Recorder) -> None:
    with pytest.raises(NotLoopback, match=r"127\.0\.0\.1"):
        check_host(host)
    with pytest.raises(NotLoopback):
        run(host=host)
    assert uvicorn_run.calls == []


def test_loopback_runs_uvicorn_quietly_on_127_0_0_1(
    uvicorn_run: Recorder, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SAFEGUARD_HOME", str(tmp_path))
    assert run(host=LOOPBACK, port=9123) == 0
    ((app, kwargs),) = uvicorn_run.calls
    assert isinstance(app, FastAPI)
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == 9123
    assert kwargs["access_log"] is False
    assert kwargs["proxy_headers"] is False
    assert kwargs["server_header"] is False
    assert DEFAULT_PORT == 8787


@pytest.mark.parametrize("port", [0, -1, 65536])
def test_port_out_of_range_is_refused(port: int, uvicorn_run: Recorder) -> None:
    with pytest.raises(ValueError, match="port"):
        run(port=port)
    assert uvicorn_run.calls == []


def test_config_error_stops_before_binding(
    uvicorn_run: Recorder, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SAFEGUARD_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text('api_key = "x"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="keychain"):
        run()
    assert uvicorn_run.calls == []


def test_missing_key_names_the_keyring_command() -> None:
    config = GatewayConfig(provider=Provider.OPENAI, base_url="https://api.openai.test/v1")
    with pytest.raises(StartupError, match="keyring set safeguard-gateway api-key:openai"):
        build_app(config, read_key=lambda _: None)


def test_keychain_failure_is_a_startup_error() -> None:
    def broken(_: str) -> ApiKey | None:
        msg = "cannot read the OS keychain (KeyringError)"
        raise KeychainError(msg)

    config = GatewayConfig(provider=Provider.ANTHROPIC, base_url="https://api.anthropic.test")
    with pytest.raises(StartupError, match="keychain"):
        build_app(config, read_key=broken)


def test_key_is_read_for_the_configured_provider_only() -> None:
    asked: list[str] = []

    def read(provider: str) -> ApiKey | None:
        asked.append(provider)
        return ApiKey(FAKE_KEY)

    build_app(GatewayConfig(provider=Provider.ANTHROPIC, base_url="https://a.test"), read_key=read)
    build_app(GatewayConfig(), read_key=read)  # ollama: no key
    assert asked == ["anthropic"]


def test_gazetteer_is_loaded(tmp_path: Path) -> None:
    gazetteer = tmp_path / "clients.csv"
    gazetteer.write_text("value,entity_type\nProject Bluebird,CODENAME\n", encoding="utf-8")
    app = build_app(GatewayConfig(gazetteer=gazetteer))
    client = TestClient(app, base_url="http://127.0.0.1")
    masked = client.post("/mask", json={"text": "About Project Bluebird."}).json()["masked"]
    assert masked == ["About [CODENAME_1]."]


def test_the_repository_web_build_is_the_default_ui() -> None:
    expected = Path(server.__file__).resolve().parents[2] / "apps" / "web" / "dist"
    assert server.default_ui_dir() == expected


def test_a_frozen_sidecar_serves_the_ui_bundled_beside_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # PyInstaller unpacks data under sys._MEIPASS and sets sys.frozen (ADR 0027).
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert server.default_ui_dir() == tmp_path / "ui"


def test_build_app_serves_the_ui_dir_it_is_given(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<!doctype html><title>ui</title>", encoding="utf-8")
    app = build_app(GatewayConfig(), ui_dir=tmp_path)
    page = TestClient(app, base_url="http://127.0.0.1").get("/")
    assert page.status_code == 200
    assert "<title>ui</title>" in page.text


def test_the_dev_ui_flag_warns_at_start_up(caplog: pytest.LogCaptureFixture) -> None:
    # Security review 2, M-g: any local app on port 5173 can then read session maps.
    with caplog.at_level(logging.WARNING, logger="gateway.proxy.server"):
        build_app(GatewayConfig(proxy=ProxyConfig(allow_dev_ui=True)))
    (record,) = [r for r in caplog.records if "5173" in r.getMessage()]
    assert record.levelno == logging.WARNING
    message = record.getMessage()
    assert "allow_dev_ui" in message
    assert "session" in message


def test_no_dev_ui_warning_by_default(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="gateway.proxy.server"):
        build_app(GatewayConfig())
    assert not [r for r in caplog.records if "5173" in r.getMessage()]


def test_unreadable_gazetteer_is_a_startup_error(tmp_path: Path) -> None:
    with pytest.raises(StartupError, match="gazetteer"):
        build_app(GatewayConfig(gazetteer=tmp_path / "missing.csv"))


# ------------------------------------------------------------------ the audit log at start-up


@pytest.fixture(autouse=True)
def memory_keyring() -> Iterator[MemoryKeyring]:
    """No test here reaches the developer's real keychain."""
    with keyring_backend(MemoryKeyring()) as backend:
        assert isinstance(backend, MemoryKeyring)
        yield backend


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "sg"
    monkeypatch.setenv("SAFEGUARD_HOME", str(path))
    return path


def _served_app(uvicorn_run: Recorder) -> FastAPI:
    ((app, _),) = uvicorn_run.calls
    return app


def test_serve_writes_the_audit_log_by_default(
    uvicorn_run: Recorder, home: Path, memory_keyring: MemoryKeyring
) -> None:
    assert run() == 0
    assert (home / "audit.db").is_file()
    assert (SERVICE, AUDIT_KEY_ID) in memory_keyring.store  # the device key, in the keychain
    health = TestClient(_served_app(uvicorn_run), base_url="http://127.0.0.1").get("/health")
    assert health.json()["audit"] is True


def test_no_keychain_refuses_to_start_and_names_the_flag(uvicorn_run: Recorder, home: Path) -> None:
    del home
    with keyring_backend(NoKeyring()), pytest.raises(StartupError) as raised:
        run()
    message = str(raised.value)
    assert "keychain" in message
    assert "--no-audit" in message
    assert uvicorn_run.calls == []


def test_no_audit_serves_without_the_keychain_or_a_log(uvicorn_run: Recorder, home: Path) -> None:
    with keyring_backend(NoKeyring()):
        assert run(audit=False) == 0
    assert not (home / "audit.db").exists()
    health = TestClient(_served_app(uvicorn_run), base_url="http://127.0.0.1").get("/health")
    assert health.json()["audit"] is False


def test_a_log_that_fails_verification_refuses_to_start(uvicorn_run: Recorder, home: Path) -> None:
    with AuditLog(home / "audit.db", KeyringKeyProvider()) as log:
        fill(log, 3)
    tamper(home / "audit.db", "UPDATE audit_log SET bytes_out = 1 WHERE id = 2")
    with pytest.raises(StartupError, match=r"row 2.*audit verify"):
        run()
    assert uvicorn_run.calls == []


def test_a_log_from_another_keychain_refuses_to_start(uvicorn_run: Recorder, home: Path) -> None:
    with AuditLog(home / "audit.db", InMemoryKeyProvider()) as log:
        fill(log, 1)
    with pytest.raises(StartupError, match="device key"):
        run()
    assert uvicorn_run.calls == []


def test_retention_days_is_applied_at_start(uvicorn_run: Recorder, home: Path) -> None:
    home.mkdir()
    (home / "config.toml").write_text("retention_days = 30\n", encoding="utf-8")
    with AuditLog(home / "audit.db", KeyringKeyProvider(), clock=Clock(now=LONG_AGO)) as log:
        fill(log, 3)
    assert run() == 0
    assert [row["kind"] for row in all_rows(home / "audit.db")] == ["checkpoint"]
    del uvicorn_run


def test_without_retention_days_every_row_is_kept(uvicorn_run: Recorder, home: Path) -> None:
    with AuditLog(home / "audit.db", KeyringKeyProvider(), clock=Clock(now=LONG_AGO)) as log:
        fill(log, 3)
    assert run() == 0
    assert len(all_rows(home / "audit.db")) == 3
    del uvicorn_run
