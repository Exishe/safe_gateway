// Builds the extension into dist/chrome (Chrome, Edge, Brave) and dist/firefox.
// Vite runs once per entry: the content script and the background worker must each
// be one classic script (a content script cannot be an ES module, and Firefox's
// background page loads a classic script); the popup is an ordinary page.
import { cpSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { build } from "vite";

const root = fileURLToPath(new URL("..", import.meta.url));
const pkg = JSON.parse(readFileSync(`${root}package.json`, "utf8"));
const out = `${root}dist/`;
const chrome = `${out}chrome/`;
const firefox = `${out}firefox/`;

// The gateway and the three sites, nothing else (ADR 0026).
const GATEWAY = "http://127.0.0.1:8787/*";
const SITES = ["https://chatgpt.com/*", "https://chat.openai.com/*", "https://claude.ai/*", "https://gemini.google.com/*"];

const shared = { configFile: false, logLevel: "warn", publicDir: false };

async function script(entry, file) {
  await build({
    ...shared,
    build: {
      outDir: chrome,
      emptyOutDir: false,
      sourcemap: false,
      minify: false, // readable for anyone who reviews what runs on their chat pages
      lib: { entry: `${root}${entry}`, formats: ["iife"], name: "safeguard", fileName: () => file },
    },
  });
}

async function popup() {
  await build({
    ...shared,
    root: `${root}src/popup`,
    base: "./",
    build: {
      outDir: `${chrome}popup`,
      emptyOutDir: false,
      sourcemap: false,
      minify: false,
      assetsInlineLimit: 0, // the extension's CSP allows 'self' only
      modulePreload: { polyfill: false },
      rollupOptions: { input: `${root}src/popup/popup.html` },
    },
  });
}

function manifest(target) {
  const base = {
    manifest_version: 3,
    name: "SafeGuard Gateway",
    version: pkg.version,
    description: "Masks personal data in what you send to ChatGPT, Claude and Gemini, through SafeGuard Gateway on this computer.",
    permissions: ["storage"],
    host_permissions: [GATEWAY, ...SITES],
    content_scripts: [{ matches: SITES, js: ["content.js"], run_at: "document_start" }],
    action: { default_popup: "popup/popup.html", default_title: "SafeGuard", default_icon: icons() },
    icons: icons(),
    content_security_policy: {
      // Extension pages and the worker may load their own scripts and reach the gateway only.
      extension_pages: "script-src 'self'; object-src 'none'; connect-src http://127.0.0.1:8787",
    },
  };
  if (target === "chrome") {
    return { ...base, minimum_chrome_version: "116", background: { service_worker: "background.js" } };
  }
  return {
    ...base,
    background: { scripts: ["background.js"] },
    browser_specific_settings: {
      gecko: {
        id: "safeguard-extension@safeguard-gateway.invalid",
        strict_min_version: "128.0",
        data_collection_permissions: { required: ["none"] },
      },
    },
  };
}

function icons() {
  return Object.fromEntries([16, 32, 48, 128].map((size) => [String(size), `icons/icon-${size}.png`]));
}

rmSync(out, { recursive: true, force: true });
mkdirSync(chrome, { recursive: true });
await script("src/content/index.ts", "content.js");
await script("src/background.ts", "background.js");
await popup();
cpSync(`${root}public/icons`, `${chrome}icons`, { recursive: true });
cpSync(chrome, firefox, { recursive: true });
writeFileSync(`${chrome}manifest.json`, `${JSON.stringify(manifest("chrome"), null, 2)}\n`);
writeFileSync(`${firefox}manifest.json`, `${JSON.stringify(manifest("firefox"), null, 2)}\n`);
console.log(`built ${pkg.version}: dist/chrome (Chrome, Edge, Brave) and dist/firefox`);
