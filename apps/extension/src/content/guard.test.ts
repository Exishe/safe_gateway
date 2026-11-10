// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Guard, type GuardState, judgeSent, READY_MS, REASON_UNMASKED, SentWatcher, SETTLE_MS, sensitiveIn } from "./guard";

// Fake values only (invalid by construction or the public test numbers).
const EMAIL = "anna.petrova@example.org";
const CARD = "4111 1111 1111 1111"; // pragma: allowlist secret
const NOT_A_CARD = "4111 1111 1111 1112"; // fails the Luhn check
const MASKED = "Write to [PERSON_1] at [EMAIL_1], card [CREDIT_CARD_1].";
const RESTORED = `Write to Anna Petrova at ${EMAIL}, card ${CARD}.`;

describe("sensitiveIn", () => {
  it("finds an e-mail address, a card number that passes Luhn, an SSN-shaped number", () => {
    expect(sensitiveIn(`mail ${EMAIL}`)).toEqual([EMAIL]);
    expect(sensitiveIn(`card ${CARD}`)).toEqual([CARD]);
    expect(sensitiveIn("ssn 078-05-1120")).toEqual(["078-05-1120"]); // pragma: allowlist secret
  });

  it("finds an IBAN that passes mod 97 and not one that fails", () => {
    expect(sensitiveIn("pay GB82 WEST 1234 5698 7654 32")).toHaveLength(1); // pragma: allowlist secret
    expect(sensitiveIn("pay GB83 WEST 1234 5698 7654 32")).toHaveLength(0);
  });

  it("ignores placeholders, plain numbers and a number that fails Luhn", () => {
    expect(sensitiveIn(MASKED)).toEqual([]);
    expect(sensitiveIn(`order ${NOT_A_CARD}`)).toEqual([]);
    expect(sensitiveIn("call me tomorrow at 10")).toEqual([]);
  });
});

describe("judgeSent", () => {
  it("accepts the text we wrote into the box, and that text restored on the page", () => {
    expect(judgeSent(MASKED, [MASKED, RESTORED])).toBe("ok");
    expect(judgeSent(RESTORED, [MASKED, RESTORED])).toBe("ok");
  });

  it("accepts a site's label around our text", () => {
    expect(judgeSent(`You said: ${MASKED}`, [MASKED])).toBe("ok");
  });

  it("accepts text that carries our placeholders", () => {
    expect(judgeSent("Also [PERSON_1] said yes", [])).toBe("ok");
  });

  it("flags a message with values the tripwire finds, even next to a placeholder", () => {
    expect(judgeSent(`Card ${CARD} for [PERSON_1]`, [MASKED])).toBe("unmasked");
    expect(judgeSent(`Mail ${EMAIL}`, [])).toBe("unmasked");
  });

  it("flags a message that is not ours and carries no placeholder", () => {
    expect(judgeSent("hello there", [MASKED])).toBe("unmasked");
  });
});

describe("SentWatcher", () => {
  const SELECTOR = '[data-role="user"]';
  const thread = (): HTMLElement => document.body;
  const add = (text: string): HTMLElement => {
    const node = document.createElement("div");
    node.dataset.role = "user";
    node.textContent = text;
    thread().append(node);
    return node;
  };
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("raises the alarm for a new message of ours that is not masked", () => {
    const watcher = new SentWatcher();
    expect(watcher.scan(document, SELECTOR, false, [])).toBeNull();
    add(`Card ${CARD}`);
    expect(watcher.scan(document, SELECTOR, false, [MASKED])).toBe(REASON_UNMASKED);
  });

  it("is quiet for a message we produced", () => {
    const watcher = new SentWatcher();
    add(MASKED);
    expect(watcher.scan(document, SELECTOR, false, [MASKED])).toBeNull();
  });

  it("judges a message once only", () => {
    const watcher = new SentWatcher();
    add(`Card ${CARD}`);
    expect(watcher.scan(document, SELECTOR, false, [])).toBe(REASON_UNMASKED);
    expect(watcher.scan(document, SELECTOR, false, [])).toBeNull();
  });

  it("treats messages that arrive while settling as history", () => {
    const watcher = new SentWatcher();
    add(`Card ${CARD}`);
    expect(watcher.scan(document, SELECTOR, true, [])).toBeNull();
    expect(watcher.scan(document, SELECTOR, false, [])).toBeNull();
  });

  it("treats a batch of messages as a conversation loading", () => {
    const watcher = new SentWatcher();
    add(`Mail ${EMAIL}`);
    add(`Card ${CARD}`);
    expect(watcher.scan(document, SELECTOR, false, [])).toBeNull();
  });

  it("does not judge an empty bubble until it has text", () => {
    const watcher = new SentWatcher();
    const node = add("");
    expect(watcher.scan(document, SELECTOR, false, [])).toBeNull();
    node.textContent = `Card ${CARD}`;
    expect(watcher.scan(document, SELECTOR, false, [])).toBe(REASON_UNMASKED);
  });

  it("ignores a message added above the last one (older history)", () => {
    const watcher = new SentWatcher();
    add("[PERSON_1] first");
    watcher.scan(document, SELECTOR, false, []);
    const old = add(`Card ${CARD}`);
    thread().prepend(old);
    expect(watcher.scan(document, SELECTOR, false, [])).toBeNull();
  });
});

describe("Guard", () => {
  let found: boolean;
  let states: GuardState[];
  let clock: number;
  const make = (): Guard =>
    new Guard({
      hasEditor: () => found,
      changed: (state) => states.push(state),
      now: () => clock,
    });
  beforeEach(() => {
    vi.useFakeTimers();
    found = false;
    states = [];
    clock = 0;
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("is ok as soon as the textbox is there", () => {
    const guard = make();
    found = true;
    guard.check();
    expect(states).toEqual(["ok"]);
  });

  it("says not ready when no textbox shows up within READY_MS, not before", () => {
    const guard = make();
    guard.check();
    vi.advanceTimersByTime(READY_MS - 1);
    expect(states).toEqual([]);
    vi.advanceTimersByTime(1);
    expect(states).toEqual(["not_ready"]);
    expect(guard.state).toBe("not_ready");
  });

  it("does not say not ready when the box appears in time", () => {
    const guard = make();
    guard.check();
    vi.advanceTimersByTime(READY_MS - 100);
    found = true;
    guard.check();
    vi.advanceTimersByTime(READY_MS);
    expect(states).toEqual(["ok"]);
  });

  it("recovers when the box appears after the banner", () => {
    const guard = make();
    guard.check();
    vi.advanceTimersByTime(READY_MS);
    found = true;
    guard.check();
    expect(states).toEqual(["not_ready", "ok"]);
  });

  it("starts the clock again after a navigation and settles", () => {
    const guard = make();
    found = true;
    guard.check();
    clock = 10_000;
    expect(guard.settling).toBe(false);
    found = false; // the single-page app replaced the page
    guard.navigated();
    expect(guard.settling).toBe(true);
    clock += SETTLE_MS;
    expect(guard.settling).toBe(false);
    vi.advanceTimersByTime(READY_MS);
    expect(states).toEqual(["ok", "checking", "not_ready"]);
  });

  it("keeps the alarm for an unmasked send until the page is reloaded", () => {
    const guard = make();
    found = true;
    guard.check();
    guard.alarm("x");
    guard.check();
    guard.navigated();
    expect(guard.state).toBe("unmasked");
    expect(states).toEqual(["ok", "unmasked"]);
  });
});
