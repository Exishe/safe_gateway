import { Background } from "./lib/background";
import { ext, originId } from "./lib/browser";
import { GatewayClient } from "./lib/gateway";
import { type ClearedNotice, isRequest } from "./lib/messages";
import { TabSessions } from "./lib/sessions";

// The background worker: the only part that talks to the gateway (ADR 0026).

const sessions = new TabSessions({
  get: (key) => ext.storage.session.get(key),
  set: (items) => ext.storage.session.set(items),
  remove: (key) => ext.storage.session.remove(key),
});

async function badge(tabId: number, text: string): Promise<void> {
  try {
    await ext.action.setBadgeBackgroundColor({ tabId, color: "#1e4d5c" });
    await ext.action.setBadgeText({ tabId, text });
  } catch {
    // the tab closed meanwhile
  }
}

const background = new Background(new GatewayClient(), sessions, badge, originId());
const ownPages = ext.runtime.getURL("/");

ext.runtime.onMessage.addListener((message: unknown, sender, sendResponse) => {
  if (sender.id !== ext.runtime.id || !isRequest(message)) return false;
  // Content scripts may mask and read their own tab's map; only the extension's own
  // pages (the popup) may read status or clear a session, for any tab.
  const tabId = sender.tab?.id;
  const fromOwnPage = (sender.url ?? "").startsWith(ownPages);
  const fromContent = tabId !== undefined && !fromOwnPage;
  let work: Promise<unknown> | null = null;
  switch (message.type) {
    case "mask":
      if (fromContent) work = background.mask(tabId, message.text);
      break;
    case "session":
      if (fromContent) work = background.mapping(tabId).then((mapping) => ({ mapping }));
      break;
    case "protection":
      if (fromContent) work = background.protection(tabId, { state: message.state, reason: message.reason });
      break;
    case "status":
      if (fromOwnPage) work = background.status(message.tabId);
      break;
    case "clear":
      if (fromOwnPage) {
        const target = message.tabId;
        work = background.clear(target).then(async (reply) => {
          const notice: ClearedNotice = { type: "cleared" };
          await ext.tabs.sendMessage(target, notice).catch(() => undefined); // no content script there
          return reply;
        });
      }
      break;
  }
  if (work === null) return false;
  work.then(sendResponse, () => {
    sendResponse(undefined);
  });
  return true; // the answer comes asynchronously
});

ext.tabs.onRemoved.addListener((tabId) => {
  void background.clear(tabId).then(() => sessions.dropProtection(tabId));
});
