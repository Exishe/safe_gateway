#!/usr/bin/env bash
# PostToolUse(Edit|Write): ruff format + ruff check --fix on the edited Python file.
# Logic and tests live in tools/hooks.py; run-hook.sh picks the project's Python.
exec "$(dirname "$0")/run-hook.sh" format
