# 0021. Audit log: hash chain, device key and retention

- Status: Accepted
- Date: 2026-11-09

## Context

Phase 5 adds a local audit log: one row per request the proxy sends upstream,
recording where it went, how much, which kinds of value were masked, and a hash
of the masked payload. The customer uses it as evidence of what left the
machine. It must never hold the text itself, raw or masked.

The log is a file on the user's machine, so anyone with write access can edit
it. What we can offer is tamper evidence: a change by someone who does not hold
the device key is reported, with the first row it affects. Someone who holds
the key and can write the file can rewrite history; any process of the same OS
user can ask the keychain for the key. Old rows must also be deletable for
retention without making the rest unverifiable.

## Decision

1. **Storage.** SQLite at `<SAFEGUARD_HOME or ~/.safeguard>/audit.db`. The file
   is created with mode 0600 before SQLite opens it; a missing directory is
   created 0700, and an existing one keeps its mode. Rollback journal
   (`journal_mode=DELETE`, not WAL), `synchronous=FULL`, `secure_delete=ON` so
   purged rows are overwritten in the file, `STRICT` tables, and
   `PRAGMA user_version = 1`; a file with a newer version is refused.

2. **One table, two kinds of row.** `audit_log` columns, in order: `id`,
   `kind` (`request` or `checkpoint`), `ts_utc`, `session_id`,
   `upstream_host`, `model`, `entity_counts_json`, `masked_payload_sha256`,
   `bytes_out`, `status`, `local`, `purged_through_id`, `purged_through_hash`,
   `purged_count`, `purged_before_utc`, `prev_hash`, `row_hash`, `hmac`. A
   request row fills the request columns and leaves the four `purged_*`
   columns NULL; a checkpoint row does the opposite.

3. **What a request row may hold.** `AuditLog.record()` takes keyword arguments
   of plain types and refuses (ValueError) any value outside its shape; it
   never trims or cleans one, because a cleaned value would still be part of
   the text.
   - `ts_utc`: the gateway's clock, `YYYY-MM-DDTHH:MM:SS.ffffffZ`, so the
     strings sort as times.
   - `session_id`: stored as the sha256 hex of what the caller passes, or `""`
     for none. A session id is a bearer handle for a mapping (ADR 0002 point
     2) and exports travel; the hash still groups a session's rows.
   - `upstream_host`: a host name, IPv4 address or bracketed IPv6 address with
     an optional port, at most 253 characters. A URL, path, query or user
     name is refused.
   - `model`: 1 to 200 characters of `A-Z a-z 0-9 . _ : / @ + -`. No spaces,
     so no sentence fits.
   - `entity_counts`: a dict of type names (`[A-Z][A-Z0-9_]{0,63}`, at most 64)
     to counts, stored as canonical JSON.
   - `masked_payload_sha256`: 64 lowercase hex characters.
   - `bytes_out`: integer from 0 to 2^53 - 1.
   - `status`: the upstream HTTP status (100 to 599), or 0 when no response
     came back (connection error, timeout, cancelled).
   - `local`: true when the upstream runs on this machine (Ollama).

4. **Canonical JSON.** RFC 8785 (JCS) restricted to what the log stores:
   objects, arrays, ASCII strings, integers within ±(2^53 - 1), booleans and
   null. It is produced by `json.dumps(sort_keys=True, separators=(",", ":"),
   ensure_ascii=True, allow_nan=False)` after a check that refuses floats,
   non-ASCII strings and larger integers. For that subset the bytes are
   exactly JCS, so any JCS library reproduces them.

5. **The chain.** A row's content is every column except `prev_hash`,
   `row_hash` and `hmac`, so `id` and `kind` are covered.
   `row_hash = sha256(prev_hash || canonical_json(content))` in lowercase hex,
   where `prev_hash` is the previous row's `row_hash` as 64 ASCII hex
   characters; the first row's `prev_hash` is 64 zeros.
   `hmac = HMAC-SHA256(device_key, "safeguard-audit/row/v1\n" || row_hash)`.
   The tags (`row`, `head`, `export`) keep the three uses of one key apart.

