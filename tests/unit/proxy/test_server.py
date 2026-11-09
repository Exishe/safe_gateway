from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from gateway.cloud.providers import Provider
from gateway.config import ConfigError, GatewayConfig
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
from gateway.secrets import ApiKey, KeychainError

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


def test_unreadable_gazetteer_is_a_startup_error(tmp_path: Path) -> None:
    with pytest.raises(StartupError, match="gazetteer"):
        build_app(GatewayConfig(gazetteer=tmp_path / "missing.csv"))
