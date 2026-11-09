# SafeGuard Gateway

SafeGuard Gateway finds personal data on the user's own machine and replaces it
with placeholders such as `[PERSON_1]` before a prompt goes to a cloud AI
model. The model answers with the placeholders, and the gateway puts the real
values back before the answer reaches the screen. A local audit log (planned,
Phase 5) will record what was sent, as hashes and counts, never as text. Customers bring their own
Anthropic, OpenAI or Azure key; development runs against a local Ollama model.

It is built for small law, accounting, medical and HR firms. It reduces
exposure; it does not make a firm compliant on its own. See
[docs/SECURITY.md](docs/SECURITY.md) for what it does and does not protect
against.

## Status

Phase 1b of 8. `safeguard scan FILE` finds, in a text file:

- names, organisations, places and dates in English and Russian, after
  `safeguard models pull` downloads the NER models (Armenian names: through
  the customer list only, until Phase 1c);
- card numbers, IBANs, US SSNs, Russian INN and SNILS, emails, phones, IP
  addresses and URLs with tokens, in any language;
- names or codenames from a customer list (`--gazetteer clients.csv`).

Street addresses, masking and rehydration, the proxy, the tamper-evident audit
log (Phase 5) and the UI come later. None of them exists yet. The build order
and exit criteria for each phase are in [docs/plans/](docs/plans/), and design decisions are in
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
