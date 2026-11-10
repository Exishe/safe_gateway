# 0028. Weekly scheduled scan

- Status: Accepted
- Date: 2026-11-10
- Builds on: ADR 0007, ADR 0010

## Context

CI runs Semgrep and pip-audit when someone pushes. Advisories and rules change
without a push, so a quiet week can hide a new finding in code that has not
changed. The leak-hunt tests are regression tests for bypasses found earlier;
they should keep running even if nobody touches the engine. Free Actions
minutes are limited (ADR 0007).

## Decision

1. **A separate workflow**, `.github/workflows/weekly.yml`, on `schedule`
   (Monday 04:23 UTC) and `workflow_dispatch`. It is separate so a failing
   scheduled scan never blocks a push.
2. **Ubuntu only.** macOS and Windows stay on tags and manual runs (ADR 0007).
3. **Steps:** Semgrep (same rules as CI), pip-audit over the locked export,
   `npm audit --omit=dev` for `apps/web` and `apps/extension`, and the leak-hunt
   regression tests: `pytest tests/unit/engine -k leak_hunt` and
   `tests/unit/parsers/test_leak_hunt_parsers.py`. The models are pulled
   and checked against their pinned SHA-256 first.
4. **Least privilege:** `permissions: contents: read`, no credentials kept by
   checkout, actions pinned by the same commit SHAs as `ci.yml`, no cache.
   `zizmor --offline` passes on it.

## Consequences

- A failed scheduled run emails the repository owner through GitHub's usual
  notification; nobody has to watch it.
- The scheduled run covers the default branch only. To scan another branch,
  start it by hand.
- GitHub turns off scheduled workflows in a repository with no activity for
  60 days; re-enable it by hand if that happens.
- This does not replace the `leak-hunter` red-team round. The tests replay
  bypasses already found. A new bypass is found by a new round, written as a
  failing test, and fixed (`docs/RUNBOOK_SUSPECTED_LEAK.md`).
- About 10 to 15 Ubuntu minutes a week is assumed, not measured; check the first
  run before relying on it.
