import { LinkContext } from "./links";
import { canonicalOf, TOKEN } from "./placeholders";

// Put the original values back into an answer (ADR 0003, ADR 0002 point 2): a port of
// rehydrate_explained() in gateway/engine/rehydrate.py. The extension restores replies
// itself, from the session map the gateway returned with /mask.

export type Reason = "not_in_mapping" | "inside_link";
export type SessionMap = Readonly<Record<string, string>>;

/** The original value of a token, or undefined when it stays as written. */
export function originalOf(map: SessionMap, token: string, key: string): string | undefined {
  // ADR 0003, the first rule that matches wins: the exact token (rule 1), then the
  // canonical key of a bracketed (rule 2) or unbracketed upper-case (rule 3) form.
  if (Object.hasOwn(map, token)) return map[token];
  if (Object.hasOwn(map, key)) return map[key];
  return undefined;
}

export interface Rehydrated {
  text: string;
  /** Each token left as written, once, in order, with why. */
  unresolved: Map<string, Reason>;
}

export function rehydrateExplained(text: string, map: SessionMap): Rehydrated {
  const links = new LinkContext();
  const unresolved = new Map<string, Reason>();
  const pieces: string[] = [];
  let cursor = 0;
  for (const match of text.matchAll(TOKEN)) {
    const plain = text.slice(cursor, match.index);
    links.feed(plain);
    pieces.push(plain);
    const token = match[0];
    // Never inside a link (ADR 0003 amendment 1), mapped or not.
    let restored = links.inside ? undefined : originalOf(map, token, canonicalOf(match));
    if (restored === undefined) {
      if (!unresolved.has(token)) unresolved.set(token, links.inside ? "inside_link" : "not_in_mapping");
      restored = token;
    }
    // The client sees the restored value, so it is context for what follows; it is
    // never scanned for tokens again.
    links.feed(restored);
    pieces.push(restored);
    cursor = match.index + token.length;
  }
  const tail = text.slice(cursor);
  links.feed(tail);
  pieces.push(tail);
  return { text: pieces.join(""), unresolved };
}
