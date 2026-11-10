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

// Mocked pages in the shape GET /audit serves (gateway/proxy/app.py, `audit` route):
// rows newest first, null columns left out, then next_before_id and verification.
function row(id, ts, host, model, counts, status, local) {
  return {
    id, kind: "request", ts_utc: ts, upstream_host: host, model, masked_payload_sha256: "a".repeat(64),
    bytes_out: 600 + id, status, row_hash: "b".repeat(64), local, entity_counts: counts,
  };
}
const NEWER_ROWS = [
  row(3, "2026-11-09T10:05:12.000000Z", "api.openai.com", "gpt-4o-mini", {}, 0, false),
  row(2, "2026-11-09T10:02:41.500000Z", "api.openai.com", "gpt-4o-mini", { CREDIT_CARD: 1, IBAN: 1 }, 200, false),
];
const OLDER_ROWS = [row(1, "2026-11-09T09:14:03.120000Z", "127.0.0.1:11434", "llama3.2", { EMAIL: 1, PERSON: 2 }, 200, true)];
function verification(ok) {
  return ok
    ? { ok: true, rows_checked: 3, first_bad_id: null, reason: null, detail: "3 rows verified", head_id: 3, head_hash: "b".repeat(64), purged_through_id: 0 }
    : { ok: false, rows_checked: 1, first_bad_id: 2, reason: "edited", detail: "its stored values do not match its row hash", head_id: 3, head_hash: "b".repeat(64), purged_through_id: 0 };
}
function auditPage(url, ok) {
  const older = new URL(url).searchParams.get("before_id") !== null;
  return older
    ? { rows: OLDER_ROWS, next_before_id: null, verification: verification(ok) }
    : { rows: NEWER_ROWS, next_before_id: 2, verification: verification(ok) };
}

// What the UI's text holds: a name, an email, a card and an IBAN, one of each. The UI
// sends its chat requests in the /mask session (ADR 0025), so the gateway keeps the
// session's placeholders and its audit entry counts their real types.
const REAL_COUNTS = ["Card number 1", "Email 1", "IBAN 1", "Name 1"];

async function countsOf(row) {
  const items = await row.getByRole("cell").nth(3).getByRole("listitem").allTextContents();
  return items.map((t) => t.trim()).sort();
}

async function auditRows(page) {
  const table = page.getByRole("table");
  await table.waitFor();
  return table.getByRole("row").filter({ has: page.getByRole("cell") });
}

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

  await step("after a send, the audit tab shows the new entry with its counts and an intact chain", async () => {
    await openTab(page, "Audit log");
    await page.getByText("Chain intact").waitFor();
    await page.getByText("1 entry checked, newest entry 1").waitFor();
    const rows = await auditRows(page);
    expect((await rows.count()) === 1, `expected 1 entry, found ${await rows.count()}`);
    const cells = await rows.first().getByRole("cell").allTextContents();
    log.push(`INFO real audit entry: ${cells.join(" | ")}`);
    expect(cells[1] === "127.0.0.1", `upstream ${cells[1]}`); // the log keeps the host, not the port
    expect(cells[2] === MODEL, `model ${cells[2]}`);
    const counts = await countsOf(rows.first());
    expect(JSON.stringify(counts) === JSON.stringify(REAL_COUNTS), `counts ${counts.join(", ")}`);
    expect(!cells[3].includes("Placeholder-shaped"), `counts ${cells[3]}`);
    expect(cells[4] === "200", `status ${cells[4]}`);
    expect(cells[5] === "This computer", `where ${cells[5]}`);
    await shot(page, "03-audit-real-1280-light");
    await openTab(page, "Mask & send");
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

  await step("both sends are in the real audit log, newest first, and the chain is intact", async () => {
    await openTab(page, "Audit log");
    await page.getByText("2 entries checked, newest entry 2").waitFor();
    const rows = await auditRows(page);
    expect((await rows.count()) === 2, `expected 2 entries, found ${await rows.count()}`);
    const newest = await rows.first().getByRole("cell").allTextContents();
    log.push(`INFO newest real audit entry: ${newest.join(" | ")}`);
    // The second send held the email in clear: the session already holds it, so the
    // gateway masked it again as [EMAIL_1] and the counts are the same real types.
    const counts = await countsOf(rows.first());
    expect(JSON.stringify(counts) === JSON.stringify(REAL_COUNTS), `counts ${counts.join(", ")}`);
  });

  await step("older entries load by before_id, from a mocked log", async () => {
    await page.route("**/audit*", (route) => route.fulfill({ json: auditPage(route.request().url(), true) }));
    await page.getByRole("button", { name: "Check again" }).click();
    await page.getByText("3 entries checked, newest entry 3").waitFor();
    const older = page.waitForRequest((r) => r.url().endsWith("/audit?before_id=2"));
    await page.getByRole("button", { name: "Show older entries" }).click();
    await older;
    await page.getByRole("table").getByText("llama3.2").waitFor();
    const rows = await auditRows(page);
    expect((await rows.count()) === 3, "older page not appended");
    expect((await page.getByRole("button", { name: "Show older entries" }).count()) === 0, "no more pages");
    await page.getByRole("table").getByText("No answer").waitFor();
  });

  await step("a gateway started with --no-audit is shown as keeping no log (mocked 404)", async () => {
    await page.unroute("**/audit*");
    allowNetworkErrors = true; // Chrome logs the 404 itself
    await page.route("**/audit*", (route) =>
      route.fulfill({
        status: 404,
        json: { error: { type: "not_found", message: "no audit log: the gateway was started with --no-audit" } },
      }),
    );
    await page.getByRole("button", { name: "Check again" }).click();
    await page.getByText("This gateway is not keeping an audit log").waitFor();
    allowNetworkErrors = false;
  });

  await step("the audit tab shows a broken chain with the first bad entry (mocked)", async () => {
    await page.unroute("**/audit*");
    await page.route("**/audit*", (route) => route.fulfill({ json: auditPage(route.request().url(), false) }));
    await page.getByRole("button", { name: "Check again" }).click();
    await page.getByRole("alert").filter({ hasText: "Chain broken at entry 2" }).waitFor();
    await page.getByText("an entry was changed after it was written").waitFor();
  });

  await step("settings show the gateway's configuration and no key field", async () => {
    await openTab(page, "Settings");
    await page.getByText("Ollama runs on this computer and needs no key.").waitFor();
    await page.getByRole("tabpanel", { name: "Settings" }).getByText(MODEL, { exact: true }).waitFor();
    await page.getByText("One tamper-evident entry for each request sent to a model").waitFor();
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

  console.log(log.join("\n"));
  process.exit(failed ? 1 : 0);
}

main().catch((error) => {
  console.log(log.join("\n"));
  console.error(error);
  process.exit(1);
});
