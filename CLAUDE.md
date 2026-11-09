# SafeGuard Gateway

Local-first privacy gateway. It detects and masks personal data on the user's
machine, sends only masked text to the customer's own cloud model (BYOK), and
restores real values in the answer. It keeps a tamper-evident local audit log.
Customers: small law, accounting, medical and HR firms.

Design decisions live in `docs/adr/`. The phase plans live in `docs/plans/`.
Read the plan for the current phase before writing code.

## Commands

```bash
uv sync                                   # install everything (dev group by default)
uv run pytest                             # full suite: sockets disabled, coverage floor 80%
uv run ruff format . && uv run ruff check .
uv run ty check
uv run python -m tools.check_no_egress    # hard rule 2
uv run python -m tools.check_licenses     # hard rule 1
uv run safeguard --version                # the CLI (scan, mask, serve, audit arrive per phase)
scripts/install-git-hooks.sh              # once per clone: enables .githooks/pre-commit
```

## Layout

```
gateway/engine/    detection, masking, rehydration. NO NETWORK, no subprocess, no dynamic imports
gateway/parsers/   pdf, docx, xlsx, txt, eml -> Document(text, spans, metadata)
gateway/audit/     sqlite hash chain, hmac, verify, export
gateway/cloud/     upstream adapters; the ONLY package allowed outbound requests
gateway/proxy/     FastAPI on 127.0.0.1:8787
gateway/cli.py     `safeguard` command
apps/              web, desktop (Tauri 2), extension (MV3), from Phase 6
tools/             repo guards used by hooks, the git pre-commit hook and CI
tests/             unit/ property/ integration/ e2e/ benchmarks/
docs/              adr/ plans/ BENCHMARKS.md APPROVAL_NEEDED.md SECURITY.md INSTALL.md
```

## Hard rules

1. **Zero cost, permissive licenses only.** Shipped code may use MIT, BSD,
   Apache-2.0, PSF, MPL-2.0, public domain, or CC-BY-4.0 data with attribution.
   Never AGPL, GPL (tooling with a runtime exception excepted), CC-BY-NC, a paid
   API or SaaS. Never PyMuPDF, pdftext, or GLiNER v0/v1 base models.
   `tools/license_policy.toml` is the source of truth.
2. **If a step needs a paid service, stop.** Add it to `docs/APPROVAL_NEEDED.md`
   with cost and reason, and continue with a free alternative or a stub.
3. **No egress from the engine.** Only `gateway/cloud/` may make outbound
   requests, and only to the upstream the user configured. Model files are
   fetched only by `safeguard models pull` (ADR 0006). Never disable the check.
4. **Test first.** Write the failing test, watch it fail, then write the code.
   Structured identifiers (SSN, card, IBAN, phone, national IDs) must reach 100%
   detection on generated data. Names, addresses and health details are
   measured, not asserted.
5. **No guessed numbers.** A recall figure goes in `docs/BENCHMARKS.md` only
   with the dataset, its version, the command and the date it was measured.
6. **Verify before you report.** Run the suite, run the CLI on a sample, open
   the UI in Playwright. Report only what you observed. Never "should work".
7. **Honest claims.** Write "reduces exposure", "tamper-evident",
   "pseudonymised". Never "guarantees", "eliminates liability", "anonymised",
   "HIPAA compliant". Compliance belongs to the customer; we provide controls
   and evidence.
8. **Fake data only.** Use Faker or invalid-by-construction values. A literal
   fake identifier in a test carries `# pragma: allowlist secret`.

## Style

Plain, readable Python with type hints. Small functions. No clever
metaprogramming. Comments explain why, not what. Ruff (`select = ["ALL"]`) and
ty must pass.

## Guards that run automatically

| Where | What |
|-------|------|
| Claude Code hooks (`.claude/hooks/`, logic in `tools/hooks.py`) | Before Bash: bash-guard (no `rm -rf`, no curl/wget beyond loopback), license-guard (denied packages), secret-scan (before `git commit`). After Edit/Write: no-egress, ruff format/fix, tests for the touched module. After Bash: license audit when dependencies change. |
| Git pre-commit (`.githooks/pre-commit`) | ruff, no-egress, staged PII scan, detect-secrets. This is the authoritative commit gate. |
| CI (`.github/workflows/ci.yml`) | Everything above plus ty, pytest, bandit, pip-audit, Semgrep, zizmor. macOS and Windows only on tags (ADR 0007). |

## How work is done

- Per phase: `superpowers:brainstorming` once for open questions, then
  `superpowers:writing-plans` into `docs/plans/phase-N.md`, then
  `superpowers:executing-plans`. Every feature goes through
  `superpowers:test-driven-development`.
- Decide reversible questions yourself and record an ADR in `docs/adr/`. Ask the
  user only about decisions that cannot be undone.
- Subagents in `.claude/agents/`: `test-writer` (Sonnet) before each feature,
  `leak-hunter` (Opus) after each engine change, `security-reviewer` (Opus)
  before each tag, `docs-checker` (Haiku, Context7) before writing against a
  library, `ui-builder` (Opus) for front ends only.
- Model routing: Sonnet or Haiku for boilerplate, tests, lookups and docs. Opus
  for engine design, rehydration logic, security review and UI design.
- Before each release tag: `anthropic-skills:semgrep`,
  `anthropic-skills:supply-chain-risk-auditor`, `code-review:code-review`.
- End of phase: all tests green, ruff clean, Semgrep clean, tag `v0.N.0`, a
  short ADR, `docs/BENCHMARKS.md` updated after any recognizer change.
- End of session: say which skills were used, what is verified working, what
  is stubbed, and the next step.
