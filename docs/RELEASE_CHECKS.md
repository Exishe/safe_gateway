# Release checks

What was checked before a tag, when, and what it found. Newest first.

## 2026-11-10, branch head 70a48f4 (candidate for v0.1.0 to v0.7.0)

### Supply chain

Tool: the `supply-chain-risk-auditor` skill (`collect.py` and `render.py`), run on
2026-11-10 against `pyproject.toml` with `uv.lock` (commit cef0d70) and
`apps/web/package-lock.json`. `gh` was not authenticated, so the repository and
Scorecard criteria could not be assessed. The coverage table in each report bounds
every statement below.

- **Python:** no known advisory for any of the 31 direct dependencies, 17 of them
  shipped, or for the 133 transitive packages at their locked versions. Deprecated
  or yanked releases: none among the 30 of 31 assessed.
- **Web UI:** no known advisory for any of the 8 shipped packages.
  - `clsx` and `class-variance-authority` each have a single human publisher. They
    have 164,311,438 and 80,794,628 weekly downloads.
  - One transitive advisory, GHSA-g3hv-75wg-m249, affects `minimatch` 10.2.6. It is
    used at build time only and is not shipped.
  - Six lockfile entries, the `@emnapi/*`, `@napi-rs/wasm-runtime`, `@tybys/wasm-util`
    and `tslib` packages, resolve from outside the npm registry and could not be
    checked for advisories.
- **Browser extension:** it ships no npm packages. Its dev-only packages have the
  same `minimatch` advisory.
- **Runtime vulnerability scan:** `pip-audit` in CI reports no known
  vulnerabilities. It cannot audit `torch 2.15.0+cpu`, which comes from the PyTorch
  index.

### Security reviews

| Date | Scope | Critical | Important | Status |
|------|-------|----------|-----------|--------|
| 2026-11-09 | Guards and hooks | 0 | 2 | fixed |
| 2026-11-09 | Phases 2–5 (29a019a) | 0 | 4 | fixed |
| 2026-11-10 | Session-aware chat, link rule, proxy limits (756896a) | 0 | 2 | fixed |

### Red team (leak-hunter)

Rounds 1a, 1b, 1c, 1d and 2 found about 75 bypasses. All of them are fixed except
4 recorded limits, which are strict xfails in `tests/`. A round on the parsers and
engine round 1e are in progress.

### CI

- **Ubuntu job, web UI job, extension job:** green on cef0d70.
- **Windows job:** failed on cef0d70, because the suite could not be collected:
  `tools/benchmark_gliner.py` imported the Unix-only `resource` module. Fixed in
  70a48f4 and re-run.
- **macOS job:** cancelled before it started on cef0d70. It needs a check of the
  repository's Actions minutes or spending limit, which only the owner can change.

### Not checked by this session

- A manual run against a local Ollama model.
- The extension on the live chatgpt.com, claude.ai and gemini.google.com sites.
- Firefox.
- Signed installers. Signing is a paid item and is on the approval list.
