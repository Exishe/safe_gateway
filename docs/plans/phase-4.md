# Phase 4: Local proxy (outline)

> Outline only. Brainstorm, then write the task plan here when the phase starts.

**Goal:** `safeguard serve` on `127.0.0.1:8787` that any OpenAI- or Anthropic-compatible client can point at.

**Scope**
- `gateway/proxy/`: FastAPI app with `POST /v1/chat/completions` (OpenAI shape), `POST /v1/messages` (Anthropic shape), `POST /mask`, `DELETE /session/{id}`, `GET /health`, `GET /audit`. Binds to `127.0.0.1` only.
- Flow per request: mask every user-role message (and earlier assistant turns, ADR 0002), add the system instruction to keep placeholders verbatim, forward through `gateway/cloud/`, stream back with the streaming rehydrator, write one audit row (Phase 5 hook point).
- `gateway/cloud/`: upstream adapters for Anthropic, OpenAI, Azure OpenAI and Ollama over httpx. Keys come from the OS keychain only.
- Config in `~/.safeguard/config.toml`: provider, base URL, model, thresholds, gazetteer path, retention days. Never keys.

**Exit criteria**
- Integration tests with a mocked upstream (no internet), for streaming and non-streaming, both API shapes.
- Manual check against local Ollama (`qwen2.5:3b`) on the developer's machine, recorded as observed.
- Security review by the `security-reviewer` agent. Tag `v0.4.0`.

**Open questions:** how to handle tool/function-call arguments and images in messages; whether `/v1/models` is needed for client compatibility.
