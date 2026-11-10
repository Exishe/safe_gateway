import type { FailureKind, Health } from "./gateway";

// Messages between the content scripts, the popup and the background worker.

export type Request =
  | { type: "mask"; text: string } // content script: mask this tab's draft
  | { type: "session" } // content script: this tab's map, after a reload
  | { type: "status"; tabId: number } // popup
  | { type: "clear"; tabId: number }; // popup

export type MaskReply =
  | { ok: true; masked: string; mapping: Record<string, string>; count: number }
  | { ok: false; kind: FailureKind; message: string };

export interface SessionReply {
  mapping: Record<string, string>;
}

export type GatewayState =
  | ({ state: "ok" } & Health)
  | { state: "down" }
  | { state: "refused"; message: string }
  | { state: "error"; message: string };

export interface StatusReply {
  gateway: GatewayState;
  session: { count: number; entities: Record<string, number> } | null;
}

export interface ClearReply {
  cleared: boolean;
  /** Whether the gateway was told; if not, its copy expires after 30 idle minutes. */
  gateway: boolean;
}

/** Sent to a tab's content script when its session is cleared. */
export interface ClearedNotice {
  type: "cleared";
}

export function isRequest(value: unknown): value is Request {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  switch (v.type) {
    case "mask":
      return typeof v.text === "string";
    case "session":
      return true;
    case "status":
    case "clear":
      return typeof v.tabId === "number" && Number.isInteger(v.tabId);
    default:
      return false;
  }
}
