# Phase 7: Browser extension (outline)

> Outline only. Brainstorm, then write the task plan here when the phase starts.

**Goal:** A Manifest V3 extension that masks prompts on ChatGPT, Claude and Gemini through the local proxy and rehydrates replies in place.

**Scope**
- `apps/extension/`: TypeScript + Vite, Manifest V3, loads in Chrome, Edge and Brave; Firefox build via webextension-polyfill.
- Content scripts for chat.openai.com, claude.ai and gemini.google.com: on submit, send the draft to `http://127.0.0.1:8787/mask`, replace the textbox with the masked version, keep the mapping in `chrome.storage.session`, watch the reply DOM and rehydrate in place.
- Badge shows the count of masked entities. If the proxy is down, block the send and show a notice.
- Settings sync with the desktop app.

**Exit criteria**
- Playwright tests against local fixture pages that mimic each site's textbox (no live sites in CI).
- Sideload instructions for pilots in `docs/INSTALL.md` (Chrome Web Store fee is on the approval list). Tag `v0.7.0`.

**Open questions:** how to survive each site's DOM changes; whether to intercept at the fetch layer instead of the textbox.
