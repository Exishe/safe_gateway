"""Hugging Face libraries are told to stay offline at process start (security review M7).

`transformers` and `huggingface_hub` read HF_HUB_OFFLINE and TRANSFORMERS_OFFLINE
when they are imported, and Presidio imports them before GLiNER's loader ran, so
setting them there was too late. Each test runs a fresh interpreter with the
variables removed, as a user's shell would start it.
"""

import json
import os
import subprocess
import sys
from typing import Any

OFFLINE = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE")
HEAVY = ("transformers", "huggingface_hub", "presidio_analyzer", "gliner", "datasets", "spacy")

# Records the variables at the moment the first engine module or Hugging Face
# library is imported.
WATCH = f"""
import importlib.abc, json, os, sys
NAMES = {OFFLINE!r}
seen = {{}}

class Watch(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        top = name.split(".")[0]
        key = "gateway.engine" if name.startswith("gateway.engine") else top
        if key in ("gateway.engine", "transformers", "huggingface_hub") and key not in seen:
            seen[key] = [os.environ.get(n) for n in NAMES]
        return None

sys.meta_path.insert(0, Watch())
"""


def _run(code: str) -> dict[str, Any]:
    env = {k: v for k, v in os.environ.items() if k not in OFFLINE}
    result = subprocess.run(
        [sys.executable, "-c", WATCH + code], capture_output=True, text=True, check=True, env=env
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_python_m_gateway_sets_them_before_any_engine_import() -> None:
    code = """
import runpy
sys.argv = ["gateway", "--version"]
try:
    runpy.run_module("gateway", run_name="__main__", alter_sys=True)
except SystemExit:
    pass
print(json.dumps({"seen": seen, "env": [os.environ.get(n) for n in NAMES]}))
"""
    out = _run(code)
    assert out["seen"]["gateway.engine"] == ["1", "1", "1"]
    assert out["env"] == ["1", "1", "1"]


def test_cli_main_sets_them_before_any_hugging_face_import() -> None:
    # The `safeguard` script imports gateway.cli, then calls main(): importing the
    # command line must not import a Hugging Face library or Presidio.
    code = f"""
from gateway import cli
heavy = sorted(m for m in sys.modules if m.split(".")[0] in {HEAVY!r})
try:
    cli.main(["--version"])
except SystemExit:
    pass
print(json.dumps({{"heavy": heavy, "env": [os.environ.get(n) for n in NAMES]}}))
"""
    out = _run(code)
    assert out["heavy"] == []
    assert out["env"] == ["1", "1", "1"]
