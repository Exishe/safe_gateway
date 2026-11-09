#!/usr/bin/env bash
# PreToolUse(Bash): refuse installs of denied packages. PostToolUse(Bash): audit licenses after a dependency change.
# Logic and tests live in tools/hooks.py; this wrapper only picks the project's Python.
set -euo pipefail
# Fail closed: without uv no guard can run, so stop the action and say why.
command -v uv >/dev/null 2>&1 || {
    echo "safeguard hook: uv is not on PATH, so this guard cannot run. Install uv (docs/INSTALL.md)." >&2
    exit 2
}
cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}"
exec uv run --quiet --frozen python -m tools.hooks license-guard
