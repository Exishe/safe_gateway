// The persistent red banner for "SafeGuard is NOT protecting this page" (ADR 0026
// amendment 2026-11-10). Like the notice it lives in its own shadow root on <html>,
// outside the site's app. It has no dismiss button: it goes away only when the
// protection is back (the textbox was found), or when the page is reloaded.

export const NOT_PROTECTING = "SafeGuard is NOT protecting this page — do not send personal data";

const STYLE = `
:host { all: initial; }
.bar {
  position: fixed; top: 0; left: 0; right: 0; z-index: 2147483647; box-sizing: border-box;
  font: 600 14px/1.4 system-ui, "Segoe UI", "Noto Sans", Roboto, Arial, sans-serif;
  color: #ffffff; background: #9b2c3b; border-bottom: 2px solid #5f1621;
  padding: 8px 16px; text-align: center;
}
.reason { display: block; font-weight: 400; font-size: 13px; overflow-wrap: anywhere; }
`;

export class Banner {
  private host: HTMLElement | null = null;

  show(reason: string): void {
    this.hide();
    const host = document.createElement("div");
    host.setAttribute("data-safeguard", "banner");
    const shadow = host.attachShadow({ mode: "open" });
    const style = document.createElement("style");
    style.textContent = STYLE;
    const bar = document.createElement("div");
    bar.className = "bar";
    bar.setAttribute("role", "alert");
    const headline = document.createElement("span");
    headline.textContent = NOT_PROTECTING;
    const detail = document.createElement("span");
    detail.className = "reason";
    detail.textContent = reason;
    bar.append(headline, detail);
    shadow.append(style, bar);
    document.documentElement.append(host);
    this.host = host;
  }

  hide(): void {
    this.host?.remove();
    this.host = null;
  }
}
