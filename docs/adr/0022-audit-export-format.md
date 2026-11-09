# 0022. Audit export: signed JSON and a text PDF summary

- Status: Accepted
- Date: 2026-11-09
- Builds on: ADR 0021

## Context

A customer hands audit evidence to someone else: an auditor, a regulator, a
client. The export has to carry the rows in a form others can check, show
whether the log verified when it was exported, and be bound to the device that
wrote it. A PDF summary is easier for a person to read. The PDF writer has to
be permissively licensed (hard rule 1): fpdf2 is LGPL and not allowed.

## Decision

1. **Command.** `safeguard audit export [--from DATE] [--to DATE] -o FILE
   [--pdf]`. Dates are UTC calendar days and both ends are inclusive: rows
   with `from 00:00:00 <= ts_utc < (to + 1 day) 00:00:00`. A missing bound is
   open. `--from` after `--to` is refused.

2. **JSON document.** UTF-8, indented for reading:

   ```json
   {
     "format": "safeguard-audit-export",
     "version": 1,
     "generated_utc": "2026-11-09T17:00:00.000000Z",
     "range": {"from_utc": "...", "to_utc_exclusive": "..."},
     "verification": {"ok": true, "rows_checked": 12, "first_bad_id": null,
                      "reason": null, "detail": "..."},
     "chain": {"head_id": 12, "head_hash": "...", "purged_through_id": 0},
     "rows": [{"id": 1, "kind": "request", "...": "every column as stored"}],
     "hmac": "..."
   }
   ```

   `range` bounds are null when open. `verification` is the `VerifyResult`
   of ADR 0021 for the whole log at export time. `chain` is the head record
   and the newest checkpoint's `purged_through_id` (0 when nothing was
   purged). Each row has all 18 columns, `local` as a JSON boolean, NULL as
   null.

3. **Signature.** `hmac = HMAC-SHA256(device_key, "safeguard-audit/export/v1\n"
   || canonical_json(document without "hmac"))`, with ADR 0021's canonical
   JSON. `verify_export(document, key)` in `gateway/audit/export.py` checks it.

4. **What others can check without the key.** The rows are exact copies of
   the columns. Anyone can recompute `row_hash = sha256(prev_hash ||
   canonical_json(row without prev_hash, row_hash, hmac))` and check that each
   exported row's `prev_hash` is the previous exported row's `row_hash` when
   their ids are consecutive. The HMACs, of the rows and of the export, need
   the device key.

5. **A broken log is still exported.** The file is written, its
   `verification` says what broke, and the command exits 1. For an
   investigation the evidence matters more than a clean exit.

6. **Files.** Written to a temporary file in the target directory, then
   renamed; mode 0600. The export holds no text, but timestamps of a person's
   work are still personal data.

7. **PDF.** `--pdf` also writes a summary at FILE with its suffix replaced by
   `.pdf`. FILE is always the JSON; an `-o` ending in `.pdf` is refused, so
   neither file can overwrite the other. The summary lists the range, the
   generation time, the verification result, the chain head, what was purged,
   totals (requests, bytes out, local and cloud, by host, by status,
   placeholders by entity type), and the JSON file's name and HMAC, so the
   PDF can be matched to its JSON. The PDF itself is not signed; the JSON is
   the evidence.

8. **PDF writer: our own, standard library only.** `gateway/audit/pdf.py`
   writes PDF 1.4 text: Courier (one of the 14 standard fonts, not embedded),
   9 pt on A4, 64 lines a page, lines wrapped at 90 characters, printable
   ASCII only (anything else becomes `?`), `\`, `(` and `)` escaped,
   uncompressed content streams, a cross-reference table with exact byte
   offsets. reportlab 5.1.0 was evaluated first: it passed
   `tools/check_licenses` (BSD, pulling in pillow under MIT-CMU), but its
   source shows that `import reportlab` executes `~/.reportlab_mods` and any
   `reportlab_mods` module on `sys.path` (`importlib` `exec_module`), and its
   `rl_config` executes `~/.reportlab_settings` and imports
   `reportlab_settings` from `sys.path`. That is code loaded at import time
   from places the user or another program can write, which the
   no-dynamic-code rule (ADR 0006, ADR 0010) forbids. It was removed again.

## Consequences

- No new dependency for the PDF, and nothing in `gateway/audit/` loads code
  by name.
- The PDF cannot show non-ASCII text, tables beyond monospaced columns, or
  compressed pages. Every value the log stores is ASCII (ADR 0021 point 3),
  so a summary loses nothing.
- A verifier with the key can tell whether the export was changed after it
  was written. A newer export whose chain does not contain an earlier export's
  head shows that the log was rewritten or rolled back in between. Comparing
  two exports is not automated yet.
