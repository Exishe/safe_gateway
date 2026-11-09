# SafeGuard Gateway

SafeGuard Gateway finds personal data on the user's own machine and replaces it
with placeholders such as `[PERSON_1]` before a prompt goes to a cloud AI
model. The model answers with the placeholders, and the gateway puts the real
values back before the answer reaches the screen. A local audit log records
what was sent, as hashes and counts, never as text. Customers bring their own
Anthropic, OpenAI or Azure key; development runs against a local Ollama model.

It is built for small law, accounting, medical and HR firms. It reduces
exposure; it does not make a firm compliant on its own. See
[docs/SECURITY.md](docs/SECURITY.md) for what it does and does not protect
against.

## Status

Phase 0 of 8 (scaffold). The repository has its guards, CI and test harness in
place. No detection, masking, proxy or UI code exists yet. The build order and
exit criteria for each phase are in [docs/plans/](docs/plans/), and design
decisions are in [docs/adr/](docs/adr/).

## Development

You need [uv](https://docs.astral.sh/uv/) and git. The full setup, including
Tesseract and Ollama for later phases, is in [docs/INSTALL.md](docs/INSTALL.md).

```bash
uv sync                          # Python 3.12 and all dev tools
scripts/install-git-hooks.sh     # once per clone
uv run pytest                    # the suite runs with network sockets disabled
uv run safeguard --version
```

`CLAUDE.md` holds the project rules: permissive licenses only, no network
access from the detection engine, tests before code, and no recall figure
without a measurement behind it.

## License

Proprietary. All rights reserved. Third-party notices will be collected in
`THIRD_PARTY_NOTICES.md` when packaging starts.
