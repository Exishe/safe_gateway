import { ENTITY_TYPES } from "./entities";

// The placeholder grammar of gateway/engine/placeholders.py (ADR 0001, ADR 0003,
// ADR 0014), ported as written. rehydrate.parity.json, generated from the engine,
// checks that this port and the engine agree.

const NAMES = [...ENTITY_TYPES].sort((a, b) => b.length - a.length); // longest first
const TYPES = NAMES.join("|");
const ANY_CASE_TYPES = NAMES.map((name) =>
  name.replace(/[A-Z]/g, (c) => `[${c}${c.toLowerCase()}]`),
).join("|");
const NUMBER = "[0-9]{1,6}"; // MAX_COUNTER = 999_999
// Python's Unicode \w (str.isalnum() or "_") or a square bracket, next to an
// unbracketed token, disqualifies it (ADR 0014 point 3 and amendment 1 point 6).
const EDGE = String.raw`[\p{L}\p{N}_\[\]]`;

export const TOKEN = new RegExp(
  // Rules 1 and 2: bracketed, type in any case, `_`, space or `-`.
  String.raw`\[(?<btype>${ANY_CASE_TYPES})[_ \-](?<bnum>${NUMBER})\]` +
    // Rule 3: unbracketed, upper-case type only, `_` or space.
    String.raw`|(?<!${EDGE})(?<utype>${TYPES})[_ ](?<unum>${NUMBER})(?!${EDGE})`,
  "gu",
);

/** The mapping key a TOKEN match would be restored from: `[TYPE_N]`. */
export function canonicalOf(match: RegExpMatchArray): string {
  const groups = match.groups ?? {};
  if (groups.btype !== undefined) return `[${groups.btype.toUpperCase()}_${groups.bnum ?? ""}]`;
  return `[${groups.utype ?? ""}_${groups.unum ?? ""}]`;
}

/** Whether a match is bracketed; an unbracketed one at the end of text can still grow. */
export function isBracketed(match: RegExpMatchArray): boolean {
  return match.groups?.btype !== undefined;
}
