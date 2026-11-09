import os

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
