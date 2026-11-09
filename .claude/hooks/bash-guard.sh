#!/usr/bin/env bash
# PreToolUse(Bash): refuse rm -rf and curl/wget to anything but loopback.
# Logic and tests live in tools/hooks.py; run-hook.sh picks the project's Python.
exec "$(dirname "$0")/run-hook.sh" bash-guard
