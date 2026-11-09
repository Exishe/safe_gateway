import { type AuditView, parseAudit } from "./audit";
import { type ChatShape, SSEParser, textOf } from "./sse";

// Every call goes to the gateway on 127.0.0.1:8787 and nowhere else. Built, the UI
// is served by the gateway, so the base is "" (same origin). Under `npm run dev` the
// page is on 127.0.0.1:5173 and calls the gateway across origins, which the gateway
// allows only with `[proxy] allow_dev_ui = true` in config.toml (ADR 0024).

export interface Detection {
  start: number;
  end: number;
  type: string;
  placeholder: string;
  score: number | null;
  source: string;
}

export interface MaskResult {
  session_id: string;
  masked: string[];
  mapping: Record<string, string>;
  entities: Record<string, number>;
  detections: Detection[][];
}

export interface Health {
  status: string;
  version: string;
  provider: string;
  local: boolean;
}

export type AuditResult = { kind: "ok"; view: AuditView } | { kind: "unavailable"; message: string };

export class GatewayError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "GatewayError";
    this.status = status;
  }
}

const UNREACHABLE =
  "The gateway is not answering on 127.0.0.1:8787. Start it with `uv run safeguard serve`, then try again.";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

async function errorFrom(response: Response): Promise<GatewayError> {
  let message = `The gateway answered ${response.status}.`;
  try {
    const body: unknown = await response.json();
    if (isRecord(body) && isRecord(body.error) && typeof body.error.message === "string") {
      message = body.error.message;
    }
  } catch {
    // keep the status line
  }
  return new GatewayError(response.status, message);
}

export function shapeFor(provider: string): ChatShape {
  return provider === "anthropic" ? "anthropic" : "openai";
}

export class GatewayClient {
  private readonly base: string;
  private readonly fetchFn: typeof fetch;

  constructor(base = "", fetchFn: typeof fetch = (...args) => fetch(...args)) {
    this.base = base;
    this.fetchFn = fetchFn;
  }

  private async send(path: string, init?: RequestInit): Promise<Response> {
    try {
      return await this.fetchFn(this.base + path, { cache: "no-store", ...init });
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") throw error;
      throw new GatewayError(0, UNREACHABLE);
    }
  }

  private async json(path: string, init?: RequestInit): Promise<unknown> {
    const response = await this.send(path, init);
    if (!response.ok) throw await errorFrom(response);
    return response.json();
  }

  private static post(body: unknown, signal?: AbortSignal): RequestInit {
    const init: RequestInit = {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    };
    if (signal !== undefined) init.signal = signal;
    return init;
  }

  async health(): Promise<Health> {
    return (await this.json("/health")) as Health;
  }

  async models(): Promise<string[]> {
    const body = await this.json("/v1/models");
    if (!isRecord(body) || !Array.isArray(body.data)) return [];
    return (body.data as unknown[])
      .map((m) => (isRecord(m) && typeof m.id === "string" ? m.id : null))
      .filter((id): id is string => id !== null);
  }

  async mask(text: string): Promise<MaskResult> {
    return (await this.json("/mask", GatewayClient.post({ text }))) as MaskResult;
  }

  async deleteSession(sessionId: string, keepalive = false): Promise<void> {
    const response = await this.send(`/session/${encodeURIComponent(sessionId)}`, {
      method: "DELETE",
      keepalive,
    });
    if (!response.ok && response.status !== 404) throw await errorFrom(response);
  }

  async audit(): Promise<AuditResult> {
    const response = await this.send("/audit");
    if (response.status === 501) {
      const error = await errorFrom(response);
      return { kind: "unavailable", message: error.message };
    }
    if (!response.ok) throw await errorFrom(response);
    return { kind: "ok", view: parseAudit(await response.json()) };
  }

  /** Streams the answer to `text`, calling `onText` with each new piece. */
  async chat(
    shape: ChatShape,
    model: string,
    text: string,
    onText: (piece: string) => void,
    signal?: AbortSignal,
  ): Promise<void> {
    const messages = [{ role: "user", content: text }];
    const path = shape === "anthropic" ? "/v1/messages" : "/v1/chat/completions";
    const body =
      shape === "anthropic"
        ? { model, max_tokens: 1024, stream: true, messages }
        : { model, stream: true, messages };
    const response = await this.send(path, GatewayClient.post(body, signal));
    if (!response.ok) throw await errorFrom(response);
    if (response.body === null) throw new GatewayError(502, "The gateway sent no answer.");
    const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
    const parser = new SSEParser();
    for (;;) {
      const { done, value } = await reader.read();
      const events = done ? parser.close() : parser.feed(value);
      for (const event of events) {
        const piece = textOf(shape, event);
        if (piece !== "") onText(piece);
      }
      if (done) return;
    }
  }
}

export const gateway = new GatewayClient(import.meta.env.DEV ? "http://127.0.0.1:8787" : "");
