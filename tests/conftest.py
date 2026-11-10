import importlib.abc
import importlib.machinery
import os
import socket
import sys
import threading
from collections.abc import Sequence
from types import ModuleType

import pytest
import urllib3  # noqa: F401  (see below)
from hypothesis import settings

# urllib3 probes for IPv6 at import time by binding a socket to ::1; it never connects.
# Presidio pulls urllib3 in (through requests), so without this import the first test to
# load Presidio would raise a pytest-socket warning every run, and a real attempt to reach
# the network would hide among them. Imported here, before sockets are blocked per test.

# Property tests: quick locally, deeper and reproducible in CI.
settings.register_profile("dev", max_examples=100)
settings.register_profile("ci", max_examples=500, derandomize=True, deadline=None)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "dev"))

# No test installs anything: pip (which spacy.cli.download runs) and uv stay offline, so
# a test that reaches an installer fails instead of fetching a package.
os.environ["PIP_NO_INDEX"] = "1"
os.environ["UV_OFFLINE"] = "1"


def _refuse_download(*_args: object, **_kwargs: object) -> None:
    message = "tests never download models: spacy.cli.download was called (ADR 0012)"
    raise RuntimeError(message)


class _SpacyCliLoader(importlib.abc.Loader):
    """Runs spaCy's own loader for spacy.cli, then replaces its download()."""

    def __init__(self, loader: importlib.abc.Loader) -> None:
        self._loader = loader

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> ModuleType | None:
        return self._loader.create_module(spec)

    def exec_module(self, module: ModuleType) -> None:
        self._loader.exec_module(module)
        module.__dict__["download"] = _refuse_download


class _NoSpacyDownloads(importlib.abc.MetaPathFinder):
    """Patches spacy.cli when something imports it, so the suite never imports spaCy itself."""

    def find_spec(
        self, fullname: str, path: Sequence[str] | None, target: ModuleType | None = None
    ) -> importlib.machinery.ModuleSpec | None:
        if fullname != "spacy.cli":
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path, target)
        if spec is not None and spec.loader is not None:
            spec.loader = _SpacyCliLoader(spec.loader)
        return spec


sys.meta_path.insert(0, _NoSpacyDownloads())


@pytest.fixture(autouse=True)
def _no_spacy_downloads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-applies the patch each test, in case a test replaced spacy.cli.download."""
    cli = sys.modules.get("spacy.cli")
    if cli is not None:
        monkeypatch.setattr(cli, "download", _refuse_download)


@pytest.fixture(autouse=True)
def _no_models_unless_marked(
    request: pytest.FixtureRequest,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A test not marked `models` runs as if no model were installed, wherever it runs.

    detect() runs NER whenever a model is installed (ADR 0012), so without this the
    same test could pass on a clean CI runner and fail on a developer's machine.
    """
    if request.node.get_closest_marker("models") is None:
        monkeypatch.setenv("SAFEGUARD_HOME", str(tmp_path_factory.mktemp("no-models")))


if sys.platform == "win32":
    # asyncio's Windows event loop wires itself up with a loopback TCP socketpair (Unix uses
    # AF_UNIX, which --allow-unix-socket permits). That pair never leaves the process, so
    # build it with the real socket class while pytest-socket blocks everything else.
    _REAL_SOCKET = socket.socket
    _REAL_SOCKETPAIR = socket.socketpair
    _PAIR_LOCK = threading.Lock()

    def _internal_socketpair(*args: int, **kwargs: int) -> tuple[socket.socket, socket.socket]:
        with _PAIR_LOCK:
            guarded = socket.socket
            socket.socket = _REAL_SOCKET  # type: ignore[misc]
            try:
                return _REAL_SOCKETPAIR(*args, **kwargs)
            finally:
                socket.socket = guarded  # type: ignore[misc]

    socket.socketpair = _internal_socketpair
