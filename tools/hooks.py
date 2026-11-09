"""Claude Code hooks for this repository. The .claude/hooks/*.sh files call this module.

Contract (Claude Code): the hook event arrives as JSON on stdin. Exit 0 lets the
tool call proceed; exit 2 blocks a PreToolUse call, or, after PostToolUse, sends
the reason on stderr back to Claude so it can fix what it just did.

Failure policy (ADR 0010): a guard that crashes exits 2, so a broken guard stops
the action instead of waving it through. The wrappers run .claude/hooks/run-hook.sh,
which prefers .venv's Python over `uv run` (uv parses uv.lock, and a conflicted lock
must not block its own repair) and turns any failure into exit 2, including a crash
before this module loads or a clone with neither .venv nor uv. settings.json marks
every guard `onFailure: block` for timeouts. Only the format hook fails open.

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
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from tools import check_licenses, check_no_egress, scan_pii
from tools.shell import raw_commands, simple_commands, unwrap

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


@dataclass(frozen=True)
class _Downloader:
    short_values: frozenset[str]  # option letters whose value is the next word
    long_values: frozenset[str]  # long options whose value is the next word
    refused: frozenset[str]  # options that send the request elsewhere or read targets from a file
    targets: frozenset[str]  # options whose value is a host: loopback only


# An option missing from these tables is taken to have no value, so a value written
# after it as a separate word is checked as a host: the guard errs towards blocking.
_DOWNLOADERS = {
    "curl": _Downloader(
        short_values=frozenset("AbcCdDeEFHKmoPQrtTuUwxXyYz"),
        long_values=frozenset(
            [
                "--cacert",
                "--capath",
                "--cert",
                "--cert-type",
                "--ciphers",
                "--config",
                "--connect-timeout",
                "--connect-to",
                "--continue-at",
                "--cookie",
                "--cookie-jar",
                "--data",
                "--data-ascii",
                "--data-binary",
                "--data-raw",
                "--data-urlencode",
                "--dns-servers",
                "--doh-url",
                "--dump-header",
                "--expand-url",
                "--form",
                "--form-string",
                "--header",
                "--interface",
                "--json",
                "--keepalive-time",
                "--key",
                "--key-type",
                "--limit-rate",
                "--max-filesize",
                "--max-redirs",
                "--max-time",
                "--oauth2-bearer",
                "--output",
                "--output-dir",
                "--pass",
                "--preproxy",
                "--proxy",
                "--proxy-header",
                "--proxy-user",
                "--proxy1.0",
                "--range",
                "--rate",
                "--referer",
                "--request",
                "--resolve",
                "--retry",
                "--retry-delay",
                "--retry-max-time",
                "--socks4",
                "--socks4a",
                "--socks5",
                "--socks5-hostname",
                "--speed-limit",
                "--speed-time",
                "--stderr",
                "--time-cond",
                "--trace",
                "--trace-ascii",
                "--unix-socket",
                "--upload-file",
                "--url",
                "--url-query",
                "--user",
                "--user-agent",
                "--variable",
                "--write-out",
            ]
        ),
        refused=frozenset(
            [
                "-K",
                "--config",
                "--connect-to",
                "--dns-servers",
                "--doh-url",
                "--expand-url",
                "--preproxy",
                "--resolve",
                "--socks4",
                "--socks4a",
                "--socks5",
                "--socks5-hostname",
                "--variable",
            ]
        ),
        targets=frozenset({"-x", "--proxy", "--proxy1.0", "--url"}),
    ),
    "wget": _Downloader(
        short_values=frozenset("OoaPtTwUeiBQlARDIX"),
        long_values=frozenset(
            [
                "--accept",
                "--append-output",
                "--base",
                "--body-data",
                "--body-file",
                "--ca-certificate",
                "--certificate",
                "--config",
                "--connect-timeout",
                "--directory-prefix",
                "--domains",
                "--execute",
                "--header",
                "--input-file",
                "--level",
                "--limit-rate",
                "--load-cookies",
                "--method",
                "--output-document",
                "--output-file",
                "--password",
                "--post-data",
                "--post-file",
                "--private-key",
                "--quota",
                "--read-timeout",
                "--referer",
                "--reject",
                "--save-cookies",
                "--timeout",
                "--tries",
                "--user",
                "--user-agent",
                "--wait",
                "--waitretry",
            ]
        ),
        refused=frozenset({"-i", "--input-file", "-e", "--execute", "--config", "-B", "--base"}),
        targets=frozenset(),
    ),
}


def _download_words(
    spec: _Downloader, arguments: list[str]
) -> tuple[list[tuple[str, str]], list[str]]:
    """The (option, value) pairs and the positional words, which curl and wget read as URLs."""
    options: list[tuple[str, str]] = []
    positional: list[str] = []
    words = iter(arguments)
    for word in words:
        if word == "--":
            positional.extend(words)
        elif word.startswith("--"):
            name, has_value, value = word.partition("=")
            if not has_value and name in spec.long_values:
                value = next(words, "")
            options.append((name, value))
        elif word.startswith("-") and len(word) > 1:
            for offset, letter in enumerate(word[1:], start=2):
                if letter in spec.short_values:
                    options.append((f"-{letter}", word[offset:] or next(words, "")))
                    break
                options.append((f"-{letter}", ""))
        else:
            positional.append(word)
    return options, positional


def _is_loopback(target: str) -> bool:
    try:
        parts = urlsplit(target if "://" in target else f"http://{target}")
        host = parts.hostname or ""
    except ValueError:
        return False
    # No user part (`127.0.0.1@evil`), globs (`{a,b}`) or backslashes: parsers disagree on them.
    netloc = parts.netloc.replace("[::1]", "")
    return host in LOCAL_HOSTS and not set(netloc) & set("@{}[]\\")


def _download_problem(program: str, arguments: list[str]) -> str | None:
    spec = _DOWNLOADERS[program]
    options, targets = _download_words(spec, arguments)
    for name, value in options:
        if name in spec.refused:
            return f"{program} {name} is blocked: it can send the request past the loopback check"
        if name in spec.targets:
            targets.append(value)
    if not targets:
        return f"{program} needs an explicit http://127.0.0.1 or http://localhost URL here"
    for target in targets:
        if not _is_loopback(target):
            return (
                f"{program} to {target} is blocked: only loopback is allowed from this repo, and "
                "every word that is not an option counts as a host (write --option=value). "
                "Use Context7 for docs, or ask the user to fetch it"
            )
    return None


# git settings that run a command, move the hooks, or send pushes somewhere else.
_GIT_RISKY_KEY = re.compile(
    r"(?i)^(core\.(sshcommand|gitproxy|fsmonitor|hookspath|askpass|editor|pager)"
    r"|sequence\.editor|diff\.external|credential\..*|include(if)?\..*|help\.autocorrect"
    r"|remote\..*\.(url|pushurl|receivepack|uploadpack|proxy)|url\..*\.(push)?insteadof"
    r"|uploadpack\.packobjectshook|protocol\..*"
    r"|.*\.(command|cmd|helper|program|textconv|clean|smudge|process))$"
)
# Variables that make git or a downloader run a command or take another route.
_RISKY_VARIABLE = re.compile(
    r"(?i)^(GIT_CONFIG_\w*|GIT_SSH(_COMMAND)?|GIT_PROXY_COMMAND|GIT_EXTERNAL_DIFF|GIT_ASKPASS"
    r"|GIT_EXEC_PATH|GIT_TEMPLATE_DIR|\w*_proxy|CURL_HOME|WGETRC)="
)
_DECLARATIONS = frozenset({"export", "declare", "typeset", "local", "readonly"})


def _git_config_problem(key: str, value: str | None) -> str | None:
    if key.lower().startswith("alias."):
        if value is None or value.lstrip().startswith("!"):
            return f"git alias {key} runs a shell command; run the command itself instead"
        return None
    if _GIT_RISKY_KEY.match(key):
        return f"git setting {key} can run a command or redirect git; ask the user to change it"
    return None


def _git_config_overrides(global_options: list[str]) -> list[tuple[str, str | None]]:
    """(key, value) of every `-c key=value`; a `--config-env` value is unknown (None)."""
    found: list[tuple[str, str | None]] = []
    for index, option in enumerate(global_options):
        following = global_options[index + 1] if index + 1 < len(global_options) else ""
        if option == "-c":
            key, _, value = following.partition("=")
            found.append((key, value))
        elif option == "--config-env" or option.startswith("--config-env="):
            setting = option.partition("=")[2] or following
            found.append((setting.partition("=")[0], None))
    return found


def _git_problem(arguments: list[str]) -> str | None:
    global_options, subcommand, rest = _git_subcommand(arguments) or (arguments, "", [])
    for key, value in _git_config_overrides(global_options):
        problem = _git_config_problem(key, value)
        if problem:
            return problem
    positional = [word for word in rest if not word.startswith("-")]
    if subcommand == "config":
        if positional[:1] == ["set"]:
            positional = positional[1:]
        if len(positional) >= 2:  # noqa: PLR2004  (a key and a value: a write)
            return _git_config_problem(positional[0], positional[1])
    if subcommand == "push" and any(
        "://" in word or re.match(r"[^/\s]+@[^/\s]+:", word) for word in rest
    ):
        return "git push to a URL is blocked: push only to a configured remote such as origin"
    if subcommand == "remote" and positional[:1] in (["add"], ["set-url"]):
        return "changing git remotes is blocked; ask the user to do it"
    return None


def _gh_problem(arguments: list[str]) -> str | None:
    words = [word for word in arguments if not word.startswith("-")]
    if words[:1] == ["gist"] and words[1:2] in (["create"], ["new"], ["edit"]):
        return "gh gist is blocked: it publishes files outside the repository"
    return None


def _variable_problem(raw: list[str]) -> str | None:
    """A risky VAR=value before the program, after `env`, or in `export`."""
    inner = unwrap(raw)
    leading = raw[: len(raw) - len(inner)]
    declared = inner[1:] if inner[:1] and inner[0] in _DECLARATIONS else []
    for word in [*leading, *declared]:
        if _RISKY_VARIABLE.match(word):
            name = word.partition("=")[0]
            return f"setting {name} is blocked: it can run a command or reroute git or downloads"
    return None


def _command_problem(raw: list[str]) -> str | None:
    inner = unwrap(raw)
    problem = _variable_problem(raw)
    if problem or not inner:
        return problem
    program, arguments = inner[0], inner[1:]
    if program == "rm" and _is_recursive_force_rm(arguments):
        return "rm -rf is denied in this repo; delete specific files instead"
    if program in _DOWNLOADERS:
        return _download_problem(program, arguments)
    if program == "git":
        return _git_problem(arguments)
    if program == "gh":
        return _gh_problem(arguments)
    return None


def bash_guard(event: Event, root: Path) -> HookResult:  # noqa: ARG001
    for raw in raw_commands(_tool_input(event, "command")):
        problem = _command_problem(raw)
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


_GIT_OPTIONS_WITH_VALUE = frozenset(
    {"-c", "-C", "--git-dir", "--work-tree", "--namespace", "--config-env"}
)
# Text that turns the hook off: the setting, the variables that inject settings, and
# --no-verify with its abbreviations (git accepts --no-veri). Checked on the words of
# the command, VAR=value included, with commit messages left out.
_HOOK_SKIP_TEXT = re.compile(r"(?i)hookspath|GIT_CONFIG_(COUNT|PARAMETERS|KEY_)|--no-veri")
_N_FLAG = re.compile(r"(?:^|\s)-[A-Za-z]*n[A-Za-z]*(?=\s|$)")
Aliases = dict[str, str | None]  # "alias.ci" -> its value; None when it cannot be known


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


def _git_aliases(root: Path) -> Aliases:
    command = ["git", "config", "-z", "--get-regexp", r"^alias\."]
    completed = subprocess.run(  # noqa: S603
        command, cwd=root, capture_output=True, text=True, check=False
    )
    aliases: Aliases = {}
    for entry in completed.stdout.split("\0"):
        key, _, value = entry.partition("\n")
        if key:
            aliases[key.lower()] = value
    return aliases


def _command_line_aliases(git_calls: list[list[str]]) -> Aliases:
    aliases: Aliases = {}
    for arguments in git_calls:
        global_options = (_git_subcommand(arguments) or (arguments, "", []))[0]
        for key, value in _git_config_overrides(global_options):
            if key.lower().startswith("alias."):
                aliases[key.lower()] = value
    return aliases


def _is_commit(arguments: list[str], aliases: Aliases) -> bool:
    """A commit, or a call of any alias: an alias can hide a commit behind another name."""
    parts = _git_subcommand(arguments)
    return parts is not None and (parts[1] == "commit" or f"alias.{parts[1].lower()}" in aliases)


def _unsafe_alias(value: str | None) -> bool:
    if value is None or _HOOK_SKIP_TEXT.search(value):
        return True
    runs_a_commit = "commit" in value or value.lstrip().startswith("!")
    return runs_a_commit and _N_FLAG.search(value) is not None


def _without_messages(words: list[str]) -> list[str]:
    """The words minus commit messages, which may mention anything."""
    kept: list[str] = []
    skip_next = False
    for word in words:
        if skip_next:
            skip_next = False
        elif word == "--message" or re.fullmatch(r"-[A-Za-z]*m", word):
            skip_next = True
        elif not word.startswith(("--message=", "-m")):
            kept.append(word)
    return kept


def _hook_skip_text(command: str) -> bool:
    words = [word for raw in raw_commands(command) for word in _without_messages(raw)]
    return _HOOK_SKIP_TEXT.search(" ".join(words)) is not None


def _skips_git_hooks(git_calls: list[list[str]], aliases: Aliases) -> bool:
    for arguments in git_calls:
        parts = _git_subcommand(arguments)
        if parts is None or not _is_commit(arguments, aliases):
            continue
        if any(re.fullmatch(r"-[A-Za-z]*n[A-Za-z]*", a) for a in parts[2]):
            return True  # -n, -anm: a message is never a lone flag-shaped word
    return False


def _git_hooks_installed(root: Path) -> bool:
    code, output = _run(["git", "config", "--get", "core.hooksPath"], root)
    if code != 0 or not output:
        return False
    return (root / output).resolve() == (root / ".githooks").resolve()


_SKIP_MESSAGE = (
    "Commits may not skip the git hook (--no-verify, -n, core.hooksPath, GIT_CONFIG_*). "
    "Commit with a plain `git commit -m ...`; the message itself may mention these words."
)


def _commit_gate_problem(git_calls: list[list[str]], aliases: Aliases, root: Path) -> str | None:
    """Why this commit would not pass through the git pre-commit hook, if it would not."""
    unsafe = sorted(key for key, value in aliases.items() if _unsafe_alias(value))
    if unsafe:
        return (
            f"git alias {', '.join(unsafe)} can skip the git hook (--no-verify, -n, hooksPath); "
            "commits are refused until the user removes it (git config --unset)."
        )
    if _skips_git_hooks(git_calls, aliases):
        return _SKIP_MESSAGE
    if not _git_hooks_installed(root):
        return (
            "The git pre-commit hook is not enabled for this clone. "
            "Run scripts/install-git-hooks.sh, then commit again."
        )
    return None


def secret_scan(event: Event, root: Path) -> HookResult:
    command = _tool_input(event, "command")
    git_calls = _git_invocations(command)
    if not git_calls:
        return ALLOW
    # The git pre-commit hook is the authoritative gate; never let a commit skip it.
    # Hook-skipping text is refused next to any git call, a commit or not.
    if _hook_skip_text(command):
        return BLOCK, _SKIP_MESSAGE
    aliases = _git_aliases(root) | _command_line_aliases(git_calls)
    if not any(_is_commit(arguments, aliases) for arguments in git_calls):
        return ALLOW
    gate_problem = _commit_gate_problem(git_calls, aliases, root)
    if gate_problem:
        return BLOCK, gate_problem

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