6. **The head record.** A chain cannot show that its newest rows were deleted.
   A one-row table `audit_head` holds `head_id`, `head_hash` and
   `HMAC(device_key, "safeguard-audit/head/v1\n" || canonical_json({"head_hash",
   "head_id"}))`, updated in the same transaction as each write. The writer
   chains from the head, not from the newest row it finds, and refuses to
   write (`AuditChainBroken`) when the head is missing, fails its HMAC or does
   not match the newest row. Otherwise a deletion at the end would be healed
   by the next write. The proxy should fail closed on that error.

7. **The device key.** 32 random bytes written as 64 hex characters, kept in
   the OS keychain by `gateway/secrets.py` (service `safeguard-gateway`, entry
   `audit-device-key`). The HMAC key is those 64 ASCII bytes, so `openssl dgst
   -sha256 -hmac` reproduces any HMAC. The writer creates the key on first use
   inside its `BEGIN IMMEDIATE` transaction, so two processes starting at once
   agree on one key. It refuses to create a key for a log that already has
   rows (`DeviceKeyMissing`): the old key is lost or the file came from
   another device, and new rows under a new key would hide that. `verify` and
   `export` never create a key. Tests inject an in-memory provider.

8. **Writes.** One SQLite connection per `AuditLog`, guarded by a thread lock;
   each `record()` is one `BEGIN IMMEDIATE` transaction (also the lock between
   processes): read and check the head, assign `id = head_id + 1`, insert,
   update the head, commit.

9. **Retention.** `purge(older_than_days=N)`, N at least 1, from
   `safeguard audit purge --older-than-days N` or, without the flag,
   `retention_days` from `config.toml` (point 12.5). In one
   transaction:
   1. verify the whole log, and refuse if it is broken, because purging the
      broken part would launder the evidence;
   2. find K, the last id of the longest run of rows from the oldest that are
      all older than the cutoff. Only a prefix of the chain is ever deleted,
      even when a clock step left an old row after a newer one;
   3. append a checkpoint row at `head_id + 1`, chained and HMACed like any
      row, with `purged_through_id = K`, `purged_through_hash` = row K's
      `row_hash`, `purged_count` and `purged_before_utc` (the cutoff);
   4. delete rows up to K, and update the head.

   The newest checkpoint is the anchor: verification expects the oldest
   remaining row to have id K + 1 and `prev_hash` equal to the carried hash.
   A checkpoint is an ordinary row, so a later purge can delete it; the next
   checkpoint carries the anchor forward. Nothing old means no checkpoint.

10. **Verification.** `verify` opens the file read-only, takes the anchor (the
    newest checkpoint, or id 0 and 64 zeros), walks the rows in id order with
    one row in memory, and stops at the first problem:
    1. a value that cannot be hashed (wrong type, non-ASCII), an unknown
       `kind`, `local` not 0 or 1, or a hash column that is not 64 hex
       characters: **edited**;
    2. the recomputed `row_hash` differs from the stored one: **reordered** if
       the row hashes correctly under another id (it was moved), otherwise
       **edited**;
    3. the HMAC does not match: **bad HMAC** (rewritten without the key, or a
       row from another device);
    4. an id below the anchor: **inserted** (a row the checkpoint says was
       purged); `prev_hash` or `id` not following the previous row:
       **reordered** if `prev_hash` names a row still in the log, otherwise
       **deleted**, reporting the first missing id;
    5. after the last row, the head: missing while rows exist, or naming a
       later row, means **deleted** (the first missing id); a bad head HMAC
       means **bad HMAC**; rows after the head mean **inserted**; a head hash
       that differs from the newest row means **edited**.

    `VerifyResult` holds `ok`, `rows_checked`, `first_bad_id`, `reason`,
    `detail`, `head_id`, `head_hash` and `purged_through_id`.
    `safeguard audit verify` prints one line and exits 0 when the log is
    intact, 1 when it is broken, and 2 when it cannot check (no file, no key,
    not an audit log).

11. **Writer API for the proxy.** `AuditLog.record(*, session_id: str,
    upstream_host: str, model: str, entity_counts: dict[str, int],
    masked_payload_sha256: str, bytes_out: int, status: int, local: bool) ->
    int` (the new row id). `AuditLogSink` in `gateway/audit/sink.py` wraps it
    for Phase 4's per-request `AuditSink` hook and accepts any
    `Mapping[str, int]` of counts, such as a `Counter` keyed by `EntityType`.
    It implements the proxy's `record(event)` (point 12). The proxy calls it
    from a worker thread, since it waits for the disk.

