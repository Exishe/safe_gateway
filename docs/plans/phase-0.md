# Phase 0: Scaffold Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A repo where every guard from the brief (license, no-egress, secrets, format, tests) runs on every edit, on every commit and in CI, and is green, before any engine code exists.

**Architecture:** Each guard is one small, tested Python module in `tools/`. Claude Code hooks, the git pre-commit hook and CI are thin wrappers that call those modules, so one rule has one implementation. The `gateway` package exists only as empty sub-packages so the egress rules have real targets.

**Tech Stack:** Python 3.12, uv (`uv_build`), ruff, ty, pytest, pytest-cov, pytest-socket, hypothesis, packaging, detect-secrets, bandit, pip-audit, zizmor, GitHub Actions.

**Spec:** `SafeGuard_Gateway___Build_Requirements_Free_Stack__Claude_Code_Prompt.pdf` (the brief), plus `docs/adr/0006`, `0007`, `0008`, `0009`.

## Global Constraints

- Python `>=3.12`, `.python-version` = `3.12`.
- Licenses allowed in shipped code: MIT, BSD, Apache-2.0, PSF, MPL-2.0, public domain, CC-BY-4.0 (data). Forbidden: AGPL, GPL (except tooling with a runtime exception), CC-BY-NC, any paid API or SaaS. Never: PyMuPDF, pdftext, GLiNER v0/v1 base models.
- `gateway/engine/` has no network, process or dynamic-import path. Only `gateway/cloud/` may make outbound requests.
- Tests never open a real network socket (`--disable-socket`).
- No feature without a failing test first.
- A step that needs a paid service stops and is added to `docs/APPROVAL_NEEDED.md`.
- Docs say "reduces exposure", "tamper-evident", "pseudonymised"; never "guarantees", "anonymised", "HIPAA compliant".

## Review Focus

1. `uv add "PyMuPDF[all]==1.24"` (mixed case, extras, version pin) must still be blocked by name. Test in Task 3.
2. A dependency with no license metadata at all must fail as "review", never pass silently. Test in Task 3.
3. `from http import client`, `import urllib.request as u` and `__import__("socket")` in `gateway/engine/` must be caught, while the word "socket" in a comment or string must not. Test in Task 2.
4. A test file that needs a fake card number must be committable with `pragma: allowlist secret`, and blocked without it. Test in Task 4.
5. A project path containing spaces must not break the hooks (`$CLAUDE_PROJECT_DIR` is quoted). Test in Task 5.

---

### Task 1: Project skeleton and CLI version

**Files:**
- Create: `pyproject.toml`, `.python-version`, `.gitignore`, `uv.lock`
- Create: `gateway/__init__.py`, `gateway/cli.py`, `gateway/{engine,parsers,audit,cloud,proxy}/__init__.py`
- Create: `tests/conftest.py`, `tests/unit/test_cli.py`, `tests/{property,integration,e2e,benchmarks}/__init__.py`

**Interfaces:**
- Produces: `gateway.__version__: str`; `gateway.cli.main(argv: list[str] | None = None) -> int`; console script `safeguard`.

- [ ] **Step 1:** `pyproject.toml` with `uv_build`, flat module root, `license = "LicenseRef-Proprietary"`, classifier `Private :: Do Not Upload`, groups `lint` (ruff, ty, shellcheck-py), `test` (pytest, pytest-cov, pytest-socket, hypothesis), `audit` (bandit, pip-audit, detect-secrets, zizmor), `tools` (packaging), `dev` including all. Pytest `addopts`: `--disable-socket --allow-unix-socket --strict-markers -ra`, coverage on `gateway` and `tools` with a floor of 80.
- [ ] **Step 2: Write the failing test** `tests/unit/test_cli.py`:
  - `test_version_flag_prints_version`: `main(["--version"])` exits 0, stdout contains `gateway.__version__`.
  - `test_no_arguments_prints_help_and_returns_zero`.
  - `test_unknown_command_returns_2`.
- [ ] **Step 3:** Run `uv run pytest tests/unit/test_cli.py`. Expected: FAIL (`gateway.cli` missing).
- [ ] **Step 4:** Implement `main` with `argparse` (stdlib, no new dependency).
- [ ] **Step 5:** Run `uv run pytest`, `uv run ruff check .`, `uv run ty check`. Expected: all pass.
- [ ] **Step 6:** Commit `chore: scaffold uv project and safeguard CLI`.

### Task 2: No-egress checker

**Files:**
- Create: `tools/__init__.py`, `tools/check_no_egress.py`
- Test: `tests/unit/tools/test_check_no_egress.py`

