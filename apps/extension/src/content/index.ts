import { ext } from "../lib/browser";
import { DomRehydrator, isEditable, type Mark } from "../lib/dom";
import type { MaskReply, Request, SessionReply } from "../lib/messages";
import type { SessionMap } from "../lib/rehydrate";
import { type Editor, readDraft, replaceDraft, sameDraft } from "./editor";
import { Notice } from "./notice";
import { type Site, siteFor } from "./sites";

// The content script on ChatGPT, Claude and Gemini (ADR 0026). On send: stop the
// send, mask the draft through the background worker, put the masked text in the
// textbox, check it reads back, then let the send go. Fail closed at every step.
// Replies: restore placeholders in text nodes as they arrive.

const site = siteFor(location.hostname);
const notice = new Notice();
let map: SessionMap = {};
let bypass = false; // true while this script re-sends what it masked
let busy = false;

function send<T>(request: Request): Promise<T | undefined> {
  return ext.runtime.sendMessage(request).then(
    (reply: unknown) => reply as T | undefined,
    () => undefined,
  );
}

// ------------------------------------------------------------------ the send path

function editorFrom(target: EventTarget | null, s: Site): Editor | null {
  if (!(target instanceof Element)) return null;
  const editor = target.closest(s.editor);
  return editor instanceof HTMLElement ? editor : null;
}

function findEditor(s: Site): Editor | null {
  const editor = document.querySelector(s.editor);
  return editor instanceof HTMLElement ? editor : null;
}

function sendButton(s: Site, near: Editor | null): HTMLButtonElement | null {
  const scope = near?.closest("form") ?? document;
  const button = scope.querySelector(s.send) ?? document.querySelector(s.send);
  return button instanceof HTMLButtonElement ? button : null;
}

function usable(button: HTMLButtonElement): boolean {
  return !button.disabled && button.getAttribute("aria-disabled") !== "true";
}

async function waitUntilUsable(button: HTMLButtonElement): Promise<boolean> {
  for (let i = 0; i < 50 && !usable(button); i += 1) await new Promise((r) => setTimeout(r, 30));
  return usable(button);
}

function blocked(body: string, editor: Editor | null): void {
  notice.show("blocked", "Not sent", body, editor);
}

async function resend(s: Site, editor: Editor): Promise<void> {
  // The send button is the most reliable way back in, whichever way the user sent;
  // without one, Enter in the textbox.
  const button = sendButton(s, editor);
  const ready = button !== null && (await waitUntilUsable(button));
  bypass = true;
  try {
    if (ready) {
      button.click();
    } else {
      editor.focus();
      const init = { key: "Enter", code: "Enter", bubbles: true, cancelable: true };
      editor.dispatchEvent(new KeyboardEvent("keydown", init));
      editor.dispatchEvent(new KeyboardEvent("keyup", init));
    }
  } finally {
    bypass = false;
  }
}

async function maskAndSend(s: Site, editor: Editor, draft: string): Promise<void> {
  busy = true;
  try {
    notice.show("working", "Masking before sending", "Checking this message on your computer.", editor);
    const reply = await send<MaskReply>({ type: "mask", text: draft });
    if (reply === undefined) {
      blocked("The extension's background worker did not answer. Reload the page, then send again.", editor);
      return;
    }
    if (!reply.ok) {
      blocked(reply.message, editor);
      return;
    }
    if (!sameDraft(readDraft(editor), draft)) {
      blocked("The message changed while it was being masked. Press send again.", editor);
      return;
    }
    map = reply.mapping;
    if (reply.masked !== draft && !(await replaceDraft(editor, reply.masked))) {
      // The box does not hold the masked text: the site would send something else.
      blocked(
        `The masked text could not be put into ${s.name}'s message box. Nothing was sent; your draft may now be partly masked.`,
        editor,
      );
      return;
    }
    await resend(s, editor);
    const what = reply.count === 1 ? "1 value" : `${reply.count} values`;
    const body =
      reply.count === 0
        ? "Nothing was found to mask; the message was sent as written."
        : `${what} pseudonymised before ${s.name} saw the message. Replies are restored on this page only.`;
    notice.show("done", reply.count === 0 ? "Sent" : "Sent masked", body, editor);
    schedule(document.body);
  } finally {
    busy = false;
  }
}

function intercept(event: Event, s: Site, editor: Editor): void {
  const draft = readDraft(editor);
  if (draft.trim() === "") return; // nothing to mask: the site does what it does
  // Stop the send before any of the site's own listeners see it.
  event.preventDefault();
  event.stopImmediatePropagation();
  if (busy) return;
  void maskAndSend(s, editor, draft);
}