12. **Proxy integration (Phase 4 + 5).** Decided 2026-11-09, without the owner
    (reversible).
    1. `AuditLogSink` implements the proxy's `AuditSink.record(event)` (ADR
       0019). A row is written for every event whose masked payload was handed
       to the upstream adapter, whatever happened next: answered, upstream error
       status, timeout or network failure, a stream that broke midway, a client
       that left. Requests refused before forwarding, `/mask` and session
       deletes send nothing anywhere and write no row. `session_id` is `""`
       (the chat routes keep no session), `bytes_out` is the length of the exact
       body sent, `masked_payload_sha256` its digest, `status` the upstream's own
       HTTP status whenever one came back (also when the client or the stream
       left later), 0 when none did. An IPv6 host is stored in brackets.
    2. **Fail closed while serving.** The row is written after the response
       (ADR 0019), so a failed write cannot undo the request in flight. Once a
       write fails, the chat routes answer 503 and forward nothing more until
       `serve` is restarted; `/mask` keeps working because it sends nothing.
       No row is written before forwarding: it would need a second write per
       request and still could not record the outcome.
    3. **Start-up.** `safeguard serve` opens `<SAFEGUARD_HOME>/audit.db` with
       the keychain's device key, verifies the whole log (or purges, which
       verifies first) and refuses to start, before binding, when the keychain
       is unavailable, the key is missing for a log with rows, or the log fails
       verification. The message names the cause and `--no-audit`.
    4. **`--no-audit` is kept**, as an explicit per-run flag, never a
       `config.toml` setting, so it cannot be left on silently. Without it a
       machine with no usable OS keychain (a headless Linux box without a
       Secret Service) could not use the gateway at all, and the masking is
       worth more than its absence. With it, `serve` prints a warning, writes
       nothing and never asks the keychain for the device key; `/health` says
       `"audit": false` and `/audit` answers 404.
    5. **Retention.** `retention_days` is read through `gateway/config.py`
       only (the separate reader of point 9 is gone), by `audit purge` and at
       `serve` start-up. Its default is now **none: every row is kept** until
       the user sets a period. Phase 4 had defaulted it to 30; a purge cannot
       be undone, and how long evidence must be kept is the customer's
       decision, so the tool does not delete it by default. Purging runs at
       start-up only; a gateway left running for months purges at its next
       start.
    6. **Security review, 2026-11-09 (M9, M10).** Before the file is created
       or tightened to 0600, an existing path is checked: a symbolic link, a
       non-regular file, or a non-empty file that is not an audit log of this
       schema is refused (`NotAnAuditLog`) and left as it was. The file is
       opened with `O_NOFOLLOW` and its mode set through the open descriptor.
       Every directory created on the way is 0700, not only the last.
       `audit purge --db FILE` needs `--older-than-days`: `config.toml` speaks
       for the default home's log only. Retention periods under 7 days are
       refused unless `audit purge --force` is given; `config.toml` cannot set
       one at all. The period used is recorded by the checkpoint already:
       `ts_utc` minus `purged_before_utc`. No column was added, so the schema
       stays at version 1.

## Consequences

- Editing, deleting, reordering or inserting rows, and rows from another
  device, are reported with the first row affected, by anyone checking with
  this device's key. Tests pick the row and column with Hypothesis.
- Not tamper-proof. Someone with the device key and write access can rebuild
  the chain from any row. Deleting the whole file, or putting back an older
  copy of it (rows and head together), cannot be detected on this machine.
  Comparing the chain head with an earlier export can detect it, so exports
  should be kept somewhere else.
- `ts_utc` comes from the local clock. It is not a trusted timestamp.
- A broken log blocks writes and purges. The fix is to move the file aside,
  keep it as evidence, and let the gateway start a new log. A new log
  verifies on its own; it does not vouch for the old one.
- HMACs are symmetric: a third party can recompute the hashes and check the
  links in an export (ADR 0022), but checking the HMACs needs the device key,
  and holding that key lets them forge rows.
- `secure_delete` overwrites purged rows inside the database file. It cannot
  wipe copies the filesystem, backups or SSD wear-levelling kept.