**Interfaces:**
- Produces: `Violation(path: str, line: int, detail: str)`; `rules_for_path(path: Path, root: Path) -> RuleSet | None`; `check_source(source: str, path: str, rules: RuleSet) -> list[Violation]`; `check_paths(paths: list[Path], root: Path) -> list[Violation]`; `main(argv: list[str] | None = None) -> int` (exit 1 on violations; no args scans `gateway/`).

Rule table (ADR 0006): `NETWORK` = socket, ssl, http, urllib, urllib3, requests, httpx, aiohttp, websocket, websockets, ftplib, smtplib, poplib, imaplib, telnetlib, xmlrpc, socketserver, grpc, paramiko, pycurl, httplib2, huggingface_hub. `ENGINE_EXTRA` = subprocess, asyncio, multiprocessing, importlib, ctypes, pty; calls to `__import__`, `eval`, `exec`; `os.system`, `os.popen`, `os.exec*`, `os.spawn*`. Engine gets both; parsers, audit, proxy and `cli.py` get `NETWORK`; cloud gets nothing.

- [ ] **Step 1: Write the failing tests:** `import socket`, `from urllib.request import urlopen`, `from http import client`, `import urllib.request as u`, `__import__("socket")`, `import subprocess`, `os.system("curl x")` each give one violation in an engine path; `import subprocess` in a parsers path gives none; `import httpx` in proxy gives one and in cloud gives none; `# socket` comment and `"socket"` string give none; a file with a syntax error gives a violation (fail closed); `rules_for_path` returns `None` outside `gateway/`; `test_repository_is_clean` runs `check_paths` on the real `gateway/` and expects `[]`.
- [ ] **Step 2:** Run them. Expected: FAIL (module missing).
- [ ] **Step 3:** Implement with `ast.walk` over `Import`, `ImportFrom` (absolute only, `level == 0`) and `Call` nodes.
- [ ] **Step 4:** Run `uv run pytest tests/unit/tools`. Expected: PASS.
- [ ] **Step 5:** Commit `feat(tools): AST no-egress checker`.

### Task 3: License checker

**Files:**
- Create: `tools/check_licenses.py`, `tools/license_policy.toml`
- Test: `tests/unit/tools/test_check_licenses.py`

**Interfaces:**
- Produces: `DistInfo(name: str, license_expression: str | None, license_text: str | None, classifiers: tuple[str, ...])`; `Policy` loaded by `load_policy(path: Path) -> Policy`; `classify(dist: DistInfo, policy: Policy) -> tuple[Literal["allowed", "forbidden", "review"], str]`; `runtime_closure(root: str) -> list[str]`; `check(dists: Iterable[DistInfo], policy: Policy) -> list[str]` (problems, empty means pass); `denied_names_in_command(command: str, policy: Policy) -> list[str]`; `main(argv) -> int`.

Order: denied name, then `License-Expression` (top-level `OR`: any alternative fully allowed; `AND`: all parts allowed), then license classifiers (all allowed: allowed; any forbidden and none allowed: forbidden; mixed: review), then short `License` text, then review. Policy exceptions turn "review" into "allowed" and never override "forbidden".

- [ ] **Step 1: Write the failing tests:** `MIT` allowed; `Apache-2.0 OR BSD-3-Clause` allowed; `AGPL-3.0-only`, `GPL-3.0-or-later`, `CC-BY-NC-4.0` forbidden; `MIT AND GPL-2.0-only` forbidden; `LGPL-3.0-only` review; classifier MIT allowed; classifiers MIT + GPL review; no metadata review; name `PyMuPDF` forbidden whatever its metadata; exception turns review into allowed and leaves forbidden forbidden; `denied_names_in_command('uv add "PyMuPDF[all]==1.24" rich')` returns `["pymupdf"]`; `pip install pdftext` caught; `uv add rich` returns `[]`; `test_project_runtime_dependencies_pass` runs `check` over `runtime_closure("safeguard-gateway")`.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Implement with `importlib.metadata` and `packaging.requirements.Requirement` (markers evaluated with `extra=""`), and `tomllib` for the policy.
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5:** Commit `feat(tools): runtime license checker and install guard`.

### Task 4: Staged-file secret and PII scan

**Files:**
- Create: `tools/scan_pii.py`, `.secrets.baseline`
- Test: `tests/unit/tools/test_scan_pii.py`

**Interfaces:**
- Produces: `Finding(path: str, line: int, kind: Literal["ssn", "card"])`; `find_pii(text: str, path: str = "<text>") -> list[Finding]`; `main(argv) -> int` (scans the given files, exit 1 on findings). Findings never echo the matched value.

