# 0009. MCP servers, hooks and scanners used while building

- Status: Accepted
- Date: 2026-11-09

## Context

The brief lists Context7, GitHub (`@modelcontextprotocol/server-github`),
Playwright and SQLite MCP servers, plus hooks for licenses, egress, tests,
secrets and formatting.

## Decision

- **GitHub MCP:** the `@modelcontextprotocol/server-github` npm package is
  deprecated upstream. `.mcp.json` uses GitHub's official remote server
  (`https://api.githubcopilot.com/mcp/`) with a token read from the
  `GITHUB_PAT` environment variable, never written to the file.
- **Playwright MCP** is pinned to an exact version, not `@latest`.
- **SQLite MCP** (`mcp-server-sqlite`, pinned, run with `uvx`) points at
  `${HOME}/.safeguard/dev-audit.db`, a separate file from the real
  `audit.db`. The server can write, so it never points at a real log.
- **Secret scanning:** `detect-secrets` (Apache-2.0, a Python dev dependency)
  instead of `gitleaks`, so no extra binary is needed. A small PII pattern
  check (SSN shape, Luhn-valid card numbers) runs next to it. Test fixtures
  that need a literal fake identifier carry a `pragma: allowlist secret`
  comment.
- **Semgrep** runs with `--metrics=off` and pinned registry rule packs
  (`p/python`, `p/secrets`), not `--config auto`, which sends project metadata
  to the registry.
- **License check** covers the runtime dependency closure of
  `safeguard-gateway`, not dev tools. Dev tools under LGPL (Semgrep) are
  allowed because they are never shipped.

## Consequences

- Contributors set `GITHUB_PAT` in their shell to use the GitHub MCP server.
- An unknown or LGPL runtime license fails the check until a reviewed
  exception is added to `tools/license_policy.toml` with a reason.
