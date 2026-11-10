import pytest

from gateway.cloud.providers import (
    PROVIDERS,
    ApiShape,
    Provider,
    check_base_url,
    is_loopback_host,
)


def test_every_provider_has_an_entry() -> None:
    assert set(PROVIDERS) == set(Provider)


def test_shapes_and_locality() -> None:
    assert PROVIDERS[Provider.ANTHROPIC].shape is ApiShape.ANTHROPIC
    assert PROVIDERS[Provider.OPENAI].shape is ApiShape.OPENAI
    assert PROVIDERS[Provider.AZURE_OPENAI].shape is ApiShape.OPENAI
    assert PROVIDERS[Provider.OLLAMA].shape is ApiShape.OPENAI
    assert PROVIDERS[Provider.OLLAMA].local
    assert not PROVIDERS[Provider.OLLAMA].needs_key
    assert all(PROVIDERS[p].needs_key for p in Provider if p is not Provider.OLLAMA)
    assert PROVIDERS[Provider.AZURE_OPENAI].default_base_url is None


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "LOCALHOST"])
def test_loopback_hosts(host: str) -> None:
    assert is_loopback_host(host)


@pytest.mark.parametrize("host", ["127.0.0.2", "0.0.0.0", "example.com", "", "localhost.example"])
def test_other_hosts_are_not_loopback(host: str) -> None:
    assert not is_loopback_host(host)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://api.openai.com/v1/", "https://api.openai.com/v1"),
        ("https://api.anthropic.com", "https://api.anthropic.com"),
        ("http://127.0.0.1:11434/v1", "http://127.0.0.1:11434/v1"),
        ("http://localhost:11434/v1", "http://localhost:11434/v1"),
        ("http://[::1]:11434/v1", "http://[::1]:11434/v1"),
    ],
)
def test_accepted_base_urls(url: str, expected: str) -> None:
    assert check_base_url(url) == expected


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://api.openai.com/v1", "https"),
        ("ftp://example.com", "https"),
        ("https://user:pw@example.com", "user"),  # pragma: allowlist secret
        ("https://example.com/v1?api-key=x", "query"),
        ("https://example.com/v1#frag", "fragment"),
        ("https:///v1", "host"),
        ("not a url", "https"),
    ],
)
def test_refused_base_urls(url: str, reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        check_base_url(url)
