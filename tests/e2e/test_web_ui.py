"""The web UI in a real browser, against `safeguard serve` and a stub upstream (ADR 0024).

Opt in with `SAFEGUARD_E2E=1` after `npm run build` in apps/web. The browser part is
Node Playwright (tests/e2e/web/flows.mjs) with the Chromium already on the machine;
this test starts the servers, hands it Faker data, and checks afterwards that none
of that data reached the upstream. It opens no socket itself: the script waits for
the servers.
"""

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.proxy_fakes import PersonalData

pytestmark = pytest.mark.e2e

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "apps" / "web" / "dist"
FLOWS = Path(__file__).parent / "web" / "flows.mjs"
SCREENSHOTS = Path(
    os.environ.get("SAFEGUARD_E2E_SCREENSHOTS", ROOT / "tests" / "e2e" / "artifacts" / "web")
)
GATEWAY_PORT = int(os.environ.get("SAFEGUARD_E2E_PORT", "18787"))
STUB_PORT = int(os.environ.get("SAFEGUARD_E2E_STUB_PORT", "18788"))
MODEL = "stub-model"
DATA = PersonalData.generate(seed=6)

if os.environ.get("SAFEGUARD_E2E") != "1":
    pytest.skip("browser tests run with SAFEGUARD_E2E=1", allow_module_level=True)
if not (DIST / "index.html").is_file():
    pytest.skip("build the UI first: npm run build in apps/web", allow_module_level=True)


def _program(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        pytest.skip(f"{name} is not installed")
    return path


@pytest.fixture
def servers(tmp_path: Path) -> Iterator[Path]:
    """The stub upstream and the gateway; yields the stub's request log."""
    home = tmp_path / "home"
    home.mkdir()
    gazetteer = home / "clients.csv"
    gazetteer.write_text(f"value,entity_type\n{DATA.name},PERSON\n", encoding="utf-8")
    (home / "config.toml").write_text(
        f'provider = "ollama"\n'
        f'base_url = "http://127.0.0.1:{STUB_PORT}/v1"\n'
        f'model = "{MODEL}"\n'
        f'gazetteer = "{gazetteer.as_posix()}"\n',
        encoding="utf-8",
    )
    log = tmp_path / "upstream.log"
    log.touch()
    env = {**os.environ, "SAFEGUARD_HOME": str(home)}
    stub = subprocess.Popen(
        [sys.executable, "-m", "tests.e2e.stub_upstream", str(STUB_PORT), str(log)],
        cwd=ROOT,
        env=env,
    )
    serve = [_program("uv"), "run", "--frozen", "safeguard", "serve", "--port", str(GATEWAY_PORT)]
    gateway = subprocess.Popen(serve, cwd=ROOT, env=env)
    try:
        yield log
    finally:
        for process in (gateway, stub):
            process.terminate()
            process.wait(timeout=20)


def test_every_flow_in_the_browser(servers: Path, tmp_path: Path) -> None:
    data = tmp_path / "data.json"
    data.write_text(
        json.dumps({"name": DATA.name, "email": DATA.email, "card": DATA.card, "iban": DATA.iban}),
        encoding="utf-8",
    )
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "GATEWAY_URL": f"http://127.0.0.1:{GATEWAY_PORT}",
        "STUB_URL": f"http://127.0.0.1:{STUB_PORT}",
        "MODEL": MODEL,
        "DATA_FILE": str(data),
        "SCREENSHOTS": str(SCREENSHOTS),
    }
    result = subprocess.run(  # noqa: PLW1510  (the return code is asserted below, with the output)
        [_program("node"), str(FLOWS)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    print(result.stdout)  # noqa: T201  (the step log, shown with -s)
    assert result.returncode == 0, result.stdout + result.stderr

    sent = servers.read_text(encoding="utf-8")
    bodies = [json.loads(line) for line in sent.splitlines() if line]
    # Two sends: all masked, then with the email switched off in the UI.
    assert len(bodies) == 2
    assert {body["model"] for body in bodies} == {MODEL}
    # The second send held the email in clear; the gateway masked it again on the way out.
    for value in [DATA.name, DATA.email, DATA.card, DATA.iban]:
        assert value not in sent, "a value reached the upstream unmasked"
