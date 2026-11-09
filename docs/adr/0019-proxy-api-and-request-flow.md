# 0019. Proxy API surface and request flow

- Status: Accepted
- Date: 2026-11-09
- Refines: ADR 0002 points 1 and 2, ADR 0003, ADR 0004 points 2 and 3, ADR 0006 point 1

## Context

Phase 4 puts the engine behind an HTTP server on the user's machine, so that
OpenAI- and Anthropic-compatible clients can point at it instead of at the
cloud. The phase outline left three questions open: tool and function-call
arguments, images, and `/v1/models`. A server on 127.0.0.1 also has a trust
boundary of its own: other programs on the machine, and web pages open in the
user's browser, can send it requests.

## Decision

### Surface

1. `gateway/proxy/` is a FastAPI app served by uvicorn on `127.0.0.1` only.
   `safeguard serve` and `gateway.proxy.server.run` refuse any other host before
   anything is bound: `0.0.0.0`, `localhost` (a hosts file can point it
   elsewhere), `::1` and other loopback addresses included. uvicorn is always
   given the literal `127.0.0.1`.
2. Routes:

   | Route | What it does |
   |-------|--------------|
   | `POST /v1/chat/completions` | OpenAI Chat Completions shape, streaming and not |
   | `POST /v1/messages` | Anthropic Messages shape, streaming and not |
   | `POST /mask` | `{"text": ...}` or `{"texts": [...]}`, optional `session_id`; returns the session id, the masked texts, the session's map and counts per type |
   | `DELETE /session/{id}` | 204, or 404 when there is no such session |
   | `GET /health` | status, version, provider, whether the upstream is local |
   | `GET /v1/models` | the configured model, OpenAI list shape, answered locally |
   | `GET /audit` | 501 until Phase 5 |

   There are no OpenAPI or documentation pages.
3. A chat route forwards only to a provider that speaks its shape (ADR 0020):
   `/v1/chat/completions` to OpenAI, Azure OpenAI and Ollama, `/v1/messages`
   to Anthropic. The gateway does not translate one shape into the other in
   this phase; a mismatch is a 400 that names the route to use.

### Request flow of the chat routes

1. Read the body (at most `max_body_bytes`) and parse it as strict JSON: no
   `NaN` or `Infinity`, no lone surrogates, no nesting deep enough to exhaust
   the parser.
2. Validate it against an allow-list per shape. Top-level fields, message keys
   and content part types not on the list are refused with 422 and the field
   named, so no text-bearing field the gateway does not know reaches the
   upstream unmasked. Forwarded parameters are type-checked so they cannot carry
   text either: numbers are numbers, flags are booleans, enum-like fields
   (`service_tier`, `reasoning_effort`) are short tokens, `stream_options`,
   `logit_bias` and Anthropic `thinking` hold only their documented keys. In an
   OpenAI `response_format` JSON schema, every string value (descriptions,
   enums, examples) is masked like message text; its keys and name are kept.
   A short list of end-user identifiers is dropped instead,
   because they do not change the answer: OpenAI `user`, `safety_identifier`,
   `metadata`, `store`, `prompt_cache_key`; Anthropic `metadata`; a message's
   `name`; message keys whose value is null or empty.
3. Collect every text in a fixed order: the system texts, then every message of
   every role in order, then the stop sequences. Mask them with one fresh
   mapping (`mask_messages`, ADR 0002 point 1) in a worker thread. This is a
   superset of ADR 0002's "user messages and earlier assistant turns": a system
   prompt can hold personal data too.
4. Add the instruction to keep placeholders verbatim as the first system text:
   a system message at index 0 (OpenAI) or a prefix of `system` (Anthropic).
   It is added on every request, so the request shape does not depend on what
   was found.
5. Model: the configured `model` replaces the client's; without one the
   client's passes through (letters, digits and `._:/@+-` only); with neither,
   400.
6. Forward through `gateway/cloud/` (ADR 0020). Only the gateway's own headers
   go upstream; the client's headers, its `Authorization` included, never do.
7. Rehydrate. Non-streaming: every text field of the answer. Streaming: the
   SSE stream is parsed, one `StreamingRehydrator` runs per choice (OpenAI) or
   per content block (Anthropic), and what it still holds when the choice
   finishes (`finish_reason`) or the block stops (`content_block_stop`) is sent
   as one more text delta just before that event. Every other event passes
   through unchanged.
8. Upstream errors: an error status with a JSON body passes through unchanged
   (ADR 0004 point 2). A non-JSON error, a redirect, a timeout or a network
   failure becomes 502 or 504 in the route's error shape. A failure after the
   stream has started becomes an error event at the end of the stream.
