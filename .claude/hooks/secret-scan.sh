#!/usr/bin/env bash
# PreToolUse(Bash): before git commit, scan staged files for keys and identifier-shaped PII.
# Logic and tests live in tools/hooks.py; this wrapper only picks the project's Python.
set -euo pipefail
cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}"
exec uv run --quiet --frozen python -m tools.hooks secret-scan