- [ ] **Step 1: Write the failing tests:** `123-45-6789` flagged as ssn; a Luhn-valid 16-digit number with spaces flagged as card; the same number with one digit changed (Luhn-invalid) not flagged; a line ending in `pragma: allowlist secret` skipped; `2026-11-09` not flagged; the finding's string form contains no digits of the value.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement with two regexes plus a Luhn check.
- [ ] **Step 4:** Run. Expected: PASS. Generate `.secrets.baseline` with `uv run detect-secrets scan`.
- [ ] **Step 5:** Commit `feat(tools): staged PII scan`.

### Task 5: Claude Code wiring

**Files:**
- Create: `CLAUDE.md`, `.mcp.json`, `.claude/settings.json`
- Create: `.claude/hooks/{_lib.sh,license-guard.sh,bash-guard.sh,no-egress.sh,test-after-edit.sh,secret-scan.sh,format.sh}`
- Create: `.claude/agents/{leak-hunter,test-writer,security-reviewer,docs-checker,ui-builder}.md`
- Create: `.claude/skills/{det-ai,humanize,hy-writer}/` (copied from the user's account)
- Test: `tests/unit/hooks/test_hooks.py`

**Interfaces:**
- Consumes: `tools.check_no_egress.main`, `tools.check_licenses.main` and `denied_names_in_command`, `tools.scan_pii.main`.
- Produces: hook contract: JSON on stdin, exit 0 to allow, exit 2 with a reason on stderr to block (PreToolUse) or report back (PostToolUse).

- [ ] **Step 1: Write the failing tests** (skipped on Windows), each feeding hook JSON on stdin to the script with `CLAUDE_PROJECT_DIR` set to a copy of the repo in a path containing a space: `license-guard` exits 2 on `uv add PyMuPDF` and 0 on `uv add rich`; `bash-guard` exits 2 on `curl https://example.com`, `rm -rf /` and 0 on `curl http://127.0.0.1:8787/health`; `no-egress` exits 2 after a Write of an engine file containing `import socket`, and 0 for a cloud file; `format` exits 0 on a non-Python file.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Write the hooks; `settings.json` wires PreToolUse(Bash) to license-guard, bash-guard and secret-scan, and PostToolUse(Edit|Write|MultiEdit) to no-egress, format and test-after-edit, plus PostToolUse(Bash) to license-guard for the post-install audit. Permissions allow `uv run`, `uv sync`, `uv add`, `pytest`, `git`, `gh`; deny `rm -rf`, `wget`, reading `.env` files.
- [ ] **Step 4:** Run. Expected: PASS. Run `shellcheck .claude/hooks/*.sh`. Expected: clean.
- [ ] **Step 5:** Commit `chore(claude): CLAUDE.md, hooks, agents, MCP config`.

### Task 6: Git pre-commit hook

**Files:**
- Create: `.githooks/pre-commit`, `scripts/install-git-hooks.sh`

- [ ] **Step 1:** Pre-commit runs on staged files: `ruff format --check`, `ruff check`, `tools.check_no_egress`, `tools.scan_pii`, `detect-secrets-hook --baseline .secrets.baseline`.
- [ ] **Step 2:** Install it (`git config core.hooksPath .githooks`) and prove it blocks: stage a file with a Luhn-valid card number. Expected: commit refused. Unstage it.
- [ ] **Step 3:** Commit `chore: git pre-commit hook`.

### Task 7: CI

**Files:**
- Create: `.github/workflows/ci.yml`, `.github/dependabot.yml`

- [ ] **Step 1:** `checks` job on Ubuntu for every push and PR: `uv sync --locked`, format check, ruff, ty, no-egress, license check, pytest, bandit, pip-audit, Semgrep (`p/python`, `p/secrets`, `--metrics=off`), zizmor. `platforms` job (macos-14 arm64, windows-latest) only on `v*` tags and `workflow_dispatch` (ADR 0007). Actions pinned by SHA, `permissions: contents: read`, `persist-credentials: false`.
- [ ] **Step 2:** Run each CI command locally. Expected: all pass. Run `uvx zizmor .github/workflows`. Expected: no findings.
- [ ] **Step 3:** Commit `ci: checks on every push, platform matrix on tags`.

### Task 8: Docs and tag

**Files:**
- Create: `README.md` (replace), `docs/INSTALL.md`, `docs/SECURITY.md`, `docs/BENCHMARKS.md`, `docs/APPROVAL_NEEDED.md`

- [ ] **Step 1:** Write the docs. BENCHMARKS says "no measurements yet" with the table header to fill; APPROVAL_NEEDED lists the brief's paid items, all unapproved.
- [ ] **Step 2:** Full verification: `uv sync --locked && uv run ruff format --check . && uv run ruff check . && uv run ty check && uv run pytest && uv run python -m tools.check_no_egress && uv run python -m tools.check_licenses`. Expected: all green.
- [ ] **Step 3:** Commit `docs: install, security, benchmarks, approvals`; tag `v0.0.0`.
