#!/usr/bin/env bash
# PostToolUse(Edit|Write): report network, process or dynamic-import code outside gateway/cloud/.
# Logic and tests live in tools/hooks.py; run-hook.sh picks the project's Python.
exec "$(dirname "$0")/run-hook.sh" no-egress
