#!/usr/bin/env bash
# PostToolUse(Edit|Write): report network, process or dynamic-import code outside gateway/cloud/.
# Logic and tests live in tools/hooks.py; this wrapper only picks the project's Python.
set -euo pipefail
cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}"
exec uv run --quiet --frozen python -m tools.hooks no-egress