9. The mapping is cleared when the response ends, streamed or not.
10. The audit hook is called once per request (below).

### The three open questions

- **Tool and function calls: refused (422) in this phase.** Masking only the
  request side would deliver placeholders to the client's tools as if they were
  values: an email tool would write to `[EMAIL_1]`. Doing it properly means
  parsing argument JSON, masking string leaves, rehydrating the leaves of the
  model's tool calls with JSON escaping, and buffering streamed arguments until
  each call is complete, in both shapes. Until that exists, refusing is the
  honest failure: nothing leaks and nothing is silently corrupted. Refused:
  `tools`, `tool_choice`, `functions`, `function_call`, `parallel_tool_calls`,
  tool-role messages, assistant `tool_calls`, and Anthropic `tool_use` and
  `tool_result` blocks.
- **Images, audio, files and documents: refused (422).** Masking works on text.
  A picture can hold a scanned passport or a signature, and the gateway cannot
  mask inside it. A client that wants to go through the gateway sends text
  only; the 422 says so.
- **`/v1/models`: served, locally.** OpenAI-compatible chat UIs call it to fill
  their model picker and fail without it. It lists the configured model only
  (nothing without one) and never calls the upstream.

### Audit hook

`gateway/proxy/audit.py` defines `AuditEvent` and an `AuditSink` protocol with
one method, `record(event)`. The app calls it once for each chat, mask and
session-delete request, in a worker thread, after the response ends, with:
route, outcome, status code, provider, upstream host, model, whether it
streamed, distinct masked values per entity type, unresolved placeholder count,
SHA-256 of the exact bytes sent upstream and of the exact bytes received,
start time and duration. No field holds text. The default sink does nothing;
Phase 5 replaces it with the hash-chained log. A sink that raises is logged by
exception type and does not change the response, which has already been sent;
Phase 5 decides whether it also needs a record before forwarding.

### Trust boundary on 127.0.0.1

1. The `Host` header must be `127.0.0.1` or `localhost` with any port, or the
   request is refused with 400. This blocks DNS rebinding, where a web page
   points its own domain at 127.0.0.1 to read the answers.
2. A request carrying an `Origin` header is refused with 403 unless that origin
   is in `cors_allow_origins` (default: empty), which also enables CORS for
   those origins only. Phase 7 adds the extension's origin there.
3. POST routes require `Content-Type: application/json` (415 otherwise). A
   cross-site form or `text/plain` POST, which browsers send without a CORS
   preflight, therefore cannot reach a route.
4. Bodies are capped at `max_body_bytes` (default 4 MiB), from the
   `Content-Length` header and again while reading, for chunked bodies: 413.
5. Sessions are created only by the server, with 256-bit random ids. `/mask`
   with a `session_id` that does not exist is a 404 and creates nothing, so a
   client cannot plant an id and have someone else's values stored under it.
6. The uvicorn access log is off, because request paths carry session ids.
   Nothing logs request or response text, and error messages never echo
   request values.

### The no-egress check

The checker's rules (ADR 0006, ADR 0010) already allow `fastapi`, `starlette`
and `uvicorn` in `gateway/proxy/` and forbid network client modules there
(`httpx`, the stdlib `http`, `urllib`, `socket`, `ssl` and the rest). No
allowance was added. The proxy holds no client network code: every outbound
byte goes through `gateway.cloud.upstream`, which turns httpx failures into its
own `UpstreamError`, so the proxy never imports httpx, not even to catch an
exception. Status codes come from `fastapi.status`, because the stdlib `http`
module is on the network list. A test pins both halves: the proxy may import
the server side, and may not import a client.

Writing that test found a gap: `httpx2` (installed with `huggingface_hub`, and
the client Starlette's TestClient prefers), `httpcore` and `httpcore2` are full
HTTP clients that the network list did not name, so any package, the engine
included, could have imported them. They are now on the list. This narrows the
checker; it allows nothing new.

## Consequences

- Agentic clients that rely on tools, and anything that sends images, get a
  clear 422 instead of working. That is a product gap, recorded here, not a
  leak.
- Clients that send a field the gateway does not know get a 422 naming it. The
  allow-list grows as fields are reviewed.
- A rehydrated value that contains a double quote, inside an answer the model
  wrote as JSON, makes that JSON invalid. Detected values rarely contain one;
  tool support will escape them.
- An OpenAI-shaped client cannot use an Anthropic key, and the reverse, until
  shape translation is added.
