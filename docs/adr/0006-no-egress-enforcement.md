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

## Amended 2026-11-09

The pre-tag security review found two gaps in `safeguard models pull` (M3, M4).

**Completion marker (M3).** A model counted as installed when its directory
existed, so an interrupted pull left an empty or half-unpacked directory that
the engine later failed on or loaded in part. Now:

- A pull downloads and verifies into a staging directory next to the target,
  moves whatever is at the target aside (a link is moved, never followed),
  renames the staged directory into place, and as its very last step writes
  `.safeguard-install.json` into it. The marker records the model id (the
  directory name) and the pinned SHA-256: the wheel's digest for spaCy, and for
  a Hugging Face snapshot one SHA-256 over every pinned file's source and digest.
- "Installed" means that marker exists and records exactly the current pin.
  One function, `gateway/engine/model_marker.is_installed`, decides it for
  `models pull`, `models list` and the engine's `ModelMissing` checks. It lives
  in the engine and does file I/O only, because the engine may not import
  `gateway/cloud/`. The engine keeps its own copy of the pins, and tests keep
  that copy equal to the downloader's.
- A directory without a valid marker is missing, and the next pull replaces
  it. Installs made before this change have no marker, so they count as
  missing until they are pulled again.

**Redirects and size (M4).** The client followed any redirect and had no size
limit. Now:

- `download_verified` follows redirects itself, at most five hops. Before
  each request, including the first, it checks that the URL is https on port
  443 and that its host is in `ALLOWED_HOSTS` (`gateway/cloud/models.py`). The
  list holds exact names, with no wildcards: `github.com` and
  `huggingface.co`, the storage hosts they were seen to redirect to on
  2026-11-09 (`release-assets.githubusercontent.com` and `us.gcp.cdn.hf.co`),
  GitHub's earlier asset host `objects.githubusercontent.com`, and the other
  download CDN hosts Hugging Face lists for firewalls (`us.aws.cdn.hf.co`,
  `cdn-lfs-us-1.hf.co`, `cdn-lfs-eu-1.hf.co`). A pull redirected anywhere else
  fails with the refused host named, and that host is added only after review.
- Each file is capped at its recorded size plus 1 MiB, or at 2 GiB when no
  size is recorded. The cap is enforced on the bytes as they stream, and
  Content-Length is used only to refuse early. The Hugging Face files record
  sizes measured on 2026-11-09 from an install whose digests matched. The
  spaCy wheels record none, so they use the 2 GiB ceiling.
- Any failure, an interrupt included, deletes the partial download.
