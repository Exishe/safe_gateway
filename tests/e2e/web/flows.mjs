// Every flow of the web UI in Chromium, against a running gateway and stub upstream.
// Started by tests/e2e/test_web_ui.py, which sets the environment below. Uses the
// Node Playwright already installed on the machine (never `playwright install`).
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { join } from "node:path";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const GATEWAY = process.env.GATEWAY_URL;
const STUB = process.env.STUB_URL;
const MODEL = process.env.MODEL;
const SHOTS = process.env.SCREENSHOTS;
const DATA = JSON.parse(readFileSync(process.env.DATA_FILE, "utf8"));
const AXE = readFileSync(join(import.meta.dirname, "../../../apps/web/node_modules/axe-core/axe.min.js"), "utf8");

const TEXT =
  `Please draft a short reply to ${DATA.name} (${DATA.email}) about the invoice they disputed. ` +
  `The card on file is ${DATA.card} and refunds go to IBAN ${DATA.iban}. ` +
  `Copy ${DATA.email} on the reply.`;

const log = [];
let failed = false;
let allowNetworkErrors = false;
let expected501 = 0;
const consoleErrors = [];
const foreignRequests = [];

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

async function waitFor(url, label) {
  for (let i = 0; i < 120; i += 1) {
    try {
      await fetch(url);
      return;
    } catch {
      await new Promise((r) => setTimeout(r, 500));
    }
  }
  throw new Error(`${label} did not start`);
}

function watch(page) {
  page.on("console", (message) => {
    if (message.type() !== "error") return;
    const text = message.text();
    if (allowNetworkErrors && /Failed to load resource|ERR_FAILED/.test(text)) return;
    // Chrome logs every HTTP error status. GET /audit answers 501 until the audit route is
    // wired (ADR 0019); the UI expects that and shows it as a state, so only that one passes.
    if (message.location().url.endsWith("/audit") && text.includes("status of 501")) {
      expected501 += 1;
      return;
    }
    consoleErrors.push(text);
  });
  page.on("pageerror", (error) => consoleErrors.push(`pageerror: ${error.message}`));
  page.on("request", (request) => {
    const url = request.url();
    if (!url.startsWith(GATEWAY) && !url.startsWith("data:")) foreignRequests.push(url);
  });
}

async function shot(page, name) {
  await page.screenshot({ path: join(SHOTS, `${name}.png`), fullPage: true });
}

async function setTheme(page, theme) {
  await page.locator(`label[title="${theme === "dark" ? "Dark" : "Light"}"]`).click();
  const dark = await page.evaluate(() => document.documentElement.classList.contains("dark"));
  expect(dark === (theme === "dark"), `theme did not switch to ${theme}`);
  await page.waitForTimeout(400); // colour transitions settle before contrast is measured
}

async function axe(page, label) {
  await page.evaluate(AXE);
  const result = await page.evaluate(async () => {
    // eslint-disable-next-line no-undef
    const run = await axe.run(document, { resultTypes: ["violations"] });
    return run.violations
      .filter((v) => v.impact === "serious" || v.impact === "critical")
      .map((v) => `${v.id} (${v.nodes.length}): ${v.nodes[0]?.target.join(" ")}`);
  });
  expect(result.length === 0, `${label}: ${result.join("; ")}`);
}

async function openTab(page, name) {
  await page.getByRole("tab", { name }).click();
}

