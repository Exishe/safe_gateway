import js from "@eslint/js";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "node_modules"] },
  {
    files: ["**/*.ts"],
    extends: [js.configs.recommended, ...tseslint.configs.strictTypeChecked],
    languageOptions: {
      ecmaVersion: 2023,
      globals: { ...globals.browser, chrome: "readonly", browser: "readonly" },
      parserOptions: { projectService: true, tsconfigRootDir: import.meta.dirname },
    },
    rules: {
      "@typescript-eslint/restrict-template-expressions": ["error", { allowNumber: true }],
      // The extension talks to 127.0.0.1:8787 only, and only from its background
      // worker (src/lib/gateway.ts); nothing else may open a connection.
      "no-restricted-globals": ["error", "XMLHttpRequest", "WebSocket", "EventSource"],
      // Text goes in as text: never parse a string as HTML in a page we run in.
      "no-restricted-properties": [
        "error",
        { property: "innerHTML", message: "use textContent or DOM nodes" },
        { property: "outerHTML", message: "use textContent or DOM nodes" },
        { property: "insertAdjacentHTML", message: "use DOM nodes" },
      ],
    },
  },
  {
    files: ["src/**/*.test.ts"],
    rules: { "no-restricted-properties": "off" }, // tests build fixture DOMs from HTML
  },
  {
    files: ["scripts/**/*.mjs"],
    extends: [js.configs.recommended],
    languageOptions: { globals: globals.node },
  },
);
