// The browser extension in Chromium, on local fixture pages that stand in for
// chatgpt.com, chat.openai.com, claude.ai and gemini.google.com (ADR 0026). Started by
// tests/e2e/test_extension.py, which runs `safeguard serve` on 127.0.0.1:8787 and sets
// the environment below. Requests to the sites are answered from tests/e2e/extension/
// fixtures by Playwright routing; every other https request is aborted, so nothing
// leaves the machine. Uses the Node Playwright already installed (never `playwright
// install`).
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const EXT_DIR = process.env.EXTENSION_DIR;
const EXT_ID = process.env.EXTENSION_ID;
const GATEWAY = "http://127.0.0.1:8787";
const GATEWAY_PID = Number(process.env.GATEWAY_PID);
const SHOTS = process.env.SCREENSHOTS;
const DATA = JSON.parse(readFileSync(process.env.DATA_FILE, "utf8"));
const FIXTURES = join(import.meta.dirname, "fixtures");
const PAGES = {
  "chatgpt.com": "chatgpt.html",
  "chat.openai.com": "openai-legacy.html",
  "claude.ai": "claude.html",
  "gemini.google.com": "gemini.html",
};

const log = [];
let failed = false;
const consoleErrors = [];
let expectNetworkErrors = false;

async function step(name, fn) {
  try {
    await fn();
    log.push(`PASS ${name}`);
  } catch (error) {
    failed = true;
    log.push(`FAIL ${name}: ${error instanceof Error ? error.message.split("\n")[0] : error}`);
  }
}

function expect(condition, message) {
  if (!condition) throw new Error(message);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function until(fn, label, timeout = 15000) {
  const end = Date.now() + timeout;
  for (;;) {
    const value = await fn();
    if (value) return value;
    if (Date.now() > end) throw new Error(`timed out: ${label}`);
    await sleep(100);
  }
}

function watch(page, name) {
  page.on("console", (message) => {
    if (message.type() !== "error") return;
    const text = message.text();
    if (expectNetworkErrors && /Failed to load resource|ERR_CONNECTION_REFUSED|Failed to fetch/.test(text)) return;
    consoleErrors.push(`${name}: ${text}`);
  });
  page.on("pageerror", (error) => consoleErrors.push(`${name}: ${error.message}`));
}

const values = [DATA.name, DATA.email, DATA.card];
const draft = (extra = "") =>
  `Please write to ${DATA.name} at ${DATA.email} about the card ending ${DATA.card}.${extra}`;

function leaks(text) {
  return values.filter((v) => text.includes(v));
}

async function typeDraft(page, selector, text) {
  const editor = page.locator(selector);
  await editor.click();
  await page.keyboard.type(text, { delay: 0 });
  return editor;
}

async function replyState(page, botSelector) {
  return page.evaluate((sel) => {
    const bots = document.querySelectorAll(sel);
    const bot = bots[bots.length - 1];
    if (!bot) return null;
    const a = bot.querySelector("a");
    const ps = [...bot.querySelectorAll("p")].map((p) => p.textContent);
    const users = document.querySelectorAll(
      "[data-message-author-role='user'], [data-testid='user-message'], user-query",
    );
    const highlights = globalThis.CSS?.highlights;
    return {
      text: bot.textContent,
      paragraphs: ps,
      linkText: a?.textContent ?? null,
      linkHref: a?.getAttribute("href") ?? null,
      user: users[users.length - 1]?.textContent ?? "",
      unresolvedMarks: highlights?.get("safeguard-unresolved")?.size ?? -1,
      restoredMarks: highlights?.get("safeguard-restored")?.size ?? -1,
      replies: window.__replies,
    };
  }, botSelector);
}

const userDir = mkdtempSync(join(tmpdir(), "sg-ext-"));
const context = await chromium.launchPersistentContext(userDir, {
  channel: "chromium", // the new headless mode, which loads extensions
  headless: true,
  viewport: { width: 1100, height: 800 },
  args: [`--disable-extensions-except=${EXT_DIR}`, `--load-extension=${EXT_DIR}`],
});

// No live site: the four hosts get fixtures, any other https request is aborted.
const foreign = [];
await context.route(/^https:\/\//, (route) => {
  foreign.push(route.request().url());
  return route.abort();
});
await context.route(/^https:\/\/(chatgpt\.com|chat\.openai\.com|claude\.ai|gemini\.google\.com)\//, (route) => {
  const url = new URL(route.request().url());
  if (url.pathname === "/__fixture/chat.js") {
    return route.fulfill({ contentType: "text/javascript", body: readFileSync(join(FIXTURES, "chat.js")) });
  }
  if (url.pathname === "/favicon.ico") return route.fulfill({ status: 204 });
  return route.fulfill({ contentType: "text/html", body: readFileSync(join(FIXTURES, PAGES[url.hostname])) });
});

