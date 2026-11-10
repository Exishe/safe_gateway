// Hard rule 1 for the web UI: every package that ships in the bundle, and every
// package we depend on directly, is MIT, BSD or Apache-2.0. Reads package-lock.json
// only, so it runs offline. Build tools pulled in transitively are listed, not failed:
// they never ship (see docs/adr/0024-web-ui.md).
import { readFileSync } from "node:fs";

const ALLOWED = new Set(["MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause"]);
const lock = JSON.parse(readFileSync(new URL("../package-lock.json", import.meta.url)));
const root = lock.packages[""];
const direct = new Set([
  ...Object.keys(root.dependencies ?? {}),
  ...Object.keys(root.devDependencies ?? {}),
]);

const problems = [];
const buildOnly = new Map();
for (const [path, meta] of Object.entries(lock.packages)) {
  if (!path) continue;
  const name = path.slice(path.lastIndexOf("node_modules/") + "node_modules/".length);
  const license = meta.license ?? "UNKNOWN";
  const isDirect = path === `node_modules/${name}` && direct.has(name);
  if (ALLOWED.has(license)) continue;
  if (!meta.dev || isDirect) {
    problems.push(`${name}@${meta.version}: ${license}${meta.dev ? " (direct)" : " (ships)"}`);
  } else {
    buildOnly.set(license, [...(buildOnly.get(license) ?? []), name]);
  }
}

for (const [license, names] of buildOnly) {
  console.log(`build tools only, ${license}: ${[...new Set(names)].join(", ")}`);
}
if (problems.length > 0) {
  console.error(`licenses not allowed:\n  ${problems.join("\n  ")}`);
  process.exit(1);
}
console.log("licenses: every shipped and direct package is MIT, BSD or Apache-2.0");
