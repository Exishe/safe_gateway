"""The local proxy: mask, forward, rehydrate (ADR 0019).

Every outbound byte goes through `gateway.cloud.upstream`; this package holds no
client network code. Nothing here logs or audits request or response text.
"""

import hashlib
import json
import logging
import time
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import anyio
from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool
from starlette.middleware.cors import CORSMiddleware

from gateway import __version__
from gateway.cloud.providers import ApiShape
from gateway.cloud.upstream import Upstream, UpstreamError, UpstreamReply, UpstreamStream
from gateway.config import ProxyConfig
from gateway.engine.detect import EngineConfig, detect
from gateway.engine.mask import Mapping, mask, mask_messages
from gateway.engine.session import SessionNotFound, SessionStore
from gateway.proxy.anthropic_shape import ANTHROPIC
from gateway.proxy.audit import AuditEvent, AuditSink, NullAuditSink, entity_counts
from gateway.proxy.errors import RequestRejected, invalid
from gateway.proxy.guards import LocalOnlyGuard
from gateway.proxy.openai_shape import OPENAI
from gateway.proxy.payload import choose_model, encode_json, parse_json_object
from gateway.proxy.shape import Body, Shape
from gateway.proxy.sse import SSEDecoder, SSEEvent, encode

_log = logging.getLogger(__name__)

CHAT_ROUTE = "/v1/chat/completions"
MESSAGES_ROUTE = "/v1/messages"
MAX_SESSIONS = 10_000  # a local client cannot grow the store without bound
_ROUTE_FOR_SHAPE = {ApiShape.OPENAI: CHAT_ROUTE, ApiShape.ANTHROPIC: MESSAGES_ROUTE}
_NO_STORE = {"cache-control": "no-store"}  # answers hold personal data again
_MASK_FIELDS = frozenset({"text", "texts", "session_id"})


@dataclass
class _Timer:
    """Start time and duration for one request's audit event."""

    route: str
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    _start: float = field(default_factory=time.monotonic)

    def event(self, outcome: str, status_code: int, **fields: Any) -> AuditEvent:  # noqa: ANN401
        duration_ms = int((time.monotonic() - self._start) * 1000)
        return AuditEvent(
            route=self.route,
            outcome=outcome,
            status_code=status_code,
            started_at=self.started_at,
            duration_ms=duration_ms,
            **fields,
        )


@dataclass
class _Forward:
    """One chat request after masking, on its way upstream."""

    shape: Shape
    mapping: Mapping
    payload: bytes
    model: str
    stream: bool
    timer: _Timer

    def event(
        self,
        outcome: str,
        status_code: int,
        upstream: Upstream,
        **fields: Any,  # noqa: ANN401  (AuditEvent's own fields, checked by its constructor)
    ) -> AuditEvent:
        return self.timer.event(
            outcome,
            status_code,
            provider=upstream.provider.value,
            upstream_host=upstream.host,
            model=self.model,
            stream=self.stream,
            entity_counts=entity_counts(self.mapping),
            request_sha256=hashlib.sha256(self.payload).hexdigest(),
            **fields,
        )


def _native_error(status_code: int, error_type: str, message: str) -> JSONResponse:
    return JSONResponse(
        {"error": {"type": error_type, "message": message}}, status_code=status_code
    )


def _require_json(request: Request) -> None:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        msg = "Content-Type must be application/json"
        raise RequestRejected(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "invalid_request_error", msg)


def _mask_texts(texts: Sequence[str], config: EngineConfig, mapping: Mapping) -> list[str]:
    return [mask(text, detect(text, config=config), mapping)[0] for text in texts]


def _mask_request(texts: list[str], config: EngineConfig) -> tuple[list[str], Mapping]:
    return mask_messages(texts, "auto", config)


