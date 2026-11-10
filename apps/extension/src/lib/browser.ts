// Firefox exposes the WebExtension APIs as `browser` (promises), Chromium as `chrome`
// (promises in Manifest V3). Every call this extension makes exists, with promises, in
// both, so this one line replaces webextension-polyfill (MPL-2.0; ADR 0026).
export const ext: typeof chrome = (globalThis as { browser?: typeof chrome }).browser ?? chrome;

/** The extension's origin host: the Chrome id, or Firefox's per-install internal UUID. */
export function originId(): string {
  return new URL(ext.runtime.getURL("/")).host;
}
