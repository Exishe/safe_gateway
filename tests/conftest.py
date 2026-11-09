import os

from hypothesis import settings

# Property tests: quick locally, deeper and reproducible in CI.
settings.register_profile("dev", max_examples=100)
settings.register_profile("ci", max_examples=500, derandomize=True, deadline=None)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "dev"))
