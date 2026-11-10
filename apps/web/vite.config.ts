import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// The UI talks only to the gateway on 127.0.0.1:8787. Built, it is served by the
// gateway itself (same origin). In `npm run dev` it calls the gateway across origins,
// which needs `[proxy] allow_dev_ui = true` in ~/.safeguard/config.toml (ADR 0024).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  server: { host: "127.0.0.1", port: 5173, strictPort: true },
  preview: { host: "127.0.0.1", port: 4173, strictPort: true },
  build: {
    // No inline scripts or styles: the gateway's CSP allows 'self' only.
    assetsInlineLimit: 0,
    sourcemap: false,
  },
  test: {
    include: ["src/**/*.test.ts"],
    environment: "node",
  },
});
