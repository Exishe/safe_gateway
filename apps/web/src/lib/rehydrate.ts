import { ENTITY_TYPES } from "./entities";

// The placeholder grammar of gateway/engine/placeholders.py (ADR 0014), used here to
// find what is placeholder-shaped in an answer. The gateway itself already restored
// its own placeholders and put back the session's ones it wrapped as [LITERAL_n]
// (ADR 0001 point 4), so only exact [TYPE_N] tokens of this session are restored
// here. Every other placeholder-shaped token is flagged, never hidden.
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
