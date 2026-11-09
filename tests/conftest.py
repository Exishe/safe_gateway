import os

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
