# Phase 6: Web UI, then Tauri shell

**Goal:** A distinctive, accessible dashboard served by the proxy on
`http://127.0.0.1:8787/`, then the same UI in a Tauri 2 desktop app.

This plan covers **part 1, the web UI**. Part 2 (the Tauri shell, the
PyInstaller sidecar, unsigned `.dmg` and `.msi` on tags, `THIRD_PARTY_NOTICES.md`)
gets its own task list when it starts.

Decisions are recorded in ADR 0024.

## Open questions, decided

| Question | Decision |
|----------|----------|
| How does the UI get spans and confidence? `/mask` returns only masked text, map and counts. | `/mask` also returns `detections`: one list per text of `{start, end, type, placeholder, score, source}`. Additive; old clients ignore it. |
| Which flow does Mask & send use? | ADR 0002 point 2: mask first in a session (`/mask`), send the masked text through `/v1/chat/completions` (or `/v1/messages` for Anthropic), rehydrate in the browser from the session map. The masked request shown is the exact text the UI sent. |
| What does switching a detection off do? | The UI puts the original value back into the text it sends. The chat route still masks whatever its detectors find, so the gateway remains the floor. The UI says so; it never claims a switched-off value was sent in clear. |
| How are placeholders the UI sends protected? | The chat route already wraps placeholder-shaped input as `[LITERAL_n]` and restores it in the answer (ADR 0001 point 4), so the browser only has to replace exact `[TYPE_N]` tokens. Anything placeholder-shaped left over is flagged as unresolved. |
| Same-origin requests from the served UI | Browsers send `Origin` on same-origin POST and DELETE. When the UI is served, an `Origin` that equals `http://` + the request's own loopback `Host` is allowed. Every other origin is still refused unless listed. |
| Vite dev server | `[proxy] allow_dev_ui = true` adds `http://127.0.0.1:5173` and `http://localhost:5173` to the CORS list. Off by default. |
| Settings | No config endpoint is added. The UI reads `/health` (provider, local, version) and `/v1/models` (model) and shows how to edit `~/.safeguard/config.toml`. The API key is never typed into the browser: the screen shows the `keyring set` command. |
| `/audit` | Coded against ADR 0021 and 0022: rows of metadata and a `VerifyResult`. A 501 shows a calm "not available in this build" state. |
| Fonts | System font stacks only: no font files to license or download. |
| Icons | Inline SVG written for this project; no icon package. |

## Tasks

Every Python task is test first. Front-end logic with rules in it (SSE parsing,
rehydration, unresolved tokens, span layout, audit parsing) gets Vitest unit
tests first.

### 1. `/mask` returns detections (proxy)
- Test: `/mask` with Faker data returns `detections[i]` whose `start`/`end`
  slice the input to the mapped original, with `type`, `placeholder`,
  `score` (0..1 or null) and `source` (`detected`, `repeat`, `literal`).
- Test: replacing each detection with its placeholder rebuilds `masked[i]`.
- Build: `_mask_texts` uses `detect` + `replacements` + `apply_replacements`.

### 2. Same-origin requests from the served UI (guard)
- Test: with `allow_same_origin=True`, `Origin: http://127.0.0.1:8787` with
  `Host: 127.0.0.1:8787` passes; `Origin: http://127.0.0.1:9999`,
  `https://127.0.0.1:8787`, `http://evil.example` are 403.
- Test: with it off (the default), the same-origin request is 403 as before.

### 3. Serve `apps/web/dist` (app, server)
- Test: `create_app(..., ui_dir=tmp)` serves `index.html` at `/` and assets
  under `/assets/`, with `Content-Security-Policy`, `X-Frame-Options: DENY`,
  `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`.
- Test: API routes still win; Host and Origin checks still run on static files.
- Test: without `ui_dir` (or a missing dir) `/` is 404, as before.
- Test: `build_app` passes the repo's `apps/web/dist` when it exists.

### 4. `allow_dev_ui` config flag
- Test: parsed from `[proxy]`, boolean only, default false.
- Test: when true, the two Vite origins are allowed by CORS; when false they
  are 403.

### 5. Scaffold `apps/web/`
- Vite 8, React 19, TypeScript 6.0, Tailwind 4, ESLint 9 with
  typescript-eslint, react-hooks and jsx-a11y, Vitest.
- shadcn/ui components copied in (Button, Badge, Card, Switch, Tabs, Table,
  Textarea, Input, Label, Alert) over Radix primitives.
- `npm run build` (typecheck + build), `npm run lint`, `npm test`.

### 6. Client logic (Vitest first)
- `api.ts`: typed calls to `/health`, `/v1/models`, `/mask`,
  `DELETE /session/{id}`, `/audit`; errors carry status and the server's
  message; 501 from `/audit` is a value, not an exception.
- `sse.ts`: SSE parser over chunks split anywhere; OpenAI and Anthropic deltas;
  error events.
- `rehydrate.ts`: exact `[TYPE_N]` replacement from the session map; leftovers
  matching the placeholder grammar listed as unresolved.
