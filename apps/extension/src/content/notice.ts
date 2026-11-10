// The inline notice above the site's textbox. It lives in its own shadow root on an
// element appended to <html>, outside the site's app, so the site's framework never
// meets a node it did not create and the site's styles do not reach it.

export type Tone = "blocked" | "working" | "done";

const STYLE = `
:host { all: initial; }
.box {
  position: fixed; z-index: 2147483647; box-sizing: border-box;
  max-width: min(36rem, calc(100vw - 1.5rem));
  font: 14px/1.45 system-ui, "Segoe UI", "Noto Sans", "Noto Sans Armenian", Roboto, Arial, sans-serif;
  color: #16202a; background: #ffffff;
  border: 1px solid #d3d9d4; border-left: 4px solid #1e4d5c; border-radius: 8px;
  box-shadow: 0 6px 24px rgba(22, 32, 42, 0.18);
  padding: 10px 12px 10px 14px; display: grid; grid-template-columns: 1fr auto; gap: 2px 12px;
}
.box.blocked { border-left-color: #9b2c3b; }
.title { font-weight: 650; letter-spacing: 0.01em; }
.blocked .title { color: #9b2c3b; }
.done .title, .working .title { color: #1e4d5c; }
.body { grid-column: 1 / 2; color: #4a5763; overflow-wrap: anywhere; }
code { font: 13px ui-monospace, "SF Mono", "Cascadia Mono", Menlo, Consolas, monospace; background: #eceee9; padding: 0 3px; border-radius: 3px; color: #16202a; }
.mark { font: 600 11px/1 system-ui, sans-serif; letter-spacing: 0.08em; text-transform: uppercase; color: #4a5763; grid-column: 1 / 3; margin-bottom: 2px; }
button {
  grid-row: 2 / 4; grid-column: 2; align-self: start;
  font: inherit; color: #1e4d5c; background: transparent; border: 1px solid #9aa59f;
  border-radius: 6px; padding: 2px 10px; cursor: pointer;
}
button:hover { background: #e1ecee; }
button:focus-visible { outline: 2px solid #1e4d5c; outline-offset: 2px; }
@media (prefers-color-scheme: dark) {
  .box { color: #e4e9e6; background: #19232c; border-color: #2c3a46; border-left-color: #86c1cf; box-shadow: 0 6px 24px rgba(0, 0, 0, 0.5); }
  .box.blocked { border-left-color: #f08c98; }
  .blocked .title { color: #f08c98; }
  .done .title, .working .title { color: #86c1cf; }
  .body, .mark { color: #9daab5; }
  code { background: #0e151b; color: #e4e9e6; }
  button { color: #86c1cf; border-color: #4f6170; }
  button:hover { background: #1d3640; }
  button:focus-visible { outline-color: #a3d2dd; }
}
@media (prefers-reduced-motion: no-preference) {
  .box { animation: rise 160ms ease-out; }
  @keyframes rise { from { opacity: 0; transform: translateY(4px); } }
}
`;

/** Text with `code` spans, as DOM nodes: never parsed as HTML. */
function richText(text: string): Node[] {
  return text.split("`").map((part, i) => {
    if (i % 2 === 0) return document.createTextNode(part);
    const code = document.createElement("code");
    code.textContent = part;
    return code;
  });
}

export class Notice {
  private host: HTMLElement | null = null;
  private timer: ReturnType<typeof setTimeout> | undefined;

  show(tone: Tone, title: string, body: string, anchor: Element | null): void {
    this.hide();
    const host = document.createElement("div");
    host.setAttribute("data-safeguard", "notice");
    const shadow = host.attachShadow({ mode: "open" });
    const style = document.createElement("style");
    style.textContent = STYLE;
    const box = document.createElement("div");
    box.className = `box ${tone}`;
    // An alert interrupts a screen reader: only for a blocked send.
    box.setAttribute("role", tone === "blocked" ? "alert" : "status");
    const mark = document.createElement("div");
    mark.className = "mark";
    mark.textContent = "SafeGuard";
    const heading = document.createElement("div");
    heading.className = "title";
    heading.textContent = title;
    const text = document.createElement("div");
    text.className = "body";
    text.append(...richText(body));
    const close = document.createElement("button");
    close.type = "button";
    close.textContent = "Dismiss";
    close.addEventListener("click", () => {
      this.hide();
    });
    box.append(mark, heading, text, close);
    shadow.append(style, box);
    document.documentElement.append(host);
    this.host = host;
    this.place(box, anchor);
    if (tone === "done") this.timer = setTimeout(() => { this.hide(); }, 5000);
  }

  private place(box: HTMLElement, anchor: Element | null): void {
    const rect = anchor?.getBoundingClientRect();
    if (rect === undefined || rect.width === 0) {
      box.style.left = "12px";
      box.style.bottom = "12px";
      return;
    }
    box.style.left = `${Math.max(12, rect.left)}px`;
    box.style.bottom = `${Math.max(12, window.innerHeight - rect.top + 8)}px`;
  }

  hide(): void {
    clearTimeout(this.timer);
    this.host?.remove();
    this.host = null;
  }
}
