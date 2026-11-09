# Phase 6: Web UI, then Tauri shell (outline)

> Outline only. Brainstorm, then write the task plan here when the phase starts.

**Goal:** A distinctive, accessible dashboard served by the proxy, then the same UI in a Tauri 2 desktop app.

**Scope**
- `apps/web/`: React + Vite + TypeScript, Tailwind, shadcn/ui. Drop a file or paste text, detections highlighted by type with confidence, toggle any span, send, side-by-side masked request and rehydrated answer with unresolved placeholders flagged, audit browser with verify status, settings (upstream, key, thresholds, gazetteer). Dark and light, keyboard accessible, checked at 390 px.
- `apps/desktop/`: Tauri 2 shell, tray icon, start-at-login option, Python engine as a PyInstaller sidecar with start, health check and restart on crash.
- CI builds unsigned `.dmg` and `.msi` on tags (ADR 0007). `docs/INSTALL.md` documents the first-run warnings. `THIRD_PARTY_NOTICES.md` added (ADR 0008).

**Exit criteria**
- Playwright click-through of every flow, no console errors; accessibility check.
- `frontend-design` and `det-ai` skills applied to the UI. Tag `v0.6.0`.
