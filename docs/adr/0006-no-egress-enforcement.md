# 0006. Enforcing "no network in the engine"

- Status: Accepted
- Date: 2026-11-09

## Context

Hard rule 2: `gateway/engine/` must have no network path, and only
`gateway/cloud/` may make outbound requests. A `grep` for `socket` gives false
positives on comments and misses `from http import client`. Third-party
libraries can also reach the network: GLiNER's `from_pretrained` downloads
from the Hugging Face Hub when a model is missing.

## Decision

Defence in depth, with one checker used everywhere:

1. `tools/check_no_egress.py` parses Python files with `ast` and fails on
   forbidden imports. Rules by package:
   - `gateway/engine/`: no network modules, no `subprocess`, `asyncio`,
     `multiprocessing`, `importlib`, or `__import__` calls.
   - `gateway/parsers/`, `gateway/audit/`, `gateway/proxy/`, `gateway/cli.py`:
     no network client modules (`requests`, `httpx`, `urllib`, `aiohttp`,
     `socket`, ...). The proxy serves HTTP through uvicorn but makes every
     outbound call through `gateway/cloud/`.
   - `gateway/cloud/`: allowed.
2. The checker runs from the Claude Code hook (on every edit), the git
   pre-commit hook, and CI.
3. Tests run with `pytest-socket` (`--disable-socket`), so any test that opens
   a real network connection fails. Integration tests that need loopback opt
   in explicitly.
4. Model files are fetched only by an explicit `safeguard models pull`
   command that lives in `gateway/cloud/`. The engine sets
   `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` before importing model
   libraries and loads only from `~/.safeguard/models`.

## Consequences

- A missing model is a clear error ("run `safeguard models pull`"), never a
  silent download.
- The static check cannot see inside third-party packages; the offline
  environment variables and the socket-blocked test run cover that gap.
