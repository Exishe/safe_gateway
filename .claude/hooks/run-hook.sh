#!/usr/bin/env bash
# Runs one guard from tools/hooks.py with the project's Python: `run-hook.sh <hook-name>`.
#
# The .venv interpreter comes first. `uv run` parses uv.lock even with --frozen, so
# a uv.lock with merge-conflict markers would otherwise make every guard fail and
# block every command, including the `git checkout -- uv.lock` that repairs it.
# uv is the fallback for a clone that has no .venv yet.
#
# Fail closed (ADR 0010): every failure, a crash on import included, exits 2.
set -uo pipefail
hook="$1"
cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}" || exit 2

python=""
for candidate in .venv/bin/python .venv/Scripts/python.exe; do
    if [ -x "$candidate" ]; then
        python="$candidate"
        break
    fi
done

if [ -n "$python" ]; then
    "$python" -m tools.hooks "$hook"
elif command -v uv >/dev/null 2>&1; then
    uv run --quiet --frozen python -m tools.hooks "$hook"
else
    echo "safeguard hook: there is no .venv and uv is not on PATH, so the $hook guard cannot run. Install uv and run \`uv sync\` (docs/INSTALL.md)." >&2
    exit 2
fi
status=$?
[ "$status" -eq 0 ] && exit 0
exit 2
