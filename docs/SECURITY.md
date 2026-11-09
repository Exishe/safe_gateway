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
| Bandit, pip-audit, Semgrep, zizmor | CI on every push |

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
  is read by the English model (ADR 0016).
- Names written in capitals are handled by a heuristic. A line in which more
  than 70% of the letters are capitals is title-cased before the models read
  it, because both models take case as a cue. A name in capitals inside an
  otherwise lower-case line is read as written and is found less often; in a
  heading in capitals, ordinary words may be masked as names.
- Dates are found by a format recognizer in every language: numeric forms
  (`12.03.1985`, `03/12/1985`, `1985-03-12`, `12.03.85`) and English or
  Russian month names (`12 March 1985`, `March 12, 1985`, `12 марта 1985`).
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
- Long unbroken runs of numbers are slow. Every possible group of digits is
  checked for every identifier type, so 100 KB of single-spaced digits takes
  several seconds instead of under one. Ordinary documents are not affected;
  splitting such runs is deferred (ADR 0013).
- Long phone lists are slow. Phone numbers are matched once per configured
  region, nine by default, so a 100 KB directory of about 3,300 contacts takes
  around 3 seconds. Configuring only the regions a customer needs reduces this;
  a single-pass matcher is deferred (ADR 0013).
- The audit log is tamper-evident, not tamper-proof. The hash chain shows that
  rows were changed; it cannot stop someone with admin rights from deleting
  the whole file. Customers should store periodic exports elsewhere.
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

Compliance with GDPR, HIPAA or any other regime remains the customer's
responsibility. The gateway provides controls and evidence for it.

## Reporting a vulnerability

Contact the maintainer privately. Do not open a public issue, and do not
include real personal data in the report.
