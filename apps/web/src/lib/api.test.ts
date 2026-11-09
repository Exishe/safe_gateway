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

  it("treats a 501 from /audit as 'not available', not as a failure", async () => {
    const { fetch } = fakeFetch(() => json({ error: { type: "not_implemented", message: "later" } }, 501));
    expect(await new GatewayClient("", fetch).audit()).toEqual({ kind: "unavailable", message: "later" });
  });

  it("deletes a session and accepts 404 as already gone", async () => {
    const { fetch, calls } = fakeFetch(() => new Response(null, { status: 404 }));
    await new GatewayClient("", fetch).deleteSession("abc/def");
    expect(calls[0]?.url).toBe("/session/abc%2Fdef");
    expect(calls[0]?.init?.method).toBe("DELETE");
  });

  it("lists the configured model", async () => {
    const { fetch } = fakeFetch(() => json({ object: "list", data: [{ id: "llama3.2" }] }));
    expect(await new GatewayClient("", fetch).models()).toEqual(["llama3.2"]);
  });
});