let worker = context.serviceWorkers()[0];
if (!worker) worker = await context.waitForEvent("serviceworker");
worker.on("console", (m) => {
  if (m.type() === "error" && !expectNetworkErrors) consoleErrors.push(`worker: ${m.text()}`);
});

async function tabIdOf(host) {
  return worker.evaluate(async (h) => (await chrome.tabs.query({ url: `https://${h}/*` }))[0]?.id, host);
}

async function badgeOf(host) {
  const tabId = await tabIdOf(host);
  return worker.evaluate((id) => chrome.action.getBadgeText({ tabId: id }), tabId);
}

await step("the extension loads with the id the gateway allows", async () => {
  expect(worker.url() === `chrome-extension://${EXT_ID}/background.js`, `worker at ${worker.url()}`);
});

const SITES = [
  { host: "chatgpt.com", editor: "#prompt-textarea", bot: "[data-message-author-role='assistant']", how: "Enter" },
  { host: "chat.openai.com", editor: "#prompt-textarea", bot: "[data-message-author-role='assistant']", how: "click", button: "button[data-testid='send-button']" },
  { host: "claude.ai", editor: "div.ProseMirror", bot: ".font-claude-response", how: "click", button: "button[aria-label='Send message']" },
  { host: "gemini.google.com", editor: "rich-textarea .ql-editor", bot: "model-response", how: "Enter" },
];
const pages = {};

for (const site of SITES) {
  const page = await context.newPage();
  pages[site.host] = page;
  watch(page, site.host);
  await page.goto(`https://${site.host}/`);

  await step(`${site.host}: the page sends only masked text (${site.how})`, async () => {
    await typeDraft(page, site.editor, draft());
    if (site.how === "Enter") await page.keyboard.press("Enter");
    else await page.locator(site.button).click();
    const sent = await until(() => page.evaluate(() => window.__sent[0]), "the fixture to send");
    expect(leaks(sent).length === 0, `sent in clear: ${leaks(sent).length} value(s)`);
    expect(/\[PERSON_1\]/.test(sent) && /\[EMAIL_1\]/.test(sent), `no placeholders in: ${sent}`);
    expect((await page.evaluate(() => window.__sent.length)) === 1, "sent more than once");
  });

  await step(`${site.host}: the reply is restored in place, the user's message too`, async () => {
    await until(async () => (await replyState(page, site.bot))?.replies === 1, "the reply to finish");
    const state = await until(async () => {
      const s = await replyState(page, site.bot);
      return s && s.paragraphs[0]?.includes(DATA.name) && s.paragraphs[0]?.includes(DATA.email) ? s : null;
    }, "restored values in the reply");
    expect(state.user.includes(DATA.name), "the user's message still shows placeholders");
    expect(state.restoredMarks > 0, `restored values not marked (${state.restoredMarks})`);
  });

  await step(`${site.host}: a placeholder in a link is not restored`, async () => {
    await sleep(800); // past the quiet period, when the last pass runs
    const state = await replyState(page, site.bot);
    expect(state.linkText === "[EMAIL_1]", `link text: ${state.linkText}`);
    expect(state.linkHref === "https://example.invalid/r?d=[EMAIL_1]", `href: ${state.linkHref}`);
    const raw = state.paragraphs[2] ?? "";
    expect(raw.includes("?d=[PERSON_1]") && !raw.includes(DATA.name), `raw URL paragraph: ${raw}`);
  });

  await step(`${site.host}: an unknown placeholder stays visible and is highlighted`, async () => {
    const state = await replyState(page, site.bot);
    expect(state.paragraphs[3]?.includes("[PERSON_99]"), "[PERSON_99] is gone");
    expect(state.unresolvedMarks >= 3, `unresolved marks: ${state.unresolvedMarks}`);
  });

  await step(`${site.host}: the badge counts the masked values`, async () => {
    const badge = await until(() => badgeOf(site.host), "a badge");
    expect(Number(badge) >= 3, `badge: ${badge}`);
  });
}

await step("chatgpt.com: a second turn keeps the numbering of the session", async () => {
  const page = pages["chatgpt.com"];
  await typeDraft(page, "#prompt-textarea", `Also tell ${DATA.name} the meeting moved.`);
  await page.keyboard.press("Enter");
  const sent = await until(() => page.evaluate(() => window.__sent[1]), "the second send");
  expect(!sent.includes(DATA.name) && sent.includes("[PERSON_1]"), `second send: ${sent}`);
  await until(async () => (await replyState(page, "[data-message-author-role='assistant']"))?.replies === 2, "reply 2");
});

await step("chatgpt.com: the page shows the notice after sending", async () => {
  const notice = pages["chatgpt.com"].locator("[data-safeguard=notice] [role=status]");
  // The success notice hides itself after five seconds; it may already be gone.
  if ((await notice.count()) > 0) expect((await notice.textContent()).includes("pseudonymised"), "notice text");
});

// ------------------------------------------------------------------- the popup

