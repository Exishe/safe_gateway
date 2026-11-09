# Phase 8: Pilot hardening (outline, ongoing)

> Outline only. Brainstorm, then write the task plan here when the phase starts.

**Goal:** Something two or three friendly firms can pilot, with honest documentation.

**Scope**
- Weekly `leak-hunter` red-team run; every bypass becomes a failing test, then a fix.
- Semgrep and pip-audit already in CI; add a scheduled weekly run.
- One-page security overview for prospects, checked with `legal:compliance-check` against GDPR Art. 4(5) and HIPAA Safe Harbor wording.
- Incident runbook for a suspected leak (`operations:runbook`).
- Unsigned pilot installers with the first-run steps documented.

**Exit criteria**
- Measured recall table current in `docs/BENCHMARKS.md`.
- Feedback from the pilot firms collected in `docs/pilot-feedback.md`. Tag `v0.8.0`.
