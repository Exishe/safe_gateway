import { beforeEach, describe, expect, it } from "vitest";
import { Background, type Badge } from "./background";
import { GatewayClient } from "./gateway";
import { type StorageArea, TabSessions } from "./sessions";

// A fake gateway: answers like gateway/proxy/app.py and records what it was sent.
interface Call {
  method: string;
  path: string;
  body: unknown;
}

function fakeGateway(reply: (call: Call) => Response | "down") {
  const calls: Call[] = [];
  const fetchFn = (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url);
    const call = {
      method: init?.method ?? "GET",
      path: url.pathname,
      body: typeof init?.body === "string" ? (JSON.parse(init.body) as unknown) : undefined,
    };
    calls.push(call);
    const answer = reply(call);
    return answer === "down" ? Promise.reject(new TypeError("Failed to fetch")) : Promise.resolve(answer);
  };
  return { calls, client: new GatewayClient(fetchFn) };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

function memoryArea(): StorageArea & { data: Map<string, unknown> } {
  const data = new Map<string, unknown>();
  return {
    data,
    get: (key) => Promise.resolve(data.has(key) ? { [key]: data.get(key) } : {}),
    set: (items) => {
      for (const [k, v] of Object.entries(items)) data.set(k, structuredClone(v));
      return Promise.resolve();
    },
    remove: (key) => {
      data.delete(key);
      return Promise.resolve();
    },
  };
}

const SID = "s".repeat(43);
const MASKED = {
  session_id: SID,
  masked: ["Write to [PERSON_1] at [EMAIL_1]"],
  mapping: { "[PERSON_1]": "Anna Petrova", "[EMAIL_1]": "anna@example.org" },
  entities: { PERSON: 1, EMAIL: 1 },
  detections: [[]],
};

let badges: Map<number, string>;
const badge: Badge = (tabId, text) => {
  badges.set(tabId, text);
  return Promise.resolve();
};

beforeEach(() => {
  badges = new Map();
});

describe("mask", () => {
  it("masks through the gateway, keeps the map per tab and sets the badge", async () => {
    const { calls, client } = fakeGateway(() => json(MASKED));
    const area = memoryArea();
    const bg = new Background(client, new TabSessions(area), badge);
    const reply = await bg.mask(7, "Write to Anna Petrova at anna@example.org");
    expect(reply).toEqual({ ok: true, masked: MASKED.masked[0], mapping: MASKED.mapping, count: 2 });
    expect(calls).toEqual([{ method: "POST", path: "/mask", body: { text: "Write to Anna Petrova at anna@example.org" } }]);
    expect(area.data.get("tab:7")).toEqual({ sessionId: SID, mapping: MASKED.mapping, entities: MASKED.entities });
    expect(badges.get(7)).toBe("2");
  });

  it("sends the tab's session id on the next turn, so numbering carries on", async () => {
    const { calls, client } = fakeGateway(() => json(MASKED));
    const bg = new Background(client, new TabSessions(memoryArea()), badge);
    await bg.mask(7, "first");
    await bg.mask(7, "second");
    expect(calls[1]?.body).toEqual({ text: "second", session_id: SID });
  });

  it("fails closed when the gateway is down", async () => {
    const { client } = fakeGateway(() => "down");
    const bg = new Background(client, new TabSessions(memoryArea()), badge);
    const reply = await bg.mask(7, "Anna Petrova");
    expect(reply.ok).toBe(false);
    expect(reply).toMatchObject({ kind: "unreachable" });
  });

  it("names the config line when the gateway refuses the extension", async () => {
    const forbidden = { error: { type: "forbidden", message: "requests from this origin are not allowed" } };
    const { client } = fakeGateway(() => json(forbidden, 403));
    const bg = new Background(client, new TabSessions(memoryArea()), badge, "abcdefghijklmnopabcdefghijklmnop");
    const reply = await bg.mask(7, "x");
    expect(reply).toMatchObject({ ok: false, kind: "refused" });
    expect(reply.ok ? "" : reply.message).toContain('extension_ids = ["abcdefghijklmnopabcdefghijklmnop"]');
  });

  it("drops a session the gateway no longer has, and blocks that send", async () => {
    let gone = false;
    const { client } = fakeGateway(() =>
      gone ? json({ error: { type: "not_found", message: "no such session" } }, 404) : json(MASKED),
    );
    const area = memoryArea();
    const bg = new Background(client, new TabSessions(area), badge);
    await bg.mask(7, "first");
    gone = true;
    const reply = await bg.mask(7, "second");
    expect(reply).toMatchObject({ ok: false, kind: "session_gone" });
    expect(area.data.has("tab:7")).toBe(false);
    expect(badges.get(7)).toBe("");
  });

  it("refuses a reply that is not the gateway's shape", async () => {
    const { client } = fakeGateway(() => json({ masked: "not a list" }));
    const bg = new Background(client, new TabSessions(memoryArea()), badge);
    expect(await bg.mask(7, "x")).toMatchObject({ ok: false, kind: "rejected" });
  });
});

