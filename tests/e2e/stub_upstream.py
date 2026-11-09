"""A stand-in for an OpenAI-shaped model server, for the browser tests (ADR 0024).

Run: `python -m tests.e2e.stub_upstream PORT LOG_FILE`. It listens on 127.0.0.1
only, answers `POST /v1/chat/completions` by streaming "Noted: <the last user
message as received>" plus one placeholder it makes up, and appends every request
body it receives to LOG_FILE, so the test can check that no personal data reached it.
"""

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

INVENTED = "[PERSON_99]"  # no session holds it: the UI must flag it as unresolved


def _chunk(delta: dict[str, str], finish: str | None = None) -> bytes:
    payload = {
        "id": "chatcmpl-stub",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "stub-model",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(payload)}\n\n".encode()


def _pieces(text: str, size: int = 5) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


class _Handler(BaseHTTPRequestHandler):
    log_file: Path

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002  (the base class's name)
        pass  # request lines would only add noise to the test output

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("content-length", "0")))
        with self.log_file.open("ab") as log:
            log.write(body + b"\n")
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        request = json.loads(body)
        user = [m["content"] for m in request["messages"] if m["role"] == "user"][-1]
        answer = f"Noted: {user} See also {INVENTED}."
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        self.wfile.write(_chunk({"role": "assistant", "content": ""}))
        for piece in _pieces(answer):
            self.wfile.write(_chunk({"content": piece}))
            self.wfile.flush()
        self.wfile.write(_chunk({}, "stop"))
        self.wfile.write(b"data: [DONE]\n\n")


def main() -> None:
    port, log_file = int(sys.argv[1]), Path(sys.argv[2])
    _Handler.log_file = log_file
    server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
