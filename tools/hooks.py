"""Claude Code hooks for this repository. The .claude/hooks/*.sh files call this module.

Contract (Claude Code): the hook event arrives as JSON on stdin. Exit 0 lets the
tool call proceed; exit 2 blocks a PreToolUse call, or, after PostToolUse, sends
the reason on stderr back to Claude so it can fix what it just did.

Failure policy (ADR 0010): a guard that crashes exits 2, so a broken guard stops
the action instead of waving it through. The wrappers exit 2 when uv is missing,
and settings.json marks every guard `onFailure: block` for timeouts. Only the
format hook fails open.

Run: `python -m tools.hooks <hook-name> < event.json`
"""

import argparse
import contextlib
import io
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

from tools import check_licenses, check_no_egress, scan_pii
from tools.shell import simple_commands

HookResult = tuple[int, str]
Event = dict[str, object]

ALLOW: HookResult = (0, "")
BLOCK = 2
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
TEST_TIMEOUT_SECONDS = 110
_DEPENDENCY_CHANGE = re.compile(r"\buv\s+(add|remove|sync|lock)\b|\bpip\s+install\b")


def _tool_input(event: Event, key: str) -> str:
    tool_input = event.get("tool_input")
    value = tool_input.get(key, "") if isinstance(tool_input, dict) else ""
    return value if isinstance(value, str) else ""


def _captured(function: Callable[[list[str]], int], argv: list[str]) -> tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = function(argv)
    return code, buffer.getvalue().strip()


def _run(command: list[str], root: Path, timeout: int | None = None) -> tuple[int, str]:
    completed = subprocess.run(  # noqa: S603  (fixed argv built here, never a shell string)
        command, cwd=root, capture_output=True, text=True, timeout=timeout, check=False
    )
    return completed.returncode, (completed.stdout + completed.stderr).strip()


# ------------------------------------------------------------------ license-guard


def license_guard(event: Event, root: Path) -> HookResult:  # noqa: ARG001  (uniform signature)
    command = _tool_input(event, "command")
    if event.get("hook_event_name") == "PostToolUse":
        if not _DEPENDENCY_CHANGE.search(command):
            return ALLOW
        code, output = _captured(check_licenses.main, [])
        if code:
            return BLOCK, f"License check failed after `{command}`:\n{output}"
        return ALLOW

    code, output = _captured(check_licenses.main, ["--command", command])
    return (BLOCK, output) if code else ALLOW


# ------------------------------------------------------------------ bash-guard


def _is_recursive_force_rm(arguments: list[str]) -> bool:
    flags: set[str] = set()
    for argument in arguments:
        if argument == "--":
            break
        if argument == "--recursive":
            flags.add("r")
        elif argument == "--force":
            flags.add("f")
        elif argument.startswith("-") and not argument.startswith("--"):
            flags.update(argument[1:].lower())
    return {"r", "f"} <= flags


def _download_problem(program: str, arguments: list[str]) -> str | None:
    urls = [argument for argument in arguments if "://" in argument]
    if not urls:
        return f"{program} needs an explicit http://127.0.0.1 or http://localhost URL here"
    for url in urls:
        host = urlsplit(url).hostname or ""
        if host not in LOCAL_HOSTS:
            return (
                f"{program} to {host or url} is blocked: only loopback is allowed from this repo. "
                "Use Context7 for docs, or ask the user to fetch it"
            )
    return None


# Commands that run another command: `sudo -E rm`, `timeout 5 curl`, `uv run curl`.
_WRAPPERS = frozenset(
    {
        "caffeinate", "chronic", "command", "doas", "env", "exec", "ionice", "nice",
        "nohup", "npx", "run", "stdbuf", "strace", "sudo", "time", "timeout", "uv",
        "uvx", "watch", "xargs",
    }
)  # fmt: skip
_GUARDED_PROGRAMS = frozenset({"rm", "curl", "wget"})


def _is_wrapper_part(token: str) -> bool:
    return (
        token in _WRAPPERS
        or token.startswith("-")
        or "=" in token
        or re.fullmatch(r"\d+(\.\d+)?[smhd]?", token) is not None
    )


