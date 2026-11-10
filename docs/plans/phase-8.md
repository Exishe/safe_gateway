# Phase 8: Pilot hardening (outline, ongoing)

> The free documentation and CI parts are done. The pilot itself and the tag are the owner's.

**Goal:** Something two or three friendly firms can pilot, with honest documentation.

**Scope**
- [ ] Weekly `leak-hunter` red-team run; every bypass becomes a failing test, then a fix.
  The scheduled regression run is done (below); a new red-team round is still a manual run of
  the agent.
- [x] Semgrep and pip-audit already in CI; scheduled weekly run added:
  `.github/workflows/weekly.yml` (Monday 04:23 UTC and manual; Ubuntu only; Semgrep, pip-audit,
  `npm audit --omit=dev` for `apps/web` and `apps/extension`, leak-hunt tests). ADR 0028.
  `zizmor --offline` passes. The first scheduled run has not happened yet.
- [x] One-page security overview for prospects: `docs/SECURITY_OVERVIEW.md`. Figures taken from
  `docs/BENCHMARKS.md` (English OpenPII run, 2026-11-10). It states pseudonymisation under GDPR
  Art. 4(5) and does not claim HIPAA Safe Harbor. `legal:compliance-check` was run; it is generic
  guidance and gave no wording change for this page. The owner or counsel should still read it.
- [x] Incident runbook for a suspected leak: `docs/RUNBOOK_SUSPECTED_LEAK.md`.
  Not yet rehearsed; do a dry run with a pilot firm.
- [x] Unsigned pilot installers with the first-run steps documented (`docs/INSTALL.md`, Phase 6).

**Exit criteria**
- [x] Measured recall table current in `docs/BENCHMARKS.md` (last run 2026-11-10; re-run after any
  recognizer change).
- [ ] Owner: run the pilot with two or three firms and collect their feedback in
  `docs/pilot-feedback.md`.
- [ ] Owner: tag `v0.8.0` after the pilot and after v0.4.0 to v0.7.0 (see `docs/RELEASE_CHECKS.md`).
