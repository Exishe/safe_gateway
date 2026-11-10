import { defineConfig } from "vitest/config";

// Vitest only. The build is scripts/build.mjs, which runs Vite once per entry so that
// the content script and the background worker are each one classic script (a content
// script cannot be an ES module, and Firefox's background page is a classic script).
export default defineConfig({
  test: {
    include: ["src/**/*.test.ts"],
    environment: "node",
  },
});
