# 0007. CI matrix and the free-minutes budget

- Status: Accepted
- Date: 2026-11-09

## Context

The brief asks for CI on every push and a macOS arm64 + Windows x64 matrix.
Private repos get 2,000 free Actions minutes a month, and GitHub bills macOS
runner minutes at 10x and Windows at 2x against that budget. Running the full
matrix on every push would use up the free tier in a few busy days, which
breaks hard rule 1 (zero cost).

## Decision

- Every push and pull request: one Ubuntu job (format, lint, no-egress,
  license check, tests, Semgrep, Bandit, pip-audit).
- macOS arm64 and Windows x64 test jobs: on version tags (`v*`) and on manual
  `workflow_dispatch` only. Installer builds (Phase 6) follow the same rule.
- Third-party actions are pinned by commit SHA, with the version in a comment.

## Consequences

- A platform-specific bug can land on `main` and only show up at tag time.
  Run the matrix by hand before risky merges.
- If the repo is made public, minutes are free and the matrix can move back to
  every push.

## Amended 2026-11-09

CI now triggers on `push` (all branches and `v*` tags) and `workflow_dispatch`,
not on `pull_request`. Every PR here comes from a branch in this repository, so
`push` already runs the same job; keeping both ran it twice per update. A PR
from a fork would not be covered; revisit if outside contributors appear.
