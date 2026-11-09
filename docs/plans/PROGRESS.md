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

## In flight (agent worktrees under `.claude/worktrees/`)

| Work | ADRs | Status |
|------|------|--------|
| Fix 7 Phase 2 red-team findings (`test_leak_hunt_2.py`) | 0014 amendment | running |
| Model download hardening (M3 marker file, M4 redirects and size cap) | 0006 amendment | merged b1b3015 |
| Fix 15 leak-hunt 1c findings (`test_leak_hunt_1c.py`) + shape property tests + benchmark re-run | 0016/0023 amendments | running |
| Phase 3 parsers (pdf, docx, xlsx, txt, eml + redacted write-back) | 0017, 0018 | running |
| Phase 4 proxy (`safeguard serve`, OpenAI/Anthropic shapes, adapters) | 0019, 0020 | running |
| Phase 5 audit log (hash chain, HMAC, verify, export, retention) | 0021, 0022 | running |
| Leak-hunt 1c on the newest engine changes (`test_leak_hunt_1c.py`) | none | merged 050ff8c (15 strict xfails) |

If an agent's worktree branch has commits but the agent is gone, merge what
is there and finish the remainder yourself or with a new agent.

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

1. Merge the in-flight work above as each piece lands. Launch a fix agent for
   the leak-hunt 1c findings.
2. Integrate Phase 4 with Phase 5: wire the proxy's `AuditSink` to
   `AuditLog.record`, and replace the `GET /audit` 501 stub.
3. Pre-tag review:
   - `security-reviewer` agent on the full diff;
   - Semgrep, supply-chain and code-review skills;
   - CI green;
   - run `workflow_dispatch` once for macOS and Windows.
4. Tag the phases whose exit criteria are met, `v0.1.0` to `v0.5.0`, only
   after each phase's exit criteria have been checked.
5. Phase 6, web UI and Tauri: follow the outline in `phase-6.md`, using the
   `ui-builder` agent and the frontend-design skill. Run Playwright
   click-through.
6. Phase 7, browser extension. Phase 8 is pilot hardening.

## Known open items

- GLiNER is slow on CPU: about 76 s per 100 KB of Armenian text.
- Keyword windows cross sentence boundaries, which over-masks (ADR 0023).
- Property tests for the new shape recognizers are still missing.
- The secret-scan hook checks the session root rather than the worktree
  (SECURITY.md limits).
