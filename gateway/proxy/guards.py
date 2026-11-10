"""The trust boundary of a server on 127.0.0.1 (ADR 0019, "Trust boundary").

Other programs on the machine and pages in the user's browser can all reach
127.0.0.1. This middleware runs before any route:

- the Host header must name 127.0.0.1 or localhost, so a page cannot read answers
  through DNS rebinding (its own domain pointed at 127.0.0.1);
- a request with an Origin header is refused unless that origin is allowed, or, when
  the gateway serves its own web UI, it is the very origin the request was sent to
  (browsers send Origin on same-origin POST and DELETE; ADR 0024);
- the body is capped, from Content-Length and again while it is read.
"""

import re
from collections.abc import Collection

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_LOCAL_HOST = re.compile(r"(127\.0\.0\.1|localhost)(:\d{1,5})?", re.IGNORECASE)


def _error(status: int, error_type: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"type": error_type, "message": message}}, status_code=status)


class LocalOnlyGuard:
    def __init__(
        self,
        app: ASGIApp,
        *,
        max_body_bytes: int,
        allowed_origins: Collection[str] = (),
        allow_same_origin: bool = False,
    ) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.allowed_origins = frozenset(allowed_origins)
        self.allow_same_origin = allow_same_origin

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":  # lifespan
            await self.app(scope, receive, send)
            return
        refusal = self._refusal(Headers(scope=scope))
        if refusal is not None:
            await refusal(scope, receive, send)
            return
        await self._capped(scope, receive, send)

    def _origin_allowed(self, origin: str, host: str) -> bool:
        if origin in self.allowed_origins:
            return True
        # The Host was checked above, so this origin is a page served from this very
        # loopback address and port: the gateway's own UI. A page anywhere else cannot
        # claim it, and a rebinding page fails the Host check first.
        return self.allow_same_origin and origin == f"http://{host.lower()}"

    def _refusal(self, headers: Headers) -> JSONResponse | None:
        host = headers.get("host", "")
        if not _LOCAL_HOST.fullmatch(host):
            return _error(
                400, "invalid_request_error", "the Host header must be 127.0.0.1 or localhost"
            )
        origin = headers.get("origin")
        if origin is not None and not self._origin_allowed(origin, host):
            return _error(403, "forbidden", "requests from this origin are not allowed")
        length = headers.get("content-length")
        if length is not None:
            if not length.isdigit():
                return _error(400, "invalid_request_error", "invalid Content-Length")
            if int(length) > self.max_body_bytes:
                return self._too_large()
        return None

    def _too_large(self) -> JSONResponse:
        return _error(
            413,
            "request_too_large",
            f"the request body is larger than {self.max_body_bytes} bytes",
        )

    async def _capped(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Run the app; past the cap it sees a disconnect and the client gets a 413.

        Not an exception: the app's own error handling would turn it into a 500 first.
        Whatever the app answers after the overflow is discarded.
        """
        cap = _BodyCap(self.max_body_bytes, receive, send)
        try:
            await self.app(scope, cap.receive, cap.send)
        except Exception:
            if not cap.too_large:  # the app's failure, not ours to hide
                raise
        if cap.too_large:
            await self._too_large()(scope, receive, send)


class _BodyCap:
    """The receive and send one request's app sees while its body is counted."""

    def __init__(self, limit: int, receive: Receive, send: Send) -> None:
        self._limit = limit
        self._receive = receive
        self._send = send
        self._received = 0
        self._started = False
        self.too_large = False

    async def receive(self) -> Message:
        if self.too_large:
            return {"type": "http.disconnect"}
        message = await self._receive()
        if message["type"] == "http.request":
            self._received += len(message.get("body", b""))
            if self._received > self._limit:
                self.too_large = not self._started
                return {"type": "http.disconnect"}
        return message

    async def send(self, message: Message) -> None:
        if self.too_large:
            return
        if message["type"] == "http.response.start":
            self._started = True
        await self._send(message)
