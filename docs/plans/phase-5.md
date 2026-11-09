# Phase 5: Tamper-evident audit log

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every request the proxy sends upstream leaves one chained, HMACed row in `~/.safeguard/audit.db` that holds counts and hashes, never text; `safeguard audit verify` names the first edited, deleted, reordered or foreign row, `export` writes signed JSON and a PDF summary, and `purge` applies retention without breaking verification.

**Architecture:** `gateway/audit/chain.py` holds canonical JSON and the three keyed or keyless digests (row hash, row HMAC, head HMAC). `rows.py` turns `record()` arguments into a validated row and checks stored rows. `store.py` owns SQLite: schema, permissions, the writer (`AuditLog.record`, `AuditLog.purge`) and the read-only opener. `verify.py` walks the chain. `export.py` builds and signs the JSON and the summary lines that `pdf.py` renders. `sink.py` adapts the writer to the proxy's hook, and `commands.py` holds the `safeguard audit` subcommands, which `gateway/cli.py` only registers. The device key comes from `gateway/secrets.py`.

**Tech Stack:** Python 3.12 standard library (`sqlite3`, `hashlib`, `hmac`, `json`, `tomllib`). Hypothesis and Faker for tests. No new dependency: reportlab was evaluated and rejected (ADR 0022 point 8).

**Spec:** ADR 0021 (chain, key, retention, verify) and ADR 0022 (export and PDF). Read both first; this plan does not repeat their rules.

## Global Constraints

- `gateway/audit/` imports no network, process or dynamic-import module; run `uv run python -m tools.check_no_egress` after each task.
- Docs say "tamper-evident", never "tamper-proof" except to deny it.
- No test touches the real keychain: `tests/unit/audit/conftest.py` installs a keyring backend that fails on any call, and tests inject `InMemoryKeyProvider`.
- Fake data only (Faker or invalid-by-construction); literal identifiers in tests carry `# pragma: allowlist secret`.
- `gateway/cli.py` changes by one import, one registration call and one dispatch line; everything else lives in `gateway/audit/commands.py`. Other agents edit `cli.py` in parallel.
- Output from `gateway/audit/commands.py` goes through `sys.stdout.write` and `sys.stderr.write` (T201 is only waived for `gateway/cli.py`).
- Commit by staging in one Bash call and committing in the next (secret-scan reads the index). Never `--no-verify`.

## Review Focus

1. The proxy passes a URL as `upstream_host`, or a client-chosen model name with spaces: `record()` raises ValueError and writes nothing. Tests in Task 1 and Task 3.
2. Someone deletes the newest row and the gateway then writes again: the write is refused (`AuditChainBroken`), so the deletion is not healed. Test in Task 3.
3. Two writers at once (threads, or two `AuditLog` objects on one file as two processes would be): ids stay consecutive and the log verifies. Test in Task 3.
4. A purge over a tampered log refuses instead of deleting the evidence; a clock step back leaves an old row after a newer one and only the prefix is purged. Tests in Task 5.
5. `verify` and `export` on a missing file or without a key create neither and exit 2. Tests in Task 9.

---

### Task 1: Canonical JSON, digests and row validation

**Files:** create `gateway/audit/chain.py`, `gateway/audit/rows.py`; tests `tests/unit/audit/test_chain.py`, `tests/unit/audit/test_rows.py`, `tests/unit/audit/conftest.py`.

**Interfaces (produces):**
- `chain.GENESIS_HASH = "0" * 64`; `chain.MAX_SAFE_INT = 2**53 - 1`.
- `chain.canonical_json(value: object) -> bytes`: ADR 0021 point 4; raises ValueError for floats, bytes, non-ASCII strings, integers beyond `MAX_SAFE_INT`, non-string keys.
- `chain.row_hash(prev_hash: str, content: Mapping[str, object]) -> str`; `chain.row_hmac(key: bytes, row_hash: str) -> str`; `chain.head_hmac(key: bytes, head_id: int, head_hash: str) -> str`; `chain.tagged_hmac(key: bytes, tag: str, data: bytes) -> str` (tags `row`, `head`, `export` become `safeguard-audit/<tag>/v1\n`).
- `rows.CONTENT_COLUMNS` (the 15 hashed columns, ADR 0021 point 2 order) and `rows.COLUMNS` (plus `prev_hash`, `row_hash`, `hmac`).
- `rows.format_ts(moment: datetime) -> str` (aware datetimes only).
- `rows.request_content(*, row_id: int, ts_utc: str, session_id: str, upstream_host: str, model: str, entity_counts: Mapping[str, int], masked_payload_sha256: str, bytes_out: int, status: int, local: bool) -> dict[str, object]`: validated content dict per ADR 0021 point 3, `session_id` hashed, counts as canonical JSON text.
- `rows.checkpoint_content(*, row_id: int, ts_utc: str, purged_through_id: int, purged_through_hash: str, purged_count: int, purged_before_utc: str) -> dict[str, object]`.
- `rows.stored_problem(row: Mapping[str, object]) -> str | None` (ADR 0021 point 10.1) and `rows.content_of(row: Mapping[str, object]) -> dict[str, object]` (`local` 0/1 becomes a bool).

