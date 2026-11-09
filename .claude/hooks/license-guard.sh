#!/usr/bin/env bash
# PreToolUse(Bash): refuse installs of denied packages. PostToolUse(Bash): audit licenses after a dependency change.
# Logic and tests live in tools/hooks.py; this wrapper only picks the project's Python.
set -euo pipefail
cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}"
exec uv run --quiet --frozen python -m tools.hooks license-guard
