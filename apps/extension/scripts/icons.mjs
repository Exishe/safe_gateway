// Renders the extension icons (public/icons/icon-N.png) from the web UI's mark.
// Run once after changing the mark: `node scripts/icons.mjs`. Uses the Node
// Playwright and Chromium already on the machine; the PNGs are committed.
import { mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const dir = fileURLToPath(new URL("../public/icons/", import.meta.url));

// A solid tile so the mark reads on light and dark toolbars alike.
const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
<rect width="32" height="32" rx="7" fill="#1e4d5c"/>
<rect x="5" y="9" width="22" height="16" rx="2.5" fill="#ffffff" opacity="0.22"/>
<path d="M5,11.5 Q5,9 7.5,9 H13 L15.5,11.5 H24.5 Q27,11.5 27,14" fill="none" stroke="#ffffff" stroke-width="2"/>
<rect x="9" y="16.5" width="14" height="3.5" rx="1" fill="#ffffff"/>
</svg>`;

mkdirSync(dir, { recursive: true });
const browser = await chromium.launch();
const page = await browser.newPage();
for (const size of [16, 32, 48, 128]) {
  await page.setViewportSize({ width: size, height: size });
  await page.setContent(
    `<html><body style="margin:0;background:transparent">${svg.replace("<svg ", `<svg width="${size}" height="${size}" `)}</body></html>`,
  );
  await page.locator("svg").screenshot({ path: `${dir}icon-${size}.png`, omitBackground: true });
}
await browser.close();
console.log(`icons written to ${dir}`);
