import { ENTITY_TYPES } from "./entities";

// The placeholder grammar of gateway/engine/placeholders.py (ADR 0014), used here to
// find what is placeholder-shaped. `restore` marks the session's placeholders in the
// request the UI sends. Answers come back already restored by the gateway, which
// sends chat requests in the session (ADR 0025), so `flag` restores nothing: a token
// the gateway left as written (inside a link, or unknown) stays so, and is flagged.
const NAMES = [...ENTITY_TYPES].sort((a, b) => b.length - a.length);
const ANY_CASE = NAMES.map((name) => name.replace(/[A-Z]/g, (c) => `[${c}${c.toLowerCase()}]`)).join("|");
const UPPER = NAMES.join("|");
const EDGE = String.raw`[\p{L}\p{N}\p{M}\p{Pc}\[\]]`;
const TOKEN = new RegExp(
  String.raw`\[(?:${ANY_CASE})[_ \-][0-9]{1,6}\]` +
    String.raw`|(?<!${EDGE})(?:${UPPER})[_ ][0-9]{1,6}(?!${EDGE})`,
  "gu",
);

export type Part =
  | { kind: "text"; text: string }
  | { kind: "restored"; text: string; placeholder: string }
  | { kind: "unresolved"; text: string };

export interface Restored {
  text: string;
  parts: Part[];
  unresolved: string[];
}

export function restore(answer: string, mapping: Readonly<Record<string, string>>): Restored {
  const parts: Part[] = [];
  const unresolved: string[] = [];
  let cursor = 0;
  for (const match of answer.matchAll(TOKEN)) {
    const token = match[0];
    if (match.index > cursor) parts.push({ kind: "text", text: answer.slice(cursor, match.index) });
    const original = Object.hasOwn(mapping, token) ? mapping[token] : undefined;
    if (original === undefined) {
      parts.push({ kind: "unresolved", text: token });
      if (!unresolved.includes(token)) unresolved.push(token);
    } else {
      parts.push({ kind: "restored", text: original, placeholder: token });
    }
    cursor = match.index + token.length;
  }
  if (cursor < answer.length) parts.push({ kind: "text", text: answer.slice(cursor) });
  return { text: parts.map((p) => p.text).join(""), parts, unresolved };
}

/**
 * An answer the gateway already restored: every placeholder-shaped token left in
 * it is flagged and shown as written, never replaced. Restoring here would undo
 * the gateway's refusal to restore inside a link (ADR 0003 amendment 1).
 */
export function flag(answer: string): Restored {
  return restore(answer, {});
}
