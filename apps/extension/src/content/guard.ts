import { TOKEN } from "../lib/placeholders";

// The fail-safe (ADR 0026 amendment 2026-11-10): on a supported site the extension must
// never stay silent while it is not protecting. Two ways to notice:
//   not ready - no textbox matched the site's selectors within a few seconds;
//   unmasked  - a new message of the user's appeared that SafeGuard did not produce.

export type GuardState = "checking" | "ok" | "not_ready" | "unmasked";

/** How long after a page load or navigation the textbox may take to appear. */
export const READY_MS = 4000;
/** How long after a page load or navigation messages that appear are history, not sends. */
export const SETTLE_MS = 3000;

export const REASON_NO_EDITOR =
  "The message box was not found with the selectors this extension knows, so messages sent here are not masked. The site probably changed.";
export const REASON_RACE =
  "The site sent a message before SafeGuard could mask it. Assume it was sent as written.";
export const REASON_UNMASKED =
  "A message you sent appeared in this conversation without going through SafeGuard's masking. Assume it was sent as written.";

// ------------------------------------------------------------ what counts as unmasked

function luhn(digits: string): boolean {
  let sum = 0;
  let double = false;
  for (let i = digits.length - 1; i >= 0; i -= 1) {
    let n = Number(digits[i]);
    if (double) {
      n *= 2;
      if (n > 9) n -= 9;
    }
    sum += n;
    double = !double;
  }
  return sum % 10 === 0;
}

function ibanValid(raw: string): boolean {
  const moved = raw.replace(/\s/gu, "").toUpperCase();
  const rotated = moved.slice(4) + moved.slice(0, 4);
  const numeric = Array.from(rotated).map((c) => (/[A-Z]/u.test(c) ? String(c.charCodeAt(0) - 55) : c)).join("");
  let remainder = 0;
  for (const digit of numeric) remainder = (remainder * 10 + Number(digit)) % 97;
  return remainder === 1;
}

/**
 * Values of a few structured kinds that are cheap to spot: e-mail addresses, card
 * numbers (Luhn), US SSN-shaped numbers and IBANs (mod 97). It is a tripwire for the
 * fail-safe, not the gateway's detector: names and addresses are not found here.
 */
export function sensitiveIn(text: string): string[] {
  const found: string[] = [];
  found.push(...(text.match(/[\w.%+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}/gu) ?? []));
  for (const candidate of text.match(/\b\d(?:[ -]?\d){12,18}\b/gu) ?? []) {
    if (luhn(candidate.replace(/\D/gu, ""))) found.push(candidate);
  }
  found.push(...(text.match(/\b\d{3}-\d{2}-\d{4}\b/gu) ?? []));
  for (const candidate of text.match(/\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b/gu) ?? []) {
    if (ibanValid(candidate)) found.push(candidate);
  }
  return found;
}

function normal(text: string): string {
  return text.replace(/\s+/gu, " ").trim();
}

/**
 * Whether a message in the thread came out of our masking: it is one of the texts we
 * wrote into the box (or that text with its values restored on the page), allowing for
 * a label the site puts around it. Anything else is "unmasked" if it holds values the
 * tripwire flags, or if it carries none of our placeholders.
 */
export function judgeSent(text: string, produced: readonly string[]): "ok" | "unmasked" {
  const t = normal(text);
  if (t === "") return "ok";
  for (const candidate of produced) {
    const p = normal(candidate);
    if (p !== "" && (t === p || t.startsWith(p) || t.endsWith(p))) return "ok";
  }
  if (sensitiveIn(text).length > 0) return "unmasked";
  return [...text.matchAll(TOKEN)].length > 0 ? "ok" : "unmasked";
}

// --------------------------------------------------------------------- the watchers

/**
 * Finds the user message that has just been added to a thread. Messages that were there
 * already, that arrive in a batch (a conversation loading), that are not the last in the
 * thread (older history scrolled in) or that arrive while the page is settling are
 * history and never raise the alarm.
 */
export class SentWatcher {
  private readonly seen = new WeakSet<Element>();

  /** The reason to raise the alarm, or null. */
  scan(root: ParentNode, selector: string, settling: boolean, produced: readonly string[]): string | null {
    const nodes = [...root.querySelectorAll(selector)];
    // A bubble still empty is not judged yet; it is looked at again when it has text.
    const fresh = nodes.filter((node) => !this.seen.has(node) && node.textContent.trim() !== "");
    for (const node of fresh) this.seen.add(node);
    const only = fresh[0];
    if (settling || fresh.length !== 1 || only === undefined || only !== nodes[nodes.length - 1]) return null;
    return judgeSent(only.textContent, produced) === "ok" ? null : REASON_UNMASKED;
  }
}

export interface GuardHooks {
  /** Whether a usable textbox is on the page right now. */
  hasEditor: () => boolean;
  /** Told whenever the state changes. */
  changed: (state: GuardState, reason: string) => void;
  now?: () => number;
}

/** The state machine behind the banner, the badge and the popup. */
export class Guard {
  state: GuardState = "checking";
  private timer: ReturnType<typeof setTimeout> | undefined;
  private settleUntil: number;
  private readonly hooks: GuardHooks;

  constructor(hooks: GuardHooks) {
    this.hooks = hooks;
    this.settleUntil = this.now() + SETTLE_MS;
  }

  private now(): number {
    return (this.hooks.now ?? Date.now)();
  }

  get settling(): boolean {
    return this.now() < this.settleUntil;
  }

  private set(state: GuardState, reason: string): void {
    if (this.state === state) return;
    this.state = state;
    this.hooks.changed(state, reason);
  }

  /** Look for the textbox; give up with "not ready" if it does not show up in time. */
  check(): void {
    if (this.state === "unmasked") return; // sticky until the page is reloaded
    if (this.hooks.hasEditor()) {
      clearTimeout(this.timer);
      this.timer = undefined;
      this.set("ok", "");
    } else if (this.timer === undefined && this.state !== "not_ready") {
      this.timer = setTimeout(() => {
        this.timer = undefined;
        if (this.state === "unmasked") return;
        if (this.hooks.hasEditor()) this.set("ok", "");
        else this.set("not_ready", REASON_NO_EDITOR);
      }, READY_MS);
    }
  }

  /** The page went to another route (a single-page app): start the clock again. */
  navigated(): void {
    this.settleUntil = this.now() + SETTLE_MS;
    clearTimeout(this.timer);
    this.timer = undefined;
    if (this.state === "ok" || this.state === "not_ready") this.set("checking", "");
    this.check();
  }

  alarm(reason: string): void {
    clearTimeout(this.timer);
    this.timer = undefined;
    this.set("unmasked", reason);
  }
}
