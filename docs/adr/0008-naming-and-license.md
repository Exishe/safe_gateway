# 0008. Package names and the product's own license

- Status: Accepted
- Date: 2026-11-09

## Context

The brief's layout uses a top-level `gateway/` package and a `safeguard` CLI.
The GitHub repository is `exishe/safe_gateway`. The product is meant to be
sold.

## Decision

- Distribution name `safeguard-gateway`, import package `gateway` (flat layout,
  as in the brief), console script `safeguard`.
- The product is proprietary: no open-source license file is added, and
  `pyproject.toml` declares `license = "LicenseRef-Proprietary"`. This keeps
  every option open; publishing under an open license later is possible, but
  taking one back is not.
- Third-party attributions (CC-BY-4.0 for OpenPII, Apache NOTICE files) are
  collected in `THIRD_PARTY_NOTICES.md` when packaging starts (Phase 6).

## Consequences

- `gateway` is a generic import name. It is only installed inside the app's
  own environment and the PyInstaller sidecar, so a clash is unlikely; rename
  it before any public PyPI release.
