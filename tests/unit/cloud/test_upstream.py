import asyncio
import json
from collections.abc import AsyncIterator, Callable

import httpx
import pytest

from gateway.cloud.providers import ApiShape, Provider
from gateway.cloud.upstream import (
    Upstream,
    UpstreamError,
    UpstreamReply,
    UpstreamSettings,
    UpstreamStream,
)
from gateway.secrets import ApiKey

FAKE_KEY = "fake-upstream-key-0123456789"  # pragma: allowlist secret
BODY = b'{"model":"m","messages":[]}'

Handler = Callable[[httpx.Request], httpx.Response]


def _upstream(
    settings: UpstreamSettings | Provider,
    base_url: str,
    handler: Handler,
    *,
    key: ApiKey | None = None,
) -> Upstream:
    if isinstance(settings, Provider):
        settings = UpstreamSettings(settings, base_url)
    return Upstream(settings, key, transport=httpx.MockTransport(handler))


def _recording(seen: list[httpx.Request], response: httpx.Response | None = None) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return response or httpx.Response(200, json={"ok": True})

    return handler


@pytest.mark.parametrize(
    ("provider", "base_url", "url", "header", "value"),
    [
        (
            Provider.OPENAI,
            "https://api.openai.test/v1",
            "https://api.openai.test/v1/chat/completions",
            "authorization",
            f"Bearer {FAKE_KEY}",
        ),
        (
            Provider.ANTHROPIC,
            "https://api.anthropic.test",
            "https://api.anthropic.test/v1/messages",
            "x-api-key",
            FAKE_KEY,
        ),
        (
            Provider.AZURE_OPENAI,
            "https://res.openai.azure.test",
            (
                "https://res.openai.azure.test/openai/deployments/my%20dep/chat/completions"
                "?api-version=2024-10-21"
            ),
            "api-key",
            FAKE_KEY,
        ),
    ],
)
def test_url_and_key_header_per_provider(
    provider: Provider, base_url: str, url: str, header: str, value: str
) -> None:
    seen: list[httpx.Request] = []
    settings = UpstreamSettings(provider, base_url, model="my dep", api_version="2024-10-21")
    upstream = _upstream(settings, base_url, _recording(seen), key=ApiKey(FAKE_KEY))
    reply = asyncio.run(upstream.send(BODY))
    assert reply == UpstreamReply(200, b'{"ok":true}', "application/json")
    (request,) = seen
    assert str(request.url) == url
    assert request.headers[header] == value
    assert request.headers["content-type"] == "application/json"
    assert request.content == BODY
    assert upstream.url == url
    assert upstream.host == httpx.URL(url).host


def test_anthropic_sends_its_api_version() -> None:
    seen: list[httpx.Request] = []
    upstream = _upstream(
        Provider.ANTHROPIC, "https://a.test", _recording(seen), key=ApiKey(FAKE_KEY)
    )
    asyncio.run(upstream.send(BODY))
    assert seen[0].headers["anthropic-version"] == "2023-06-01"
    assert upstream.shape is ApiShape.ANTHROPIC
    assert not upstream.local


def test_ollama_sends_no_key_and_is_local() -> None:
    seen: list[httpx.Request] = []
    upstream = _upstream(Provider.OLLAMA, "http://127.0.0.1:11434/v1", _recording(seen))
    asyncio.run(upstream.send(BODY))
    (request,) = seen
    assert str(request.url) == "http://127.0.0.1:11434/v1/chat/completions"
    assert not {"authorization", "x-api-key", "api-key"} & set(request.headers)
    assert upstream.local
    assert upstream.shape is ApiShape.OPENAI


def test_provider_that_needs_a_key_refuses_to_start_without_one() -> None:
    with pytest.raises(ValueError, match="keychain"):
        _upstream(Provider.OPENAI, "https://api.openai.test/v1", _recording([]))


def test_azure_without_a_deployment_is_refused() -> None:
    with pytest.raises(ValueError, match="deployment"):
        _upstream(Provider.AZURE_OPENAI, "https://r.test", _recording([]), key=ApiKey(FAKE_KEY))


def test_unsafe_base_url_is_refused() -> None:
    with pytest.raises(ValueError, match="https"):
        _upstream(Provider.OLLAMA, "http://ollama.example.com/v1", _recording([]))


def test_redirect_to_another_host_is_not_followed() -> None:
    seen: list[httpx.Request] = []
    redirect = httpx.Response(307, headers={"location": "https://elsewhere.test/collect"})
    upstream = _upstream(
        Provider.OPENAI,
        "https://api.openai.test/v1",
        _recording(seen, redirect),
        key=ApiKey(FAKE_KEY),
    )
    with pytest.raises(UpstreamError) as error:
        asyncio.run(upstream.send(BODY))
    assert error.value.status == 502
    assert "redirect" in str(error.value)
    assert [r.url.host for r in seen] == ["api.openai.test"]


