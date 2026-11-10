import { describe, expect, it } from "vitest";
import { GatewayClient, GatewayError } from "./api";

interface Call {
  url: string;
  init: RequestInit | undefined;
}

function fakeFetch(respond: (call: Call) => Response): { fetch: typeof fetch; calls: Call[] } {
  const calls: Call[] = [];
  const fn = (input: RequestInfo | URL, init?: RequestInit) => {
    const call = { url: input instanceof Request ? input.url : String(input), init };
    calls.push(call);
    return Promise.resolve(respond(call));
  };
  return { fetch: fn, calls };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

describe("GatewayClient", () => {
  it("calls the gateway's own origin and sends JSON", async () => {
    const { fetch, calls } = fakeFetch(() =>
      json({ session_id: "s", masked: ["x"], mapping: {}, entities: {}, detections: [[]] }),
    );
    await new GatewayClient("", fetch).mask("x");
    expect(calls[0]?.url).toBe("/mask");
    expect(calls[0]?.init?.method).toBe("POST");
    expect(new Headers(calls[0]?.init?.headers).get("content-type")).toBe("application/json");
    expect(calls[0]?.init?.body).toBe(JSON.stringify({ text: "x" }));
  });

  it("turns an error body into a GatewayError with the gateway's message", async () => {
    const { fetch } = fakeFetch(() => json({ error: { type: "invalid_request_error", message: "bad text" } }, 400));
    const error = await new GatewayClient("", fetch).mask("x").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(GatewayError);
    expect((error as GatewayError).status).toBe(400);
    expect((error as GatewayError).message).toBe("bad text");
  });

  it("reads the Anthropic error shape too", async () => {
    const { fetch } = fakeFetch(() => json({ type: "error", error: { type: "x", message: "from anthropic" } }, 429));
    const error = await new GatewayClient("", fetch).health().catch((e: unknown) => e);
    expect((error as GatewayError).message).toBe("from anthropic");
  });

  it("explains a gateway that cannot be reached", async () => {
    const fetch = (() => Promise.reject(new TypeError("Failed to fetch"))) as typeof globalThis.fetch;
    const error = await new GatewayClient("", fetch).health().catch((e: unknown) => e);
    expect((error as GatewayError).status).toBe(0);
    expect((error as GatewayError).message).toMatch(/safeguard serve/);
  });

  it("treats a 404 from /audit (started with --no-audit) as 'no audit log', not as a failure", async () => {
    const message = "no audit log: the gateway was started with --no-audit";
    const { fetch } = fakeFetch(() => json({ error: { type: "not_found", message } }, 404));
    expect(await new GatewayClient("", fetch).audit()).toEqual({ kind: "disabled", message });
  });

  it("asks for an older page of the audit log with before_id", async () => {
    const verification = { ok: true, rows_checked: 0, first_bad_id: null, reason: null, detail: "", head_id: 0 };
    const { fetch, calls } = fakeFetch(() => json({ rows: [], next_before_id: null, verification }));
    const client = new GatewayClient("", fetch);
    await client.audit();
    await client.audit(41);
    expect(calls.map((c) => c.url)).toEqual(["/audit", "/audit?before_id=41"]);
  });

  it("fails on another audit error", async () => {
    const { fetch } = fakeFetch(() => json({ error: { type: "x", message: "disk full" } }, 500));
    const error = await new GatewayClient("", fetch).audit().catch((e: unknown) => e);
    expect((error as GatewayError).message).toBe("disk full");
  });

  it("deletes a session and accepts 404 as already gone", async () => {
    const { fetch, calls } = fakeFetch(() => new Response(null, { status: 404 }));
    await new GatewayClient("", fetch).deleteSession("abc/def");
    expect(calls[0]?.url).toBe("/session/abc%2Fdef");
    expect(calls[0]?.init?.method).toBe("DELETE");
  });

  it.each([
    ["openai", "/v1/chat/completions", 'data: {"choices":[{"index":0,"delta":{"content":"Hi [PERSON_1]"}}]}\n\n'],
    [
      "anthropic",
      "/v1/messages",
      'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hi [PERSON_1]"}}\n\n',
    ],
  ] as const)("sends the %s chat in its session, as a header and never in the body", async (shape, path, sse) => {
    const { fetch, calls } = fakeFetch(
      () => new Response(sse, { status: 200, headers: { "content-type": "text/event-stream" } }),
    );
    const pieces: string[] = [];
    await new GatewayClient("", fetch).chat(shape, "m", "Hi [PERSON_1]", "sess-1", (p) => pieces.push(p));
    expect(calls[0]?.url).toBe(path);
    expect(new Headers(calls[0]?.init?.headers).get("x-safeguard-session")).toBe("sess-1");
    // The body is exactly the chat request: no session field rides along in it.
    const messages = [{ role: "user", content: "Hi [PERSON_1]" }];
    const body = shape === "anthropic" ? { model: "m", max_tokens: 1024, stream: true, messages } : { model: "m", stream: true, messages };
    expect(calls[0]?.init?.body).toBe(JSON.stringify(body));
    expect(pieces.join("")).toBe("Hi [PERSON_1]");
  });

  it("lists the configured model", async () => {
    const { fetch } = fakeFetch(() => json({ object: "list", data: [{ id: "llama3.2" }] }));
    expect(await new GatewayClient("", fetch).models()).toEqual(["llama3.2"]);
  });
});
