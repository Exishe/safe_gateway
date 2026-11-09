# SafeGuard Gateway

SafeGuard Gateway finds personal data on the user's own machine and replaces it
with placeholders such as `[PERSON_1]` before a prompt goes to a cloud AI
model. The model answers with the placeholders, and the gateway puts the real
values back before the answer reaches the screen. A local, tamper-evident audit
log records what was sent, as hashes and counts, never as text. Customers bring
their own Anthropic, OpenAI or Azure key; development runs against a local
Ollama model.

It is built for small law, accounting, medical and HR firms. It reduces
exposure; it does not make a firm compliant on its own. See
[docs/SECURITY.md](docs/SECURITY.md) for what it does and does not protect
against.

## Status

Phases 0 to 5 of 8 are built; nothing is tagged as a release yet. What exists
today, all from the command line:

- `safeguard scan FILE` lists the personal data it finds: card numbers, IBANs,
  US SSNs, Russian INN and SNILS, emails, phones, IP addresses and URLs with
  tokens in any language; names, organisations, places and dates in English
  and Russian, and names and places in Armenian and other alphabets, after
  `safeguard models pull` downloads the local NER models; and names or
  codenames from a customer list (`--gazetteer clients.csv`).
- `safeguard mask FILE` replaces what it finds with placeholders in text, PDF,
  DOCX, XLSX and email files, and can write the placeholder map separately.
- `safeguard serve` runs a proxy on 127.0.0.1 that OpenAI- and
  Anthropic-compatible clients can point at. It masks each request before it
  leaves the machine, forwards it to the configured upstream (Anthropic,
  OpenAI, Azure OpenAI or a local Ollama) and restores the real values in the
  answer, streamed or not. Tool calls and images are refused rather than sent
  unmasked. It has been tested against mocked upstreams; a run against a real
  Ollama is still a manual check to do.
- Every request the proxy sends upstream leaves one row in a hash-chained,
  HMAC-signed SQLite log: counts, hashes, host, model, bytes and status, never
  text. `safeguard audit verify` names the first edited, deleted or reordered
  row, `audit export` writes signed JSON and a PDF summary, `audit purge`
  applies a retention period, and `GET /audit` on the proxy shows recent rows
  with the verify result. The log is tamper-evident, not tamper-proof: see
  [docs/SECURITY.md](docs/SECURITY.md).

Detection is never complete. Structured identifiers are checked by format and
checksum; names, addresses and health details are statistical, and their recall is
measured in [docs/BENCHMARKS.md](docs/BENCHMARKS.md). Street addresses are only partly
covered.

Not built yet: the web and desktop UI (Phase 6) and the browser extension
(Phase 7). The build order and exit criteria for each phase are in
[docs/plans/](docs/plans/), and design decisions are in
[docs/adr/](docs/adr/).

## Development

You need [uv](https://docs.astral.sh/uv/) and git. The full setup, including
Tesseract and Ollama for later phases, is in [docs/INSTALL.md](docs/INSTALL.md).

```bash
uv sync                          # Python 3.12 and all dev tools
scripts/install-git-hooks.sh     # once per clone
uv run pytest                    # the suite runs with network sockets disabled
uv run safeguard scan notes.txt                         # table of detections
uv run safeguard scan --json --gazetteer clients.csv notes.txt
```

`CLAUDE.md` holds the project rules: permissive licenses only, no network
access from the detection engine, tests before code, and no recall figure
without a measurement behind it.

## License

Proprietary. All rights reserved. Third-party notices will be collected in
`THIRD_PARTY_NOTICES.md` when packaging starts.