- `segments.ts`: original text + detections + toggles -> highlighted segments
  and the request text.
- `audit.ts`: tolerant parsing of `/audit` rows and verify result.

### 7. Screens
- Shell: header with gateway status (provider, local or cloud), theme switch
  (system, light, dark; stored in `localStorage`), tabs for the three screens.
- Mask & send: paste or drop `.txt`; detections highlighted by type with
  confidence; each toggleable by keyboard; model field; Send streams;
  masked request and rehydrated answer side by side; unresolved placeholders
  flagged in a banner and inline; Clear deletes the session.
- Audit: verify banner (intact, broken with first bad row and reason, cannot
  check), table (time, upstream, model, entity counts, status, local or
  cloud), 501 state.
- Settings: provider, model, local from the gateway; config.toml example for
  base URL, model, thresholds, gazetteer, `allow_dev_ui`; the keyring command.

### 8. E2E (`tests/e2e/`, marker `e2e`)
- A stub OpenAI-shaped upstream on 127.0.0.1 (stdlib `http.server`) that
  streams "Noted: <masked user text>" plus one placeholder it invents.
- A pytest test marked `e2e`, skipped unless `SAFEGUARD_E2E=1` and the build
  exists: starts the stub and `uv run safeguard serve` with a temp
  `SAFEGUARD_HOME`, runs the Node Playwright script, asserts it passed.
- The script clicks through every flow, fails on any console error or CSP
  violation, checks keyboard toggling, and takes screenshots at 390 and
  1280 px in light and dark. Audit states beyond 501 are driven by
  Playwright request interception.

### 9. CI
- A `web` job on ubuntu: `npm ci`, `npm run lint`, `npm test`, `npm run build`
  in `apps/web`, actions pinned by SHA. `zizmor --offline` clean.

### 10. Docs
- ADR 0024, `docs/INSTALL.md` section for the UI, PROGRESS.

## Exit criteria for part 1

- Python gate green; `npm run build`, `npm run lint`, `npm test` green.
- E2E click-through passes with no console errors; screenshots at 390 and
  1280 px in both themes.

## Part 2: the desktop shell

Decisions are recorded in ADR 0027. Python tasks are test first; the shell's
lifecycle rules are plain Rust functions behind a `Backend` trait, with unit tests
written before the code and a fake clock and process.

### 11. Frozen UI path (proxy)
- Test: with `sys.frozen` and `sys._MEIPASS` set, `default_ui_dir()` is
  `<_MEIPASS>/ui`.

### 12. Sidecar build (`tools/build_sidecar.py`, `tools/sidecar_entry.py`)
- Test: the PyInstaller command is one-folder, bundles `apps/web/dist` as `ui`,
  copies the distribution metadata, writes under `apps/desktop/src-tauri/resources`,
  bundles no models, and fails with a clear message when the UI build is missing.
- Build: PyInstaller in its own uv group `sidecar`; run it, start the result with a
  temporary `SAFEGUARD_HOME`, and read `/health`.

### 13. Shell logic (Rust, `apps/desktop/src-tauri/src/`)
- `health.rs`: parse `/health`; a std-only GET on loopback.
- `supervisor.rs`: backoff 1, 2, 4 ... 30 s; ready on `/health`; restart on exit or
  six missed checks; startup timeout; give up after 5 failed runs; a 60 s healthy run
  resets the count; stopping during a backoff starts nothing.
- `view.rs`: which view for each state, the tray line, percent-encoding of the reason,
  and the navigation allow-list (own pages and `http://127.0.0.1:8787` only).
- `sidecar.rs`: the real process backend (SIGTERM then kill; adopt a running
  gateway; no console window on Windows).

### 14. Tauri app
- Window created in code with `on_navigation`; tray menu (Open dashboard, status,
  Restart gateway, Start at login, Quit); closing the window hides it.
- Config: CSP limited to `127.0.0.1:8787`, an empty capability, no updater, no shell,
  fs or http plugin. Static pages `ui/` for loading, restarting and error.

### 15. CI (`desktop` job)
- On `v*` tags and `workflow_dispatch` only (ADR 0007): builds the sidecar and the
  app on macos-14 (`.dmg`), windows-latest (`.msi`) and ubuntu (`.deb`); unsigned;
  uploads artifacts. Actions pinned by SHA; `zizmor --offline` clean.

### 16. Notices and docs
- `tools/gen_notices.py` writes `THIRD_PARTY_NOTICES.md` from uv metadata, the web
  lock file and `cargo metadata`; it fails on a crate with no permissive license.
- `docs/INSTALL.md`: first-run warnings for unsigned apps, building from source.
- ADR 0027 and the ADR README.

## Exit criteria for part 2

- Python gate green; `cargo test` green; `cargo check` of the app green where the
  system libraries exist.
- The frozen sidecar answers `/health` and serves the UI.
- Tag `v0.6.0` (done by the owner after the tag build is looked at on macOS and
  Windows).
