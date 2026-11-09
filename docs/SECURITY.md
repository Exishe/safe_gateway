# Security

This page states what SafeGuard Gateway is designed to do, how the repository
enforces it today, and the limits a customer should know about. It will grow
with each phase; the threat model gets its full review before the first pilot.

## Design goals

1. Personal data leaves the machine only as placeholders. Detection and masking
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

## What enforces this today (Phase 0)

| Control | Where it runs |
|---------|---------------|
| AST check of every module under `gateway/` except `gateway/cloud/`: network modules and LLM SDKs everywhere; also process, dynamic-import and cloud/proxy imports in the engine | Claude Code hook on every edit, git pre-commit, CI |
| Tests run with sockets disabled (`pytest-socket`) | every test run |
| License check of the runtime dependency closure, extras included, on Linux, macOS and Windows; PyMuPDF and pdftext denied by name | Claude Code hook before and after installs, CI |
| Scan for keys (detect-secrets) and identifier-shaped data | Claude Code hook before commits, which also refuses commits that skip the git hook; git pre-commit on staged files; CI on every tracked file |
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
- The audit log is tamper-evident, not tamper-proof. The hash chain shows that
  rows were changed; it cannot stop someone with admin rights from deleting
  the whole file. Customers should store periodic exports elsewhere.
- Pilot installers are unsigned (see [INSTALL.md](INSTALL.md)).
- The repository guards match names, not behaviour. Code that builds module
  names at run time, or a shell command hidden inside `bash -c`, can get past
  them. CI and code review are the backstops (ADR 0010).

Compliance with GDPR, HIPAA or any other regime remains the customer's
responsibility. The gateway provides controls and evidence for it.

## Reporting a vulnerability

Contact the maintainer privately. Do not open a public issue, and do not
include real personal data in the report.
