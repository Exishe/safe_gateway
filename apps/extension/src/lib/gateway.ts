// The only code in the extension that opens a connection, and only to the gateway on
// 127.0.0.1:8787 (the manifest's host permission). It runs in the background worker:
// a content script's fetch would carry the site's origin, which the gateway refuses.

export const GATEWAY = "http://127.0.0.1:8787";

export interface Health {
  status: string;
  version: string;
  provider: string;
  local: boolean;
  audit?: boolean;
}

export interface MaskResult {
  session_id: string;
  masked: string[];
  mapping: Record<string, string>;
  entities: Record<string, number>;
}

export type FailureKind = "unreachable" | "refused" | "session_gone" | "rejected";

export class GatewayFailure extends Error {
  readonly kind: FailureKind;
  readonly status: number;

  constructor(kind: FailureKind, status: number, message: string) {
    super(message);
    this.name = "GatewayFailure";
    this.kind = kind;
    this.status = status;
  }
}

const MASK_TIMEOUT_MS = 120_000; // detection on a long draft takes a while on CPU
const SHORT_TIMEOUT_MS = 5_000;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isStringRecord(value: unknown): value is Record<string, string> {
  return isRecord(value) && Object.values(value).every((v) => typeof v === "string");
}

function isCountRecord(value: unknown): value is Record<string, number> {
  return isRecord(value) && Object.values(value).every((v) => typeof v === "number");
}

function asMaskResult(body: unknown): MaskResult {
  if (
    isRecord(body) &&
    typeof body.session_id === "string" &&
    Array.isArray(body.masked) &&
    body.masked.length === 1 &&
    typeof body.masked[0] === "string" &&
    isStringRecord(body.mapping) &&
    isCountRecord(body.entities)
  ) {
    return {
      session_id: body.session_id,
      masked: [body.masked[0]],
      mapping: body.mapping,
      entities: body.entities,
    };
  }
  throw new GatewayFailure("rejected", 200, "The gateway's answer was not in the expected shape.");
}

async function errorOf(response: Response): Promise<{ type: string; message: string }> {
  try {
    const body: unknown = await response.json();
    if (isRecord(body) && isRecord(body.error)) {
      const { type, message } = body.error;
      return { type: typeof type === "string" ? type : "", message: typeof message === "string" ? message : "" };
    }
  } catch {
    // not JSON: keep the status
  }
  return { type: "", message: "" };
}

export class GatewayClient {
  private readonly fetchFn: typeof fetch;

  constructor(fetchFn: typeof fetch = (...args) => fetch(...args)) {
    this.fetchFn = fetchFn;
  }

  private async send(path: string, init: RequestInit, timeoutMs: number): Promise<Response> {
    try {
      return await this.fetchFn(GATEWAY + path, {
        cache: "no-store",
        credentials: "omit",
        redirect: "error",
        signal: AbortSignal.timeout(timeoutMs),
        ...init,
      });
    } catch {
      throw new GatewayFailure("unreachable", 0, "SafeGuard Gateway is not answering on 127.0.0.1:8787.");
    }
  }

  private static async fail(response: Response): Promise<GatewayFailure> {
    const error = await errorOf(response);
    if (response.status === 403) return new GatewayFailure("refused", 403, error.message);
    if (response.status === 404 && error.type === "not_found") return new GatewayFailure("session_gone", 404, error.message);
    return new GatewayFailure("rejected", response.status, error.message || `The gateway answered ${response.status}.`);
  }

  async health(): Promise<Health> {
    const response = await this.send("/health", { method: "GET" }, SHORT_TIMEOUT_MS);
    if (!response.ok) throw await GatewayClient.fail(response);
    const body: unknown = await response.json();
    if (!isRecord(body) || typeof body.version !== "string" || typeof body.provider !== "string") {
      throw new GatewayFailure("rejected", response.status, "The gateway's health answer was not in the expected shape.");
    }
    const health: Health = {
      status: typeof body.status === "string" ? body.status : "",
      version: body.version,
      provider: body.provider,
      local: body.local === true,
    };
    if (typeof body.audit === "boolean") health.audit = body.audit;
    return health;
  }

  async mask(text: string, sessionId?: string): Promise<MaskResult> {
    const payload = sessionId === undefined ? { text } : { text, session_id: sessionId };
    const response = await this.send(
      "/mask",
      { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(payload) },
      MASK_TIMEOUT_MS,
    );
    if (!response.ok) throw await GatewayClient.fail(response);
    return asMaskResult(await response.json());
  }

  /** True when the gateway deleted it or never had it; false when it could not be told. */
  async deleteSession(sessionId: string): Promise<boolean> {
    try {
      const response = await this.send(`/session/${encodeURIComponent(sessionId)}`, { method: "DELETE" }, SHORT_TIMEOUT_MS);
      return response.ok || response.status === 404;
    } catch {
      return false;
    }
  }
}
