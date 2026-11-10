// Whether answer text, read left to right, is inside a link (ADR 0003 amendment 1).
// A port of gateway/engine/links.py: same states, same transitions. Restoring a value
// inside a URL would let an answer send it to a host the answer chose, so every rule
// errs towards "inside"; a false positive leaves a placeholder visible.
//
// - a bare URL: from `//`, from a `www.` that starts a word (any case), or from a
//   colon that ends a scheme (`[A-Za-z][A-Za-z0-9+.-]*`, any case: `https:host`,
//   `javascript:`, `data:`, `mailto:`), up to the next ASCII whitespace. A colon
//   followed by whitespace, as in `Note: ...`, is prose and ends there;
// - a Markdown link or image target: from `](` to its closing `)`, or a blank line;
// - a link reference definition: the destination after `]:` anywhere on a line (a
//   block quote, a list item, a label split over lines), on that line or the next;
// - an autolink: `<scheme:` up to `>` or ASCII whitespace;
// - an HTML attribute value of any attribute inside a tag, quoted or not;
// - CSS: `url(` up to its closing `)` outside quotes, and the string or word after
//   `@import`, any case.

type Ref = "none" | "await" | "dest" | "angle";
type Tag = "none" | "name" | "autolink" | "in_tag" | "await_value" | "quoted" | "unquoted";
type Css = "none" | "url" | "await_import" | "string" | "word";