- [ ] Tests: `canonical_json({"b": 1, "a": [True, None, "x"]}) == b'{"a":[true,null,"x"],"b":1}'`; floats, `"é"`, `2**53` and `{1: 2}` raise; `row_hash` equals `hashlib.sha256(b"0"*64 + b'{"id":1}').hexdigest()` for `{"id": 1}`; `row_hmac` equals `hmac.new(key, b"safeguard-audit/row/v1\n" + h.encode(), sha256)`; row and head HMACs of the same input differ; `format_ts(datetime(2026, 11, 9, 8, 5, 3, 7, tzinfo=UTC)) == "2026-11-09T08:05:03.000007Z"` and a naive datetime raises; `request_content` hashes `session_id` (and keeps `""`), writes counts as `{"EMAIL":2,"PERSON":1}`; it refuses `https://api.example.com/v1`, `user@host`, a host with a space, model `"gpt 4"`, a 201-character model, key `"person"`, a negative count, `True` as a count or as `bytes_out`, a non-hex or upper-case sha256, `bytes_out = -1` or `2**53`, status 99, 600 and 1000; it accepts every `EntityType` name, `127.0.0.1:11434`, `[::1]:8787`, model `qwen2.5:3b`; `stored_problem` flags `local = 2`, an unknown `kind`, a float and a short `row_hash`.
- [ ] Run, FAIL; implement; PASS; commit `feat(audit): canonical JSON, chain digests and row validation`.

### Task 2: Device key in the keychain

**Files:** modify `gateway/secrets.py`; test `tests/unit/test_secrets.py`.

**Interfaces (produces):** `secrets.AUDIT_KEY_ID = "audit-device-key"`; `secrets.load_device_key(provider: KeyProvider) -> bytes | None`; `secrets.device_key(provider: KeyProvider) -> bytes` (get or create 64 lowercase hex characters from `os.urandom(32)`). A stored value that is not 64 hex characters raises ValueError. `KeyProvider` is the protocol in `gateway/engine/session_disk.py`.

- [ ] Tests: first call stores a 64-hex key under `audit-device-key` and returns it; second call returns the same bytes; two providers get different keys; `load_device_key` returns None and creates nothing; a corrupt stored value raises.
- [ ] Run, FAIL; implement; PASS; commit `feat: audit device key in the OS keychain`.

### Task 3: SQLite store and writer

**Files:** create `gateway/audit/store.py`; test `tests/unit/audit/test_store.py`.

**Interfaces:**
- Consumes: Task 1, Task 2.
- Produces: `store.default_audit_path() -> Path`; `AuditLogError(Exception)` with subclasses `AuditChainBroken`, `DeviceKeyMissing`, `NotAnAuditLog`; `AuditLog(path: Path, key_provider: KeyProvider, clock: Callable[[], datetime] = utc_now)` with `record(...) -> int` (signature in ADR 0021 point 11), `close()`, context manager; `store.open_readonly(path: Path) -> sqlite3.Connection` (raises `FileNotFoundError` or `NotAnAuditLog`); `store.read_head(conn) -> tuple[object, object, object] | None`; `store.iter_rows(conn, where: str = "", params: Sequence[object] = ()) -> Iterator[dict[str, object]]`.

- [ ] Tests: a fresh home gets a 0700 directory and a 0600 file, and no journal file remains after a write; ids are 1, 2, 3 and each row's `prev_hash` is the previous `row_hash` (first: 64 zeros); `row_hash` and `hmac` recompute with `chain` from the stored columns; the head names the newest row; reopening continues the chain; the table's columns equal `rows.COLUMNS`; a ValueError from validation writes nothing; deleting the newest row, or the head, then writing raises `AuditChainBroken`; a log with rows and no key raises `DeviceKeyMissing` and creates no key; a file with `user_version = 2` raises `NotAnAuditLog`; 8 threads x 25 records on one `AuditLog`, and 4 `AuditLog` objects on one file, give ids 1..N.
- [ ] Run, FAIL; implement; PASS; commit `feat(audit): SQLite store with chained, HMACed rows`.

### Task 4: Verify

**Files:** create `gateway/audit/verify.py`; tests `tests/unit/audit/test_verify.py`, `tests/property/test_audit_tamper.py`.

