import { LinkContext } from "./links";
import { canonicalOf, isBracketed, TOKEN } from "./placeholders";
import { originalOf, type Reason, type SessionMap } from "./rehydrate";

// Rehydration in a page's reply DOM (ADR 0026). The rules of rehydrate.ts, plus:
//
// - only the data of existing text nodes is changed: no node is added, removed or
//   moved, so a framework that owns the DOM (React, Angular) keeps working, and no
//   attribute is ever touched (an href, a src, a title);
// - text inside an <a> is never restored, nor the editable textbox, form fields,
//   scripts and styles;
// - a token split across text nodes (streamed chunks, inline markup) is restored into
//   its first node and its other pieces are emptied;
// - each pass starts from the masked text: a node this class changed is read back
//   with its placeholders, so a restored value is never scanned again, a pass is
//   idempotent, and an empty map puts every placeholder back.

/** Elements whose text is never rehydrated, with everything inside them. */
const SKIPPED_TAGS = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEXTAREA", "INPUT", "SELECT", "TEMPLATE", "OPTION"]);
/** Elements that start a new line for the link rule (as Markdown renders paragraphs). */
const BLOCK_TAGS = new Set([
  "P", "DIV", "LI", "UL", "OL", "PRE", "BLOCKQUOTE", "H1", "H2", "H3", "H4", "H5", "H6",
  "TABLE", "TR", "TD", "TH", "SECTION", "ARTICLE", "HEADER", "FOOTER", "DL", "DT", "DD", "HR",
  "MAIN", "ASIDE", "NAV", "FIGURE", "FIGCAPTION", "DETAILS", "SUMMARY",
]);

export function isEditable(element: Element): boolean {
  const editable = element.getAttribute("contenteditable");
  return (editable !== null && editable !== "false") || element.getAttribute("role") === "textbox";
}

function isSkipped(element: Element): boolean {
  return SKIPPED_TAGS.has(element.tagName.toUpperCase()) || isEditable(element) || element.hasAttribute("data-safeguard");
}

interface Segment {
  start: number; // in the node's data as written by the last pass
  end: number;
  original: string; // the masked text it stands for
}

interface NodeRecord {
  data: string;
  segments: Segment[];
}

interface Piece {
  node: Text;
  start: number; // offsets in the pass's masked text
  end: number;
  inLink: boolean;
}

export interface DomPass {
  /** Tokens showing their original value after this pass. */
  restored: number;
  /** Tokens left as written, once each, in order, with why. Deferred ones are not listed. */
  unresolved: Map<string, Reason>;
  /** Where each restored value and each visible token sits, in the text as written now. */
  marks: Mark[];
}

export interface Mark {
  kind: "restored" | "unresolved";
  node: Text;
  start: number;
  end: number;
}

export class DomRehydrator {
  private readonly map: () => SessionMap;
  private readonly records = new WeakMap<Text, NodeRecord>();

  constructor(map: () => SessionMap) {
    this.map = map;
  }

