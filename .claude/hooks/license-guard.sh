#!/usr/bin/env bash
# PreToolUse(Bash): refuse installs of denied packages. PostToolUse(Bash): audit licenses after a dependency change.
# Logic and tests live in tools/hooks.py; run-hook.sh picks the project's Python.
exec "$(dirname "$0")/run-hook.sh" license-guard