**Interfaces:**
- Consumes: Task 3 (`open_readonly`, `iter_rows`, `read_head`).
- Produces: `Reason(StrEnum)` with `EDITED = "edited"`, `DELETED = "deleted"`, `REORDERED = "reordered"`, `INSERTED = "inserted"`, `BAD_HMAC = "bad HMAC"`; frozen `VerifyResult(ok: bool, rows_checked: int, first_bad_id: int | None, reason: Reason | None, detail: str, head_id: int | None, head_hash: str | None, purged_through_id: int)`; `verify_connection(conn, key: bytes) -> VerifyResult`; `verify_file(path: Path, key: bytes) -> VerifyResult`; `AuditLog.verify() -> VerifyResult`.

- [ ] Tests: empty log OK with 0 rows; 5 rows OK; deleting row 1, 3 or 5 gives DELETED with that id; deleting the head gives DELETED at 6; a bad head HMAC gives BAD_HMAC; `verify_file` leaves the file's bytes unchanged.
- [ ] Property tests (Hypothesis picks row, column and value; tampering goes through `PRAGMA ignore_check_constraints`): any change to any column of any row is reported, as EDITED or BAD_HMAC at that row for every column but `id`, and as REORDERED for `id`; deleting any row gives DELETED at its id; swapping any two rows gives REORDERED at the lower id; any row replaced by the same-id row of a log written with another key gives BAD_HMAC at that id.
- [ ] Run, FAIL; implement; PASS; commit `feat(audit): verify names the first broken row and why`.

### Task 5: Purge with a checkpoint

**Files:** modify `gateway/audit/store.py`; create `gateway/audit/retention.py`; tests `tests/unit/audit/test_purge.py`, `tests/unit/audit/test_retention.py`.

**Interfaces (produces):** frozen `PurgeResult(deleted: int, checkpoint_id: int | None, purged_through_id: int | None)`; `AuditLog.purge(*, older_than_days: int) -> PurgeResult` (raises ValueError for N < 1, `AuditChainBroken` when verify fails); `retention.retention_days(home: Path) -> int | None` reading top-level `retention_days` from `<home>/config.toml` (ValueError when it is not an integer of at least 1).

- [ ] Tests: rows at day 0, 1, 2, 40, 41 with now = day 45 and N = 30 delete 3 rows and append checkpoint id 6 with `purged_through_id` 3 and row 3's hash, and verify passes with `purged_through_id == 3`; a second purge later deletes the old checkpoint and carries the anchor; purging everything leaves only the checkpoint and verifies; nothing old writes nothing; afterwards deleting the checkpoint, editing its carried hash, or deleting the oldest remaining row is detected; a row restored below the anchor is INSERTED; a purge over a log with an edited row raises and deletes nothing; with the clock stepped back (day 0, 1, 50, 2) only rows 1 and 2 go; purged rows' payload hashes are not in the file's bytes; `retention_days` reads `retention_days = 90`, returns None without a file or key, and refuses `0`, `"90"` and `1.5`.
- [ ] Run, FAIL; implement; PASS; commit `feat(audit): retention purge behind a chained checkpoint`.

### Task 6: No text in the log

**Files:** test `tests/property/test_audit_no_text.py`.

- [ ] Property test (Hypothesis drives Faker seeds): build a note from Faker (name, email, phone, street address, sentence), mask it with `mask()` over `detect(..., ner="off")` spans, record `sha256(masked)`, per-type counts, and the raw note as `session_id` (stored hashed); then neither the note, the masked text, any sentence of either longer than 12 characters, nor any Faker value appears in any cell of any table or in the raw bytes of the db file.
- [ ] Run; it must pass against Task 3. Make it fail once on purpose by storing `session_id` unhashed, watch it fail, revert. Commit `test(audit): no stored column or file byte holds the text`.

### Task 7: Signed JSON export

**Files:** create `gateway/audit/export.py`; test `tests/unit/audit/test_export.py`.

**Interfaces (produces):** `export.day_range(start: date | None, end: date | None) -> tuple[str | None, str | None]` (ValueError when start > end); `export.build_export(conn, key: bytes, *, start: date | None, end: date | None, now: datetime) -> dict[str, object]` (ADR 0022 point 2, signed); `export.verify_export(document: Mapping[str, object], key: bytes) -> bool`; `export.write_private(path: Path, data: bytes) -> None` (temp file and rename, mode 0600).

