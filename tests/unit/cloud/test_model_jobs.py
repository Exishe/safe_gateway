"""The one-at-a-time model pull behind POST /models/pull (ADR 0029). No network."""

import threading
from pathlib import Path

import httpx
import pytest

from gateway.cloud.model_jobs import ModelPuller, ModelStep
from gateway.cloud.models import ModelDownloadError


class _Fake:
    """A step whose `pull` records the call and installs nothing on disk."""

    def __init__(self, key: str, *, has: bool = False, error: Exception | None = None) -> None:
        self.key = key
        self.has = has
        self.error = error
        self.pulled = 0
        self.gate: threading.Event | None = None

    def step(self) -> ModelStep:
        return ModelStep(self.key, f"{self.key}-model", "1", self._installed, self._pull)

    def _installed(self) -> bool:
        return self.has

    def _pull(self, _client: httpx.Client) -> Path:
        if self.gate is not None:
            self.gate.wait(5)
        self.pulled += 1
        if self.error is not None:
            raise self.error
        self.has = True
        return Path("/nowhere")


def _puller(*fakes: _Fake) -> ModelPuller:
    # The factory builds an unused client: no request is ever sent in these tests.
    return ModelPuller([f.step() for f in fakes], client_factory=httpx.Client)


def test_status_before_any_pull_lists_each_model_as_missing_or_installed() -> None:
    puller = _puller(_Fake("en", has=True), _Fake("ru"))
    status = puller.status()
    assert status["state"] == "idle"
    assert [(m["key"], m["status"]) for m in status["models"]] == [
        ("en", "installed"),
        ("ru", "missing"),
    ]
    assert status["error"] is None


def test_a_pull_downloads_only_what_is_missing_and_finishes() -> None:
    en, ru = _Fake("en", has=True), _Fake("ru")
    puller = _puller(en, ru)
    assert puller.start() is True
    puller.wait(5)
    assert (en.pulled, ru.pulled) == (0, 1)
    status = puller.status()
    assert status["state"] == "done"
    assert all(m["status"] == "installed" for m in status["models"])


def test_a_second_start_while_running_is_refused() -> None:
    slow = _Fake("en")
    gate = threading.Event()
    slow.gate = gate
    puller = _puller(slow)
    assert puller.start() is True
    status = puller.status()
    assert status["state"] == "running"
    assert status["current"] == "en"
    assert [m["status"] for m in status["models"]] == ["downloading"]
    assert puller.start() is False
    gate.set()
    puller.wait(5)
    assert slow.pulled == 1


def test_a_failed_download_is_reported_and_stops_the_run() -> None:
    bad = _Fake("en", error=ModelDownloadError("en_core_web_lg: checksum mismatch"))
    later = _Fake("ru")
    puller = _puller(bad, later)
    puller.start()
    puller.wait(5)
    status = puller.status()
    assert status["state"] == "failed"
    assert status["error"] == "en_core_web_lg: checksum mismatch"
    assert status["models"][0]["status"] == "failed"
    assert later.pulled == 0


@pytest.mark.parametrize("error", [OSError("disk full"), ValueError("bad pin")])
def test_other_expected_errors_are_reported_not_raised(error: Exception) -> None:
    puller = _puller(_Fake("en", error=error))
    puller.start()
    puller.wait(5)
    assert puller.status()["state"] == "failed"
    assert puller.status()["error"] == str(error)


def test_a_failed_run_can_be_started_again() -> None:
    flaky = _Fake("en", error=OSError("network down"))
    puller = _puller(flaky)
    puller.start()
    puller.wait(5)
    flaky.error = None
    assert puller.start() is True
    puller.wait(5)
    assert puller.status()["state"] == "done"
    assert puller.status()["error"] is None
