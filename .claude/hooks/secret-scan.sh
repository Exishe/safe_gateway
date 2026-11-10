#!/usr/bin/env bash
# PreToolUse(Bash): before git commit, scan staged files for keys and identifier-shaped PII.
# Logic and tests live in tools/hooks.py; run-hook.sh picks the project's Python.
exec "$(dirname "$0")/run-hook.sh" secret-scan
