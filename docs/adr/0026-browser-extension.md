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

## Amendment 1, 2026-11-10: stop the whole send gesture, and never fail silently

**What happened.** The first live test on chatgpt.com (Windows 11, Chrome, fake data)
sent the message unmasked. The popup still said "Connected" and "3 values
pseudonymised", the badge read 3, and the page showed "Not sent: the message changed
while it was being masked". So the content script was injected, intercepted a send and
masked it through the gateway, but the site had already sent the original text and
cleared its box; the read-back check (point 2) then failed, correctly, after the leak.
The check was fail-closed for the next step and too late for the site's.

**Root cause.** Reasoned, not observed on the live site (chatgpt.com answers this
build machine's browser with a bot check, so it could not be loaded here). Point 1
stopped `keydown` for Enter and `click` for the button. A send is a gesture of several
events, and a site may send on one we did not stop: the press (`pointerdown` or
`mousedown`) of the button, or the release (`keyup`) of Enter, and clear its box a
moment later. Our handler, running on the click or the key press, then saw a draft
still in the box, started masking, and found it gone afterwards. Fixtures that
reproduce exactly that (`tests/e2e/extension/fixtures/chatgpt-send-on-pointerdown.html`,
`chatgpt-send-on-keyup.html`) sent the real values in the old build and are masked in
this one. A second defect was found on the way and also reproduced: the box was
found with `document.querySelector` on a selector list, whose first match in document
order can be the hidden fallback textarea that chatgpt.com keeps in the form before its
ProseMirror box. On the click and submit paths that gave an empty draft, which the
code treats as "nothing to mask", so the send passed with no notice at all
(`chatgpt-hidden-fallback.html`). Whether either is what the live page does is not
confirmed; the owner's evidence fits the first.

**Decision.**
1. Every event of a send gesture is stopped synchronously, in the capture phase on
   `window`, before any await and before the site's listeners: `keydown`, `keypress`
   and `keyup` for Enter without Shift (Ctrl or Cmd with Enter included),
   `beforeinput` for `insertParagraph` (and `insertLineBreak` in a textarea),
   `pointerdown`, `mousedown`, `pointerup`, `mouseup` and `click` on the send button
   or on any submit button of the box's form, and `submit`. The first of them starts
   the masking; the others are only stopped, while it runs and for 300 ms after.
   Masked text is sent by clicking the button with a pass-through flag set, as before.
2. The box is resolved afresh on every event, never cached, among the visible matches:
   the focused one, then one holding a draft. Nothing is attached to an element, so a
   single-page app that replaces the box needs no re-attaching.
3. If the box is empty when the masking returns, the site sent the real text first:
   the extension raises the alarm below instead of "Not sent".
4. **Not ready.** If no visible box matches the site's selectors within 4 seconds of
   a page load or a navigation, a persistent red banner reads "SafeGuard is NOT
   protecting this page - do not send personal data", the badge shows "!", and the
   popup shows the same. It clears if the box appears later.
5. **Unmasked send detected.** A new user message in the thread (selector `user` in
   `sites.ts`) that is not the text we wrote into the box (as written or restored),
   or that holds a value the tripwire recognises, or that carries none of our
   placeholders, raises the same banner and badge, and the state stays until the page
   is reloaded. History is not judged: messages present at load, arriving within 3
   seconds of a load or navigation, arriving as a batch, or not the last in the
   thread. The tripwire (`sensitiveIn` in `guard.ts`) finds e-mail addresses, Luhn
   card numbers, SSN-shaped numbers and IBANs. It does not call the gateway.
6. The content script reports its state to the worker (`protection` message); the
   worker keeps it per tab in `chrome.storage.session`, "!" wins over the count on the
   badge, and the status reply carries it to the popup.

**Consequences.** A bypass is now noisy, not silent, but it can still happen: a
listener registered before the content script, or a send that raises no event, goes
out first, and the alarm comes after. The tripwire misses names and addresses. A page
of a supported site without a message box (settings, login) shows the banner. The
live site was not tested; the fixtures model mechanisms, not the real page.

