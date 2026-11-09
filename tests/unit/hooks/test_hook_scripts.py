"""The .claude/hooks/*.sh wrappers and their wiring in .claude/settings.json."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
HOOK_DIR = REPO / ".claude" / "hooks"
SCRIPTS = [
    "license-guard.sh",
    "bash-guard.sh",
    "secret-scan.sh",
    "no-egress.sh",
    "format.sh",
    "test-after-edit.sh",
]

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="hooks are bash scripts")


def run_hook(script: str, event: dict[str, object]) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(REPO)}
    return subprocess.run(
        [str(HOOK_DIR / script)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=120,
    )


def bash_event(command: str) -> dict[str, object]:
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }


@pytest.mark.parametrize("script", SCRIPTS)
def test_script_exists_and_is_executable(script: str) -> None:
    path = HOOK_DIR / script
    assert path.is_file()
    assert os.access(path, os.X_OK)


def test_license_guard_script_blocks_pymupdf() -> None:
    result = run_hook("license-guard.sh", bash_event("uv add PyMuPDF"))
    assert result.returncode == 2
    assert "pymupdf" in result.stderr


def test_bash_guard_script_blocks_remote_curl_and_allows_loopback() -> None:
    assert run_hook("bash-guard.sh", bash_event("curl https://example.com")).returncode == 2
    assert (
        run_hook("bash-guard.sh", bash_event("curl http://127.0.0.1:8787/health")).returncode == 0
    )


def test_no_egress_script_passes_on_a_clean_repo_file() -> None:
    event: dict[str, object] = {
        "hook_event_name": "PostToolUse",
        "tool_name": "Edit",
        "tool_input": {"file_path": str(REPO / "gateway" / "cli.py")},
    }
    assert run_hook("no-egress.sh", event).returncode == 0


def test_settings_wire_every_script_to_the_right_event() -> None:
    settings = json.loads((REPO / ".claude" / "settings.json").read_text())

    def commands(event: str, matcher: str) -> str:
        groups = [g for g in settings["hooks"][event] if g["matcher"] == matcher]
        return " ".join(h["command"] for g in groups for h in g["hooks"])

    pre_bash = commands("PreToolUse", "Bash")
    post_edit = commands("PostToolUse", "Edit|Write|MultiEdit")
    post_bash = commands("PostToolUse", "Bash")

    for script in ("license-guard.sh", "bash-guard.sh", "secret-scan.sh"):
        assert script in pre_bash
    for script in ("no-egress.sh", "format.sh", "test-after-edit.sh"):
        assert script in post_edit
    assert "license-guard.sh" in post_bash
    # Quoted project dir: a path with spaces must not split the command.
    assert '"$CLAUDE_PROJECT_DIR"/.claude/hooks/' in pre_bash


def test_settings_deny_destructive_and_secret_reading_commands() -> None:
    deny = json.loads((REPO / ".claude" / "settings.json").read_text())["permissions"]["deny"]
    assert "Bash(rm -rf *)" in deny
    assert "Read(./.env)" in deny