class _Gateway:
    def __init__(
        self,
        upstream: Upstream,
        engine_config: EngineConfig,
        model: str | None,
        audit: AuditSink,
        sessions: SessionStore,
    ) -> None:
        self.upstream = upstream
        self.engine_config = engine_config
        self.model = model
        self.audit = audit
        self.sessions = sessions
        self.session_lock = anyio.Lock()  # one Mapping must not be extended by two threads

    async def record(self, event: AuditEvent) -> None:
        try:
            await run_in_threadpool(self.audit.record, event)
        except Exception as error:  # noqa: BLE001  (the response is already decided)
            _log.warning("audit sink failed: %s", type(error).__name__)

    # ---------------------------------------------------------------- chat routes

    async def chat(self, request: Request, shape: Shape, route: str) -> Response:
        timer = _Timer(route)
        try:
            forward = await self._prepare(request, shape, timer)
        except RequestRejected as error:
            await self.record(
                timer.event(
                    "rejected",
                    error.status,
                    provider=self.upstream.provider.value,
                    upstream_host=self.upstream.host,
                )
            )
            body = shape.error_body(error.error_type, error.message)
            return JSONResponse(body, status_code=error.status)
        if forward.stream:
            return await self._stream(forward)
        return await self._whole(forward)

    async def _prepare(self, request: Request, shape: Shape, timer: _Timer) -> _Forward:
        _require_json(request)
        if self.upstream.shape is not shape.api_shape:
            msg = (
                f"the configured provider {self.upstream.provider.value} speaks the "
                f"{self.upstream.shape.value} API; use POST {_ROUTE_FOR_SHAPE[self.upstream.shape]}"
            )
            raise invalid(msg)
        body = shape.validate(parse_json_object(await request.body()))
        model = choose_model(self.model, body.get("model"))
        masked, mapping = await run_in_threadpool(
            _mask_request, shape.collect_texts(body), self.engine_config
        )
        outgoing = shape.with_instruction(shape.with_texts(body, masked))
        outgoing["model"] = model
        return _Forward(
            shape, mapping, encode_json(outgoing), model, bool(body.get("stream")), timer
        )

    async def _upstream_failed(self, forward: _Forward, error: UpstreamError) -> Response:
        forward.mapping.clear()
        await self.record(forward.event("upstream_error", error.status, self.upstream))
        body = forward.shape.error_body("upstream_error", str(error))
        return JSONResponse(body, status_code=error.status)

    async def _error_reply(self, forward: _Forward, reply: UpstreamReply) -> Response:
        """An upstream error status: JSON passes through unchanged, anything else is a 502."""
        forward.mapping.clear()
        digest = hashlib.sha256(reply.body).hexdigest()
        try:
            json.loads(reply.body)
        except ValueError:
            await self.record(
                forward.event("upstream_error", 502, self.upstream, response_sha256=digest)
            )
            message = f"upstream answered {reply.status} without a JSON body"
            body = forward.shape.error_body("upstream_error", message)
            return JSONResponse(body, status_code=status.HTTP_502_BAD_GATEWAY)
        await self.record(
            forward.event("upstream_error", reply.status, self.upstream, response_sha256=digest)
        )
        return Response(reply.body, status_code=reply.status, media_type="application/json")

    async def _whole(self, forward: _Forward) -> Response:
        try:
            reply = await self.upstream.send(forward.payload)
        except UpstreamError as error:
            return await self._upstream_failed(forward, error)
        if reply.status >= status.HTTP_400_BAD_REQUEST:
            return await self._error_reply(forward, reply)
        digest = hashlib.sha256(reply.body).hexdigest()
        try:
            payload = json.loads(reply.body)
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            error = UpstreamError(502, f"upstream {self.upstream.host} did not answer with JSON")
            return await self._upstream_failed(forward, error)
        unresolved = forward.shape.rehydrate_reply(payload, forward.mapping)
        event = forward.event(
            "forwarded",
            reply.status,
            self.upstream,
            response_sha256=digest,
            unresolved=len(unresolved),
        )
        forward.mapping.clear()
        await self.record(event)
        return JSONResponse(payload, status_code=reply.status, headers=_NO_STORE)

    async def _stream(self, forward: _Forward) -> Response:
        try:
            opened = await self.upstream.open_stream(forward.payload)
        except UpstreamError as error:
            return await self._upstream_failed(forward, error)
        if isinstance(opened, UpstreamReply):
            return await self._error_reply(forward, opened)
        if opened.content_type != "text/event-stream":
            await opened.aclose()
            error = UpstreamError(502, f"upstream {self.upstream.host} did not stream its answer")
            return await self._upstream_failed(forward, error)
        return StreamingResponse(
            self._relay(forward, opened), media_type="text/event-stream", headers=_NO_STORE
        )

    async def _relay(self, forward: _Forward, opened: UpstreamStream) -> AsyncIterator[bytes]:
        digest = hashlib.sha256()
        decoder = SSEDecoder()
        rewriter = forward.shape.stream_rewriter(forward.mapping)
        outcome = "forwarded"

        def out(events: list[SSEEvent]) -> bytes:
            return b"".join(encode(event) for event in events)

        try:
            try:
                async for chunk in opened.chunks():
                    digest.update(chunk)
                    for event in decoder.feed(chunk):
                        yield out(rewriter.on_event(event))
                for event in decoder.close():
                    yield out(rewriter.on_event(event))
                yield out(rewriter.finish())
            except UpstreamError as error:
                outcome = "upstream_error"
                yield out([*rewriter.finish(), forward.shape.stream_error(str(error))])
        except BaseException:
            outcome = "cancelled"  # the client went away
            raise
        finally:
            with anyio.CancelScope(shield=True):
                await opened.aclose()
                event = forward.event(
                    outcome,
                    opened.status,
                    self.upstream,
                    response_sha256=digest.hexdigest(),
                    unresolved=len(rewriter.unresolved),
                )
                forward.mapping.clear()
                await self.record(event)

    # ---------------------------------------------------------------- mask and sessions

    async def mask(self, request: Request) -> Response:
        timer = _Timer("/mask")
        try:
            _require_json(request)
            texts, session_id = _mask_request_fields(parse_json_object(await request.body()))
            async with self.session_lock:
                session_id, mapping, masked = await self._mask_in_session(texts, session_id)
                result = {
                    "session_id": session_id,
                    "masked": masked,
                    "mapping": mapping.to_dict(),
                    "entities": entity_counts(mapping),
                }
        except RequestRejected as error:
            await self.record(timer.event("rejected", error.status))
            return _native_error(error.status, error.error_type, error.message)
        await self.record(timer.event("masked", 200, entity_counts=result["entities"]))
        return JSONResponse(result, headers=_NO_STORE)

    async def _mask_in_session(
        self, texts: list[str], session_id: str | None
    ) -> tuple[str, Mapping, list[str]]:
        self.sessions.purge_expired()
        if session_id is None:
            if len(self.sessions) >= MAX_SESSIONS:
                msg = "too many open sessions; delete some with DELETE /session/{id}"
                raise RequestRejected(503, "overloaded", msg)
            mapping = Mapping()
        else:
            try:
                mapping = self.sessions.get(session_id)
            except SessionNotFound:
                # Never created under a client's id: it could be someone else's namespace.
                msg = "no such session; omit session_id to start a new one"
                raise RequestRejected(404, "not_found", msg) from None
        masked = await run_in_threadpool(_mask_texts, texts, self.engine_config, mapping)
        if session_id is None:
            session_id = self.sessions.create(mapping)
        return session_id, mapping, masked

    async def delete_session(self, session_id: str) -> Response:
        timer = _Timer("/session")
        if self.sessions.delete(session_id):
            await self.record(timer.event("session_deleted", 204))
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        await self.record(timer.event("session_not_found", 404))
        return _native_error(404, "not_found", "no such session")


