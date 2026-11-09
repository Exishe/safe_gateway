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


def test_settings_allow_only_specific_commands_without_a_prompt() -> None:
    allow = json.loads((REPO / ".claude" / "settings.json").read_text())["permissions"]["allow"]
    # A wildcard over a whole tool runs anything: `uv run bash -c ...`, `git -c alias.x=!...`.
    for broad in ("Bash(uv run *)", "Bash(git *)", "Bash(gh *)", "Bash(uv *)", "Bash(*)"):
        assert broad not in allow
    assert not [rule for rule in allow if rule.startswith("Bash(gh ")]
    for specific in (
        "Bash(uv run pytest *)",
        "Bash(uv run ruff *)",
        "Bash(uv run ty *)",
        "Bash(uv run python -m tools.*)",
        "Bash(uv run safeguard *)",
        "Bash(git status*)",
        "Bash(git diff*)",
        "Bash(git log*)",
        "Bash(git add *)",
        "Bash(git commit *)",
        "Bash(git push origin claude/*)",
        "Bash(git push -u origin claude/*)",
    ):
        assert specific in allow


def test_settings_deny_publishing_and_git_config_overrides() -> None:
    deny = json.loads((REPO / ".claude" / "settings.json").read_text())["permissions"]["deny"]
    for rule in (
        "Bash(gh gist *)",
        "Bash(gh api *)",
        "Bash(gh repo edit *)",
        "Bash(git -c *)",
        "Bash(git push http*)",
    ):
        assert rule in deny


# ---- review fixes (Important #3, #6)


def test_wrapper_blocks_when_uv_is_missing() -> None:
    env = {"PATH": "/usr/bin:/bin", "CLAUDE_PROJECT_DIR": str(REPO), "HOME": os.environ["HOME"]}
    result = subprocess.run(
        [str(HOOK_DIR / "bash-guard.sh")],
        input=json.dumps(bash_event("ls")),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 2
    assert "uv" in result.stderr


def test_guards_fail_closed_on_timeouts_and_crashes() -> None:
    settings = json.loads((REPO / ".claude" / "settings.json").read_text())
    by_script = {
        hook["command"].rsplit("/", 1)[-1] + f"@{event}": hook.get("onFailure", "continue")
        for event, groups in settings["hooks"].items()
        for group in groups
        for hook in group["hooks"]
    }
    for script in ("bash-guard.sh", "license-guard.sh", "secret-scan.sh"):
        assert by_script[f"{script}@PreToolUse"] == "block"
    for script in ("no-egress.sh", "test-after-edit.sh", "license-guard.sh"):
        assert by_script[f"{script}@PostToolUse"] == "block"


def test_pre_commit_passes_non_ascii_names_through_intact(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)  # noqa: S607
    (repo / "Договор.txt").write_text("text\n")
    subprocess.run(["git", "add", "Договор.txt"], cwd=repo, check=True)  # noqa: S607

    # A stand-in uv records how the hook calls each tool.
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "uv.log"
    fake_uv = fake_bin / "uv"
    fake_uv.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$UV_LOG"\n')
    fake_uv.chmod(0o755)
    env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}", "UV_LOG": str(log)}

    result = subprocess.run(
        [str(REPO / ".githooks" / "pre-commit")], cwd=repo, env=env, check=False
    )

    assert result.returncode == 0
    detect_calls = [line for line in log.read_text().splitlines() if "detect-secrets" in line]
    assert detect_calls
    assert "Договор.txt" in detect_calls[0]


def test_mcp_config_loads_without_a_github_token() -> None:
    text = (REPO / ".mcp.json").read_text()
    assert "${GITHUB_PAT}" not in text
    assert "${GITHUB_PAT:-}" in text