const AUDIT_ROWS = {
  rows: [
    {
      id: 1, kind: "request", ts_utc: "2026-11-09T09:14:03.120000Z", session_id: "", upstream_host: "127.0.0.1:11434",
      model: "llama3.2", entity_counts_json: '{"EMAIL":1,"PERSON":2}', masked_payload_sha256: "a".repeat(64),
      bytes_out: 640, status: 200, local: 1,
    },
    {
      id: 2, kind: "request", ts_utc: "2026-11-09T10:02:41.500000Z", session_id: "", upstream_host: "api.openai.com",
      model: "gpt-4o-mini", entity_counts_json: '{"CREDIT_CARD":1,"IBAN":1}', masked_payload_sha256: "b".repeat(64),
      bytes_out: 1210, status: 200, local: 0,
    },
    {
      id: 3, kind: "request", ts_utc: "2026-11-09T10:05:12.000000Z", session_id: "", upstream_host: "api.openai.com",
      model: "gpt-4o-mini", entity_counts_json: "{}", masked_payload_sha256: "c".repeat(64),
      bytes_out: 300, status: 0, local: 0,
    },
  ],
};
const VERIFY_OK = { ok: true, rows_checked: 3, first_bad_id: null, reason: null, detail: "the log is intact", head_id: 3 };
const VERIFY_BROKEN = {
  ok: false, rows_checked: 1, first_bad_id: 2, reason: "edited", detail: "its stored values do not match its row hash", head_id: 3,
};

