import { type GatewayClient, GatewayFailure } from "./gateway";
import type { ClearReply, GatewayState, MaskReply, Protection, ProtectionState, StatusReply } from "./messages";
import { countOf, type TabSessions } from "./sessions";

// What the background worker does, without the browser APIs, so it can be tested.

export type Badge = (tabId: number, text: string) => Promise<void>;

function messageFor(failure: GatewayFailure, extensionId: string): string {
  switch (failure.kind) {
    case "unreachable":
      return "SafeGuard Gateway is not answering on 127.0.0.1:8787, so this message could not be masked. Start it with `safeguard serve`, then send again.";
    case "refused":
      return `The gateway refused this extension. Add extension_ids = ["${extensionId}"] under [proxy] in ~/.safeguard/config.toml, restart the gateway, then send again.`;
    case "session_gone":
      return "The gateway no longer holds this tab's session: it was idle for 30 minutes, or the gateway restarted. Placeholders in earlier replies stay as placeholders. Start a new chat if you can, then send again.";
    case "rejected":
      return `The gateway could not mask this message. ${failure.message}`.trim();
  }
}

/** The badge when the page is not protected: "!" wins over the count. */
export const ALARM_BADGE = "!";

function unprotected(state: ProtectionState | undefined): boolean {
  return state === "not_ready" || state === "unmasked";
}

export class Background {
  private readonly client: GatewayClient;
  private readonly sessions: TabSessions;
  private readonly badge: Badge;
  private readonly extensionId: string;

  constructor(client: GatewayClient, sessions: TabSessions, badge: Badge, extensionId = "<extension id>") {
    this.client = client;
    this.sessions = sessions;
    this.badge = badge;
    this.extensionId = extensionId;
  }

  async mask(tabId: number, text: string): Promise<MaskReply> {
    const existing = await this.sessions.get(tabId);
    try {
      const result = await this.client.mask(text, existing?.sessionId);
      const masked = result.masked[0] ?? "";
      await this.sessions.put(tabId, { sessionId: result.session_id, mapping: result.mapping, entities: result.entities });
      const count = countOf(result.entities);
      await this.showBadge(tabId, count);
      return { ok: true, masked, mapping: result.mapping, count };
    } catch (error) {
      const failure =
        error instanceof GatewayFailure ? error : new GatewayFailure("rejected", 0, "Unexpected error while masking.");
      if (failure.kind === "session_gone") {
        // Never start a fresh session under the old map: [PERSON_1] would then mean
        // someone else. The next send starts clean (ADR 0026).
        await this.sessions.drop(tabId);
        await this.showBadge(tabId, 0);
      }
      return { ok: false, kind: failure.kind, message: messageFor(failure, this.extensionId) };
    }
  }

  async mapping(tabId: number): Promise<Record<string, string>> {
    const session = await this.sessions.get(tabId);
    const count = session === null ? 0 : countOf(session.entities);
    await this.showBadge(tabId, count); // a reload resets the badge
    return session?.mapping ?? {};
  }

  /** The count of masked values, or "!" while the page is not protected. */
  private async showBadge(tabId: number, count: number): Promise<void> {
    const report = await this.sessions.getProtection(tabId);
    if (unprotected(report?.state)) await this.badge(tabId, ALARM_BADGE);
    else await this.badge(tabId, count > 0 ? String(count) : "");
  }

  /** The content script's report on its page (ADR 0026 amendment 2026-11-10). */
  async protection(tabId: number, report: Protection): Promise<void> {
    await this.sessions.putProtection(tabId, report);
    const session = await this.sessions.get(tabId);
    await this.showBadge(tabId, session === null ? 0 : countOf(session.entities));
  }

  async status(tabId: number): Promise<StatusReply> {
    const session = await this.sessions.get(tabId);
    let gateway: GatewayState;
    try {
      gateway = { state: "ok", ...(await this.client.health()) };
    } catch (error) {
      if (error instanceof GatewayFailure && error.kind === "refused") {
        gateway = { state: "refused", message: messageFor(error, this.extensionId) };
      } else if (error instanceof GatewayFailure && error.kind !== "unreachable") {
        gateway = { state: "error", message: error.message };
      } else {
        gateway = { state: "down" };
      }
    }
    return {
      gateway,
      session: session === null ? null : { count: countOf(session.entities), entities: session.entities },
      protection: await this.sessions.getProtection(tabId),
    };
  }

  async clear(tabId: number): Promise<ClearReply> {
    const session = await this.sessions.get(tabId);
    await this.sessions.drop(tabId);
    await this.showBadge(tabId, 0);
    const told = session === null ? true : await this.client.deleteSession(session.sessionId);
    return { cleared: true, gateway: told };
  }
}