def _mask_request_fields(data: Body) -> tuple[list[str], str | None]:
    unknown = sorted(set(data) - _MASK_FIELDS)
    if unknown:
        msg = f"unknown field {unknown[0]!r}; expected text or texts, and session_id"
        raise invalid(msg)
    if ("text" in data) == ("texts" in data):
        msg = "send exactly one of 'text' (a string) or 'texts' (a list of strings)"
        raise invalid(msg)
    texts = [data["text"]] if "text" in data else data["texts"]
    if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
        msg = "'text' must be a string and 'texts' a list of strings"
        raise invalid(msg)
    session_id = data.get("session_id")
    if session_id is not None and not isinstance(session_id, str):
        msg = "'session_id' must be a string"
        raise invalid(msg)
    return texts, session_id


def _add_routes(app: FastAPI, gateway: _Gateway, model: str | None) -> None:
    def chat_route(shape: Shape, route: str) -> Callable[[Request], Any]:
        async def handler(request: Request) -> Response:
            return await gateway.chat(request, shape, route)

        return handler

    app.add_api_route(CHAT_ROUTE, chat_route(OPENAI, CHAT_ROUTE), methods=["POST"])
    app.add_api_route(MESSAGES_ROUTE, chat_route(ANTHROPIC, MESSAGES_ROUTE), methods=["POST"])

    @app.post("/mask")
    async def mask_route(request: Request) -> Response:
        return await gateway.mask(request)

    @app.delete("/session/{session_id}")
    async def delete_session(session_id: str) -> Response:
        return await gateway.delete_session(session_id)

    @app.get("/health")
    async def health() -> Response:
        upstream = gateway.upstream
        return JSONResponse(
            {
                "status": "ok",
                "version": __version__,
                "provider": upstream.provider.value,
                "local": upstream.local,
            }
        )

    @app.get("/v1/models")
    async def models() -> Response:
        # Answered here: OpenAI-compatible UIs fill their model picker from it (ADR 0019).
        data = (
            []
            if model is None
            else [{"id": model, "object": "model", "created": 0, "owned_by": "safeguard-gateway"}]
        )
        return JSONResponse({"object": "list", "data": data})

    @app.get("/audit")
    async def audit() -> Response:
        return _native_error(
            status.HTTP_501_NOT_IMPLEMENTED,
            "not_implemented",
            "the audit log arrives in Phase 5",
        )


def create_app(  # noqa: PLR0913  (keyword-only options, each with a safe default)
    upstream: Upstream,
    *,
    engine_config: EngineConfig | None = None,
    model: str | None = None,
    proxy: ProxyConfig | None = None,
    audit: AuditSink | None = None,
    sessions: SessionStore | None = None,
) -> FastAPI:
    proxy = proxy or ProxyConfig()
    gateway = _Gateway(
        upstream,
        engine_config or EngineConfig(),
        model,
        audit or NullAuditSink(),
        sessions if sessions is not None else SessionStore(),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        yield
        await upstream.aclose()

    app = FastAPI(
        title="SafeGuard Gateway",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    _add_routes(app, gateway, model)
    if proxy.cors_allow_origins:  # Phase 7: the extension's origin (ADR 0019)
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(proxy.cors_allow_origins),
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=["content-type", "authorization", "x-api-key", "anthropic-version"],
        )
    # Added last, so it runs first: nothing reaches a route or CORS before it.
    app.add_middleware(
        LocalOnlyGuard,
        max_body_bytes=proxy.max_body_bytes,
        allowed_origins=proxy.cors_allow_origins,
    )
    return app