def test_redirect_is_not_followed_when_streaming() -> None:
    seen: list[httpx.Request] = []
    redirect = httpx.Response(302, headers={"location": "https://elsewhere.test/"})
    upstream = _upstream(Provider.OLLAMA, "http://127.0.0.1:1/v1", _recording(seen, redirect))
    with pytest.raises(UpstreamError, match="redirect"):
        asyncio.run(upstream.open_stream(BODY))
    assert len(seen) == 1


def _raising(error: Exception) -> Handler:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise error

    return handler


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (httpx.ReadTimeout("slow"), 504),
        (httpx.ConnectTimeout("slow"), 504),
        (httpx.ConnectError("refused"), 502),
        (httpx.RemoteProtocolError("bad"), 502),
    ],
)
def test_transport_failures_become_upstream_errors(error: Exception, status: int) -> None:
    upstream = _upstream(
        Provider.OPENAI, "https://api.openai.test/v1", _raising(error), key=ApiKey(FAKE_KEY)
    )
    with pytest.raises(UpstreamError) as raised:
        asyncio.run(upstream.send(BODY))
    assert raised.value.status == status
    assert "api.openai.test" in str(raised.value)
    assert FAKE_KEY not in str(raised.value)
    assert raised.value.__cause__ is None


def test_error_status_is_returned_as_a_reply() -> None:
    answer = httpx.Response(429, json={"error": {"message": "slow down"}})
    upstream = _upstream(Provider.OLLAMA, "http://127.0.0.1:1/v1", _recording([], answer))
    reply = asyncio.run(upstream.send(BODY))
    assert reply.status == 429
    assert json.loads(reply.body) == {"error": {"message": "slow down"}}


def test_error_status_when_streaming_is_returned_as_a_reply() -> None:
    answer = httpx.Response(400, json={"error": {"message": "bad"}})
    upstream = _upstream(Provider.OLLAMA, "http://127.0.0.1:1/v1", _recording([], answer))
    reply = asyncio.run(upstream.open_stream(BODY))
    assert isinstance(reply, UpstreamReply)
    assert reply.status == 400


async def _chunks() -> AsyncIterator[bytes]:
    for chunk in (b"data: 1\n\n", b"data: ", b"2\n\n"):
        yield chunk


def test_stream_yields_chunks_in_order() -> None:
    answer = httpx.Response(200, headers={"content-type": "text/event-stream"}, content=_chunks())
    upstream = _upstream(Provider.OLLAMA, "http://127.0.0.1:1/v1", _recording([], answer))

    async def read() -> tuple[UpstreamStream, list[bytes]]:
        stream = await upstream.open_stream(BODY)
        assert isinstance(stream, UpstreamStream)
        try:
            return stream, [chunk async for chunk in stream.chunks()]
        finally:
            await stream.aclose()
            await upstream.aclose()

    stream, chunks = asyncio.run(read())
    assert stream.status == 200
    assert stream.content_type == "text/event-stream"
    assert b"".join(chunks) == b"data: 1\n\ndata: 2\n\n"


async def _broken() -> AsyncIterator[bytes]:
    yield b"data: 1\n\n"
    msg = "connection reset"
    raise httpx.ReadError(msg)


def test_failure_mid_stream_is_an_upstream_error() -> None:
    answer = httpx.Response(200, headers={"content-type": "text/event-stream"}, content=_broken())
    upstream = _upstream(Provider.OLLAMA, "http://127.0.0.1:1/v1", _recording([], answer))

    async def read() -> list[bytes]:
        stream = await upstream.open_stream(BODY)
        assert isinstance(stream, UpstreamStream)
        try:
            return [chunk async for chunk in stream.chunks()]
        finally:
            await stream.aclose()

    with pytest.raises(UpstreamError) as error:
        asyncio.run(read())
    assert error.value.status == 502


def test_client_ignores_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.test:3128")
    upstream = Upstream(
        UpstreamSettings(Provider.OPENAI, "https://api.openai.test/v1"), ApiKey(FAKE_KEY)
    )
    assert upstream.trust_env is False
    assert upstream.follow_redirects is False
    asyncio.run(upstream.aclose())


def test_repr_hides_the_key() -> None:
    upstream = _upstream(
        Provider.OPENAI, "https://api.openai.test/v1", _recording([]), key=ApiKey(FAKE_KEY)
    )
    assert FAKE_KEY not in repr(upstream)
    assert "api.openai.test" in repr(upstream)
