"""The browser extension in a real Chromium, against `safeguard serve` (ADR 0026).

Opt in with `SAFEGUARD_E2E=1` after `npm run build` in apps/extension. The browser
part is Node Playwright (tests/e2e/extension/flows.mjs) on local fixture pages that
stand in for the chat sites; no live site is contacted. The extension talks to
127.0.0.1:8787 only, so the gateway runs on that port, with a temporary
SAFEGUARD_HOME, the test-only file keyring and a stub upstream. The extension's id is
derived from its directory, as Chrome derives it for an unpacked extension, and put in
`[proxy] extension_ids` before the gateway starts.
"""

import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.e2e.helpers import program, server_env
from tests.proxy_fakes import PersonalData

pytestmark = pytest.mark.e2e

ROOT = Path(__file__).resolve().parents[2]
EXTENSION = (ROOT / "apps" / "extension" / "dist" / "chrome").resolve()
FLOWS = Path(__file__).parent / "extension" / "flows.mjs"
SCREENSHOTS = Path(
    os.environ.get(
        "SAFEGUARD_E2E_EXTENSION_SCREENSHOTS", ROOT / "tests" / "e2e" / "artifacts" / "extension"
    )
)
GATEWAY_PORT = 8787  # the extension's one host permission
STUB_PORT = int(os.environ.get("SAFEGUARD_E2E_STUB_PORT", "18788"))
DATA = PersonalData.generate(seed=7)

if os.environ.get("SAFEGUARD_E2E") != "1":
    pytest.skip("browser tests run with SAFEGUARD_E2E=1", allow_module_level=True)
if not (EXTENSION / "manifest.json").is_file():
    pytest.skip(
        "build the extension first: npm run build in apps/extension", allow_module_level=True
    )


def unpacked_extension_id(path: Path) -> str:
    """The id Chromium gives an unpacked extension: sha256 of its path, hex in a to p."""
    digest = hashlib.sha256(str(path).encode()).hexdigest()[:32]
    return digest.translate(str.maketrans("0123456789abcdef", "abcdefghijklmnop"))


@pytest.fixture
def servers(tmp_path: Path) -> Iterator[tuple[Path, subprocess.Popen[bytes]]]:
    home = tmp_path / "home"
    home.mkdir()
    gazetteer = home / "clients.csv"
    gazetteer.write_text(f"value,entity_type\n{DATA.name},PERSON\n", encoding="utf-8")
    (home / "config.toml").write_text(
        f'provider = "ollama"\n'
        f'base_url = "http://127.0.0.1:{STUB_PORT}/v1"\n'
        f'model = "stub-model"\n'
        f'gazetteer = "{gazetteer.as_posix()}"\n'
        f"[proxy]\n"
        f'extension_ids = ["{unpacked_extension_id(EXTENSION)}"]\n',
        encoding="utf-8",
    )
    log = tmp_path / "upstream.log"
    log.touch()
    env = server_env(tmp_path, home)
    stub = subprocess.Popen(
        [sys.executable, "-m", "tests.e2e.stub_upstream", str(STUB_PORT), str(log)],
        cwd=ROOT,
        env=env,
    )
    # The gateway itself, not `uv run`, so the test can stop it to see the extension
    # fail closed.
    safeguard = Path(sys.executable).with_name("safeguard")
    serve = [str(safeguard), "serve", "--port", str(GATEWAY_PORT)]
    gateway = subprocess.Popen(serve, cwd=ROOT, env=env)
    try:
        yield log, gateway
    finally:
        for process in (gateway, stub):
            process.terminate()
            process.wait(timeout=20)


def test_the_extension_in_the_browser(
    servers: tuple[Path, subprocess.Popen[bytes]], tmp_path: Path
) -> None:
    upstream_log, gateway = servers
    data = tmp_path / "data.json"
    data.write_text(
        json.dumps({"name": DATA.name, "email": DATA.email, "card": DATA.card}), encoding="utf-8"
    )
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "EXTENSION_DIR": str(EXTENSION),
        "EXTENSION_ID": unpacked_extension_id(EXTENSION),
        "GATEWAY_PID": str(gateway.pid),
        "DATA_FILE": str(data),
        "SCREENSHOTS": str(SCREENSHOTS),
    }
    result = subprocess.run(  # noqa: PLW1510  (the return code is asserted below, with the output)
        [program("node"), str(FLOWS)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    print(result.stdout)  # noqa: T201  (the step log, shown with -s)
    assert result.returncode == 0, result.stdout + result.stderr
    # The extension masks through /mask only; it never calls a chat route.
    assert upstream_log.read_text(encoding="utf-8") == ""
