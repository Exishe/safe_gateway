# Phase 5: Audit log (outline)

> Outline only. Brainstorm, then write the task plan here when the phase starts.

**Goal:** A tamper-evident local log that records what was sent, without ever storing the text.

**Scope**
- `gateway/audit/`: SQLite at `~/.safeguard/audit.db`. Columns: id, ts_utc, session_id, upstream_host, model, entity_counts_json, masked_payload_sha256, bytes_out, status, local (true for Ollama), prev_hash, row_hash, hmac.
- `row_hash = sha256(prev_hash || canonical_json(row))`; HMAC keyed with a device key stored in the keychain.
- `safeguard audit verify` reports the first broken row. `safeguard audit export --from --to` writes signed JSON and a PDF summary (PDF writer must be permissively licensed).
- Retention by days from config, implemented so that deleting old rows does not break verification of the rest (checkpoint row).

**Exit criteria**
- Tests: editing, deleting or reordering any row is detected; a property test that no stored column contains the raw or masked text.
- Docs call it "tamper-evident", never "tamper-proof". Tag `v0.5.0` (MVP = Phases 0 to 5 plus a bare web UI).
