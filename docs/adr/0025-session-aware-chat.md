# 0025. Session-aware chat routes: the model and the audit log keep the real types

- Status: Accepted
- Date: 2026-11-10
- Amends: ADR 0024 points 2 and 3 and its first three consequences
- Builds on: ADR 0002 point 2, ADR 0003 amendment 1, ADR 0014 point 4, ADR 0019, ADR 0021 point 3

## Context

ADR 0024 left a known limitation. The web UI masks a text with `POST /mask`, which
creates a session, and sends the masked text to `/v1/chat/completions` or
`/v1/messages`. The chat routes are stateless (ADR 0002 point 1): they treat the
session's `[PERSON_1]` as placeholder-shaped input and wrap it as `[LITERAL_n]`
(ADR 0014 point 4). So the model cannot tell a person from an email, and the audit
row for a text with a name, an email, a card and an IBAN reads `LITERAL: 4`. As
evidence of what kinds of data left the machine, that row is weaker than one for an
API client's request.

## Decision

1. **The session travels in a header.** A chat request may carry
   `x-safeguard-session: <id>`, the id `POST /mask` returned. A header, not a body
   field: the body allow-lists of ADR 0019 stay as they are, nothing has to be
   stripped, and the gateway already never forwards a client's headers
   (ADR 0019, request flow point 6), so the id cannot reach the upstream. The
   header is added to the CORS allow-list, which only matters for the opt-in dev
   UI (ADR 0024 point 7). Without the header nothing changes.

2. **An unusable id is a 404 and nothing is sent.** A value that is not 1 to 128
   of `A-Z a-z 0-9 _ -` (what `secrets.token_urlsafe` writes), an id the store
   never issued, and an expired or deleted session all get the same 404 in the
   route's error shape. The message never repeats the id. Sessions are still
   created only by the server (ADR 0019, trust boundary point 5), so a client
   cannot plant one.

3. **Masking uses the session's mapping.** Detection runs first, outside the
   session lock, as for `/mask`. Under the lock, the size cap of `/mask` is checked
   (413 before anything is added), and the texts are masked with the session's
   mapping by `mask_detected_messages` (`gateway/engine/mask.py`), with every
   placeholder the session holds as the set to keep:
   - a token that is exactly one of the session's placeholders, `[LITERAL_n]` ones
     included, is left as written. A detected span wholly inside such a token is
     dropped (the token is text the gateway wrote); a span that crosses its edge is
     kept and united with it, as ADR 0014 point 5 unites overlaps (fail-safe);
   - every other placeholder-shaped token is still `[LITERAL_n]`: a placeholder the
     session never issued (`[PERSON_7]`), and another spelling of one it did
     (`[person_1]`, `PERSON_1`);
   - values in clear are detected and masked in the same session. The known-value
     sweep (ADR 0014 amendment 1) also runs with the session's values, so a value
     the user switched off in the UI goes out as its session placeholder;
   - new entries are saved to disk when the store persists, as `/mask` does.

4. **The answer is restored with the session's mapping**, by the same rehydration
   as every chat request. The link rule (ADR 0003 amendment 1, security review I3)
   applies unchanged: a placeholder inside a link stays as written and counts as
   unresolved. The mapping is not cleared when the response ends; it belongs to the
   session, which ends with `DELETE /session/{id}` or its idle TTL. A session
   deleted while an answer streams leaves the rest of that answer's placeholders
   unresolved, which fails closed.

5. **The audit row counts what was sent, by type.** The event's `entity_counts`
   are the distinct placeholders of the session's mapping that appear in the masked
   texts sent upstream (`entity_counts_present`), not the whole session, which may
   hold values from earlier checks. They are read when the request is masked. The
   event carries the session id; `AuditLogSink` passes it to the log, which stores
   its sha256 in `session_id` (ADR 0021 point 3), so a session's rows group without
   the id being kept. `GET /audit` still never shows that column. Nothing logs the
   id: the access log is off (ADR 0019) and errors name types only.

6. **Who may use a session.** The id is a 256-bit bearer handle. Whoever holds it
   already received the session's whole map from `/mask`. A chat request in the
   session can make the gateway restore any of the session's values into an
   answer, which gives that holder nothing it did not already have. The Host and
   Origin guards still run before every route, so a page on another origin or a
   rebinding page cannot send the request at all.

7. **The web UI sends its session.** `GatewayClient.chat` takes the session id and
   sends it in the header. The answer arrives already restored, so the browser no
   longer restores anything: `flag()` in `apps/web/src/lib/rehydrate.ts` marks every
   placeholder-shaped token left in the answer as not restored and shows it as
   written. Restoring in the browser would undo the gateway's refusal to restore
   inside a link. `restore()` is still used to mark the session's placeholders in
   the request pane. The "Show placeholders" switch, which swapped restored values
   back for their placeholders, is removed: the browser no longer knows where the
   gateway put a value. The request pane still shows exactly what was sent, with
   every placeholder marked.

8. **Tests.** `tests/unit/engine/test_mask_session.py` (keep set, forged and
   respelled tokens, spans inside a kept token, values in clear),
   `tests/integration/proxy/test_session_chat.py` (both API shapes, streaming and
   not: typed placeholders upstream and no Faker value, audit counts
   `PERSON: 1, EMAIL: 1`, unknown, expired and malformed ids as 404 with nothing
   sent, forged placeholders as `LITERAL`, the session outliving the request, the
   link rule, the dev-UI preflight), `tests/unit/audit/test_sink.py` (the hash is
   stored, the id is not), and the browser test, whose Audit screen must now show
   `Card number 1, Email 1, IBAN 1, Name 1` after a UI send, and whose stub must
   receive the four typed placeholders and no `LITERAL`.

## Consequences

- The model sees `[PERSON_1]` and `[EMAIL_1]` and can write sensible answers about
  them, and the audit row is evidence of which kinds of data a UI request masked.
  ADR 0024's first three consequences no longer hold for the UI.
- Switching a value off in the UI still does not send it in clear: once `/mask`
  found it, the session's sweep masks it again with the same placeholder. The UI
  says so. Sending a detected value in clear remains a policy change for later.
- A value the session restored that is itself placeholder-shaped (the user typed
  `[PERSON_9]`, held as `[LITERAL_1]`) is flagged by the browser as not restored,
  although it is the user's own text. This is rare and errs towards attention.
- An API client may use the header too. Without it, the chat routes stay stateless
  and clear their mapping after each response, as ADR 0002 point 1 decides.
- A session now lives as long as it is used by chat requests as well, since each
  use restarts its idle timer (ADR 0002 point 2).
