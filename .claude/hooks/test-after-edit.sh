#!/usr/bin/env bash
# PostToolUse(Edit|Write): run the fast tests for the touched module and report failures.
# Logic and tests live in tools/hooks.py; run-hook.sh picks the project's Python.
exec "$(dirname "$0")/run-hook.sh" test-after-edit
