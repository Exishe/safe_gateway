# 0002. Where the placeholder map lives

- Status: Accepted
- Date: 2026-11-09

## Context

Under GDPR Art. 4(5) masked text plus the mapping is still personal data, so
the mapping must stay on the device, be as short-lived as possible, and be
deleted at session end. OpenAI- and Anthropic-shaped requests are stateless:
each request resends the whole conversation, including earlier assistant
answers, which the client already holds in rehydrated (real) form.

## Decision

1. **Proxy chat routes are stateless by default.** Each request builds a fresh
   mapping over the full message list. Because placeholders are numbered in
   order of first appearance (ADR 0001) and earlier messages do not change,
   the same value gets the same placeholder on every turn without the proxy
   keeping any state. The mapping lives only for the request and its streamed
   response, then is dropped.
2. **Explicit sessions for split flows.** The browser extension and the web UI
   mask first and rehydrate later. For those, `POST /mask` returns a random
   session id. The mapping is kept in process memory with an idle TTL
   (default 30 minutes) and is deleted on `DELETE /session/{id}` or on expiry.
   The extension keeps its copy in `chrome.storage.session`, which the browser
   clears when it closes.
3. **Disk persistence is opt-in** (document workflows only). The store is
   encrypted with Fernet under a per-session key held in the OS keychain via
   `keyring`, and is deleted with the session.

## Consequences

- No server-side state for the common case, so there is nothing to leak after
  the response ends.
- If a user edits an old message, numbering can shift between turns. That only
  confuses the model; it cannot reveal a value.