- [ ] Tests: rows on 1, 2 and 3 November with `--from 2026-11-02 --to 2026-11-02` export only the 2 November row; open bounds export all; `verify_export` is True, and False after changing any row value, the head or `verification`, and with another key; every exported row's `row_hash` recomputes with plain `hashlib.sha256` over `prev_hash` and `canonical_json` of the row minus three fields; a tampered log exports with `verification.ok` false; the file is 0600 and a failed write leaves no temp file.
- [ ] Run, FAIL; implement; PASS; commit `feat(audit): signed JSON export`.

### Task 8: Text PDF

**Files:** create `gateway/audit/pdf.py`; modify `gateway/audit/export.py` (`summary_lines(document: Mapping[str, object], json_name: str) -> list[str]`); tests `tests/unit/audit/test_pdf.py`, `tests/unit/audit/test_export.py`.

**Interfaces (produces):** `pdf.text_pdf(lines: Sequence[str], *, title: str) -> bytes`; `pdf.LINES_PER_PAGE = 64`; `pdf.MAX_LINE = 90`.

- [ ] Tests: output starts `%PDF-1.4` and ends `%%EOF`; every xref offset points at `N 0 obj`; `startxref` points at `xref`; 130 lines give 3 pages; `(`, `)` and `\` are escaped and `é` becomes `?`; a 200-character line wraps; with `pdftotext` installed (skipped otherwise) the extracted text holds every line; `summary_lines` shows the verification result, head, totals by host, status and entity type, and the JSON's HMAC.
- [ ] Run, FAIL; implement; PASS; commit `feat(audit): text-only PDF summary without a PDF library`.

### Task 9: `safeguard audit`

**Files:** create `gateway/audit/commands.py`; modify `gateway/cli.py`; test `tests/unit/audit/test_commands.py`.

**Interfaces (produces):** `commands.register(add_parser: Callable[..., argparse.ArgumentParser]) -> None`; `commands.run(args: argparse.Namespace) -> int`; `commands.default_key_provider() -> KeyProvider` (tests monkeypatch it). Subcommands: `verify [--db PATH]`, `export [--from DATE] [--to DATE] -o FILE [--pdf] [--db PATH]`, `purge [--older-than-days N] [--db PATH]`. Exit codes per ADR 0021 point 10 and ADR 0022 point 5; purge exits 0 with nothing to do when there is no file, 1 when the log is broken, 2 without a retention period.

- [ ] Tests (through `gateway.cli.main`): verify prints `OK` and exits 0; after a tampered value it exits 1 and the output names `row 2` and `edited`; no file exits 2 and creates none; no key exits 2 and creates none; export writes the JSON (and `.pdf` with `--pdf`), `-o x.pdf` exits 2, a bad date exits 2, a broken log exits 1 but writes the file; purge with `--older-than-days` and with `retention_days` in `config.toml` deletes old rows and verify still exits 0; purge without either exits 2; `safeguard audit` alone prints help.
- [ ] Run, FAIL; implement; PASS; run each command on a temp `SAFEGUARD_HOME`; commit `feat: safeguard audit verify, export and purge`.

### Task 10: Proxy adapter

**Files:** create `gateway/audit/sink.py`; modify `gateway/audit/__init__.py` (public names); test `tests/unit/audit/test_sink.py`.

**Interfaces (produces):** `AuditLogSink(log: AuditLog)` with `record_request(*, session_id: str, upstream_host: str, model: str, entity_counts: Mapping[str, int], masked_payload_sha256: str, bytes_out: int, status: int, local: bool) -> int` and `close()`; `AuditLogSink.open_default(key_provider: KeyProvider | None = None) -> AuditLogSink`.

- [ ] Tests: a `Counter` keyed by `EntityType` is stored as plain names; the row verifies; `open_default` writes to `<SAFEGUARD_HOME>/audit.db`.
- [ ] Run, FAIL; implement; PASS; commit `feat(audit): AuditLogSink for the proxy's per-request hook`.

### Task 11: Close-out

- [ ] `docs/SECURITY.md`: what the audit log proves and what it does not (key holder can rebuild the chain; whole-file deletion and rollback; local clock; HMAC is symmetric).
- [ ] Full gate: `set -o pipefail; uv run ruff format --check . && uv run ruff check . && uv run ty check && uv run python -m tools.check_no_egress && uv run python -m tools.check_licenses && uv run pytest -q`.
- [ ] Demo on a temp `SAFEGUARD_HOME` (a file-backed keyring stands in for the OS keychain where none exists): record rows through the API, `verify` OK, change one byte of a value through `sqlite3`, `verify` names the row, `export --pdf` writes both files, `pdftotext` reads the PDF.
- [ ] Later in this phase (not in this pass): wire `AuditLogSink` into the proxy, apply `retention_days` at proxy start, `safeguard audit verify-export` and comparing two exports' heads, `leak-hunter` and `security-reviewer` rounds, tag `v0.5.0`.
