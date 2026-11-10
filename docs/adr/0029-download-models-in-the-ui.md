# 0029. Download models from the web UI

- Status: Accepted
- Date: 2026-11-10
- Builds on: ADR 0006, ADR 0015, ADR 0019, ADR 0024, ADR 0027

## Context

A first run needed a terminal (`safeguard-sidecar models pull`), which the small
offices in the desktop app's audience do not have. The pull itself already exists and
is pinned and checksum-verified (ADR 0006, ADR 0015).

## Decision

1. **Two routes on the proxy.** `GET /models` returns the pull state and one row per
   model (`installed`, `missing`, `downloading`, `failed`); `POST /models/pull` starts
   a pull and answers 202 with the same body. Both sit behind the existing
   `LocalOnlyGuard` (Host must be loopback, Origin must be the gateway's own UI or an
   allowed origin), on a server bound to 127.0.0.1. No new guard was invented.
2. **The network call stays in `gateway/cloud/`.** `gateway/cloud/model_jobs.py`
   (`ModelPuller`) runs the same `models.pull` and `hub.pull` calls as the CLI, with
   the same pins, size caps and digest checks. `gateway/proxy/` only starts it and
   reads its status, so `tools.check_no_egress` is unchanged and still passes.
3. **One pull at a time.** `start()` returns false while a pull runs, and the route
   answers 409. The pull runs on a daemon thread and stops at the first failure,
   which is shown as the state `failed` with the error text. Nothing half-downloaded
   is installed (the pull stages and marks, as before). Pressing the button again
   retries; models already installed are skipped.
4. **Status, not byte progress.** The poll (every 1.5 s while running) shows which
   model is downloading. The existing download code has no byte callback and adding one
   would change the verified path, so there is no percentage.
5. **The UI.** Settings gets a "Language models" section: the list, a "Download
   models" button (disabled while running), the failure text, and a note on size
   (about 900 MB) and that this is the one network call the gateway makes by itself,
   to GitHub (spaCy) and Hugging Face (GLiNER). The page still cannot change where
   data goes: the button takes no input.

## Consequences

- `POST /models/pull` is a state-changing route reachable by any local program. The
  worst a local program can do is start a download of the two pinned models, which the
  user would start anyway; it cannot choose a URL.
- A gateway restarted during a pull loses the status; the next `GET /models` reads the
  disk again, and a cut-short pull has no marker, so it counts as missing.
- Tests use fake steps with no network (`tests/unit/cloud/test_model_jobs.py`,
  `tests/unit/proxy/test_models_route.py`); the browser flow mocks the two routes. A
  real download through the button was not run in this change.
