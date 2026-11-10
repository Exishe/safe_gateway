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

## Amendment 2026-11-09: the disk store as built

`gateway/engine/session_disk.py` holds `EncryptedMappingStore`; it is used only
when a `SessionStore` is built with `persist=...`, so the default stays
memory-only and writes nothing. It stores the mapping (placeholder to original
value, as JSON) encrypted with Fernet in `<SAFEGUARD_HOME>/sessions/` (default
`~/.safeguard/sessions`), one file per session, written to a temporary file and
renamed, mode 0600 in a 0700 directory. The plaintext is never written. Each
session has its own key from `Fernet.generate_key()`. The key is held by a
`KeyProvider`; the default, `gateway/secrets.py`, uses `keyring` with service
`safeguard-gateway`. File name and key name are both the sha256 of the session
id, so neither reveals the id and an expired file can be matched to its key. A
wrong key or a changed or truncated file raises `InvalidToken`; no partial
mapping is returned. Delete and expiry remove the file and the key, and
`purge_older_than(seconds)` does the same for files not written for that long.
Encryption protects the files at rest; it does not protect against another
process of the same user, which can ask the keychain for the key.
