// The app's own pages: loading, restarting, error. The Rust shell sets the state through
// the URL fragment (`#loading`, `#restarting?attempt=2&reason=...`, `#error?reason=...`).
// All text goes in with textContent; nothing from the fragment is ever parsed as HTML.
"use strict";

const COPY = {
  loading: {
    title: "Starting SafeGuard Gateway",
    detail:
      "Getting the privacy gateway ready on this computer. This can take a minute the first time.",
  },
  restarting: {
    title: "Restarting the gateway",
    detail: "The gateway stopped unexpectedly. It is being started again.",
  },
  error: {
    title: "The gateway could not start",
    detail: "SafeGuard Gateway tried several times and gave up. Nothing was sent anywhere.",
    hint: "Choose Restart gateway in the tray menu to try again. If it keeps failing, check the sidecar.log file in the app's log folder.",
  },
};

function parse(hash) {
  const [name, query = ""] = hash.replace(/^#/, "").split("?");
  const params = new URLSearchParams(query);
  return { name: name in COPY ? name : "loading", params };
}

function setText(id, text) {
  const element = document.getElementById(id);
  element.textContent = text ?? "";
  element.hidden = !text;
}

function render() {
  const { name, params } = parse(window.location.hash);
  const copy = COPY[name];
  document.getElementById("view").dataset.state = name;
  document.getElementById("title").textContent = copy.title;
  document.getElementById("detail").textContent = copy.detail;
  const reason = params.get("reason");
  const attempt = params.get("attempt");
  setText("reason", reason ? `Last message: ${reason}` : "");
  setText("hint", copy.hint ?? (attempt ? `Try ${attempt}.` : ""));
}

window.addEventListener("hashchange", render);
render();
