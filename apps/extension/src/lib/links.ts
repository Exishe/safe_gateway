// Whether answer text, read left to right, is inside a link (ADR 0003 amendment 1).
// A port of gateway/engine/links.py: same states, same transitions. Restoring a value
// inside a URL would let an answer send it to a host the answer chose, so every rule
// errs towards "inside"; a false positive leaves a placeholder visible.
//
// - a bare URL: from `//` or a `www.` that starts a word, up to the next whitespace;
// - a Markdown link or image target: from `](` to its closing `)`, or a blank line;
// - a link reference definition: the destination after `[label]:` at a line start;
// - an autolink: `<scheme:` up to `>` or whitespace;
// - an HTML attribute value of any attribute inside a tag, quoted or not.

type Ref = "none" | "candidate" | "await" | "dest" | "angle";
type Tag = "none" | "name" | "autolink" | "in_tag" | "await_value" | "quoted" | "unquoted";

const TRIGGER = /[\n/.(<]/g; // outside every context, only these can open one
const RECENT = 5; // code points kept: `www.` needs the one before it
const MAX_INDENT = 3;

// Python's str.isspace(), which differs from JavaScript's \s at a few code points.
const SPACE = new Set([
  0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x1c, 0x1d, 0x1e, 0x1f, 0x20, 0x85, 0xa0, 0x1680,
  0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x2007, 0x2008, 0x2009,
  0x200a, 0x2028, 0x2029, 0x202f, 0x205f, 0x3000,
]);
const ALNUM = /^[\p{L}\p{N}]$/u;
const ASCII_ALPHA = /^[A-Za-z]$/;
const NAME_CHAR = /^[A-Za-z0-9+.-]$/;

function isSpace(char: string): boolean {
  return SPACE.has(char.codePointAt(0) ?? -1);
}

function lastCodePoints(text: string, count: number): string {
  return Array.from(text).slice(-count).join("");
}

export class LinkContext {
  private recent = "";
  private lineBlank = true; // only whitespace so far on this line
  private indent = 0;
  private bare = false;
  private mdDepth: number | null = null; // open parentheses inside `](...)`
  private mdAngle = false;
  private ref: Ref = "none";
  private tag: Tag = "none";
  private quote = "";

  get inside(): boolean {
    return (
      this.bare ||
      this.mdDepth !== null ||
      this.ref === "await" ||
      this.ref === "dest" ||
      this.ref === "angle" ||
      this.tag === "autolink" ||
      this.tag === "await_value" ||
      this.tag === "quoted" ||
      this.tag === "unquoted"
    );
  }

  feed(text: string): void {
    let index = 0;
    const end = text.length;
    while (index < end) {
      if (this.idle()) {
        TRIGGER.lastIndex = index;
        const found = TRIGGER.exec(text);
        const stop = found === null ? end : found.index;
        if (stop > index) {
          // 12 code units hold at least the last five code points.
          const skipped = text.slice(Math.max(index, stop - 12), stop);
          this.recent = lastCodePoints(this.recent + skipped, RECENT);
          index = stop;
          continue;
        }
      }
      const char = String.fromCodePoint(text.codePointAt(index) ?? 0);
      this.step(char);
      index += char.length;
    }
  }

  private idle(): boolean {
    return (
      !this.lineBlank &&
      !this.bare &&
      this.mdDepth === null &&
      this.ref === "none" &&
      this.tag === "none" &&
      !this.recent.endsWith("<")
    );
  }

  private step(char: string): void {
    const prev = lastCodePoints(this.recent, 1);
    this.line(char);
    this.bareUrl(char, prev);
    this.markdown(char, prev);
    this.reference(char, prev);
    this.html(char, prev);
    this.recent = lastCodePoints(this.recent + char, RECENT);
  }

  private line(char: string): void {
    if (char === "\n") {
      if (this.lineBlank) {
        // A blank line ends every Markdown construct; a quoted attribute value runs
        // on to its quote, as a browser reads it.
        this.mdDepth = null;
        this.mdAngle = false;
        this.ref = "none";
        if (this.tag !== "quoted") this.tag = "none";
      }
      if (this.ref === "candidate") this.ref = "none";
      this.lineBlank = true;
      this.indent = 0;
    } else if (isSpace(char)) {
      if (this.lineBlank) this.indent += 1;
    } else if (this.lineBlank) {
      this.lineBlank = false;
      if (char === "[" && this.indent <= MAX_INDENT && this.ref === "none") this.ref = "candidate";
    }
  }

  private bareUrl(char: string, prev: string): void {
    if (this.bare) this.bare = !isSpace(char);
    else if ((char === "/" && prev === "/") || (char === "." && this.startsWww())) this.bare = true;
  }

  private startsWww(): boolean {
    const recent = Array.from(this.recent);
    const joined = recent.join("");
    if (!joined.endsWith("www") && !joined.endsWith("WWW")) return false;
    const before = recent[recent.length - 4];
    return recent.length <= 3 || before === undefined || !ALNUM.test(before);
  }

  private markdown(char: string, prev: string): void {
    if (this.mdDepth === null) {
      if (char === "(" && prev === "]") this.mdDepth = 0;
    } else if (this.mdAngle) {
      this.mdAngle = char !== ">";
    } else if (char === "<") {
      this.mdAngle = true;
    } else if (char === "(") {
      this.mdDepth += 1;
    } else if (char === ")") {
      this.mdDepth = this.mdDepth === 0 ? null : this.mdDepth - 1;
    }
  }

  private reference(char: string, prev: string): void {
    switch (this.ref) {
      case "candidate":
        if (char === ":" && prev === "]") this.ref = "await";
        break;
      case "await":
        if (char === "<") this.ref = "angle";
        else if (!isSpace(char)) this.ref = "dest";
        break;
      case "dest":
        if (isSpace(char)) this.ref = "none";
        break;
      case "angle":
        if (char === ">" || char === "\n") this.ref = "none";
        break;
      case "none":
        break;
    }
  }

  private html(char: string, prev: string): void {
    switch (this.tag) {
      case "none":
        if (prev === "<" && ASCII_ALPHA.test(char)) this.tag = "name";
        break;
      case "quoted":
        if (char === this.quote) this.tag = "in_tag";
        break;
      case "await_value":
        this.tag = valueStart(char);
        this.quote = char;
        break;
      case "name":
        this.tag = afterName(char);
        break;
      case "autolink":
        this.tag = char === ">" || isSpace(char) ? "none" : "autolink";
        break;
      case "in_tag":
        this.tag = char === ">" ? "none" : char === "=" ? "await_value" : "in_tag";
        break;
      case "unquoted":
        this.tag = char === ">" ? "none" : isSpace(char) ? "in_tag" : "unquoted";
        break;
    }
  }
}

function afterName(char: string): Tag {
  if (char === ":") return "autolink"; // `<https:`: a scheme, so an autolink
  if (NAME_CHAR.test(char)) return "name";
  if (isSpace(char) || char === "/") return "in_tag";
  return "none";
}

function valueStart(char: string): Tag {
  if (char === '"' || char === "'") return "quoted";
  if (char === ">") return "none";
  if (isSpace(char)) return "await_value";
  return "unquoted";
}
