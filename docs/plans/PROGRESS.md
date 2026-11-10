# Build progress and resume checklist

Kept current by the build session. If a session resumes after a usage limit or
a restart, start here.

## Owner's standing authorization (2026-11-09)

Zero cost is rule 1: free and open-source only. Decide reversible questions
yourself and record them as ADRs. The owner is asleep and will not answer
questions or approvals, so continue on your own. Push only to
`claude/github-push-config-w3r1jp`. No PR unless asked. Never skip git hooks.
Supabase or any other paid or hosted service is not needed: the product is
local-first.

## Done (on the branch)

- Phase 0: repo, guards, CI.
- Phase 1a/1b/1c: structured recognizers, spaCy NER, GLiNER (Armenian), NER
  input normalisation (ADR 0016), dates, shape and keyword recognizers
  (ADR 0023).
- Phase 2: mask, rehydrate, streaming rehydrator, session store, encrypted
  disk store, `safeguard mask`.
- Guard hardening (security review I1, I2, M1, M5–M10), hook deadlock fixes
  (ADR 0010).

## Status (2026-11-10, head 36b50c4)

Every phase this session could finish is built, merged and pushed:

- **Phases 0–7:** built, with tests, red-team rounds, security reviews, the web UI,
  the extension, and the Tauri desktop shell with installers built in CI.
- **Phase 8, free parts:**
  - weekly scan (`.github/workflows/weekly.yml`; it runs once this branch is on the
    default branch);
  - `docs/SECURITY_OVERVIEW.md`;
  - `docs/RUNBOOK_SUSPECTED_LEAK.md`.

CI run 42765143614 on 1855771 is green for the Ubuntu checks, the Windows tests,
the web UI, the extension, and the desktop `.deb` and `.msi`. Tags v0.0.0 to v0.3.0
were created but could not be pushed: this session's git access allows only the
working branch (HTTP 403). The commands are in `docs/RELEASE_CHECKS.md`. No agent is
running.

## Needs the owner

- **macOS:** paused by the owner on 2026-11-10 (ADR 0007 amendment). Restore
  it before a Mac firm joins the pilot.
- **Done 2026-11-10:** PR #1 merged into `main`; release `v0.3.0` published.
- **Live checks:** run against a local Ollama model; try the extension on the live
  chat sites; test on Firefox. These unlock tags v0.4.0 to v0.7.0.
- **Pilot:** collect firms' feedback in `docs/pilot-feedback.md`, then tag v0.8.0.

## Merge procedure

1. `git cherry-pick` the agent's commits onto the branch.
2. Resolve conflicts:
   - `uv.lock`: keep both sides, then run `uv lock`. If a version range
     changed, run `uv lock --upgrade-package X`.
   - ADR numbers: renumber on collision.
3. Run `git cherry-pick --continue --no-edit`. The guard refuses `git -c core.editor`.
4. Run the gate with `set -o pipefail`:
   ruff format --check, ruff check, ty, check_no_egress, check_licenses,
   `scan_pii --tracked`, then `pytest -q`. Push only if it is green.

## Next, in order

1. Merge the in-flight work above as each piece lands. Fix the security
   review's Critical and Important findings.
2. Leak-hunter round on `gateway/parsers/`; add 12-digit Maestro cards to the
   card recognizer (a strict xfail from Phase 3).
3. Pre-tag review:
   - `security-reviewer` agent on the full diff;
   - Semgrep, supply-chain and code-review skills;
   - CI green;
   - run `workflow_dispatch` once for macOS and Windows.
4. Tag the phases whose exit criteria are met, `v0.1.0` to `v0.5.0`, only
   after each phase's exit criteria have been checked.
5. Phase 6, web UI and Tauri. Part 1, the web UI, is built (`apps/web/`, ADR
   0024): the e2e click-through passes, and CI has a `web` job. Next is part 2,
   the Tauri shell with the PyInstaller sidecar, per `phase-6.md`. When
   `GET /audit` is wired, run the e2e again against real rows.
6. Phase 7, browser extension: built (`apps/extension/`, ADR 0026, plan in
   `phase-7.md`). `[proxy] extension_ids` in the gateway; the rehydrator is
   pinned to the engine by parity vectors; the e2e (`tests/e2e/test_extension.py`,
   Chromium on local fixture pages) passes; CI has an `extension` job. Not done:
   Firefox tested in a browser, a check against the live sites, settings sync with
   the desktop app, the pre-tag review and tag `v0.7.0`. Phase 8 is pilot
   hardening.

## Known open items

- GLiNER is slow on CPU: about 76 s per 100 KB of Armenian text.
- Keyword windows cross sentence boundaries, which over-masks (ADR 0023).
- Property tests for the new shape recognizers are still missing.
- The secret-scan hook checks the session root rather than the worktree
  (SECURITY.md limits).
