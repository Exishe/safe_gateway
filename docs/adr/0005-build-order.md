# 0005. Proxy before extension

- Status: Accepted
- Date: 2026-11-09

## Context

The brief offers three front doors: desktop app, browser extension, and an
OpenAI-compatible URL. All of them call the same local proxy.

## Decision

Build in the brief's order: engine, mask/rehydrate, parsers, proxy, audit,
web UI and Tauri, then the extension. The extension is a client of
`POST /mask`, so it cannot come first.

## Consequences

- The first demo is a CLI plus the OpenAI-compatible endpoint, which any AI
  client can already use.
- Extension work waits for a stable `/mask` contract.
