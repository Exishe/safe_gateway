# Phase 4: Local proxy

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `safeguard serve` on `127.0.0.1:8787` that any OpenAI- or Anthropic-compatible client can point at: requests are masked before they leave the machine, answers come back with the real values restored, streamed or not.

**Architecture:** `gateway/config.py` reads `config.toml` (never keys). `gateway/secrets.py` reads API keys from the OS keychain. `gateway/cloud/providers.py` is the provider table and the base-URL rules; `gateway/cloud/upstream.py` is the only code that sends anything, over `httpx.AsyncClient`, to the configured base URL only, without following redirects. `gateway/proxy/` is the FastAPI app: `guards.py` (Host, Origin, body cap), `sse.py` (SSE codec), `openai_shape.py` and `anthropic_shape.py` (allow-list validation, text collection, instruction, rehydration of whole and streamed answers), `audit.py` (the Phase 5 hook point), `app.py` (routes), `server.py` (loopback check, uvicorn). The CLI gains `safeguard serve`.

**Tech Stack:** FastAPI (MIT) on Starlette (BSD-3), uvicorn (BSD-3), httpx (BSD-3, already a dependency), stdlib `tomllib`, `keyring` (MIT, already a dependency). Tests: pytest, Faker, `httpx.MockTransport`, Starlette's `TestClient` (in process, no sockets).

**Spec:** ADR 0002, 0003, 0004, 0006, 0010, 0014; decisions of this phase in ADR 0019 (proxy surface and request flow) and ADR 0020 (adapters, configuration, keys).

## Global Constraints

- `gateway/proxy/`, `gateway/config.py` and `gateway/secrets.py` import no network client module; only `gateway/cloud/` imports httpx. Status codes come from `fastapi.status`, not the stdlib `http` module (it is on the network list).
- The server binds to the literal `127.0.0.1`; any other host is refused before anything is bound.
- No text in logs, error messages or audit events: counts, hashes, types, status codes and host names only.
- Keys: OS keychain only, read once at start-up, wrapped in `ApiKey` whose `repr` hides it.
- Fail closed: anything the gateway does not know how to mask (unknown field, image, tool call) is refused with a 4xx, never forwarded.
- Tests use Faker values for personal data and an in-process upstream (`httpx.MockTransport`); sockets stay disabled.

## Decisions (ADR 0019, ADR 0020)

