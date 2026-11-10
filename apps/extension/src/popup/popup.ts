import { ext } from "../lib/browser";
import { familyOf, labelOf } from "../lib/entities";
import { NOT_PROTECTING } from "../content/banner";
import type { ClearReply, Request, StatusReply } from "../lib/messages";

function byId(id: string): HTMLElement {
  const element = document.getElementById(id);
  if (element === null) throw new Error(`popup.html has no #${id}`);
  return element;
}

function buttonById(id: string): HTMLButtonElement {
  const element = byId(id);
  if (!(element instanceof HTMLButtonElement)) throw new Error(`#${id} is not a button`);
  return element;
}

/** Text with `code` spans, as DOM nodes. */
function setRich(element: HTMLElement, text: string): void {
  element.replaceChildren(
    ...text.split("`").map((part, i) => {
      if (i % 2 === 0) return document.createTextNode(part);
      const code = document.createElement("code");
      code.textContent = part;
      return code;
    }),
  );
}

async function ask<T>(request: Request): Promise<T | undefined> {
  try {
    const reply: unknown = await ext.runtime.sendMessage(request);
    return reply as T | undefined;
  } catch {
    return undefined;
  }
}

/** The tab the popup is about: the active tab, or `?tab=` when opened as a page. */
async function targetTab(): Promise<number | null> {
  const asked = Number(new URLSearchParams(location.search).get("tab"));
  if (Number.isInteger(asked) && asked > 0) return asked;
  const [tab] = await ext.tabs.query({ active: true, currentWindow: true });
  return tab?.id ?? null;
}

function showGateway(status: StatusReply | undefined): void {
  const state = byId("state");
  const text = byId("state-text");
  const detail = byId("state-detail");
  const gateway = status?.gateway ?? { state: "error" as const, message: "The extension's worker did not answer." };
  state.dataset.state = gateway.state;
  switch (gateway.state) {
    case "ok":
      text.textContent = "Connected";
      detail.textContent = `Version ${gateway.version} · ${gateway.provider} · ${gateway.local ? "model on this computer" : "cloud model"}${gateway.audit === false ? " · audit log off" : ""}`;
      break;
    case "down":
      text.textContent = "Not running";
      setRich(detail, "Sends from this browser are blocked until it runs. Start it with `uv run safeguard serve`.");
      break;
    case "refused":
      text.textContent = "Refuses this extension";
      setRich(detail, gateway.message);
      break;
    case "error":
      text.textContent = "Not answering as expected";
      detail.textContent = gateway.message;
      break;
  }
}

/** The page's own report: whether SafeGuard is protecting it (ADR 0026 amendment). */
function showProtection(status: StatusReply | undefined): void {
  const card = byId("protection");
  const text = byId("protection-text");
  const detail = byId("protection-detail");
  const report = status?.protection ?? null;
  card.dataset.state = report?.state ?? "unknown";
  detail.textContent = report?.reason ?? "";
  switch (report?.state) {
    case "ok":
      text.textContent = "Protecting this page";
      break;
    case "not_ready":
    case "unmasked":
      text.textContent = NOT_PROTECTING;
      break;
    case "checking":
      text.textContent = "Looking for the message box…";
      break;
    case undefined:
      text.textContent = "No chat page reported for this tab.";
      break;
  }
}

function showTab(status: StatusReply | undefined): void {
  const session = status?.session ?? null;
  const count = session?.count ?? 0;
  byId("count").textContent = String(count);
  byId("count-label").textContent = count === 1 ? "value pseudonymised" : "values pseudonymised";
  byId("empty").hidden = session !== null;
  const types = byId("types");
  types.replaceChildren(
    ...Object.entries(session?.entities ?? {})
      .filter(([, n]) => n > 0)
      .sort(([, a], [, b]) => b - a)
      .map(([type, n]) => {
        const item = document.createElement("li");
        item.className = `type fam-${familyOf(type)}`;
        const badge = document.createElement("b");
        badge.textContent = String(n);
        item.append(badge, document.createTextNode(labelOf(type)));
        return item;
      }),
  );
  buttonById("clear").disabled = session === null;
}

async function refresh(tabId: number | null): Promise<void> {
  const status = tabId === null ? undefined : await ask<StatusReply>({ type: "status", tabId });
  showProtection(status);
  showGateway(status);
  showTab(status);
}

async function main(): Promise<void> {
  const tabId = await targetTab();
  const clear = buttonById("clear");
  const said = byId("said");
  clear.addEventListener("click", () => {
    if (tabId === null) return;
    clear.disabled = true;
    void ask<ClearReply>({ type: "clear", tabId }).then(async (reply) => {
      said.textContent =
        reply === undefined
          ? "Could not clear: the extension's worker did not answer."
          : reply.gateway
            ? "Cleared here and in the gateway. Placeholders on the page show as sent."
            : "Cleared here. The gateway could not be told; its copy expires after 30 idle minutes.";
      await refresh(tabId);
    });
  });
  await refresh(tabId);
}

void main();
