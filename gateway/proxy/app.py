"""The local proxy: mask, forward, rehydrate (ADR 0019).

Every outbound byte goes through `gateway.cloud.upstream`; this package holds no
client network code. Nothing here logs or audits request or response text.
"""

import contextlib
import hashlib
import json
import logging
import re
import time
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool
from starlette.middleware.cors import CORSMiddleware
from starlette.types import Receive, Scope, Send

from gateway import __version__
from gateway.cloud.providers import ApiShape
from gateway.cloud.upstream import Upstream, UpstreamError, UpstreamReply, UpstreamStream
from gateway.config import ProxyConfig, extension_origins
from gateway.engine.detect import EngineConfig, detect
from gateway.engine.known import KnownValues
from gateway.engine.mask import Mapping, mask_detected_messages, mask_messages
from gateway.engine.placeholders import TOKEN
from gateway.engine.session import SessionNotFound, SessionStore
from gateway.engine.types import Span
from gateway.proxy.anthropic_shape import ANTHROPIC
from gateway.proxy.audit import (
    AuditEvent,
    AuditReader,
    AuditSink,
    NullAuditSink,
    entity_counts,
    entity_counts_present,
)
from gateway.proxy.detections import Detection, mask_detected_with_detections
from gateway.proxy.errors import RequestRejected, invalid
from gateway.proxy.guards import LocalOnlyGuard
from gateway.proxy.openai_shape import OPENAI
from gateway.proxy.payload import (
    choose_model,
    encode_json,
    parse_json_object,
    refuse_identifiers,
)
from gateway.proxy.shape import Body, Shape
from gateway.proxy.sse import SSEDecoder, SSEEvent, SSELineTooLongError, encode
from gateway.proxy.ui import UiFiles, has_ui

_log = logging.getLogger(__name__)