  /**
   * Rehydrate the text under `root`. With `final` false, an unbracketed token at the
   * very end of the text is left for later: the next streamed chunk could extend it
   * (`PERSON_1` then `2`), as the engine's streaming rehydrator holds it back.
   */
  run(root: Element, final = true): DomPass {
    const { pieces, masked, covered } = this.collect(root);
    const out = pieces.map(() => ({ parts: [] as string[], length: 0, segments: [] as Segment[] }));
    const links = new LinkContext();
    const unresolved = new Map<string, Reason>();
    const map = this.map();
    let restored = 0;
    const marks: Mark[] = [];

    const overlapping = (from: number, to: number): number[] =>
      pieces.flatMap((p, i) => (p.start < to && p.end > from ? [i] : []));
    const plain = (from: number, to: number, mark = false): void => {
      for (const i of overlapping(from, to)) {
        const piece = pieces[i] as Piece;
        const text = masked.slice(Math.max(from, piece.start), Math.min(to, piece.end));
        const target = out[i];
        if (target === undefined) continue;
        if (mark && text !== "") {
          marks.push({ kind: "unresolved", node: piece.node, start: target.length, end: target.length + text.length });
        }
        target.parts.push(text);
        target.length += text.length;
      }
    };
    const value = (from: number, to: number, original: string): void => {
      overlapping(from, to).forEach((i, n) => {
        const piece = pieces[i] as Piece;
        const target = out[i];
        if (target === undefined) return;
        const stood = masked.slice(Math.max(from, piece.start), Math.min(to, piece.end));
        const written = n === 0 ? original : "";
        if (n === 0) {
          marks.push({ kind: "restored", node: piece.node, start: target.length, end: target.length + written.length });
        }
        target.segments.push({ start: target.length, end: target.length + written.length, original: stood });
        target.parts.push(written);
        target.length += written.length;
      });
    };

    let cursor = 0;
    for (const match of masked.matchAll(TOKEN)) {
      const start = match.index;
      const token = match[0];
      const end = start + token.length;
      const before = masked.slice(cursor, start);
      plain(cursor, start);
      links.feed(before);
      const inLink = links.inside || overlapping(start, end).some((i) => pieces[i]?.inLink === true);
      const wasRestored = covered.slice(start, end).every(Boolean);
      const deferred = !final && !isBracketed(match) && end === masked.length && !wasRestored;
      const original = inLink || deferred ? undefined : originalOf(map, token, canonicalOf(match));
      if (original === undefined) {
        if (!deferred && !unresolved.has(token)) unresolved.set(token, inLink ? "inside_link" : "not_in_mapping");
        plain(start, end, !deferred);
        links.feed(token);
      } else {
        value(start, end, original);
        links.feed(original); // what the reader sees is the context of what follows
        restored += 1;
      }
      cursor = end;
    }
    plain(cursor, masked.length);

    pieces.forEach((piece, i) => {
      const target = out[i];
      if (target === undefined) return;
      const data = target.parts.join("");
      if (piece.node.data !== data) piece.node.data = data;
      if (target.segments.length > 0) this.records.set(piece.node, { data, segments: target.segments });
      else this.records.delete(piece.node);
    });
    return { restored, unresolved, marks };
  }

  /** The text under `root` as the site wrote it (placeholders, not restored values). */
  private collect(root: Element): { pieces: Piece[]; masked: string; covered: boolean[] } {
    const pieces: Piece[] = [];
    const chunks: string[] = [];
    const covered: boolean[] = []; // which characters a previous pass had restored
    let length = 0;
    let lastBlock: Element | null = null;
    let breakPending = false;
    const push = (text: string, restoredBefore: boolean): void => {
      chunks.push(text);
      for (let i = 0; i < text.length; i += 1) covered.push(restoredBefore);
      length += text.length;
    };
    if (root.closest("[contenteditable]:not([contenteditable=false]), [role=textbox]") !== null) {
      return { pieces, masked: "", covered };
    }
    const walker = root.ownerDocument.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
      acceptNode: (node) =>
        node instanceof Element && isSkipped(node) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
    });
    for (let node = walker.nextNode(); node !== null; node = walker.nextNode()) {
      if (node instanceof Element) {
        if (node.tagName.toUpperCase() === "BR") breakPending = true;
        continue;
      }
      if (!(node instanceof Text)) continue;
      const block = blockOf(node, root);
      if (pieces.length > 0 && (breakPending || block !== lastBlock)) push("\n", false);
      breakPending = false;
      lastBlock = block;
      const start = length;
      const record = this.records.get(node);
      if (record !== undefined && record.data === node.data) {
        let at = 0;
        for (const segment of record.segments) {
          push(node.data.slice(at, segment.start), false);
          push(segment.original, true);
          at = segment.end;
        }
        push(node.data.slice(at), false);
      } else {
        this.records.delete(node); // the site wrote this node again: its text is masked text
        push(node.data, false);
      }
      pieces.push({ node, start, end: length, inLink: node.parentElement?.closest("a") != null });
    }
    return { pieces, masked: chunks.join(""), covered };
  }
}

function blockOf(node: Node, root: Element): Element {
  for (let element = node.parentElement; element !== null && element !== root; element = element.parentElement) {
    if (BLOCK_TAGS.has(element.tagName.toUpperCase())) return element;
  }
  return root;
}
