"""Send masked requests to the configured upstream, and nowhere else (ADR 0020).

This is the only code that makes the proxy's outbound calls. The URL comes from the
configuration alone, redirects are never followed, and the environment cannot add a
proxy or credentials (`trust_env=False`). httpx failures become `UpstreamError`, so
callers never handle, or import, httpx themselves.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from gateway import __version__
from gateway.cloud.providers import (
    PROVIDERS,
    ApiShape,
    Provider,
    check_base_url,
    is_loopback_host,
)
from gateway.secrets import ApiKey

ANTHROPIC_VERSION = "2023-06-01"
_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=60.0, pool=10.0)
_BAD_GATEWAY = 502
_GATEWAY_TIMEOUT = 504
_ERROR_STATUS = 400
MAX_REPLY_BYTES = 16 * 1024 * 1024  # a whole (non-streamed) answer, decoded (review M12)


class UpstreamError(Exception):
    """The upstream could not be reached or answered unusably. Never holds a key."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class UpstreamSettings:
    provider: Provider
    base_url: str
    model: str | None = None
    api_version: str | None = None


@dataclass(frozen=True)
class UpstreamReply:
    """A whole answer: a non-streamed one, or an error status with its body."""

    status: int
    body: bytes
    content_type: str


def _content_type(response: httpx.Response) -> str:
    return response.headers.get("content-type", "").split(";", 1)[0].strip().lower()


class UpstreamStream:
    """A streamed answer with a success status; close it with `aclose()`."""

    def __init__(self, response: httpx.Response, host: str) -> None:
        self._response = response
        self._host = host
        self.status = response.status_code
        self.content_type = _content_type(response)

    async def chunks(self) -> AsyncIterator[bytes]:
        try:
            async for chunk in self._response.aiter_bytes():
                yield chunk
        except httpx.HTTPError as error:
            raise _translate(error, self._host) from None

    async def aclose(self) -> None:
        await self._response.aclose()


def _translate(error: httpx.HTTPError, host: str) -> UpstreamError:
    # Only the type and host: httpx messages can repeat request details.
    if isinstance(error, httpx.TimeoutException):
        return UpstreamError(_GATEWAY_TIMEOUT, f"upstream {host} timed out")
    return UpstreamError(_BAD_GATEWAY, f"cannot reach upstream {host} ({type(error).__name__})")


def _too_large(host: str) -> UpstreamError:
    return UpstreamError(_BAD_GATEWAY, f"upstream {host} answered with more than 16 MiB")


async def _read_capped(response: httpx.Response, host: str) -> bytes:
    """The whole body, decoded, or a 502 past MAX_REPLY_BYTES; the response is closed.

    The cap counts decoded bytes as they arrive, so neither a false Content-Length
    nor a compressed body can make the gateway hold more (security review M12).
    """
    try:
        declared = response.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_REPLY_BYTES:
            raise _too_large(host)
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > MAX_REPLY_BYTES:
                raise _too_large(host)
            chunks.append(chunk)
        return b"".join(chunks)
    except httpx.HTTPError as error:
        raise _translate(error, host) from None
    finally:
        await response.aclose()


def _redirect_error(host: str) -> UpstreamError:
    return UpstreamError(
        _BAD_GATEWAY, f"upstream {host} answered with a redirect; the gateway does not follow it"
    )


class Upstream:
    def __init__(
        self,
        settings: UpstreamSettings,
        api_key: ApiKey | None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        info = PROVIDERS[settings.provider]
        if info.needs_key and api_key is None:
            msg = (
                f"no API key for {settings.provider.value} in the OS keychain; set it with: "
                f"uv run keyring set safeguard-gateway api-key:{settings.provider.value}"
            )
            raise ValueError(msg)
        self.provider = settings.provider
        self.shape: ApiShape = info.shape
        self.url = _url(settings)
        self.host = httpx.URL(self.url).host
        # From where the bytes go, not the provider type: an Ollama on another host
        # is not local (security review M2). /health and the audit log report this.
        self.local = is_loopback_host(self.host)
        self._headers = _headers(settings.provider, api_key if info.needs_key else None)
        self._client = httpx.AsyncClient(
            transport=transport,
            timeout=_TIMEOUT,
            follow_redirects=False,
            trust_env=False,
        )

    @property
    def trust_env(self) -> bool:
        return self._client.trust_env

    @property
    def follow_redirects(self) -> bool:
        return self._client.follow_redirects

    def __repr__(self) -> str:
        return f"Upstream({self.provider.value}, {self.url})"

    def _request(self, body: bytes) -> httpx.Request:
        return self._client.build_request("POST", self.url, content=body, headers=self._headers)

    async def send(self, body: bytes) -> UpstreamReply:
        """POST `body` and read the whole answer, at most MAX_REPLY_BYTES of it."""
        try:
            response = await self._client.send(self._request(body), stream=True)
        except httpx.HTTPError as error:
            raise _translate(error, self.host) from None
        if response.is_redirect:
            await response.aclose()
            raise _redirect_error(self.host)
        content = await _read_capped(response, self.host)
        return UpstreamReply(response.status_code, content, _content_type(response))

    async def open_stream(self, body: bytes) -> UpstreamReply | UpstreamStream:
        """POST `body` for a streamed answer; an error status comes back read, as a reply."""
        try:
            response = await self._client.send(self._request(body), stream=True)
        except httpx.HTTPError as error:
            raise _translate(error, self.host) from None
        if response.is_redirect:
            await response.aclose()
            raise _redirect_error(self.host)
        if response.status_code >= _ERROR_STATUS:
            content = await _read_capped(response, self.host)
            return UpstreamReply(response.status_code, content, _content_type(response))
        return UpstreamStream(response, self.host)

    async def aclose(self) -> None:
        await self._client.aclose()


def _url(settings: UpstreamSettings) -> str:
    base = check_base_url(settings.base_url)
    match settings.provider:
        case Provider.ANTHROPIC:
            return f"{base}/v1/messages"
        case Provider.AZURE_OPENAI:
            if not settings.model:
                msg = "azure_openai needs the deployment name as its model"
                raise ValueError(msg)
            deployment = quote(settings.model, safe="")
            version = quote(settings.api_version or "2024-10-21", safe="")
            return f"{base}/openai/deployments/{deployment}/chat/completions?api-version={version}"
        case Provider.OPENAI | Provider.OLLAMA:
            return f"{base}/chat/completions"


def _headers(provider: Provider, api_key: ApiKey | None) -> dict[str, str]:
    headers = {
        "content-type": "application/json",
        "user-agent": f"safeguard-gateway/{__version__}",
    }
    if api_key is None:
        return headers
    match provider:
        case Provider.ANTHROPIC:
            headers["x-api-key"] = api_key.reveal()
            headers["anthropic-version"] = ANTHROPIC_VERSION
        case Provider.AZURE_OPENAI:
            headers["api-key"] = api_key.reveal()
        case Provider.OPENAI | Provider.OLLAMA:
            headers["authorization"] = f"Bearer {api_key.reveal()}"
    return headers