CHAT_ROUTE = "/v1/chat/completions"
MESSAGES_ROUTE = "/v1/messages"
SESSION_HEADER = "x-safeguard-session"  # ADR 0025: a /mask session for the chat routes
# What `secrets.token_urlsafe` writes, with room to spare; anything else cannot be an id.
_SESSION_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_NO_SESSION = "no such session, or it expired; check the text again with POST /mask"
MAX_SESSIONS = 10_000  # a local client cannot grow the store without bound
MAX_SESSION_ENTRIES = 50_000
_ROUTE_FOR_SHAPE = {ApiShape.OPENAI: CHAT_ROUTE, ApiShape.ANTHROPIC: MESSAGES_ROUTE}
_NO_STORE = {"cache-control": "no-store"}  # answers hold personal data again
_MASK_FIELDS = frozenset({"text", "texts", "session_id"})
AUDIT_DEFAULT_LIMIT = 50
AUDIT_MAX_LIMIT = 500
_AUDIT_PARAMS = frozenset({"limit", "before_id"})
AUDIT_FAILED_MESSAGE = (
    "the audit log could not be written, so the gateway forwards nothing more; "
    "run `safeguard audit verify` and restart `safeguard serve`"
)
# The Vite dev server, allowed only with `[proxy] allow_dev_ui = true` (ADR 0024).
DEV_UI_ORIGINS = ("http://127.0.0.1:5173", "http://localhost:5173")


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
    counts: dict[str, int]  # read when masked: a session's mapping grows afterwards
    session_id: str | None = None  # the /mask session the request was masked in (ADR 0025)

    def release(self) -> None:
        """Drop a request's own mapping; a session's lives on until it is deleted or expires."""
        if self.session_id is None:
            self.mapping.clear()

    def event(
        self,
        outcome: str,
        status_code: int,
        upstream: Upstream,
        upstream_status: int | None,
        **fields: Any,  # noqa: ANN401  (AuditEvent's own fields, checked by its constructor)
    ) -> AuditEvent:
        """The event for a payload handed to the upstream; `upstream_status` None: no answer."""
        return self.timer.event(
            outcome,
            status_code,
            provider=upstream.provider.value,
            upstream_host=upstream.host,
            model=self.model,
            stream=self.stream,
            entity_counts=self.counts,
            session_id=self.session_id,
            request_sha256=hashlib.sha256(self.payload).hexdigest(),
            bytes_out=len(self.payload),
            upstream_status=upstream_status,
            local=upstream.local,
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


def _detect_texts(texts: Sequence[str], config: EngineConfig) -> list[list[Span]]:
    return [detect(text, config=config) for text in texts]


def new_entries_at_most(texts: Sequence[str], spans: Sequence[list[Span]], mapping: Mapping) -> int:
    """How many entries masking into `mapping` can add at most.

    Every stretch mask replaces starts at a span, a placeholder-shaped token (a
    `[LITERAL_n]`, ADR 0014 point 4) or a repeat the known-value sweep finds, so each
    new entry is paid for by one of them. The sweep counts: two repeats of one value
    can overlap (a periodic value), and their united stretch is a new, longer value.
    Repeats are counted for the session's values and for this request's spans.
    """
    values = mapping.known_values()
    for text, found in zip(texts, spans, strict=True):
        for span in found:
            values.setdefault(text[span.start : span.end], span.entity_type)
    known = KnownValues(values)
    repeats = 0
    for text, found in zip(texts, spans, strict=True):
        # A repeat that is exactly a span is that span, already counted.
        exact = {(span.start, span.end) for span in found}
        repeats += sum(1 for r in known.spans(text) if (r.start, r.end) not in exact)
    tokens = sum(1 for text in texts for _ in TOKEN.finditer(text))
    return sum(len(found) for found in spans) + tokens + repeats


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
        # Set once a write to the audit sink fails: from then on nothing is forwarded,
        # because it could not be recorded (ADR 0021 point 12, fail closed).
        self.audit_failed = False

    async def record(self, event: AuditEvent) -> None:
        try:
            await run_in_threadpool(self.audit.record, event)
        except Exception as error:  # noqa: BLE001  (the response is already decided)
            self.audit_failed = True
            # The type only: a traceback or message could repeat request details.
            _log.error(
                "audit sink failed (%s); forwarding nothing more until restarted",
                type(error).__name__,
            )

    # ---------------------------------------------------------------- chat routes

    async def chat(self, request: Request, shape: Shape, route: str) -> Response:
        timer = _Timer(route)
        if self.audit_failed:
            body = shape.error_body("audit_unavailable", AUDIT_FAILED_MESSAGE)
            return JSONResponse(body, status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
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
        session_id = _session_header(request)
        body = shape.validate(parse_json_object(await request.body()))
        model = choose_model(self.model, body.get("model"))
        # Before anything else uses the model id, the audit log included (review 2, M-c).
        short = [("model", model), *shape.short_fields(body)]
        await run_in_threadpool(refuse_identifiers, short, self.engine_config)
        texts = shape.collect_texts(body)
        if session_id is None:
            masked, mapping = await run_in_threadpool(_mask_request, texts, self.engine_config)
            counts = entity_counts(mapping)
        else:
            masked, mapping = await self._mask_chat_in_session(texts, session_id)
            counts = entity_counts_present(masked, mapping)
        outgoing = shape.with_instruction(shape.with_texts(body, masked))
        outgoing["model"] = model
        payload = encode_json(outgoing)
        stream = bool(body.get("stream"))
        return _Forward(shape, mapping, payload, model, stream, timer, counts, session_id)

    async def _mask_chat_in_session(
        self, texts: list[str], session_id: str
    ) -> tuple[list[str], Mapping]:
        """Mask with the session's mapping; the placeholders it issued stay as written.

        Values in clear are detected and added to the session, as /mask would.
        """
        spans = await run_in_threadpool(_detect_texts, texts, self.engine_config)
        async with self.session_lock:
            mapping = self._session_mapping(session_id, _NO_SESSION)
            _check_room(mapping, texts, spans)
            issued = frozenset(mapping.to_dict())
            masked = await run_in_threadpool(mask_detected_messages, texts, spans, mapping, issued)
            with contextlib.suppress(SessionNotFound):  # expired meanwhile: 404 next time
                await run_in_threadpool(self.sessions.save, session_id)
        return masked, mapping

    def _session_mapping(self, session_id: str, message: str) -> Mapping:
        try:
            return self.sessions.get(session_id)
        except SessionNotFound:
            # Never created under a client's id: it could be someone else's namespace.
            raise RequestRejected(404, "not_found", message) from None

    async def _upstream_failed(
        self, forward: _Forward, error: UpstreamError, upstream_status: int | None = None
    ) -> Response:
        event = forward.event("upstream_error", error.status, self.upstream, upstream_status)
        forward.release()
        await self.record(event)
        body = forward.shape.error_body("upstream_error", str(error))
        return JSONResponse(body, status_code=error.status)

    async def _error_reply(self, forward: _Forward, reply: UpstreamReply) -> Response:
        """An upstream error status: JSON passes through unchanged, anything else is a 502."""
        try:
            json.loads(reply.body)
        except ValueError:
            passes_through = False
        else:
            passes_through = True
        status_code = reply.status if passes_through else status.HTTP_502_BAD_GATEWAY
        digest = hashlib.sha256(reply.body).hexdigest()
        event = forward.event(
            "upstream_error", status_code, self.upstream, reply.status, response_sha256=digest
        )
        forward.release()
        await self.record(event)
        if passes_through:
            return Response(reply.body, status_code=reply.status, media_type="application/json")
        message = f"upstream answered {reply.status} without a JSON body"
        body = forward.shape.error_body("upstream_error", message)
        return JSONResponse(body, status_code=status_code)

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
            return await self._upstream_failed(forward, error, reply.status)
        unresolved = forward.shape.rehydrate_reply(payload, forward.mapping)
        event = forward.event(
            "forwarded",
            reply.status,
            self.upstream,
            reply.status,
            response_sha256=digest,
            unresolved=len(unresolved),
        )
        forward.release()
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
            return await self._upstream_failed(forward, error, opened.status)
        relay = _Relay(self, forward, opened)
        return _RelayResponse(relay)

    # ---------------------------------------------------------------- mask and sessions

    async def mask(self, request: Request) -> Response:
        timer = _Timer("/mask")
        try:
            _require_json(request)
            texts, session_id = _mask_request_fields(parse_json_object(await request.body()))
            # Detection takes the time and touches no mapping, so it runs before the
            # lock; the lock covers only allocating placeholders (review M12).
            spans = await run_in_threadpool(_detect_texts, texts, self.engine_config)
            async with self.session_lock:
                session_id, mapping, masked, found = await self._mask_in_session(
                    texts, spans, session_id
                )
                result = {
                    "session_id": session_id,
                    "masked": masked,
                    "detections": found,
                    "mapping": mapping.to_dict(),
                    "entities": entity_counts(mapping),
                }
        except RequestRejected as error:
            await self.record(timer.event("rejected", error.status))
            return _native_error(error.status, error.error_type, error.message)
        await self.record(timer.event("masked", 200, entity_counts=result["entities"]))
        return JSONResponse(result, headers=_NO_STORE)

    async def _mask_in_session(
        self, texts: list[str], spans: list[list[Span]], session_id: str | None
    ) -> tuple[str, Mapping, list[str], list[list[Detection]]]:
        self.sessions.purge_expired()
        if session_id is None:
            if len(self.sessions) >= MAX_SESSIONS:
                msg = "too many open sessions; delete some with DELETE /session/{id}"
                raise RequestRejected(503, "overloaded", msg)
            mapping = Mapping()
        else:
            msg = "no such session; omit session_id to start a new one"
            mapping = self._session_mapping(session_id, msg)
        _check_room(mapping, texts, spans)
        masked, found = await run_in_threadpool(
            mask_detected_with_detections, texts, spans, mapping
        )
        if session_id is None:
            session_id = self.sessions.create(mapping)
        else:
            # get() saved before masking; the entries just added must reach disk too (M11).
            with contextlib.suppress(SessionNotFound):  # expired meanwhile: 404 next time
                await run_in_threadpool(self.sessions.save, session_id)
        return session_id, mapping, masked, found

    async def delete_session(self, session_id: str) -> Response:
        timer = _Timer("/session")
        if self.sessions.delete(session_id):
            await self.record(timer.event("session_deleted", 204))
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        await self.record(timer.event("session_not_found", 404))
        return _native_error(404, "not_found", "no such session")


class _Relay:
    """One streamed answer: rehydrated on the way through, closed exactly once.

    `close()` runs whether or not the body was ever read: a client that leaves before
    the first byte still gets the upstream closed, the mapping cleared and an audit
    event (security review, phase 4).
    """

    def __init__(self, gateway: _Gateway, forward: _Forward, opened: UpstreamStream) -> None:
        self._gateway = gateway
        self._forward = forward
        self._opened = opened
        self._digest = hashlib.sha256()
        self._rewriter = forward.shape.stream_rewriter(forward.mapping)
        self._outcome = "cancelled"  # until the stream reaches its end
        self._closed = False

    @staticmethod
    def _out(events: list[SSEEvent]) -> bytes:
        return b"".join(encode(event) for event in events)

    async def events(self) -> AsyncIterator[bytes]:
        decoder = SSEDecoder()
        try:
            try:
                async for chunk in self._opened.chunks():
                    self._digest.update(chunk)
                    for event in decoder.feed(chunk):
                        yield self._out(self._rewriter.on_event(event))
                for event in decoder.close():
                    yield self._out(self._rewriter.on_event(event))
                yield self._out(self._rewriter.finish())
                self._outcome = "forwarded"
            except (UpstreamError, SSELineTooLongError) as error:
                self._outcome = "upstream_error"
                error_event = self._forward.shape.stream_error(str(error))
                yield self._out([*self._rewriter.finish(), error_event])
        finally:
            await self.close()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with anyio.CancelScope(shield=True):
            await self._opened.aclose()
            event = self._forward.event(
                self._outcome,
                self._opened.status,
                self._gateway.upstream,
                self._opened.status,  # it answered, even if the client or the stream left later
                response_sha256=self._digest.hexdigest(),
                unresolved=len(self._rewriter.unresolved),
            )
            self._forward.release()
            await self._gateway.record(event)


class _RelayResponse(StreamingResponse):
    def __init__(self, relay: _Relay) -> None:
        super().__init__(relay.events(), media_type="text/event-stream", headers=_NO_STORE)
        self._relay = relay

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self._relay.close()


def _check_room(mapping: Mapping, texts: Sequence[str], spans: Sequence[list[Span]]) -> None:
    if len(mapping) + new_entries_at_most(texts, spans, mapping) > MAX_SESSION_ENTRIES:
        # Refused before anything is added: the session stays as it was (review M12).
        msg = (
            f"this session would hold more than {MAX_SESSION_ENTRIES} values; "
            "start a new session by omitting session_id"
        )
        raise RequestRejected(status.HTTP_413_CONTENT_TOO_LARGE, "session_full", msg)


def _session_header(request: Request) -> str | None:
    """The chat request's session id (ADR 0025); None when the header is absent.

    A value that cannot be an id is refused like an unknown one, and never echoed.
    """
    value = request.headers.get(SESSION_HEADER)
    if value is None:
        return None
    if not _SESSION_ID.fullmatch(value):
        raise RequestRejected(404, "not_found", _NO_SESSION)
    return value


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


def _whole_number(value: str, name: str, low: int, high: int) -> int:
    if not value.isascii() or not value.isdigit() or not low <= int(value) <= high:
        msg = f"{name!r} must be a whole number from {low} to {high}"
        raise invalid(msg)
    return int(value)


def _audit_params(request: Request) -> tuple[int, int | None]:
    params = request.query_params
    unknown = sorted(set(params) - _AUDIT_PARAMS)
    if unknown:
        msg = f"unknown parameter {unknown[0]!r}; expected limit and before_id"
        raise invalid(msg)
    limit = _whole_number(
        params.get("limit", str(AUDIT_DEFAULT_LIMIT)), "limit", 1, AUDIT_MAX_LIMIT
    )
    before = params.get("before_id")
    before_id = None if before is None else _whole_number(before, "before_id", 1, 2**53 - 1)
    return limit, before_id


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
                "audit": not isinstance(gateway.audit, NullAuditSink),
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
    async def audit(request: Request) -> Response:
        reader = gateway.audit
        if not isinstance(reader, AuditReader):
            message = "no audit log: the gateway was started with --no-audit"
            return _native_error(status.HTTP_404_NOT_FOUND, "not_found", message)
        try:
            limit, before_id = _audit_params(request)
        except RequestRejected as error:
            return _native_error(error.status, error.error_type, error.message)
        # One more row than asked tells whether an older page exists.
        rows = await run_in_threadpool(reader.recent, limit + 1, before_id)
        verification = await run_in_threadpool(reader.verification)
        page = rows[:limit]
        more = len(rows) > limit
        body = {
            "rows": page,
            "next_before_id": page[-1]["id"] if more else None,
            "verification": verification,
        }
        return JSONResponse(body, headers=_NO_STORE)


def _browser_origins(proxy: ProxyConfig) -> tuple[str, ...]:
    origins = (*proxy.cors_allow_origins, *extension_origins(proxy.extension_ids))
    if proxy.allow_dev_ui:
        return (*origins, *DEV_UI_ORIGINS)
    return origins


def create_app(  # noqa: PLR0913  (keyword-only options, each with a safe default)
    upstream: Upstream,
    *,
    engine_config: EngineConfig | None = None,
    model: str | None = None,
    proxy: ProxyConfig | None = None,
    audit: AuditSink | None = None,
    sessions: SessionStore | None = None,
    ui_dir: Path | None = None,
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
    app.state.gateway = gateway  # for tests that check how requests share it
    _add_routes(app, gateway, model)
    serve_ui = ui_dir is not None and has_ui(ui_dir)
    if serve_ui and ui_dir is not None:
        # Mounted after the routes, so an API path always wins over a file.
        app.mount("/", UiFiles(ui_dir), name="ui")
    origins = _browser_origins(proxy)
    if origins:  # Phase 7: the extension's origin (ADR 0019); the dev UI (ADR 0024)
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(origins),
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=[
                "content-type",
                "authorization",
                "x-api-key",
                "anthropic-version",
                SESSION_HEADER,
            ],
        )
    # Added last, so it runs first: nothing reaches a route, a file or CORS before it.
    app.add_middleware(
        LocalOnlyGuard,
        max_body_bytes=proxy.max_body_bytes,
        allowed_origins=origins,
        allow_same_origin=serve_ui,
    )
    return app