const TRIGGER = /[\n/.(<:@]/g; // outside every context, only these can open one
const RECENT = 8; // code points kept: `@import` needs seven
const IMPORT = "@import";

// Python's str.isspace(), which differs from JavaScript's \s at a few code points.
// Only line blankness and the end of a tag name use it; everything that ends a URL
// uses ASCII_SPACE.
const SPACE = new Set([
  0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x1c, 0x1d, 0x1e, 0x1f, 0x20, 0x85, 0xa0, 0x1680,
  0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x2007, 0x2008, 0x2009,
  0x200a, 0x2028, 0x2029, 0x202f, 0x205f, 0x3000,
]);
// HTML's and WHATWG's ASCII whitespace. Anything else, a no-break space or a control
// character included, can be part of a URL (security review 2, M-a).
const ASCII_SPACE = new Set([" ", "\t", "\n", "\r", "\f"]);
const ALNUM = /^[\p{L}\p{N}]$/u;
const LETTER = /^[A-Za-z]$/;
const SCHEME_CHAR = /^[A-Za-z0-9+.-]$/;

function isSpace(char: string): boolean {
  return SPACE.has(char.codePointAt(0) ?? -1);
}

function lastCodePoints(text: string, count: number): string {
  return Array.from(text).slice(-count).join("");
}

/** Whether the run of scheme characters that ends `chunk` holds a letter. */
function schemeAfter(hasLetter: boolean, chunk: string): boolean {
  // Scheme characters are ASCII, so reading code units finds the same run.
  let start = chunk.length;
  while (start > 0 && SCHEME_CHAR.test(chunk.charAt(start - 1))) start -= 1;
  const letter = Array.from(chunk.slice(start)).some((char) => LETTER.test(char));
  return letter || (hasLetter && start === 0);
}

export class LinkContext {
  private recent = "";
  private lineBlank = true; // only whitespace so far on this line
  private scheme = false; // the scheme characters just read hold a letter
  private bare = false;
  private mdDepth: number | null = null; // open parentheses inside `](...)`
  private mdAngle = false;
  private ref: Ref = "none";
  private tag: Tag = "none";
  private quote = "";
  private css: Css = "none";
  private cssQuote = "";

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
      this.tag === "unquoted" ||
      this.css !== "none"
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
          this.scheme = schemeAfter(this.scheme, text.slice(index, stop));
          // 2 * RECENT code units hold at least the last RECENT code points.
          const skipped = text.slice(Math.max(index, stop - 2 * RECENT), stop);
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

  /** Nothing is open, and no character but a trigger can open anything. */
  private idle(): boolean {
    return (
      !this.lineBlank &&
      !this.bare &&
      this.mdDepth === null &&
      this.ref === "none" &&
      this.tag === "none" &&
      this.css === "none" &&
      !this.recent.endsWith("<") &&
      // `@impor` is read one character at a time, so its `t` is seen.
      !lastCodePoints(this.recent, IMPORT.length - 1).includes("@")
    );
  }

  private step(char: string): void {
    const prev = lastCodePoints(this.recent, 1);
    this.line(char);
    this.bareUrl(char, prev);
    this.markdown(char, prev);
    this.reference(char, prev);
    this.html(char, prev);
    this.style(char);
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
      this.lineBlank = true;
    } else if (!isSpace(char)) {
      this.lineBlank = false;
    }
  }

  private bareUrl(char: string, prev: string): void {
    if (this.bare) {
      this.bare = !ASCII_SPACE.has(char);
    } else if (
      (char === "/" && prev === "/") ||
      (char === "." && this.startsWww()) ||
      (char === ":" && this.scheme)
    ) {
      // After `scheme:` the URL is open at once: a token may follow the colon
      // directly. Whitespace next, as in `Note: x`, closes it again.
      this.bare = true;
    }
    this.scheme = SCHEME_CHAR.test(char) ? this.scheme || LETTER.test(char) : false;
  }

  private startsWww(): boolean {
    const recent = Array.from(this.recent);
    if (!this.recent.toLowerCase().endsWith("www")) return false;
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
      case "none":
        // Anywhere, not only at the start of a line: `> [r]: ...` and `- [r]: ...`
        // define a reference too, and so does a label split over lines.
        if (char === ":" && prev === "]") this.ref = "await";
        break;
      case "await":
        if (char === "<") this.ref = "angle";
        else if (!ASCII_SPACE.has(char)) this.ref = "dest";
        break;
      case "dest":
        if (ASCII_SPACE.has(char)) this.ref = "none";
        break;
      case "angle":
        if (char === ">" || char === "\n") this.ref = "none";
        break;
    }
  }

  private html(char: string, prev: string): void {
    switch (this.tag) {
      case "none":
        if (prev === "<" && LETTER.test(char)) this.tag = "name";
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
        this.tag = char === ">" || ASCII_SPACE.has(char) ? "none" : "autolink";
        break;
      case "in_tag":
        this.tag = char === ">" ? "none" : char === "=" ? "await_value" : "in_tag";
        break;
      case "unquoted":
        this.tag = char === ">" ? "none" : ASCII_SPACE.has(char) ? "in_tag" : "unquoted";
        break;
    }
  }

  private style(char: string): void {
    switch (this.css) {
      case "url":
        this.inCssUrl(char);
        break;
      case "string":
        if (char === this.cssQuote) this.css = "none";
        break;
      case "await_import":
        if (char === '"' || char === "'") {
          this.css = "string";
          this.cssQuote = char;
        } else if (!ASCII_SPACE.has(char)) {
          this.css = "word";
          this.cssWord(char);
        }
        break;
      case "word":
        this.cssWord(char);
        break;
      case "none":
        if (char === "(" && this.endsWithUrl()) {
          this.css = "url";
          this.cssQuote = "";
        } else if ((char === "t" || char === "T") && this.endsWithImpor()) {
          this.css = "await_import";
        }
        break;
    }
  }

  private endsWithUrl(): boolean {
    return this.recent.toLowerCase().endsWith("url");
  }

  private endsWithImpor(): boolean {
    return this.recent.toLowerCase().endsWith(IMPORT.slice(0, -1));
  }

  private inCssUrl(char: string): void {
    if (this.cssQuote) {
      if (char === this.cssQuote) this.cssQuote = "";
    } else if (char === '"' || char === "'") {
      this.cssQuote = char;
    } else if (char === ")") {
      this.css = "none";
    }
  }

  private cssWord(char: string): void {
    if (char === "(" && this.endsWithUrl()) {
      this.css = "url";
      this.cssQuote = "";
    } else if (ASCII_SPACE.has(char) || char === ";") {
      this.css = "none";
    }
  }
}

function afterName(char: string): Tag {
  if (char === ":") return "autolink"; // `<https:`: a scheme, so an autolink
  if (SCHEME_CHAR.test(char)) return "name";
  if (isSpace(char) || char === "/") return "in_tag";
  return "none";
}

function valueStart(char: string): Tag {
  if (char === '"' || char === "'") return "quoted";
  if (char === ">") return "none";
  if (ASCII_SPACE.has(char)) return "await_value";
  return "unquoted";
}