function listen(s: Site): void {
  window.addEventListener(
    "keydown",
    (event) => {
      // An Enter that confirms an IME composition is not a send; some browsers flag it
      // only with the legacy keyCode 229.
      // eslint-disable-next-line @typescript-eslint/no-deprecated
      const composing = event.isComposing || event.keyCode === 229;
      if (bypass || event.key !== "Enter" || event.shiftKey || composing) return;
      const editor = editorFrom(event.target, s);
      if (editor !== null) intercept(event, s, editor);
    },
    true,
  );
  const onClick = (event: Event): void => {
    if (bypass || !(event.target instanceof Element)) return;
    const button = event.target.closest(s.send);
    if (button === null) return;
    const editor = findEditor(s);
    if (editor !== null) intercept(event, s, editor);
  };
  window.addEventListener("click", onClick, true);
  window.addEventListener(
    "submit",
    (event) => {
      if (bypass || !(event.target instanceof HTMLFormElement)) return;
      const editor = event.target.querySelector(s.editor);
      if (editor instanceof HTMLElement) intercept(event, s, editor);
    },
    true,
  );
}

// -------------------------------------------------------------- the reply path

const rehydrator = new DomRehydrator(() => map);
const pending = new Set<Element>();
const recent = new Set<Element>();
let soon: ReturnType<typeof setTimeout> | undefined;
let quiet: ReturnType<typeof setTimeout> | undefined;
const marks = new Map<Element, Mark[]>();

function rootOf(node: Node, s: Site): Element | null {
  const element = node instanceof Element ? node : node.parentElement;
  if (element === null || element.closest("[data-safeguard]") !== null) return null;
  if (element.closest(s.editor) !== null || isEditable(element)) return null;
  return element.closest(s.message) ?? element.closest("main") ?? document.body;
}

function highlight(): void {
  const registry = (globalThis.CSS as { highlights?: HighlightRegistry } | undefined)?.highlights;
  if (registry === undefined) return; // no Custom Highlight API: the text itself still shows
  const restored = new Highlight();
  const unresolved = new Highlight();
  for (const list of marks.values()) {
    for (const mark of list) {
      if (!mark.node.isConnected || mark.end > mark.node.length) continue;
      const range = new Range();
      range.setStart(mark.node, mark.start);
      range.setEnd(mark.node, mark.end);
      (mark.kind === "restored" ? restored : unresolved).add(range);
    }
  }
  registry.set("safeguard-restored", restored);
  registry.set("safeguard-unresolved", unresolved);
}

function runOn(roots: Iterable<Element>, final: boolean): void {
  for (const root of roots) {
    if (!root.isConnected) {
      marks.delete(root);
      continue;
    }
    marks.set(root, rehydrator.run(root, final).marks);
  }
  // A root inside another is run twice; the second pass changes nothing (idempotent).
  highlight();
}

function schedule(root: Element): void {
  pending.add(root);
  recent.add(root);
  clearTimeout(soon);
  soon = setTimeout(() => {
    const roots = [...pending];
    pending.clear();
    runOn(roots, false);
  }, 40);
  clearTimeout(quiet);
  quiet = setTimeout(() => {
    // The text stopped changing: a token at the very end is now complete.
    const roots = [...recent];
    recent.clear();
    runOn(roots, true);
  }, 600);
}

function watch(s: Site): void {
  const observer = new MutationObserver((records) => {
    if (Object.keys(map).length === 0 && marks.size === 0) return;
    for (const record of records) {
      const root = rootOf(record.target, s);
      if (root !== null) schedule(root);
    }
  });
  observer.observe(document.documentElement, { childList: true, characterData: true, subtree: true });
}

function addHighlightStyle(): void {
  const style = document.createElement("style");
  style.setAttribute("data-safeguard", "highlight");
  style.textContent =
    "::highlight(safeguard-restored){text-decoration:underline dotted 1px;text-underline-offset:3px}" +
    "::highlight(safeguard-unresolved){background-color:rgba(155,44,59,.16);text-decoration:underline wavy rgba(155,44,59,.85) 1px}";
  document.documentElement.append(style);
}

function start(s: Site): void {
  listen(s);
  ext.runtime.onMessage.addListener((message: unknown) => {
    if (typeof message === "object" && message !== null && (message as { type?: unknown }).type === "cleared") {
      map = {};
      runOn([document.body], true); // every restored value goes back to its placeholder
      marks.clear();
      highlight();
    }
  });
  const ready = (): void => {
    addHighlightStyle();
    watch(s);
    void send<SessionReply>({ type: "session" }).then((reply) => {
      if (reply !== undefined && Object.keys(reply.mapping).length > 0) {
        map = reply.mapping;
        schedule(document.body);
      }
    });
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", ready, { once: true });
  else ready();
}

if (site !== null) start(site);
