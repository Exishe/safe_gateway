# Runbook: suspected leak of personal data through the gateway

**Owner:** the firm's gateway administrator, with the firm's data protection officer (DPO) or
legal contact | **Frequency:** as needed | **Last updated:** 2026-11-10 | **Last run:** never

This is operational guidance, not legal advice. Whether an event is a personal data breach,
whom to tell and by when are decisions for the customer, with its own DPO and counsel.

## Purpose

Use it when someone suspects that personal data reached the cloud model provider unmasked, or
reached anyone else through the gateway. Examples: a name or number visible in a provider
conversation history, a user reporting that a document was not masked, a failed audit check.

What the gateway can and cannot tell you (from `docs/SECURITY.md`):

- The audit log holds hashes, counts, timestamps, the upstream host and the model. It never holds
  text, masked or raw. It can show **when** requests were sent and **how many values of each type
  were masked**. It cannot show **what was missed**: a missed value is, by definition, not counted.
  Establishing what left the machine needs the source document and the provider's own records.
- It is evidence of what this gateway sent. It does not cover other software on the machine, or
  text a user pasted into a cloud service directly (for example with the browser extension off, or
  on a site it does not know).

## Prerequisites

- [ ] Access to the machine that ran `safeguard serve`, as the same OS user (the audit key is in
      that user's OS keychain).
- [ ] The provider account or its administrator, to revoke the API key.
- [ ] The name of the firm's DPO or legal contact.
- [ ] A place to keep evidence that the gateway user cannot change.

## Procedure

### Step 1: Triage (first 30 minutes)

Write down, in a file kept outside the gateway's folders: who reported it, when, the document or
chat concerned, which interface was used (`safeguard serve` with the web UI, the desktop app, the
browser extension, the CLI `mask`), the provider and the model. Do not copy the personal data into
the note; say where it is.

Decide which case it is:

| Case | Sign | Go to |
|------|------|-------|
| A. Missed value | The gateway ran, and a value that should have been masked reached the provider | Steps 2 to 7 |
| B. Bypass | The text never went through the gateway (extension off, unknown site, attachment, direct paste) | Steps 2, 4 (note that the log will not show it), 5 to 7 |
| C. Gateway damaged | `safeguard audit verify` fails, or the proxy refuses to start or stops forwarding | Steps 2 and 3, then escalate to the maintainer |

**Expected result:** a dated note naming the case. **If unsure:** treat it as case A.

### Step 2: Contain

Stop new exposure first; investigate after.

```
# Stop the gateway: quit the desktop app, or press Ctrl+C in the terminal running
#   safeguard serve
# Then revoke the provider key at the provider (their console or admin page).
```

Revoke the key **at the provider**. Removing it from the keychain on this machine does not make
the key unusable if a copy exists. Issue a new key only after Step 6.

Tell users to stop sending client material through the gateway, the extension and chat sites
until cleared. Ask the provider, through its own process, what it retains and whether
conversation history can be deleted; do not assume anything was deleted.

**Expected result:** no further requests leave the machine. **If it fails** (the process will not
stop): end it from the task manager or with `kill <pid>`, and disconnect the machine from the
network until the key is revoked.

### Step 3: Keep the audit log as evidence

Do **not** run `safeguard audit purge`, delete `audit.db`, or restart `serve` to see if it
recovers. A broken log is moved aside and kept (`docs/SECURITY.md`). Copy the whole gateway
folder (`SAFEGUARD_HOME`, by default `~/.safeguard`) to the evidence location, then run:

```
safeguard audit verify
safeguard audit export --from YYYY-MM-DD --to YYYY-MM-DD -o incident-audit.json --pdf
```

Start the range a day before the first suspected request (dates are UTC days). The export is
written even if the log fails verification; the command then exits 1. Keep that file too. Record
the output of `verify` as it appears, and the export's `chain.head_hash`.

**Expected result:** `verify` reports an intact chain, or names the first bad row and why.
**If it fails:** that is case C. Keep the files; do not repair them.

An intact result is evidence against tampering by anyone without the device key. It does not rule
out someone who had the key, or a log that was deleted or replaced by an older copy. Compare the
head against any earlier periodic export to look for a rollback.

### Step 4: Assess from the audit log

Open the exported JSON. Each row has `ts_utc`, `upstream_host`, `model`, `entity_counts_json`,
`bytes_out`, `status`, `local` and the hash of the masked payload.

- Select the rows in the suspected window and for the suspected provider (`upstream_host`). Rows
  with `local` true went to a model on this machine.
- Read `entity_counts_json` per row: the values the gateway **did** mask. A row with no counts
  for a request that held names, or counts well below what the source document holds, points to
  a detection gap.
- A `status` of 0 means no answer came back; the request may still have reached the provider.
- Match rows to the source document by time and size (`bytes_out`). The log has no text, so you
  cannot read what was sent. Running `safeguard scan FILE` on the source today shows what the
  current engine finds, not what the engine found at the time.
- List what remains unknown: which values the model may have seen unmasked, whose they are, and
  the categories (health, financial, national identifiers).

**Expected result:** a table of rows, hosts and masked counts, and a written list of unknowns.
Write "unknown" where it is unknown; do not estimate.

### Step 5: Notify, through the customer's own process

Give the DPO or legal contact: the triage note, the Step 3 output, the Step 4 table and unknowns,
the provider and model, and the dates. The customer decides whether this is a reportable breach,
to whom, within what time, and what clients are told; the duties depend on its jurisdiction and
sector and are not stated here. Masked text is pseudonymised, not anonymous (GDPR Art. 4(5)), so
masked text that reached a provider can still be personal data. The DPO should know that when
judging scope.

If the cause may be a flaw in the gateway, also report it privately to the maintainer
(`docs/SECURITY.md`, "Reporting a vulnerability"). Do not put real personal data in the report;
describe the shape of the input or use invented values.

### Step 6: Reproduce and fix (maintainer)

Use fake data only (hard rule 8).

1. Build a fake input with the same shape as the missed value (Faker, or an invalid-by-construction
   number; mark literals `# pragma: allowlist secret`).
2. Write the bypass as a **failing test** first, named after the trick, under
   `tests/unit/engine/` or `tests/unit/parsers/`, as the `leak-hunter` agent does
   (`.claude/agents/leak-hunter.md`). Run it and watch it fail. Name the file with `leak_hunt` so
   the weekly scan runs it (ADR 0028).
3. Fix the engine, parser or extension selector. Run `uv run pytest`,
   `uv run ruff format . && uv run ruff check .`, `uv run ty check` and
   `uv run python -m tools.check_no_egress`.
4. Run the `leak-hunter` agent on the change (CLAUDE.md requires it after each engine change).
5. If a recognizer changed, re-measure and update `docs/BENCHMARKS.md` with the command, dataset,
   revision and date (hard rule 5). If the gap is not fully closed, state it in `docs/SECURITY.md`.
6. Release a patch version and tell the pilot firms what changed and what stays open.

**If a fix is not quick:** document the limit in `docs/SECURITY.md` and tell customers what to do
meanwhile (add the affected names to the gazetteer, review text before sending).

### Step 7: Restart and lessons learned

Restart only after the fix is installed and a new provider key is in the keychain. Run
`safeguard audit verify` first. If it fails, keep the old log as evidence and start a new one.

Within a week, review with the DPO and the maintainer. Record in the incident note: timeline,
case, root cause, what the audit log did and did not show, what changed, and what is still open.
Ask: were exports kept regularly? Did users know when the extension was not masking? Should a
review step or a name list be required? Add follow-ups to the phase plan.

## Verification

- [ ] The gateway was stopped and the provider key revoked at the provider.
- [ ] The `verify` output and the export are stored outside the gateway's folders, with the date.
- [ ] The DPO or legal contact has the package from Step 5.
- [ ] A failing test existed before the fix and passes after it.
- [ ] The release notes and `docs/SECURITY.md` state any limit that remains.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `verify` cannot read the key | Another OS user, or a locked keychain | Run as the user who ran `serve`; unlock the keychain |
| `verify` names a bad row | Edited or damaged log, or another device's log | Keep the file, do not repair it, escalate |
| Export exits 1 | The log failed verification | Keep the export; it records what broke |
| No rows in the window | Wrong dates (UTC days), `--no-audit` was used, or the request never passed the gateway | Widen the range; ask how `serve` was started; consider case B |
| Counts look right but a value leaked | The value was in an attachment, an image, or a name the models missed | Case A or B; go to Step 6 |

## Rollback

Nothing here changes customer data. Containment (Step 2) is undone by issuing a new provider key
and restarting the gateway after Step 7.

## Escalation

| Situation | Contact | Method |
|-----------|---------|--------|
| Possible breach of personal data | The firm's DPO or legal contact | The firm's own process |
| Provider-side retention or deletion | The provider's support or security contact | The provider's process |
| Possible flaw in the gateway | The maintainer | Private report (`docs/SECURITY.md`) |

## History

| Date | Run by | Notes |
|------|--------|-------|
| | | |