async function main() {
  await waitFor(`${STUB}/`, "the stub upstream");
  await waitFor(`${GATEWAY}/health`, "the gateway");

  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, colorScheme: "light" });
  const page = await context.newPage();
  watch(page);

  await step("the page loads from the gateway and shows its status", async () => {
    await page.goto(`${GATEWAY}/`);
    await page.getByText("Ollama, on this computer").waitFor();
    expect((await page.title()) === "SafeGuard Gateway", "title");
    await shot(page, "01-compose-1280-light");
  });

  await step("a file that is not .txt is refused with directions", async () => {
    await page.locator('input[type="file"]').setInputFiles({ name: "scan.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4") });
    await page.getByRole("alert").filter({ hasText: "scan.pdf is not a .txt file" }).waitFor();
  });

  await step("a .txt file opens into the text box", async () => {
    await page.locator('input[type="file"]').setInputFiles({ name: "note.txt", mimeType: "text/plain", buffer: Buffer.from("A short note.") });
    await page.getByText("Opened note.txt").waitFor();
    expect((await page.getByLabel("Text to check").inputValue()) === "A short note.", "file text");
  });

  await step("a .txt file dropped on the text box opens", async () => {
    await page.getByLabel("Text to check").evaluate((target, text) => {
      const transfer = new DataTransfer();
      transfer.items.add(new File([text], "letter.txt", { type: "text/plain" }));
      target.dispatchEvent(new DragEvent("dragover", { dataTransfer: transfer, bubbles: true, cancelable: true }));
      target.dispatchEvent(new DragEvent("drop", { dataTransfer: transfer, bubbles: true, cancelable: true }));
    }, TEXT);
    await page.getByText("Opened letter.txt").waitFor();
    expect((await page.getByLabel("Text to check").inputValue()) === TEXT, "dropped text");
  });

  await step("Ctrl+Enter checks the text; detections are shown with type and confidence", async () => {
    await page.getByLabel("Text to check").focus();
    await page.keyboard.press("Control+Enter");
    await page.getByRole("heading", { name: "Review before sending" }).waitFor();
    const exhibits = await page.locator("button.exhibit").allTextContents();
    for (const value of [DATA.name, DATA.email, DATA.card, DATA.iban]) {
      expect(exhibits.some((t) => t.startsWith(value)), `no highlight for a ${value.length}-character value`);
    }
    const ledger = page.getByRole("list", { name: "Detected values" });
    expect((await ledger.getByRole("switch").count()) >= 4, "ledger rows");
    await ledger.getByText(/Confidence \d+%/).first().waitFor();
    await ledger.getByText("Email", { exact: true }).waitFor();
    const request = await page.getByRole("region", { name: "Request to send" }).textContent();
    expect(request.includes("[EMAIL_1]") && !request.includes(DATA.email), "request is not masked");
    await ledger.getByText(`[EMAIL_1], 2 places`).waitFor();
  });

  await step("a detection toggles from the keyboard", async () => {
    const first = page.locator("button.exhibit").first();
    await page.getByRole("heading", { name: "Review before sending" }).focus();
    for (let i = 0; i < 10 && !(await first.evaluate((el) => el === document.activeElement)); i += 1) {
      await page.keyboard.press("Tab");
    }
    expect(await first.evaluate((el) => el === document.activeElement), "Tab never reached the first detection");
    await page.keyboard.press("Space");
    expect((await first.getAttribute("aria-pressed")) === "false", "Space did not switch masking off");
    let request = await page.getByRole("region", { name: "Request to send" }).textContent();
    expect(request.includes(DATA.name), "switched-off value not in the request");
    await page.getByText("Switched-off values go into your request as written").waitFor();
    await page.keyboard.press("Enter");
    expect((await first.getAttribute("aria-pressed")) === "true", "Enter did not switch masking back on");
    request = await page.getByRole("region", { name: "Request to send" }).textContent();
    expect(!request.includes(DATA.name), "value still in clear");
  });

  await step("send streams the answer and restores values; an invented placeholder is flagged", async () => {
    await page.getByRole("button", { name: "Send pseudonymised request" }).click();
    await page.getByText("Answer complete, with placeholders that could not be restored.").waitFor({ timeout: 30000 });
    const answer = await page.getByRole("region", { name: "Answer" }).textContent();
    expect(answer.startsWith("Noted: "), "answer missing");
    expect(answer.includes(DATA.email) && answer.includes(DATA.name), "values not restored");
    expect(answer.includes("[PERSON_99] (not restored)"), "unresolved placeholder not marked");
    await page.getByRole("alert").filter({ hasText: "1 placeholder could not be restored" }).waitFor();
    const sent = await page.getByRole("region", { name: "Request sent" }).textContent();
    expect(!sent.includes(DATA.email), "sent request shows the value");
    await shot(page, "02-answer-1280-light");
  });

  await step("Show placeholders swaps restored values for their placeholders", async () => {
    await page.getByRole("switch", { name: "Show placeholders" }).click();
    let answer = await page.getByRole("region", { name: "Answer" }).textContent();
    expect(answer.includes("[EMAIL_1]") && !answer.includes(DATA.email), "placeholders not shown");
    await page.getByRole("switch", { name: "Show placeholders" }).click();
    answer = await page.getByRole("region", { name: "Answer" }).textContent();
    expect(answer.includes(DATA.email), "values not back");
  });

  await step("a value switched off in the ledger is sent as written and masked again by the gateway", async () => {
    const ledger = page.getByRole("list", { name: "Detected values" });
    const emailSwitch = ledger.getByRole("switch", { name: new RegExp(`^Email ${DATA.email.replace(/[.+]/g, "\\$&")}`) });
    await emailSwitch.click();
    expect((await emailSwitch.getAttribute("aria-checked")) === "false", "switch did not turn off");
    const request = await page.getByRole("region", { name: "Request to send" }).textContent();
    expect(request.includes(DATA.email), "email not in clear in the request");
    await page.getByRole("button", { name: "Send again" }).click();
    await page.getByText("Answer complete, with placeholders that could not be restored.").waitFor({ timeout: 30000 });
    const answer = await page.getByRole("region", { name: "Answer" }).textContent();
    expect(answer.includes(DATA.email), "email not in the answer");
    await emailSwitch.click();
  });

  await step("the audit tab shows the gateway's 501 calmly", async () => {
    await openTab(page, "Audit log");
    await page.getByText("This gateway does not serve the audit log yet").waitFor();
  });

  await step("the audit tab shows rows and an intact chain", async () => {
    await page.route("**/audit", (route) => route.fulfill({ json: { ...AUDIT_ROWS, verify: VERIFY_OK } }));
    await page.getByRole("button", { name: "Check again" }).click();
    await page.getByText("Chain intact").waitFor();
    const table = page.getByRole("table");
    expect((await table.getByRole("row").count()) === 4, "table rows");
    await table.getByText("api.openai.com").first().waitFor();
    await table.getByText("No answer").waitFor();
    await table.getByText("Cloud").first().waitFor();
    await shot(page, "03-audit-intact-1280-light");
  });

  await step("the audit tab shows a broken chain with the first bad entry", async () => {
    await page.unroute("**/audit");
    await page.route("**/audit", (route) => route.fulfill({ json: { ...AUDIT_ROWS, verification: VERIFY_BROKEN } }));
    await page.getByRole("button", { name: "Check again" }).click();
    await page.getByRole("alert").filter({ hasText: "Chain broken at entry 2" }).waitFor();
    await page.getByText("an entry was changed after it was written").waitFor();
  });

  await step("settings show the gateway's configuration and no key field", async () => {
    await openTab(page, "Settings");
    await page.getByText("Ollama runs on this computer and needs no key.").waitFor();
    await page.getByRole("tabpanel", { name: "Settings" }).getByText(MODEL, { exact: true }).waitFor();
    expect((await page.locator('input[type="password"]').count()) === 0, "a key field exists");
    await page.getByLabel("an example config.toml", { exact: true }).waitFor();
  });

  for (const theme of ["light", "dark"]) {
    await step(`${theme}: every screen passes axe (serious and critical) and is captured at 1280 and 390 px`, async () => {
      await setTheme(page, theme);
      for (const [tab, name] of [["Mask & send", "mask"], ["Audit log", "audit"], ["Settings", "settings"]]) {
        await page.setViewportSize({ width: 1280, height: 900 });
        await openTab(page, tab);
        if (name === "audit") await page.getByText("Chain broken at entry 2").first().waitFor();
        await axe(page, `${name} ${theme} 1280`);
        await shot(page, `${name}-1280-${theme}`);
        await page.setViewportSize({ width: 390, height: 844 });
        await axe(page, `${name} ${theme} 390`);
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
        expect(overflow <= 0, `${name} ${theme} scrolls sideways at 390 px by ${overflow}px`);
        await shot(page, `${name}-390-${theme}`);
      }
      await page.setViewportSize({ width: 1280, height: 900 });
    });
  }

  await step("Clear deletes the session in the gateway", async () => {
    await setTheme(page, "light");
    await openTab(page, "Mask & send");
    const deleted = page.waitForResponse((r) => r.url().includes("/session/") && r.request().method() === "DELETE");
    await page.getByRole("button", { name: "Clear" }).click();
    expect((await deleted).status() === 204, "session not deleted");
    expect((await page.getByLabel("Text to check").inputValue()) === "", "text not cleared");
    await axe(page, "compose light");
  });

  await step("a cloud provider shows the keyring command, never a key field", async () => {
    await page.route("**/health", (route) =>
      route.fulfill({ json: { status: "ok", version: "0.0.0", provider: "openai", local: false } }),
    );
    await page.reload();
    await page.getByText("OpenAI, in the cloud").waitFor();
    await openTab(page, "Settings");
    await page.getByText("uv run keyring set safeguard-gateway api-key:openai").waitFor();
    await page.unroute("**/health");
  });

  await step("a gateway that does not answer is reported with what to do", async () => {
    allowNetworkErrors = true;
    await page.route("**/health", (route) => route.abort());
    await page.reload();
    await page.getByText("Gateway not answering.").waitFor();
    await openTab(page, "Settings");
    await page.getByText("Start it with `uv run safeguard serve`").waitFor();
    await page.unroute("**/health");
    allowNetworkErrors = false;
  });

  await browser.close();

  await step("no console errors, CSP violations or page errors", async () => {
    expect(consoleErrors.length === 0, consoleErrors.join(" | "));
  });
  await step("no request left the gateway's origin", async () => {
    expect(foreignRequests.length === 0, foreignRequests.join(" "));
  });

  log.push(`NOTE GET /audit answered 501 ${expected501} time(s); Chrome logged each as a console error, expected`);
  console.log(log.join("\n"));
  process.exit(failed ? 1 : 0);
}

main().catch((error) => {
  console.log(log.join("\n"));
  console.error(error);
  process.exit(1);
});
