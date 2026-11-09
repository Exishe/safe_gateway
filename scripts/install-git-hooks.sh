#!/usr/bin/env bash
# Point git at the repository's hooks (.githooks/). Run once per clone.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
git config core.hooksPath .githooks
echo "git hooks enabled: $(git config core.hooksPath)"
