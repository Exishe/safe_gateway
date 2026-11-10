"""A model pull the web UI can start and poll (ADR 0029).

This is the same pinned, checksum-verified pull as `safeguard models pull` (ADR 0006);
only the trigger differs. It lives in `gateway/cloud/` because it is the one place that
makes the call. One pull runs at a time, on a background thread, and `status()` is cheap
enough to poll.
"""

import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from gateway.cloud import hub, models

# What the UI shows next to the button; the hosts are the pinned URLs' (ADR 0006, ADR 0015).
SOURCE_NOTE = (
    "This is the one network call the gateway makes by itself. It downloads the pinned "
    "language models, about 900 MB in total, from GitHub (spaCy) and Hugging Face (GLiNER) "
    "and checks each file's SHA-256 before installing it."
)
_EXPECTED_ERRORS = (models.ModelDownloadError, OSError, ValueError)


@dataclass(frozen=True)
class ModelStep:
    key: str
    name: str
    version: str
    installed: Callable[[], bool]
    pull: Callable[[httpx.Client], Path]


def default_steps() -> list[ModelStep]:
    steps = [
        ModelStep(
            lang,
            spec.name,
            spec.version,
            lambda spec=spec: models.installed(spec),
            lambda client, spec=spec: models.pull(spec, client),
        )
        for lang, spec in models.SPACY_MODELS.items()
    ]
    gliner = hub.GLINER_MODEL
    steps.append(
        ModelStep(
            "gliner",
            gliner.name,
            gliner.revision[:7],
            lambda: hub.installed(gliner),
            lambda client: hub.pull(gliner, client),
        )
    )
    return steps


class ModelPuller:
    def __init__(
        self,
        steps: Sequence[ModelStep] | None = None,
        *,
        client_factory: Callable[[], httpx.Client] = models.open_client,
    ) -> None:
        self._steps = list(default_steps() if steps is None else steps)
        self._client_factory = client_factory
        self._lock = threading.Lock()
        self._state = "idle"  # idle, running, done, failed
        self._current: str | None = None
        self._failed: str | None = None
        self._error: str | None = None
        self._thread: threading.Thread | None = None

    def status(self) -> dict[str, Any]:
        with self._lock:
            state, current, failed, error = self._state, self._current, self._failed, self._error
        rows = [
            {
                "key": step.key,
                "name": step.name,
                "version": step.version,
                "status": self._row_status(step, current, failed),
            }
            for step in self._steps
        ]
        body: dict[str, Any] = {"state": state, "current": current, "error": error, "models": rows}
        body["source_note"] = SOURCE_NOTE
        return body

    @staticmethod
    def _row_status(step: ModelStep, current: str | None, failed: str | None) -> str:
        if step.key == current:
            return "downloading"
        if step.key == failed:
            return "failed"
        return "installed" if step.installed() else "missing"

    def start(self) -> bool:
        """Begin a pull; False when one is already running."""
        first = next((step.key for step in self._steps if not step.installed()), None)
        with self._lock:
            if self._state == "running":
                return False
            self._state, self._current, self._failed, self._error = "running", first, None, None
            # Started under the lock: the thread's first lock request just waits for it.
            self._thread = threading.Thread(target=self._run, name="models-pull", daemon=True)
            self._thread.start()
        return True

    def wait(self, timeout: float) -> None:
        """Block until the running pull ends (tests and shutdown)."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _set_current(self, key: str | None) -> None:
        with self._lock:
            self._current = key

    def _run(self) -> None:
        try:
            with self._client_factory() as client:
                for step in self._steps:
                    if step.installed():
                        continue
                    self._set_current(step.key)
                    step.pull(client)
        except _EXPECTED_ERRORS as error:
            with self._lock:
                self._failed, self._current = self._current, None
                self._state, self._error = "failed", str(error)
            return
        with self._lock:
            self._state, self._current = "done", None
