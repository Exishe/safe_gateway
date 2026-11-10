# Phase 7: Browser extension (task plan)

**Goal:** A Manifest V3 extension that masks prompts on ChatGPT, Claude and Gemini
through the local gateway before they are sent, and restores the answers in place.

**Decisions** are recorded in ADR 0026. In short:

- The extension masks at the **textbox**, not at the fetch layer: the site's own state
  then holds masked text, so every later turn it resends is masked too, and nothing has
  to run in the page's main world or parse a site's private request format.
- Only the **background** worker talks to `http://127.0.0.1:8787`. Content scripts ask it
  through `runtime.sendMessage`; their own fetches would carry the site's origin, which
  the gateway refuses.
- The map lives in `chrome.storage.session` (trusted contexts only), keyed by tab, with
  the gateway's session id. Content scripts get a copy in memory.
- The rehydrator is a TypeScript port of `gateway/engine/placeholders.py`,
  `rehydrate.py` and `links.py`: the three rules of ADR 0003, the link rule of its
  amendment 1, plus the DOM rules: text nodes only, never inside `<a>`, never inside the
  editable textbox, never an attribute. Unknown placeholders stay visible.
- **Fail closed:** if `/mask` fails, or the textbox does not read back as the masked text,
  the send is blocked and an inline notice says why.
- No runtime npm dependencies. The popup is plain TypeScript and CSS with the web UI's
  tokens. `browser`/`chrome` are bridged by a ten-line shim, not webextension-polyfill.
- Gateway side: `[proxy] extension_ids`, strictly validated: a Chrome-family id
  (32 letters a to p) allows `chrome-extension://<id>`; a Firefox internal UUID allows
  `moz-extension://<uuid>`. Everything else stays refused.

## Tasks

1. **Gateway: `[proxy] extension_ids`** (test first, `tests/unit/test_config.py`,
   `tests/unit/proxy/test_app.py`). Parse and validate; derive origins; the app allows
   them for CORS and the Origin guard; a different id, a page origin and a bad Host are
   still refused. Document in `docs/INSTALL.md`.
2. **Scaffold `apps/extension/`**: package.json (pinned versions as in apps/web),
   tsconfig, eslint (strict type-checked), vitest, license check script, and a build
   script that runs Vite once per entry (content script and background as IIFE, popup as
   a page) and writes `dist/chrome/` and `dist/firefox/` with their manifests.
3. **Rehydrator** (vitest first): `placeholders.ts` (grammar), `links.ts` (LinkContext
   port), `rehydrate.ts` (string rehydration with reasons), then `dom.ts` (text nodes,
   tokens split across nodes, `<a>` and editor exclusion, already-restored ranges never
   rescanned, trailing unbracketed tokens deferred until the text is quiet). A test
   checks the entity list matches the engine (`tests/unit/test_extension_entities.py`).
4. **Gateway client and background** (vitest with a fake fetch): `/health`, `/mask` with
   the tab's `session_id`, a 404 drops the tab's session, `DELETE /session/{id}`, badge
   text per tab, cleanup when a tab closes.
5. **Content script**: site adapters (editor, send button, reply root) for chatgpt.com
   and chat.openai.com, claude.ai, gemini.google.com; capture-phase interception of
   Enter, the send button and form submit; replace the draft (`insertText`, with a read
   back check), re-send; inline notice; MutationObserver rehydration.
6. **Popup**: status from `/health`, entity counts for the active tab, clear session,
   dashboard link; light and dark; keyboard reachable; checked at 390 px too.
7. **E2E** (`tests/e2e/test_extension.py` + `tests/e2e/extension/flows.mjs`): Chromium
   with the unpacked extension in a persistent context; fixture pages for the three sites
   served by Playwright routing under their real host names (no request leaves the
   machine; anything not fixture is aborted); `safeguard serve` with a stub upstream, a
   temp `SAFEGUARD_HOME` and the file keyring. Assert: only masked text is "sent"; the
   reply is restored in place; a placeholder in a link is not; unknown placeholders stay
   visible; with the gateway stopped the send is blocked with a notice. Screenshots of
   the popup, light and dark.
8. **CI**: an `extension` job (install with scripts off, licenses, lint with typecheck,
   unit tests, build), actions pinned by SHA; `zizmor --offline`.
9. **Docs**: ADR 0026, INSTALL sideload steps (Chrome, Edge, Brave via Load unpacked;
   Firefox via about:debugging), SECURITY limits, PROGRESS.

## Exit criteria

- `npm run build`, `npm run lint`, `npm test` pass in `apps/extension/`.
- The e2e run passes and its screenshots exist.
- The Python gate is green. Tag `v0.7.0` only after the pre-tag review (not in this run).

## Out of scope (recorded in ADR 0026)

- Attachments and voice input on the sites are not masked.
- Settings sync with the desktop app (waits for Phase 6 part 2).
- Store publishing (fee; `docs/APPROVAL_NEEDED.md`).
