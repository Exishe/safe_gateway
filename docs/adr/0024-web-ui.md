# 0024. Web UI: mask first, send through the gateway, restore in the browser

- Status: Accepted
- Date: 2026-11-09
- Builds on: ADR 0002 point 2, ADR 0019, ADR 0021, ADR 0022

## Context

Phase 6 adds a browser UI in `apps/web/` for the three jobs a small office has:
check a text before it goes to a model, see what left the machine (the audit log),
and see how the gateway is set up. The UI must show detections with their type and
confidence, let the user switch any of them off, send with streaming, and show the
masked request next to the restored answer with unresolved placeholders flagged. It
talks only to the gateway on `127.0.0.1:8787`, and the API key is never typed into it.

`POST /mask` returned masked text, the map and counts, but not where each value
was or how sure the detector was. The chat routes always mask what their detectors
find; nothing a client sends can turn that off.

## Decision

1. **Detections from `/mask`.** The response gains `detections`: one list per text
   of `{start, end, type, placeholder, score, source}`, exactly the replacements
   mask() made (`gateway/proxy/detections.py`). Offsets are Unicode code points,
   as in Python; the UI converts them to UTF-16. `score` is the best score of the
   recognizer spans in that stretch, or null; `source` is `detected`, `repeat`
   (another occurrence of a value the session already holds) or `literal`
   (placeholder-shaped input). Writing each placeholder over its stretch gives the
   masked text back; a test checks that. The field is additive.

2. **Flow: mask first, send the masked text, restore in the browser** (ADR 0002
   point 2). The UI calls `/mask`, which creates a session. The request it sends is
   the user's text with each detection replaced by its placeholder, or by its
   original value where the user switched it off. That text goes to
   `/v1/chat/completions`, or to `/v1/messages` when `/health` names Anthropic. The
   chat route treats the session's `[TYPE_N]` tokens as placeholder-shaped input:
   it wraps them as `[LITERAL_n]` and puts them back in the answer (ADR 0001 point
   4). So the browser only replaces exact `[TYPE_N]` tokens from the session's map.
   Every other placeholder-shaped token, found with the engine's grammar ported to
   TypeScript, is shown as written and flagged. The masked request on screen is the
   exact text the UI sent.

3. **Switching a detection off is honoured in the request and stated honestly.**
   Switches work per value (per placeholder), so one value is never masked in one
   place and clear in another. The chat route still masks whatever its detectors
   find. In practice a switched-off value is usually masked again on the way out;
   the browser test shows this. The UI says so in plain words and never claims the
   value went out in clear. Placeholder-shaped input (`LITERAL`) cannot be switched
   off: sent as written, it could not be told apart from a session placeholder
   when the answer is restored.

4. **Detections that do not line up fail closed.** If a detection overlaps
   another, falls outside the text, or does not slice to the value the map holds,
   the UI sends nothing and asks the user to check again. Skipping it would send
   that value unmasked.

5. **Served by the gateway.** `create_app(ui_dir=...)` mounts the build at `/`
   (`gateway/proxy/ui.py`). `build_app` passes the source checkout's
   `apps/web/dist` and serves it when `index.html` is there. API routes are added
   first and always win. Static responses carry `Content-Security-Policy:
   default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self';
   frame-ancestors 'none'; ...`, `X-Frame-Options: DENY`, `nosniff` and
   `Referrer-Policy: no-referrer`. The build has no inline script or style, so the
   CSP needs no `unsafe-inline`. The `LocalOnlyGuard` still runs first on every
   request, files included.

6. **Same-origin requests.** Browsers send `Origin` on same-origin POST and
   DELETE. When the UI is served, the guard also allows an `Origin` equal to
   `http://` plus the request's own `Host`, which it has already checked is
   127.0.0.1 or localhost. A page on any other origin cannot send that header. A
   rebinding page fails the Host check first. Without a build, nothing changes.

7. **The dev server is opt-in.** `[proxy] allow_dev_ui = true` adds
   `http://127.0.0.1:5173` and `http://localhost:5173` to CORS and the origin list.
   It is off by default and must be a boolean. `npm run dev` calls
   `http://127.0.0.1:8787` directly.

8. **Settings are read-only.** No configuration endpoint was added. The screen
   shows `/health` and `/v1/models` and explains how to edit `config.toml`. For a
   cloud provider it shows the `keyring set` command; there is no key field. A web
   page therefore cannot change where data goes.

9. **Audit.** The client reads `GET /audit` tolerantly: rows as stored (ADR 0021
   columns, `entity_counts_json` or `entity_counts`, `local` as 0/1 or boolean),
   and a `VerifyResult` under `verify`, `verification` or `verify_result`. A 501
   is a state ("not served yet", plus the CLI commands), not an error. A broken
   chain names the first bad entry and the reason, and says to keep the file.

10. **Stack and licenses.** React 19, Vite 8, TypeScript 6.0, Tailwind 4,
    shadcn/ui components copied in over Radix primitives, ESLint 9 with
    typescript-eslint, react-hooks and jsx-a11y, and Vitest. Every package that
    ships in the bundle and every direct dependency is MIT or Apache-2.0;
    `npm run licenses` checks that offline from the lockfile. Build tools bring in
    ISC, MPL-2.0 (lightningcss, axe-core), CC-BY-4.0 data (caniuse-lite),
    BlueOak-1.0.0, Python-2.0 and CC0 packages transitively. None of these ship;
    the script lists them. There are no font files and no icon package: the UI uses
    system font stacks and SVG icons drawn for this project.

11. **Design.** Archival paper and binding teal, with claret kept for what needs
    attention. One bold element: each detected value carries a file tab with its
    placeholder, which lifts away when switched off. Measured WCAG contrast:
    light ink on paper 15.2, muted on paper 6.8, muted on sunken 6.3, button text
    9.2, claret on its wash 6.2, tab text 6.7 to 7.1; dark ink on paper 14.3,
    muted on paper 7.4, button text 8.9, tab text 9.4 to 9.5. axe reports no
    serious or critical violations on any screen, in either theme, at 390 px or
    1280 px.

12. **Browser test.** `tests/e2e/test_web_ui.py` (marker `e2e`) is opt-in with
    `SAFEGUARD_E2E=1`. It starts a stub OpenAI-shaped upstream (stdlib, 127.0.0.1)
    and `uv run safeguard serve` with a temporary `SAFEGUARD_HOME`, then runs Node
    Playwright (`tests/e2e/web/flows.mjs`) against the Chromium already installed.
    It fails on any console error or CSP violation, and on any request to another
    origin. Afterwards it checks that none of the Faker values reached the stub.
    One console message is allowed: Chrome logs the expected 501 from `GET /audit`.

## Consequences

- The model sees the session's placeholders as `[LITERAL_n]`, so it loses the
  type: it cannot tell that `[LITERAL_1]` is a person. Answers still restore
  correctly. A session-aware chat route (a `session_id` the gateway uses as the
  mapping) would keep the types; that needs its own ADR.
- If the model writes a canonical `[TYPE_N]` of the session on its own, never
  having seen it, the browser restores it. This is unlikely, because the model
  only saw `[LITERAL_n]`. The session-aware route above would remove it.
- A user cannot yet send a detected value in clear. A per-request allow-list
  would be a policy change to the chat routes and is not made here.
- The UI is served only from a source checkout. The desktop app (Phase 6 part 2)
  bundles the build with the sidecar.
