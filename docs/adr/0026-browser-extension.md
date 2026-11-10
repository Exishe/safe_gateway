# 0026. Browser extension: mask at the textbox, restore in text nodes

- Status: Accepted
- Date: 2026-11-10
- Builds on: ADR 0002 point 2, ADR 0003 and its amendment 1, ADR 0014, ADR 0019

## Context

Phase 7 adds a Manifest V3 extension (`apps/extension/`) for people who use ChatGPT,
Claude and Gemini in the browser rather than through an API client. It must mask a
draft through the local gateway before the site sends it, restore the reply on the
page, keep the map only in the browser session, and fail closed when the gateway is
not there. The phase outline left two questions open: intercept at the textbox or at
the fetch layer, and how to survive each site's DOM changes. Publishing to the Chrome
Web Store costs a fee (`docs/APPROVAL_NEEDED.md`), so pilots sideload.

## Decision

1. **Mask at the textbox.** On Enter, a click on the send button, or a form submit,
   a capture-phase listener on `window` (registered at `document_start`, so it runs
   before the site's own) stops the event, reads the draft, sends it to `POST /mask`,
   writes the masked text into the box with `insertText` (a paste event as fallback),
   reads the box back, and only then clicks the site's send button. Fetch-layer
   interception was rejected: it needs a script in the page's main world, it would
   parse each site's private and changing request format, and the site's own state
   would keep the real text and resend it in every later turn. At the textbox, the
   site's state holds masked text, so later turns are masked too.
2. **Fail closed.** The send is blocked, with an inline notice that says why, when
   the gateway does not answer, refuses the extension (403), no longer holds the
   tab's session (404), answers in an unexpected shape, the draft changed while it
   was masked, or the box does not read back as the masked text.
3. **Only the background worker talks to the gateway.** A content script's fetch
   carries the site's origin, which the gateway refuses. The worker's requests carry
   `chrome-extension://<id>` (or `moz-extension://<uuid>`). The extension pages' CSP
   adds `connect-src http://127.0.0.1:8787`.
4. **Gateway: `[proxy] extension_ids`.** A list of ids, not origins: 32 letters a to
   p (Chrome, Edge, Brave) allows `chrome-extension://<id>`; a lower-case UUID
   (Firefox's per-install Internal UUID) allows `moz-extension://<uuid>`. Anything
   else is a config error, so a typo cannot widen what is allowed. The origins join
   `cors_allow_origins` for CORS and the Origin guard; the Host guard is unchanged,
   and every other origin, the chat sites included, is still refused (tests in
   `tests/unit/proxy/test_app.py`). `cors_allow_origins` still accepts the same
   origins written out.
5. **Sessions per tab.** `chrome.storage.session` holds `{sessionId, mapping,
   entities}` under `tab:<id>`; it is memory only and readable by the extension's own
   pages and worker. A content script gets its tab's map by message and keeps it in
   its isolated world. Every turn in a tab reuses the session, so a value keeps its
   placeholder. When the gateway answers 404 for the session, the extension drops it
   and blocks that send: starting a fresh session under the old map would make
   `[PERSON_1]` mean someone else. Closing the tab, or "Clear" in the popup, deletes
   the session in the gateway and here.
6. **Restore in text nodes only.** `src/lib/dom.ts` changes the data of existing text
   nodes and nothing else: no node is added, moved or removed (so the site's
   framework keeps working) and no attribute is touched. Never inside `<a>`, the
   textbox, form fields, scripts or styles. A placeholder split across text nodes
   (streamed chunks, inline markup) is restored into its first node. Each pass starts
   from the masked text (nodes it changed are read back with their placeholders), so
   a restored value is never rescanned and clearing the map puts every placeholder
   back. An unbracketed token at the very end of the text waits until the text has
   been quiet for 600 ms, as the engine's streaming rehydrator holds it back.
7. **The engine's rules, ported and pinned.** `placeholders.ts`, `links.ts` and
   `rehydrate.ts` port the engine's grammar, link context and rules as written,
   including the link rule (ADR 0003 amendment 1) over the text of the message, with
   a line break between blocks. `tests/unit/test_extension_parity.py` writes the
   engine's output for 496 answers (96 written by hand, the link-rule examples and
   Unicode edges among them, the widened rule of security review 2 included, and 400
   fuzzed ones) to `rehydrate.parity.json`, fails if the file is stale, and the
   vitest suite checks the port against it.
8. **Unresolved placeholders stay visible** and are highlighted with the CSS Custom
   Highlight API (no DOM change); restored values get a dotted underline the same
   way. The badge shows the tab's count of masked values.
9. **No runtime dependencies.** The popup is plain TypeScript and CSS with the web
   UI's tokens copied (ADR 0024), not React: three widgets do not justify shipping a
   framework into an extension that runs on chat pages. Instead of
   webextension-polyfill (MPL-2.0) a one-line shim picks `browser` or `chrome`; every
   call used returns a promise in both. The build (`scripts/build.mjs`) runs Vite once
   per entry and writes `dist/chrome` (service worker) and `dist/firefox` (background
   script, gecko id, Firefox 128 or later). Output is not minified, so a reviewer can
   read what runs on their chat pages.
10. **Permissions:** `storage`, and host permissions for `http://127.0.0.1:8787/*`
    and the four site origins only.

## Consequences

- The selectors in `src/content/sites.ts` follow the live sites as of late 2026.
  When a site renames its textbox or send button, the extension stops intercepting
  there: the message goes out unmasked. This is the main limit and cannot be fully
  closed from a content script; the e2e runs against fixtures, not the live sites,
  so it does not catch it. A pilot should check the notice appears on each send.
- The site's own page can read what the user types before it is masked, and the
  restored reply, because both are in its DOM. The extension reduces what is
  submitted; it does not hide anything from the page's scripts.
- Attachments, pasted images and voice input are not masked.
- The model on the site does not get the gateway's "keep placeholders verbatim"
  instruction, so more placeholders may come back paraphrased and stay visible.
- Firefox is built but not tested in a browser here (no Firefox on the build
  machine); Chrome-family behaviour is what the e2e verifies.
- Settings sync with the desktop app waits for the Tauri shell (Phase 6 part 2).
