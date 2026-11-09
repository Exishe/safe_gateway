from pathlib import Path

import pytest

from gateway.cloud.providers import Provider
from gateway.config import (
    DEFAULT_MAX_BODY_BYTES,
    ConfigError,
    GatewayConfig,
    ProxyConfig,
    config_path,
    load_config,
    parse_config,
    safeguard_home,
)
from gateway.engine.types import EntityType


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_home_and_path_honour_safeguard_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SAFEGUARD_HOME", str(tmp_path))
    assert safeguard_home() == tmp_path
    assert config_path() == tmp_path / "config.toml"


def test_home_defaults_to_dot_safeguard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SAFEGUARD_HOME", raising=False)
    assert safeguard_home() == Path.home() / ".safeguard"


def test_missing_file_gives_local_ollama_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SAFEGUARD_HOME", str(tmp_path))
    config = load_config()
    assert config == GatewayConfig(
        provider=Provider.OLLAMA,
        base_url="http://127.0.0.1:11434/v1",
        model=None,
        api_version=None,
        gazetteer=None,
        retention_days=30,
        thresholds={},
        proxy=ProxyConfig(),
    )
    assert config.proxy.max_body_bytes == DEFAULT_MAX_BODY_BYTES
    assert config.proxy.cors_allow_origins == ()


def test_every_field_is_read(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
provider = "openai"
base_url = "https://api.openai.com/v1/"
model = "gpt-4o-mini"
gazetteer = "clients.csv"
retention_days = 90

[thresholds]
PERSON = 0.6
URL_WITH_TOKEN = 0.5

[proxy]
max_body_bytes = 1024
cors_allow_origins = ["chrome-extension://abcdefghijklmnop"]
""",
    )
    config = load_config(path)
    assert config.provider is Provider.OPENAI
    assert config.base_url == "https://api.openai.com/v1"
    assert config.model == "gpt-4o-mini"
    assert config.gazetteer == Path("clients.csv")
    assert config.retention_days == 90
    assert config.thresholds == {EntityType.PERSON: 0.6, EntityType.URL_WITH_TOKEN: 0.5}
    assert config.proxy == ProxyConfig(
        max_body_bytes=1024, cors_allow_origins=("chrome-extension://abcdefghijklmnop",)
    )


def test_provider_default_base_url_is_filled_in() -> None:
    assert parse_config({"provider": "anthropic"}).base_url == "https://api.anthropic.com"


def test_gazetteer_path_expands_the_home_directory() -> None:
    config = parse_config({"gazetteer": "~/clients.csv"})
    assert config.gazetteer == Path.home() / "clients.csv"


def test_azure_needs_base_url_and_model() -> None:
    with pytest.raises(ConfigError, match="base_url"):
        parse_config({"provider": "azure_openai", "model": "deployment"})
    with pytest.raises(ConfigError, match="model"):
        parse_config({"provider": "azure_openai", "base_url": "https://r.openai.azure.com"})
    config = parse_config(
        {"provider": "azure_openai", "base_url": "https://r.openai.azure.com", "model": "dep"}
    )
    assert config.api_version == "2024-10-21"


def test_api_version_only_for_azure() -> None:
    with pytest.raises(ConfigError, match="api_version"):
        parse_config({"provider": "openai", "api_version": "2024-10-21"})


@pytest.mark.parametrize(
    "data",
    [
        {"api_key": "x"},
        {"openai_token": "x"},
        {"proxy": {"secret": "x"}},
        {"password": "x"},
        {"Credentials": "x"},
        {"proxy": {"auth": "x"}},
        {"bearer": "x"},
    ],
)
def test_key_like_fields_are_refused(data: dict[str, object]) -> None:
    with pytest.raises(ConfigError, match="keychain"):
        parse_config(data)


def test_key_like_value_is_refused() -> None:
    with pytest.raises(ConfigError, match="keychain"):
        parse_config({"model": "sk-proj-abcdefghijklmnopqrstuvwxyz"})  # pragma: allowlist secret


def test_key_message_names_the_field_not_the_value(tmp_path: Path) -> None:
    path = _write(tmp_path, 'api_key = "do-not-echo-this"\n')  # pragma: allowlist secret
    with pytest.raises(ConfigError) as error:
        load_config(path)
    assert "api_key" in str(error.value)
    assert "do-not-echo-this" not in str(error.value)


@pytest.mark.parametrize(
    ("data", "match"),
    [
        ({"provder": "openai"}, "provder"),
        ({"proxy": {"port": 1}}, "port"),
        ({"provider": "gemini"}, "provider"),
        ({"thresholds": {"PERSON": 1.5}}, "PERSON"),
        ({"thresholds": {"NOT_A_TYPE": 0.5}}, "NOT_A_TYPE"),
        ({"thresholds": {"PERSON": "high"}}, "PERSON"),
        ({"base_url": "http://api.example.com"}, "base_url"),
        ({"base_url": "https://u:p@api.example.com"}, "base_url"),  # pragma: allowlist secret
        ({"retention_days": 0}, "retention_days"),
        ({"retention_days": True}, "retention_days"),
        ({"model": ""}, "model"),
        ({"model": 3}, "model"),
        ({"proxy": {"max_body_bytes": 0}}, "max_body_bytes"),
        ({"proxy": {"cors_allow_origins": "*"}}, "cors_allow_origins"),
        ({"proxy": {"cors_allow_origins": ["*"]}}, "cors_allow_origins"),
        ({"proxy": []}, "proxy"),
        ({"thresholds": []}, "thresholds"),
        ({"gazetteer": 5}, "gazetteer"),
    ],
)
def test_invalid_values_are_refused(data: dict[str, object], match: str) -> None:
    with pytest.raises(ConfigError, match=match):
        parse_config(data)


def test_loopback_http_base_url_is_accepted() -> None:
    config = parse_config({"provider": "ollama", "base_url": "http://localhost:11434/v1"})
    assert config.base_url == "http://localhost:11434/v1"


def test_invalid_toml_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"config\.toml"):
        load_config(_write(tmp_path, "provider = \n"))


def test_unreadable_file_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(tmp_path)  # a directory
