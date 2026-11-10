"""GET /models and POST /models/pull: status and a guarded, one-at-a-time pull (ADR 0029)."""

import threading
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

from gateway.cloud.model_jobs import ModelPuller, ModelStep
from gateway.cloud.providers import Provider
from gateway.proxy.app import create_app
from tests.proxy_fakes import FakeUpstream


def _never(_: httpx.Request) -> httpx.Response:
    msg = "the model routes must not call the upstream"
    raise AssertionError(msg)


class _Disk:
    def __init__(self) -> None:
        self.have = False
        self.gate = threading.Event()
        self.pulls = 0

    def step(self) -> ModelStep:
        return ModelStep("en", "en_core_web_lg", "3.8.0", lambda: self.have, self._pull)

    def _pull(self, _client: httpx.Client) -> Path:
        self.gate.wait(5)
        self.pulls += 1
        self.have = True
        return Path("/nowhere")


@pytest.fixture
def disk() -> _Disk:
    return _Disk()


@pytest.fixture
def puller(disk: _Disk) -> ModelPuller:
    return ModelPuller([disk.step()], client_factory=httpx.Client)


def _client(puller: ModelPuller) -> TestClient:
    app = create_app(FakeUpstream(Provider.OLLAMA, _never).upstream(), models=puller)
    return TestClient(app, base_url="http://127.0.0.1:8787")


def test_get_models_reports_status(puller: ModelPuller) -> None:
    body = _client(puller).get("/models").json()
    assert body["state"] == "idle"
    assert body["models"] == [
        {"key": "en", "name": "en_core_web_lg", "version": "3.8.0", "status": "missing"}
    ]
    assert "hugging face" in body["source_note"].lower()


def test_post_starts_a_pull_and_a_poll_sees_it_finish(puller: ModelPuller, disk: _Disk) -> None:
    client = _client(puller)
    disk.gate.set()
    reply = client.post("/models/pull")
    assert reply.status_code == 202
    puller.wait(5)
    assert client.get("/models").json()["state"] == "done"
    assert disk.pulls == 1


def test_a_second_post_while_one_runs_is_a_conflict(puller: ModelPuller, disk: _Disk) -> None:
    client = _client(puller)
    assert client.post("/models/pull").status_code == 202
    second = client.post("/models/pull")
    assert second.status_code == 409
    assert second.json()["error"]["type"] == "conflict"
    disk.gate.set()
    puller.wait(5)
    assert disk.pulls == 1


def test_a_foreign_host_cannot_start_a_pull(puller: ModelPuller, disk: _Disk) -> None:
    client = _client(puller)
    reply = client.post("/models/pull", headers={"host": "evil.example"})
    assert reply.status_code == 400
    assert disk.pulls == 0
    assert puller.status()["state"] == "idle"


def test_a_foreign_origin_cannot_start_a_pull(puller: ModelPuller) -> None:
    reply = _client(puller).post("/models/pull", headers={"origin": "https://evil.example"})
    assert reply.status_code == 403
    assert puller.status()["state"] == "idle"


def test_a_foreign_origin_cannot_read_status(puller: ModelPuller) -> None:
    reply = _client(puller).get("/models", headers={"origin": "https://evil.example"})
    assert reply.status_code == 403
