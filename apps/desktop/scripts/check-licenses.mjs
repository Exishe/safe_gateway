// Hard rule 1 for the desktop shell's npm side: only build tooling (the Tauri CLI and its
// platform binaries), none of it shipped in the app. Every package must be MIT, BSD or
// Apache-2.0; an "A OR B" expression passes when each alternative does. Reads
// package-lock.json only, so it runs offline.
import { readFileSync } from "node:fs";

const ALLOWED = new Set(["MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC"]);
const lock = JSON.parse(readFileSync(new URL("../package-lock.json", import.meta.url)));

const problems = [];
for (const [path, meta] of Object.entries(lock.packages)) {
  if (!path) continue;
  const license = meta.license ?? "UNKNOWN";
  const parts = license.split(/\s+OR\s+/);
  if (!parts.every((part) => ALLOWED.has(part))) {
    problems.push(`${path}: ${license}`);
  }
}
if (problems.length > 0) {
  console.error(`licenses not allowed:\n  ${problems.join("\n  ")}`);
  process.exit(1);
}
console.log("licenses: every desktop build package is MIT, BSD, ISC or Apache-2.0");
