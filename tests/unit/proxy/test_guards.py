from collections.abc import Iterator

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from gateway.proxy.guards import LocalOnlyGuard

CAP = 64
ORIGIN = "chrome-extension://abcdefghijklmnop"


async def _echo(request: Request) -> JSONResponse:
    return JSONResponse({"size": len(await request.body())})


def _client(origins: tuple[str, ...] = ()) -> TestClient:
    app = Starlette(routes=[Route("/echo", _echo, methods=["GET", "POST"])])
    guarded = LocalOnlyGuard(app, max_body_bytes=CAP, allowed_origins=origins)
    return TestClient(guarded, base_url="http://127.0.0.1:8787")


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.1:8787", "localhost", "LOCALHOST:1"])
def test_loopback_host_headers_pass(host: str) -> None:
    response = _client().get("/echo", headers={"host": host})
    assert response.status_code == 200


@pytest.mark.parametrize(
    "host", ["evil.example", "evil.example:8787", "127.0.0.1.evil.example", "", "[::1]:8787"]
)
def test_other_host_headers_are_refused(host: str) -> None:
    response = _client().get("/echo", headers={"host": host})
    assert response.status_code == 400
    assert "Host" in response.json()["error"]["message"]


def test_origin_not_on_the_list_is_refused() -> None:
    response = _client().post("/echo", headers={"origin": "https://evil.example"}, content=b"x")
    assert response.status_code == 403
    assert response.json()["error"]["type"] == "forbidden"


def test_origin_on_the_list_passes() -> None:
    response = _client((ORIGIN,)).post("/echo", headers={"origin": ORIGIN}, content=b"x")
    assert response.status_code == 200


def test_body_at_the_cap_passes() -> None:
    response = _client().post("/echo", content=b"x" * CAP)
    assert response.json() == {"size": CAP}


def test_content_length_over_the_cap_is_413() -> None:
    response = _client().post("/echo", content=b"x" * (CAP + 1))
    assert response.status_code == 413
    assert "64" in response.json()["error"]["message"]


def test_bad_content_length_is_400() -> None:
    response = _client().post("/echo", headers={"content-length": "lots"}, content=b"x")
    assert response.status_code == 400


def _chunks() -> Iterator[bytes]:
    for _ in range(10):
        yield b"x" * 10


def test_chunked_body_over_the_cap_is_413() -> None:
    response = _client().post("/echo", content=_chunks())
    assert response.status_code == 413


def test_lifespan_passes_through() -> None:
    with _client() as client:  # runs startup and shutdown through the guard
        assert client.get("/echo").status_code == 200


def _same_origin_client(*, allow: bool) -> TestClient:
    app = Starlette(routes=[Route("/echo", _echo, methods=["GET", "POST"])])
    guarded = LocalOnlyGuard(app, max_body_bytes=CAP, allow_same_origin=allow)
    return TestClient(guarded, base_url="http://127.0.0.1:8787")


@pytest.mark.parametrize(
    ("host", "origin"),
    [
        ("127.0.0.1:8787", "http://127.0.0.1:8787"),
        ("localhost:8787", "http://localhost:8787"),
        ("LOCALHOST:8787", "http://localhost:8787"),
    ],
)
def test_the_served_ui_may_call_its_own_origin(host: str, origin: str) -> None:
    client = _same_origin_client(allow=True)
    response = client.post("/echo", headers={"host": host, "origin": origin}, content=b"x")
    assert response.status_code == 200


@pytest.mark.parametrize(
    "origin",
    [
        "http://127.0.0.1:9999",  # another server on this machine
        "https://127.0.0.1:8787",
        "http://localhost:8787",  # not the Host the request was sent to
        "http://evil.example:8787",
        "null",
        "http://127.0.0.1:8787/",
    ],
)
def test_other_origins_stay_refused_when_the_ui_is_served(origin: str) -> None:
    client = _same_origin_client(allow=True)
    headers = {"host": "127.0.0.1:8787", "origin": origin}
    response = client.post("/echo", headers=headers, content=b"x")
    assert response.status_code == 403


def test_same_origin_is_refused_when_no_ui_is_served() -> None:
    client = _same_origin_client(allow=False)
    headers = {"host": "127.0.0.1:8787", "origin": "http://127.0.0.1:8787"}
    response = client.post("/echo", headers=headers, content=b"x")
    assert response.status_code == 403
