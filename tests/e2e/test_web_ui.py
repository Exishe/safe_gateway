"""The web UI in a real browser, against `safeguard serve` and a stub upstream (ADR 0024).

Opt in with `SAFEGUARD_E2E=1` after `npm run build` in apps/web. The browser part is
Node Playwright (tests/e2e/web/flows.mjs) with the Chromium already on the machine;
this test starts the servers, hands it Faker data, and checks afterwards that none
of that data reached the upstream. It opens no socket itself: the script waits for
the servers.
"""

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


@pytest.fixture
def servers(tmp_path: Path) -> Iterator[tuple[Path, dict[str, str]]]:
    """The stub upstream and the gateway; yields the stub's request log and their env."""
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
    env = server_env(tmp_path, home)
    stub = subprocess.Popen(
        [sys.executable, "-m", "tests.e2e.stub_upstream", str(STUB_PORT), str(log)],
        cwd=ROOT,
        env=env,
    )
    serve = [program("uv"), "run", "--frozen", "safeguard", "serve", "--port", str(GATEWAY_PORT)]
    gateway = subprocess.Popen(serve, cwd=ROOT, env=env)
    try:
        yield log, env
    finally:
        for process in (gateway, stub):
            process.terminate()
            process.wait(timeout=20)


def test_every_flow_in_the_browser(servers: tuple[Path, dict[str, str]], tmp_path: Path) -> None:
    upstream_log, server_env = servers
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
        [program("node"), str(FLOWS)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    print(result.stdout)  # noqa: T201  (the step log, shown with -s)
    assert result.returncode == 0, result.stdout + result.stderr

    sent = upstream_log.read_text(encoding="utf-8")
    bodies = [json.loads(line) for line in sent.splitlines() if line]
    # Two sends: all masked, then with the email switched off in the UI.
    assert len(bodies) == 2
    assert {body["model"] for body in bodies} == {MODEL}
    # The second send held the email in clear; the gateway masked it again on the way out.
    for value in [DATA.name, DATA.email, DATA.card, DATA.iban]:
        assert value not in sent, "a value reached the upstream unmasked"
    # Sent in the /mask session (ADR 0025): the model sees typed placeholders, never a
    # [LITERAL_n] standing in for one, and never the session id.
    for body in bodies:
        text = json.dumps(body)
        for placeholder in ["[PERSON_1]", "[EMAIL_1]", "[CREDIT_CARD_1]", "[IBAN_1]"]:
            assert placeholder in text
        assert "LITERAL" not in text
        assert "session" not in text.lower()

    # The log the browser read is the real one: the command line verifies it too.
    verify = subprocess.run(  # noqa: PLW1510  (asserted below)
        [program("uv"), "run", "--frozen", "safeguard", "audit", "verify"],
        cwd=ROOT,
        env=server_env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert verify.returncode == 0, verify.stdout + verify.stderr
