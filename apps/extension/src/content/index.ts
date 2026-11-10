import { ext } from "../lib/browser";
import { DomRehydrator, isEditable, type Mark } from "../lib/dom";
import type { MaskReply, Request, SessionReply } from "../lib/messages";
import { rehydrateExplained, type SessionMap } from "../lib/rehydrate";
import { type Editor, isVisible, readDraft, replaceDraft, resolveEditor, sameDraft } from "./editor";
import { Banner } from "./banner";
import { Guard, REASON_RACE, SentWatcher } from "./guard";
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

// What a send looks like to the page is not one event but a gesture: a key goes down,
// is typed, comes up; a button gets a pointer press, a release and a click. A site may
// send on any of them, and clear its box a moment later, so a script that waits for
// "click" or "keydown" and then awaits the gateway can find the message already gone.
// So every event of the gesture is stopped, synchronously, in the capture phase on
// window, before any await; the masking runs afterwards (ADR 0026 amendment).

const SETTLE_AFTER_SEND_MS = 300;
const produced: string[] = []; // the masked texts this script wrote into the box
let guard: Guard | undefined;
let shiftDown = false;
let swallowUntil = 0; // the rest of a gesture we stopped, until then

function remember(masked: string): void {
  produced.push(masked);
  if (produced.length > 20) produced.shift();
}

/** What a user message of ours may read as on the page: as written, or restored. */
function producedTexts(): string[] {
  return produced.flatMap((text) => [text, rehydrateExplained(text, map).text]);
}

function isField(editor: Editor): boolean {
  return editor instanceof HTMLTextAreaElement || editor instanceof HTMLInputElement;
}

function stop(event: Event): void {
  event.preventDefault();
  event.stopImmediatePropagation();
}

/** The textbox an event came from, found through the event's own path. */
function editorFromEvent(event: Event, s: Site): Editor | null {
  for (const node of event.composedPath()) {
    if (node instanceof HTMLElement && node.matches(s.editor) && isVisible(node)) return node;
  }
  return null;
}

/** The send button an event is about: the site's, or any submit button of the box's form. */
function sendTarget(event: Event, s: Site): HTMLElement | null {
  for (const node of event.composedPath()) {
    if (!(node instanceof HTMLElement)) continue;
    if (node.matches(s.send)) return node;
    if (node.matches('button[type="submit"], input[type="submit"]')) {
      const form = node.closest("form");
      if (form !== null && resolveEditor(s.editor, form) !== null) return node;
    }
  }
  return null;
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
      if (readDraft(editor).trim() === "") {
        // The box was emptied while the gateway worked: the site sent the real text
        // anyway. Fail-closed cannot undo that; say so, loudly.
        guard?.alarm(REASON_RACE);
        blocked("The site sent this message before it could be masked. Assume it went out as written.", editor);
      } else {
        blocked("The message changed while it was being masked. Press send again.", editor);
      }
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
    remember(reply.masked);
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
    swallowUntil = Date.now() + SETTLE_AFTER_SEND_MS;
  }
}

/**
 * Stop a send event, synchronously, and start masking its draft. An event of a gesture
 * we are already handling is only stopped. An empty box is left alone: nothing to mask.
 */
function intercept(event: Event, s: Site, editor: Editor): void {
  if (busy || Date.now() < swallowUntil) {
    stop(event);
    return;
  }
  const draft = readDraft(editor);
  if (draft.trim() === "") return;
  stop(event);
  void maskAndSend(s, editor, draft);
}

function listen(s: Site): void {
  const onKey = (event: Event): void => {
    if (!(event instanceof KeyboardEvent)) return;
    shiftDown = event.shiftKey;
    // An Enter that confirms an IME composition is not a send; some browsers flag it
    // only with the legacy keyCode 229.
    // eslint-disable-next-line @typescript-eslint/no-deprecated
    const composing = event.isComposing || event.keyCode === 229;
    if (bypass || event.key !== "Enter" || event.shiftKey || composing) return;
    const editor = editorFromEvent(event, s);
    if (editor !== null) intercept(event, s, editor);
  };
  // Enter is a send on keydown, keypress or keyup, depending on the site.
  for (const type of ["keydown", "keypress", "keyup"]) window.addEventListener(type, onKey, true);
  window.addEventListener(
    "beforeinput",
    (event) => {
      if (bypass || !(event instanceof InputEvent) || event.isComposing || shiftDown) return;
      const editor = editorFromEvent(event, s);
      if (editor === null) return;
      // Enter in a contenteditable is insertParagraph; in a textarea, insertLineBreak.
      const enter = event.inputType === "insertParagraph" || (event.inputType === "insertLineBreak" && isField(editor));
      if (enter) intercept(event, s, editor);
    },
    true,
  );
  const onPress = (event: Event): void => {
    if (bypass) return;
    const button = sendTarget(event, s);
    if (button === null) return;
    const editor = resolveEditor(s.editor, button.closest("form") ?? document) ?? resolveEditor(s.editor);
    if (editor !== null) intercept(event, s, editor);
  };
  // A press, a release and a click: a site may send on any of them.
  for (const type of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) {
    window.addEventListener(type, onPress, true);
  }
  window.addEventListener(
    "submit",
    (event) => {
      if (bypass || !(event.target instanceof HTMLFormElement)) return;
      const editor = resolveEditor(s.editor, event.target);
      if (editor !== null) intercept(event, s, editor);
    },
    true,
  );
}

// -------------------------------------------------------------- the fail-safe

const CHECK_DEBOUNCE_MS = 250;

function startGuard(s: Site): void {
  const banner = new Banner();
  const g = new Guard({
    hasEditor: () => resolveEditor(s.editor) !== null,
    changed: (state, reason) => {
      if (state === "not_ready" || state === "unmasked") banner.show(reason);
      else banner.hide();
      void send({ type: "protection", state, reason });
    },
  });
  guard = g;
  void send({ type: "protection", state: "checking", reason: "" });
  g.check();
  const sent = new SentWatcher();
  let lastUrl = location.href;
  let checking: ReturnType<typeof setTimeout> | undefined;
  const look = (): void => {
    if (location.href !== lastUrl) {
      lastUrl = location.href;
      g.navigated();
    }
    // Before the rehydrator (40 ms) can change the bubble's text.
    const reason = sent.scan(document, s.user, g.settling, producedTexts());
    if (reason !== null) g.alarm(reason);
    clearTimeout(checking);
    checking = setTimeout(() => {
      g.check();
    }, CHECK_DEBOUNCE_MS);
  };
  new MutationObserver(look).observe(document.documentElement, { childList: true, characterData: true, subtree: true });
  window.addEventListener("popstate", look);
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
  startGuard(s);
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