def _invocations(simple: list[str]) -> list[tuple[str, list[str]]]:
    """The guarded programs this simple command actually runs, with their arguments.

    `grep curl .` mentions curl but runs grep; `nice -n 10 rm -rf x` runs rm.
    """
    found = []
    for index, token in enumerate(simple):
        program = token.rsplit("/", 1)[-1]
        if program in _GUARDED_PROGRAMS and all(map(_is_wrapper_part, simple[:index])):
            found.append((program, simple[index + 1 :]))
    return found


def bash_guard(event: Event, root: Path) -> HookResult:  # noqa: ARG001
    for simple in simple_commands(_tool_input(event, "command")):
        for program, arguments in _invocations(simple):
            if program == "rm" and _is_recursive_force_rm(arguments):
                return BLOCK, "rm -rf is denied in this repo; delete specific files instead"
            if program in {"curl", "wget"}:
                problem = _download_problem(program, arguments)
                if problem:
                    return BLOCK, problem
    return ALLOW


# ------------------------------------------------------------------ no-egress


def no_egress(event: Event, root: Path) -> HookResult:
    path = Path(_tool_input(event, "file_path"))
    if path.suffix != ".py":
        return ALLOW
    violations = check_no_egress.check_paths([path], root)
    if not violations:
        return ALLOW
    lines = "\n".join(str(v) for v in violations)
    return BLOCK, (
        f"{lines}\nHard rule 2 (ADR 0006): only gateway/cloud/ may reach the network, and the "
        "engine may not start processes or load code by name. Move the call or remove it."
    )


# ------------------------------------------------------------------ format


def format_file(event: Event, root: Path) -> HookResult:
    path = _tool_input(event, "file_path")
    if not path.endswith(".py"):
        return ALLOW
    _run([sys.executable, "-m", "ruff", "format", "--quiet", path], root)
    # Never auto-remove an import or variable that the next edit is about to use.
    unfixable = "F401,F841,ERA001"
    _run(
        [sys.executable, "-m", "ruff", "check", "--fix", "--quiet", "--unfixable", unfixable, path],
        root,
    )
    return ALLOW  # leftover lint is reported by the pre-commit hook and CI


# ------------------------------------------------------------------ test-after-edit


def test_target_for(path: Path, root: Path) -> Path | None:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    if relative.suffix != ".py" or not relative.parts:
        return None
    unit = root / "tests" / "unit"
    top = relative.parts[0]
    if top == "tests":
        return path if relative.name.startswith("test_") else unit
    if top not in {"gateway", "tools"}:
        return None
    package_dirs = relative.parts[1:-1] if top == "gateway" else ("tools", *relative.parts[1:-1])
    own_test = unit.joinpath(*package_dirs, f"test_{relative.stem}.py")
    return own_test if own_test.exists() else unit


