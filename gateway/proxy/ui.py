"""The built web UI (`apps/web/dist`), served from `/` with strict headers (ADR 0024).

The page talks only to the gateway itself, so the Content-Security-Policy allows
nothing but its own origin: no inline script, no remote font, no other host to
send data to, and no framing by another page.
"""

from pathlib import Path

from starlette.datastructures import MutableHeaders
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "form-action 'none'; "
    "frame-ancestors 'none'"
)
_HEADERS = {
    "content-security-policy": CONTENT_SECURITY_POLICY,
    "x-frame-options": "DENY",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "cross-origin-opener-policy": "same-origin",
    "cross-origin-resource-policy": "same-origin",
}


def has_ui(ui_dir: Path | None) -> bool:
    return ui_dir is not None and (ui_dir / "index.html").is_file()


class UiFiles:
    """StaticFiles for the build directory, every response with the headers above."""

    def __init__(self, ui_dir: Path) -> None:
        self._files: ASGIApp = StaticFiles(directory=ui_dir, html=True)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in _HEADERS.items():
                    headers[name] = value
            await send(message)

        await self._files(scope, receive, send_with_headers)
