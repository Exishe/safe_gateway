# 0010. Guards fail closed, and every layer is checked by the next

- Status: Accepted (refines 0006 and 0009)
- Date: 2026-11-09

## Context

The end-of-phase review of Phase 0 confirmed working bypasses in every guard,
for example:

- `from ..cloud import x` and `import openai` passed the no-egress check, and
  new modules such as `gateway/config.py` had no rules at all.
- `git add -A && git commit --no-verify` skipped every secret check, and CI
  did not scan for secrets either.
- Files with Russian or Armenian names skipped detect-secrets, and cp1252 or
  UTF-16 exports (Excel) skipped the PII scan.
- Multi-line Bash commands and wrappers such as `timeout 5 curl` passed
  bash-guard.
- A crashed hook or a missing `uv` let the action through.

## Decision

1. **Default deny for egress.** Every path under `gateway/` gets the
   no-network rules except `gateway/cloud/`. Relative imports are resolved
   before checking. The engine may not import `gateway.cloud`,
   `gateway.proxy`, LLM SDKs, web frameworks, process or dynamic-loading
   modules. The check fails when its target is missing or empty.
2. **Guards fail closed.** A crashing hook exits 2. The wrappers exit 2 when
   there is neither a `.venv` nor `uv`. settings.json marks every guard
   `onFailure: block`, which also covers timeouts. Only the format hook fails
   open. The wrappers run `.venv`'s Python first and `uv run` only without a
   `.venv`: `uv run` parses `uv.lock` even with `--frozen`, so a lock with
   merge-conflict markers blocked every command, including its own repair
   (observed during a cherry-pick, 2026-11-09).
3. **The git hook cannot be skipped from Claude Code.** secret-scan refuses
   commits with `--no-verify`, `-n` or a `core.hooksPath` override, and
   refuses commits when the clone has not run `scripts/install-git-hooks.sh`.
4. **CI is the last layer.** It scans every tracked file for identifier-shaped
   PII and secrets, and checks licenses on macOS and Windows too, because
   platform-specific dependencies ship in the `.dmg` and `.msi`.
5. **File names travel NUL-separated** (`git ... -z`) everywhere. Text is
   decoded with replacement characters, so no file is skipped for its
   encoding; UTF-16 is detected by its byte-order mark.
6. **The license closure follows extras** (`uvicorn[standard]`), and a full
   license text is searched for forbidden terms ("non-commercial", "Commons
   Clause", GPL) before any permissive marker can allow it.

## Consequences

- A broken environment stops work with a message instead of silently
  disabling a guard. The fix is always stated in the message.
- These remain name-based guards, not a sandbox: `bash -c "..."`,
  `uv run python -c "..."` and code that builds module names at run time can
  still get past them. The cloud session's network policy and CI are the
  backstops; the residual list is kept in `docs/SECURITY.md`.