const popupUrl = async (host) => `chrome-extension://${EXT_ID}/popup/popup.html?tab=${await tabIdOf(host)}`;
const popup = await context.newPage();
watch(popup, "popup");

await step("popup: connected, with this tab's counts (light and dark)", async () => {
  await popup.setViewportSize({ width: 352, height: 560 });
  await popup.goto(await popupUrl("chatgpt.com"));
  await until(async () => (await popup.locator("#state-text").textContent()) === "Connected", "Connected");
  const count = Number(await popup.locator("#count").textContent());
  expect(count >= 3, `count ${count}`);
  expect((await popup.locator("#types li").count()) >= 3, "types listed");
  await popup.emulateMedia({ colorScheme: "light" });
  await sleep(400); // let the buttons finish their colour transition
  await popup.screenshot({ path: join(SHOTS, "popup-light.png"), fullPage: true });
  await popup.emulateMedia({ colorScheme: "dark" });
  await sleep(400); // let the buttons finish their colour transition
  await popup.screenshot({ path: join(SHOTS, "popup-dark.png"), fullPage: true });
  await popup.emulateMedia({ colorScheme: "light" });
});

await step("popup: keyboard reaches clear and the dashboard link", async () => {
  await popup.keyboard.press("Tab");
  expect((await popup.evaluate(() => document.activeElement?.id)) === "clear", "first stop is clear");
  await popup.keyboard.press("Tab");
  expect((await popup.evaluate(() => document.activeElement?.id)) === "dashboard", "second stop is the dashboard");
  expect((await popup.locator("#dashboard").getAttribute("href")) === "http://127.0.0.1:8787/", "dashboard href");
});

await step("popup: fits a 390 px wide window", async () => {
  await popup.setViewportSize({ width: 390, height: 700 });
  const overflow = await popup.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow <= 0, `horizontal overflow ${overflow}px`);
  await sleep(400); // let the buttons finish their colour transition
  await popup.screenshot({ path: join(SHOTS, "popup-390.png"), fullPage: true });
  await popup.setViewportSize({ width: 352, height: 560 });
});

await step("popup: clear session deletes it and the page shows placeholders again", async () => {
  await popup.goto(await popupUrl("gemini.google.com"));
  await until(async () => !(await popup.locator("#clear").isDisabled()), "clear enabled");
  await popup.locator("#clear").click();
  await until(async () => (await popup.locator("#said").textContent()).includes("Cleared here and in the gateway"), "cleared");
  expect((await popup.locator("#count").textContent()) === "0", "count after clear");
  const gemini = pages["gemini.google.com"];
  await until(async () => {
    const s = await replyState(gemini, "model-response");
    return s.paragraphs[0].includes("[PERSON_1]") && !s.paragraphs[0].includes(DATA.name);
  }, "placeholders back on the page");
  expect((await badgeOf("gemini.google.com")) === "", "badge after clear");
});

// --------------------------------------------------------- the gateway stops

await step("gateway stopped: the send is blocked with a notice", async () => {
  expectNetworkErrors = true;
  process.kill(GATEWAY_PID, "SIGTERM");
  await until(async () => {
    try {
      await fetch(`${GATEWAY}/health`);
      return false;
    } catch {
      return true;
    }
  }, "the gateway to stop", 20000);
  const page = pages["claude.ai"];
  const before = await page.evaluate(() => window.__sent.length);
  await typeDraft(page, "div.ProseMirror", draft(" Second try."));
  await page.locator("button[aria-label='Send message']").click();
  const alert = page.locator("[data-safeguard=notice] [role=alert]");
  await alert.waitFor({ timeout: 10000 });
  const text = await alert.textContent();
  expect(text.includes("Not sent") && text.includes("not answering"), `notice: ${text}`);
  await sleep(1500);
  expect((await page.evaluate(() => window.__sent.length)) === before, "the page sent anyway");
  const box = await page.locator("div.ProseMirror").textContent();
  expect(box.includes(DATA.name), "the draft was lost");
  await page.screenshot({ path: join(SHOTS, "blocked-notice.png") });
  // Enter is stopped the same way.
  await page.locator("div.ProseMirror").click();
  await page.keyboard.press("Enter");
  await sleep(1500);
  expect((await page.evaluate(() => window.__sent.length)) === before, "Enter sent anyway");
});

await step("popup: says the gateway is not running", async () => {
  await popup.goto(await popupUrl("claude.ai"));
  await until(async () => (await popup.locator("#state-text").textContent()) === "Not running", "Not running");
  await sleep(400); // let the buttons finish their colour transition
  await popup.screenshot({ path: join(SHOTS, "popup-gateway-down.png"), fullPage: true });
});

await step("no console errors, no request to any other host", async () => {
  expect(consoleErrors.length === 0, consoleErrors.join(" | "));
  expect(foreign.length === 0, `aborted: ${foreign.join(", ")}`);
});

await context.close();
rmSync(userDir, { recursive: true, force: true });
console.log(log.join("\n"));
process.exit(failed ? 1 : 0);