def test_after_edit(event: Event, root: Path) -> HookResult:
    target = test_target_for(Path(_tool_input(event, "file_path")), root)
    if target is None or not target.exists():
        return ALLOW
    command = [sys.executable, "-m", "pytest", "-q", "-x", "--no-cov", "-p", "no:cacheprovider"]
    try:
        code, output = _run([*command, str(target)], root, timeout=TEST_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return BLOCK, f"Tests for {target} did not finish in {TEST_TIMEOUT_SECONDS}s"
    no_tests_collected = 5
    if code in (0, no_tests_collected):
        return ALLOW
    tail = "\n".join(output.splitlines()[-40:])
    return BLOCK, f"Tests failed after this edit ({target.relative_to(root)}):\n{tail}"


# ------------------------------------------------------------------ secret-scan


def _staged_files(root: Path) -> list[str]:
    # -z: without it git C-quotes non-ASCII names ("Договор.txt") and tools miss them.
    command = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"]
    completed = subprocess.run(command, cwd=root, capture_output=True, check=False)  # noqa: S603
    if completed.returncode != 0:
        return []
    return [name for name in completed.stdout.decode().split("\0") if name]


def detect_secrets_binary() -> Path:
    return Path(sys.executable).with_name("detect-secrets-hook")


def _git_invocations(command: str) -> list[list[str]]:
    """Arguments of every real `git ...` call; text that merely mentions git does not count."""
    return [arguments for program, *arguments in simple_commands(command) if program == "git"]


_GIT_OPTIONS_WITH_VALUE = frozenset({"-c", "-C", "--git-dir", "--work-tree", "--namespace"})


def _git_subcommand(arguments: list[str]) -> tuple[list[str], str, list[str]] | None:
    """Split `git [global options] SUBCOMMAND [args]` into its three parts."""
    takes_value = False
    for index, argument in enumerate(arguments):
        if takes_value:
            takes_value = False
        elif argument in _GIT_OPTIONS_WITH_VALUE:
            takes_value = True
        elif not argument.startswith("-"):
            return arguments[:index], argument, arguments[index + 1 :]
    return None


def _is_commit(arguments: list[str]) -> bool:
    parts = _git_subcommand(arguments)
    return parts is not None and parts[1] == "commit"


def _skips_git_hooks(git_calls: list[list[str]]) -> bool:
    for arguments in git_calls:
        parts = _git_subcommand(arguments)
        if parts is None:
            continue
        global_options, subcommand, rest = parts
        if any("hookspath" in option.lower() for option in global_options):
            return True  # git -c core.hooksPath=/dev/null commit
        if subcommand == "config" and any("hookspath" in a.lower() for a in rest):
            return True  # rewiring the hooks in the same command as a commit
        if subcommand == "commit" and (
            "--no-verify" in rest or any(re.fullmatch(r"-[A-Za-z]*n[A-Za-z]*", a) for a in rest)
        ):
            return True
    return False


def _git_hooks_installed(root: Path) -> bool:
    code, output = _run(["git", "config", "--get", "core.hooksPath"], root)
    if code != 0 or not output:
        return False
    return (root / output).resolve() == (root / ".githooks").resolve()


def secret_scan(event: Event, root: Path) -> HookResult:
    git_calls = _git_invocations(_tool_input(event, "command"))
    if not any(_is_commit(arguments) for arguments in git_calls):
        return ALLOW
    # The git pre-commit hook is the authoritative gate; never let a commit skip it.
    if _skips_git_hooks(git_calls):
        return BLOCK, "Commits may not skip the git hook (--no-verify, -n, core.hooksPath)."
    if not _git_hooks_installed(root):
        return BLOCK, (
            "The git pre-commit hook is not enabled for this clone. "
            "Run scripts/install-git-hooks.sh, then commit again."
        )

    problems = []
    code, output = _captured(scan_pii.main, ["--staged", "--repo", str(root)])
    if code:
        problems.append(output)

    staged = _staged_files(root)
    detector = detect_secrets_binary()
    if staged and not detector.exists():
        problems.append(f"detect-secrets-hook not found at {detector}; run `uv sync`")
    elif staged:
        baseline = root / ".secrets.baseline"
        options = ["--baseline", str(baseline)] if baseline.exists() else []
        code, output = _run([str(detector), *options, *staged], root)
        if code:
            problems.append(output)

    if problems:
        return BLOCK, "Commit blocked by secret-scan:\n" + "\n".join(problems)
    return ALLOW


# ------------------------------------------------------------------ entry point

HOOKS: dict[str, Callable[[Event, Path], HookResult]] = {
    "license-guard": license_guard,
    "bash-guard": bash_guard,
    "no-egress": no_egress,
    "format": format_file,
    "test-after-edit": test_after_edit,
    "secret-scan": secret_scan,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Claude Code hooks for SafeGuard Gateway.")
    parser.add_argument("hook", choices=sorted(HOOKS))
    args = parser.parse_args(argv)

    try:
        event = json.load(sys.stdin)
        root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd())
        code, message = HOOKS[args.hook](event, root)
    except Exception as error:  # noqa: BLE001  (fail closed on any crash, see module docstring)
        code, message = (
            BLOCK,
            (
                f"{args.hook} guard crashed: {error!r}. The action was stopped; "
                f"run `uv run python -m tools.hooks {args.hook}` by hand to debug."
            ),
        )
    if message:
        print(message, file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