describe("sessions", () => {
  it("clears a tab's session at the gateway and here", async () => {
    const { calls, client } = fakeGateway((call) => (call.method === "DELETE" ? new Response(null, { status: 204 }) : json(MASKED)));
    const area = memoryArea();
    const bg = new Background(client, new TabSessions(area), badge);
    await bg.mask(3, "x");
    expect(await bg.clear(3)).toEqual({ cleared: true, gateway: true });
    expect(calls.at(-1)).toEqual({ method: "DELETE", path: `/session/${SID}`, body: undefined });
    expect(area.data.has("tab:3")).toBe(false);
    expect(badges.get(3)).toBe("");
  });

  it("still forgets the map when the gateway cannot be told", async () => {
    let down = false;
    const { client } = fakeGateway(() => (down ? "down" : json(MASKED)));
    const area = memoryArea();
    const bg = new Background(client, new TabSessions(area), badge);
    await bg.mask(3, "x");
    down = true;
    expect(await bg.clear(3)).toEqual({ cleared: true, gateway: false });
    expect(area.data.has("tab:3")).toBe(false);
  });

  it("reports the gateway's health and the tab's counts", async () => {
    const health = { status: "ok", version: "0.7.0", provider: "openai", local: false, audit: true };
    const { client } = fakeGateway((call) => (call.path === "/health" ? json(health) : json(MASKED)));
    const bg = new Background(client, new TabSessions(memoryArea()), badge);
    await bg.mask(5, "x");
    expect(await bg.status(5)).toEqual({
      gateway: { state: "ok", status: "ok", version: "0.7.0", provider: "openai", local: false, audit: true },
      session: { count: 2, entities: { PERSON: 1, EMAIL: 1 } },
      protection: null,
    });
    expect((await bg.status(6)).session).toBeNull();
  });

  it("says the gateway is down when /health does not answer", async () => {
    const { client } = fakeGateway(() => "down");
    const bg = new Background(client, new TabSessions(memoryArea()), badge);
    expect((await bg.status(1)).gateway).toEqual({ state: "down" });
  });
});

describe("protection reports (ADR 0026 amendment 2026-11-10)", () => {
  const NOT_READY = { state: "not_ready", reason: "no textbox" } as const;

  it("shows ! on the badge and in the status while a page is not protected", async () => {
    const { client } = fakeGateway(() => json(MASKED));
    const bg = new Background(client, new TabSessions(memoryArea()), badge);
    await bg.protection(3, NOT_READY);
    expect(badges.get(3)).toBe("!");
    expect((await bg.status(3)).protection).toEqual(NOT_READY);
  });

  it("keeps ! after masking and after the content script re-reads its map", async () => {
    const { client } = fakeGateway(() => json(MASKED));
    const bg = new Background(client, new TabSessions(memoryArea()), badge);
    await bg.protection(3, { state: "unmasked", reason: "x" });
    await bg.mask(3, "x");
    expect(badges.get(3)).toBe("!");
    await bg.mapping(3);
    expect(badges.get(3)).toBe("!");
  });

  it("goes back to the count when the page reports ok, and clears on checking", async () => {
    const { client } = fakeGateway(() => json(MASKED));
    const bg = new Background(client, new TabSessions(memoryArea()), badge);
    await bg.mask(3, "x");
    await bg.protection(3, NOT_READY);
    expect(badges.get(3)).toBe("!");
    await bg.protection(3, { state: "ok", reason: "" });
    expect(badges.get(3)).toBe("2");
    await bg.protection(3, NOT_READY);
    await bg.protection(3, { state: "checking", reason: "" });
    expect(badges.get(3)).toBe("2");
  });

  it("is not erased by clearing the session: the page is still unprotected", async () => {
    const { client } = fakeGateway(() => json(MASKED));
    const bg = new Background(client, new TabSessions(memoryArea()), badge);
    await bg.mask(3, "x");
    await bg.protection(3, { state: "unmasked", reason: "x" });
    await bg.clear(3);
    expect(badges.get(3)).toBe("!");
  });
});