- Tool and function calls: refused with 422 until both directions are done (mask argument leaves, rehydrate the model's tool-call arguments with JSON escaping, buffer streamed arguments).
- Images, audio, files, documents: refused with 422; the gateway masks text only.
- `/v1/models`: served locally from the configured model, no upstream call.
- No shape translation: `/v1/chat/completions` needs an OpenAI-shaped provider (OpenAI, Azure OpenAI, Ollama), `/v1/messages` Anthropic.
- Every text of every role is masked (system and stop sequences included), with one fresh mapping per request.

## Review Focus

1. No original value reaches the upstream: an integration test sends Faker emails, SSNs, cards, IBANs and a gazetteer name through both routes, streamed and not, and asserts none appears in the upstream body bytes or in any of its JSON string leaves. Task 9.
2. A placeholder split across SSE events (`[PER` | `SON_1]`), and split one byte at a time at the transport level, is restored; streamed output equals the non-streamed answer. Tasks 6, 7, 9.
3. Redirects from the upstream are not followed; the request goes only to the configured host. Task 3.
4. The server refuses a non-loopback host; Host and Origin checks; body cap with and without `Content-Length`; unknown session ids create nothing. Tasks 8, 9, 10.
5. The audit event holds no text: its repr contains none of the request's values. Task 9.
6. Config with a key-like field is refused; keys never come from the environment. Tasks 1, 2.

---

### Task 0: Dependencies, ADRs, plan

- [ ] `uv add fastapi uvicorn`; `uv run python -m tools.check_licenses` passes with no new exception.
- [ ] ADR 0019, ADR 0020, `docs/adr/README.md`; this plan. Commit `docs: phase 4 plan, ADR 0019 and 0020; add fastapi and uvicorn`.

### Task 1: Configuration

**Files:** create `gateway/cloud/providers.py`, `gateway/config.py`; tests `tests/unit/cloud/test_providers.py`, `tests/unit/test_config.py`.

**Interfaces (produces):**
- `providers.Provider(StrEnum)`: `anthropic`, `openai`, `azure_openai`, `ollama`. `ApiShape(StrEnum)`: `openai`, `anthropic`. `ProviderInfo(shape, default_base_url, needs_key, local)`; `PROVIDERS: Mapping[Provider, ProviderInfo]`. `is_loopback_host(host) -> bool`; `check_base_url(url) -> str` (trailing `/` removed; ValueError unless `https`, or `http` on a loopback host; no user info, query or fragment).
- `config.ConfigError(ValueError)`; `safeguard_home() -> Path`; `config_path() -> Path`; `ProxyConfig(max_body_bytes=4 MiB, cors_allow_origins=())`; `GatewayConfig(provider, base_url, model, api_version, gazetteer, retention_days, thresholds, proxy)`; `parse_config(data) -> GatewayConfig`; `load_config(path=None) -> GatewayConfig` (missing file: defaults, provider ollama).

- [ ] Tests: defaults without a file; `SAFEGUARD_HOME` honoured; every field parsed; provider default base URL filled in; azure without `base_url` or `model` refused; `api_version` outside azure refused; `api_key`, `[openai] token`, `password` at any level refused with a message naming the keychain; `URL_WITH_TOKEN` under `[thresholds]` accepted; a value starting `sk-` refused; unknown field refused; threshold outside 0..1 or unknown entity type refused; `http://` to a non-loopback host refused, to `127.0.0.1` accepted; user info or query in `base_url` refused; `max_body_bytes` < 1 refused; invalid TOML is a ConfigError.
- [ ] Run, FAIL; implement; PASS; commit `feat(config): config.toml without keys`.

### Task 2: API keys from the keychain

**Files:** modify `gateway/secrets.py`; test `tests/unit/test_secrets.py`.

**Interfaces (produces):** `ApiKey(value)` with `reveal() -> str`, `repr`/`str` hiding the value; `KeychainError(RuntimeError)`; `api_key_user(provider) -> str` (`api-key:<provider>`); `read_api_key(provider, service=SERVICE) -> ApiKey | None` (KeychainError when no keychain backend works).

- [ ] Tests (in-memory keyring backend set with `keyring.set_keyring`, restored after): key read under `api-key:openai`; missing key gives None; empty string gives None; a failing backend raises KeychainError without the key in the message; `repr(ApiKey(...))` and `str(...)` do not contain the value; an environment variable named like a key is never read; existing `KeyringKeyProvider` get/set/delete.
- [ ] Run, FAIL; implement; PASS; commit `feat(secrets): API keys from the OS keychain`.

### Task 3: Upstream adapter

**Files:** create `gateway/cloud/upstream.py`; test `tests/unit/cloud/test_upstream.py`.

**Interfaces (produces):** `UpstreamSettings(provider, base_url, model=None, api_version=None)`; `UpstreamError(Exception)` with `status`; `UpstreamReply(status, body, content_type)`; `UpstreamStream` with `status`, `content_type`, `chunks() -> AsyncIterator[bytes]`, `aclose()`; `Upstream(settings, api_key, transport=None)` with `provider`, `shape`, `local`, `host`, `url`, `async send(body: bytes) -> UpstreamReply`, `async open_stream(body) -> UpstreamReply | UpstreamStream` (a reply when the status is an error), `aclose()`.

- [ ] Tests (MockTransport): URL per provider (Azure deployment and `api-version`, quoted); key header per provider and no key header for Ollama; a provider that needs a key without one raises ValueError; `content-type: application/json`; a 302 to another host is a 502 UpstreamError and the handler saw one request only; timeout is 504, connection error 502, messages without the key; error status returned as a reply with its body; streaming yields the chunks in order and `aclose` closes; the client is built with `trust_env=False`.
- [ ] Run, FAIL; implement; PASS; commit `feat(cloud): upstream adapters for Anthropic, OpenAI, Azure OpenAI, Ollama`.

### Task 4: SSE codec

**Files:** create `gateway/proxy/sse.py`; test `tests/unit/proxy/test_sse.py`.

**Interfaces (produces):** `SSEEvent(data, event=None, id=None, retry=None)`; `SSEDecoder.feed(chunk: bytes) -> list[SSEEvent]`, `close() -> list[SSEEvent]`; `encode(event) -> bytes`.

- [ ] Tests: two events in one chunk; one event split at every byte gives the same events; `\r\n`, `\r` (also `\r` | `\n` across chunks) and `\n` endings; multi-line `data`; comments and unknown fields ignored; a multi-byte UTF-8 character split across chunks; `event:` and `id:` kept; `encode` then decode round-trips; an unterminated last event is returned by `close()`.
- [ ] Run, FAIL; implement; PASS; commit `feat(proxy): SSE codec`.

### Task 5: Errors, payload helpers, audit hook

**Files:** create `gateway/proxy/errors.py`, `gateway/proxy/payload.py`, `gateway/proxy/audit.py`; tests `tests/unit/proxy/test_payload.py`, `tests/unit/proxy/test_audit.py`.

**Interfaces (produces):**
- `errors.RequestRejected(Exception)(status, error_type, message)`.
- `payload.PLACEHOLDER_INSTRUCTION`; `parse_json_object(body: bytes) -> dict[str, Any]` (400 for invalid JSON, `NaN`, nesting too deep, a lone surrogate, a non-object); `choose_model(configured, requested) -> str`; `encode_json(obj) -> bytes`.
- `audit.AuditEvent` (frozen, no text fields); `AuditSink` protocol `record(event) -> None`; `NullAuditSink`; `entity_counts(mapping) -> dict[str, int]`.

- [ ] Tests: each 400 case; the instruction names the bracket form; `choose_model` prefers the configured model, accepts `qwen2.5:3b`, refuses spaces and an empty or missing model; `entity_counts` counts distinct values per type; `AuditEvent` has no `str` field other than route, outcome, provider, host, model and the two hashes.
- [ ] Run, FAIL; implement; PASS; commit `feat(proxy): request parsing helpers and audit hook point`.

### Task 6: OpenAI shape

**Files:** create `gateway/proxy/openai_shape.py`; test `tests/unit/proxy/test_openai_shape.py`.

**Interfaces (produces):** `OPENAI` (a `Shape`): `validate(body) -> dict` (allow-list, drop-list, 422 with the field named), `collect_texts(body) -> list[str]`, `with_texts(body, texts) -> dict`, `with_instruction(body) -> dict`, `rehydrate_reply(payload, mapping) -> list[str]` (in place; unresolved), `stream_rewriter(mapping)` with `on_event(event) -> list[SSEEvent]`, `finish() -> list[SSEEvent]`, `unresolved`; `error_body(error_type, message)`; `stream_error(message) -> SSEEvent`.

- [ ] Tests: string and text-part content collected in order (system, user, assistant, stop); `image_url` part 422 naming images; `tools`, `functions`, a `tool` message and assistant `tool_calls` 422 naming tools; unknown top-level field 422; `user` and message `name` dropped; null `refusal` dropped; `with_texts` puts texts back in the same places; instruction inserted at index 0; non-streamed choices rehydrated, unresolved listed; stream: `[PER` + `SON_1]` restored, flush sent as its own chunk before the `finish_reason` chunk, two choices kept apart, `[DONE]` flushes what is left, usage-only chunk passes, non-JSON data passes.
- [ ] Run, FAIL; implement; PASS; commit `feat(proxy): OpenAI chat completions shape`.

### Task 7: Anthropic shape

**Files:** create `gateway/proxy/anthropic_shape.py`; test `tests/unit/proxy/test_anthropic_shape.py`.

**Interfaces (produces):** `ANTHROPIC` with the same members as `OPENAI`.

- [ ] Tests: `system` as string and as text blocks, messages as string and blocks, `stop_sequences` collected in order; `image` and `document` blocks 422; `tool_use`, `tool_result`, `tools` 422; `metadata` dropped; `cache_control` kept; instruction prefixed to a string system, prepended to a block list, set when absent; non-streamed text blocks rehydrated, other blocks untouched; stream: split placeholder restored, flush sent as a `content_block_delta` before `content_block_stop`, two blocks kept apart, `ping` and `message_delta` pass.
- [ ] Run, FAIL; implement; PASS; commit `feat(proxy): Anthropic messages shape`.

### Task 8: Guards

**Files:** create `gateway/proxy/guards.py`; test `tests/unit/proxy/test_guards.py`.

**Interfaces (produces):** `LocalOnlyGuard(app, *, max_body_bytes, allowed_origins)`, a pure ASGI middleware.

- [ ] Tests (tiny Starlette app): Host `127.0.0.1:8787` and `localhost` pass, `evil.example` and a missing Host get 400; an `Origin` not on the list gets 403, one on the list passes; `Content-Length` over the cap gets 413 before the app runs; a chunked body over the cap gets 413; a body at the cap passes; lifespan passes through.
- [ ] Run, FAIL; implement; PASS; commit `feat(proxy): loopback guards and body cap`.

### Task 9: App and routes

**Files:** create `gateway/proxy/app.py`; tests `tests/unit/proxy/test_app.py`, `tests/integration/proxy/test_round_trip.py`, helper `tests/proxy_fakes.py`.

**Interfaces (produces):** `create_app(upstream, *, engine_config, model=None, proxy=ProxyConfig(), audit=None, sessions=None) -> FastAPI`.

- [ ] Integration tests (Faker values, gazetteer name, MockTransport upstream that records each request): both routes, streamed and not, round trip restores every value; upstream body bytes and JSON leaves contain none of them; the instruction is present; the client's `Authorization` header is not forwarded; a placeholder split across SSE events and the upstream body delivered one byte at a time; upstream JSON error passes through with its status; non-JSON error, redirect, timeout become 502/504 in the route's shape; failure mid-stream becomes an error event; audit called once per request with counts and hashes that match the bytes, and no value in its repr; shape mismatch is 400.
- [ ] Unit tests: `/health`; `/audit` 501; `/v1/models` with and without a model; `/mask` text and texts, session reuse continues numbering, unknown `session_id` 404 and nothing created; `DELETE /session/{id}` 204 then 404; 415 without JSON content type; docs pages absent.
- [ ] Run, FAIL; implement; PASS; commit `feat(proxy): chat, mask and session routes`.

### Task 10: Server and `safeguard serve`

**Files:** create `gateway/proxy/server.py`; modify `gateway/cli.py` (one subcommand registration, one handler); tests `tests/unit/proxy/test_server.py`, `tests/unit/test_cli.py`.

**Interfaces (produces):** `LOOPBACK = "127.0.0.1"`; `NotLoopback(ValueError)`; `check_host(host)`; `build_app(config, read_key=read_api_key) -> FastAPI`; `run(host=LOOPBACK, port=8787) -> int`. CLI: `safeguard serve [--host 127.0.0.1] [--port 8787]`.

- [ ] Tests (uvicorn.run replaced by a recorder): `0.0.0.0`, `localhost`, `::1`, `127.0.0.2` refused before uvicorn runs; `127.0.0.1` runs with `access_log=False`, `proxy_headers=False`; port outside 1..65535 refused; missing key for openai refused with the keyring command in the message; ollama needs no key; config error exits 2; the CLI passes host and port through.
- [ ] Run, FAIL; implement; PASS; commit `feat: safeguard serve`.

### Task 11: No-egress pin and close-out

- [ ] Test in `tests/unit/tools/test_check_no_egress.py`: `gateway/proxy/` may import `fastapi`, `starlette`, `uvicorn` and `gateway.cloud`; may not import `httpx`, `http` or `socket`. Run the checker on the tree.
- [ ] `docs/SECURITY.md`: the proxy's trust boundary and limits. `docs/INSTALL.md`: config file and `keyring set`.
- [ ] Security review of the diff against `f06d574` (security-reviewer checklist); fix Critical and Important findings.
- [ ] Full gate: ruff format and check, ty, no-egress, licenses, pytest. `safeguard serve` started, `/mask` with curl on 127.0.0.1, a `/v1/chat/completions` round trip against a stub upstream on loopback, server stopped.
- [ ] **Manual check against a local Ollama (`qwen2.5:3b`): not run in this session** (no Ollama in the build container). To be done on the developer's machine and recorded as observed.
- [ ] Later in this phase (not in this pass): tag `v0.4.0` after the manual Ollama check.
