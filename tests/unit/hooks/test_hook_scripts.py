"""The .claude/hooks/*.sh wrappers and their wiring in .claude/settings.json."""

import json
import os
import shutil
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
    "run-hook.sh",  # the shared runner every wrapper execs
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


CONFLICTED_LOCK = "version = 1\n<<<<<<< HEAD\nrevision = 2\n=======\nrevision = 3\n>>>>>>> x\n"


def project_copy(tmp_path: Path, *, with_venv: bool) -> Path:
    """The guard code, the hooks and a uv.lock with merge-conflict markers."""
    project = tmp_path / "project"
    skip = shutil.ignore_patterns("__pycache__")
    for name in ("tools", "gateway"):
        shutil.copytree(REPO / name, project / name, ignore=skip)
    shutil.copytree(HOOK_DIR, project / ".claude" / "hooks")
    shutil.copy2(REPO / "pyproject.toml", project / "pyproject.toml")
    (project / "uv.lock").write_text(CONFLICTED_LOCK)
    if with_venv:
        if sys.prefix == sys.base_prefix:
            pytest.skip("needs the tests to run from the project's virtual environment")
        (project / ".venv").symlink_to(sys.prefix, target_is_directory=True)
    return project


def run_project_hook(
    project: Path, script: str, command: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(project / ".claude" / "hooks" / script)],
        input=json.dumps(bash_event(command)),
        capture_output=True,
        text=True,
        env={**(env or os.environ), "CLAUDE_PROJECT_DIR": str(project)},
        check=False,
        timeout=120,
    )


def test_wrapper_blocks_when_there_is_neither_a_venv_nor_uv(tmp_path: Path) -> None:
    project = project_copy(tmp_path, with_venv=False)
    env = {"PATH": "/usr/bin:/bin", "HOME": os.environ["HOME"]}
    result = run_project_hook(project, "bash-guard.sh", "ls", env)
    assert result.returncode == 2
    assert "uv" in result.stderr


@pytest.mark.parametrize("script", ["bash-guard.sh", "license-guard.sh", "secret-scan.sh"])
def test_guards_still_run_when_uv_lock_does_not_parse(tmp_path: Path, script: str) -> None:
    # `uv run` parses uv.lock even with --frozen: the guards must not depend on it, or a
    # conflicted lock blocks every command, including the one that repairs it.
    project = project_copy(tmp_path, with_venv=True)

    repair = run_project_hook(project, script, "git checkout -- uv.lock")
    assert repair.returncode == 0, repair.stderr

    if script == "bash-guard.sh":
        assert run_project_hook(project, script, "rm -rf /").returncode == 2


@pytest.mark.parametrize("script", ["bash-guard.sh", "license-guard.sh", "secret-scan.sh"])
def test_guards_still_run_when_an_engine_module_does_not_parse(tmp_path: Path, script: str) -> None:
    # scan_pii imports the engine. If the guards imported it up front, a merge conflict in
    # an engine module would block every command, including the one that repairs it.
    project = project_copy(tmp_path, with_venv=True)
    detect = project / "gateway" / "engine" / "detect.py"
    detect.write_text(detect.read_text() + "\n<<<<<<< HEAD\nx = 1\n=======\nx = 2\n>>>>>>> y\n")

    repair = run_project_hook(project, script, "git checkout -- gateway/engine/detect.py")
    assert repair.returncode == 0, repair.stderr

    if script == "bash-guard.sh":
        assert run_project_hook(project, script, "rm -rf /").returncode == 2
    if script == "secret-scan.sh":
        # Committing still needs the staged PII scan, so it fails closed.
        assert run_project_hook(project, script, "git commit -m wip").returncode == 2


def test_a_crash_before_the_guard_starts_still_blocks(tmp_path: Path) -> None:
    project = project_copy(tmp_path, with_venv=True)
    (project / "tools" / "hooks.py").write_text("raise ImportError('broken checkout')\n")
    result = run_project_hook(project, "bash-guard.sh", "ls")
    assert result.returncode == 2


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
