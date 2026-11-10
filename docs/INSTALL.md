# Install

## Developer machine (macOS)

Everything here is free. Homebrew is assumed.

```bash
brew install uv git gh node        # Node 20 or newer, for the front ends later
brew install tesseract tesseract-lang   # OCR, Phase 3; includes Russian and Armenian
brew install ollama                # local stand-in for the cloud model
ollama pull qwen2.5:3b             # about 2 GB; tests never call a paid API
```

The desktop shell (Phase 6) also needs Rust and the Tauri CLI:

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
cargo install tauri-cli
```

Then, in the repository:

```bash
uv sync                          # installs Python 3.12 if missing, plus dev tools
scripts/install-git-hooks.sh     # turns on the pre-commit gate
uv run safeguard models pull    # English and Russian NER models, about 900 MB, SHA-256 checked
uv run pytest
```

On Windows the same steps work with `winget install astral-sh.uv` and the
Tesseract and Ollama Windows installers. The git hooks need Git Bash.

On Windows, also install Microsoft's free **Visual C++ Redistributable (x64)**
from https://aka.ms/vs/17/release/vc_redist.x64.exe. PyTorch, which GLiNER and the
spaCy stack load, needs it. Without it the gateway refuses every request with an
error naming `torch\lib\c10.dll` (WinError 126) and sends nothing upstream. To
check: `uv run python -c "import torch; print(torch.__version__)"`.

The project pins uv (`required-version` in `pyproject.toml`). If uv reports a
version mismatch, run `uv self update 0.11.32`.

### Running the local proxy (Phase 4)

`uv run safeguard serve` listens on `http://127.0.0.1:8787` (`--port` changes
the port; any host but 127.0.0.1 is refused). Point an OpenAI-compatible client
at `http://127.0.0.1:8787/v1`, or an Anthropic-compatible one at
`http://127.0.0.1:8787`. Without a configuration file it forwards to a local
Ollama on `http://127.0.0.1:11434/v1`, so nothing leaves the machine.

To use a cloud model, write `~/.safeguard/config.toml` (or
`$SAFEGUARD_HOME/config.toml`; ADR 0020 lists every field):

```toml
provider = "openai"       # anthropic | openai | azure_openai | ollama
model = "gpt-4o-mini"     # optional; replaces the model the client asks for
```

and put the API key in the OS keychain, never in the file. The keyring command
prompts for it, so it does not land in your shell history:

```bash
uv run keyring set safeguard-gateway api-key:openai
```

The gateway reads the key once at start-up; restart it after changing the key.

### The web UI (Phase 6)

Node 22.12 or later builds it once; the gateway then serves it at
`http://127.0.0.1:8787/`:

```bash
cd apps/web && npm ci --ignore-scripts && npm run build && cd ../..
uv run safeguard serve      # open http://127.0.0.1:8787/
```

Without `apps/web/dist` the gateway serves the API only. To work on the UI with
hot reload, set `allow_dev_ui = true` under `[proxy]` in config.toml, restart the
gateway, and run `npm run dev` in `apps/web` (http://127.0.0.1:5173). Leave the
flag off otherwise.

Browser tests (Node Playwright with an installed Chromium):

```bash
SAFEGUARD_E2E=1 uv run pytest tests/e2e -m e2e --no-cov -s
```

Screenshots go to `tests/e2e/artifacts/web/`. The test starts `safeguard serve` with
a temporary `SAFEGUARD_HOME` and a test-only file keyring
(`tests/e2e/web/file_keyring.py`), so it writes and verifies a real audit log
without touching your keychain or `~/.safeguard`.

### The desktop app (Phase 6)

The desktop app is a Tauri 2 shell around the same gateway: it starts the engine
(`safeguard serve`, built as a sidecar), shows the web UI in a window, and keeps a
tray icon with **Open dashboard**, the gateway's status, **Restart gateway**, a
**Start at login** switch and **Quit** (ADR 0027). Closing the window leaves it in
the tray; **Quit** stops the gateway. Installers come from the `desktop` job in CI,
which runs on `v*` tags and by hand (Actions, **ci**, **Run workflow**) and uploads a
`.dmg`, an `.msi` and a `.deb` as workflow artifacts.

The installers are **not signed**: a signing certificate costs money
([APPROVAL_NEEDED.md](APPROVAL_NEEDED.md)). Your system will warn you the first time.
Download only from your own build or from someone you trust, and check the warning
below matches the name SafeGuard Gateway.

- **macOS (Gatekeeper).** Open the `.dmg` and drag SafeGuard Gateway to Applications.
  The first time, do not double-click it: right-click (or Control-click) the app and
  choose **Open**, then **Open** again in the dialog. If macOS says the app "is
  damaged", run `xattr -dr com.apple.quarantine "/Applications/SafeGuard Gateway.app"`
  and open it again. Apple silicon (arm64) only.
- **Windows (SmartScreen).** Run the `.msi`. When "Windows protected your PC" appears,
  choose **More info**, then **Run anyway**. Windows 10 (1809) or later with the
  WebView2 runtime, which Windows 11 and current Windows 10 already have.
- **Linux (Debian, Ubuntu).** `sudo apt install ./SafeGuard*.deb` (the package is `safe-guard-gateway`). It needs
  `libwebkit2gtk-4.1-0` and a system tray (on GNOME, the AppIndicator extension).
  A keyring service (GNOME Keyring or KWallet) must be running: the audit log's key
  lives there.

**First run.** The installer carries the engine but not the language models. Open
Settings in the app and press **Download models** (about 900 MB, SHA-256 checked; the
only step that needs the internet, to GitHub and Hugging Face). The same download is
available from a terminal: the engine inside the app takes the same commands as the
CLI (`safeguard-sidecar models pull`, run from the app's `safeguard-sidecar` resource
folder), and a source checkout uses `uv run safeguard models pull`. Until the models are installed, names and addresses are not detected; structured
identifiers (SSN, card, IBAN, phone) work without models. Settings are the same file
as before: `~/.safeguard/config.toml`. The sidecar's log is `sidecar.log` in the app's
log folder (macOS `~/Library/Logs/com.safeguard.gateway`, Windows
`%LOCALAPPDATA%\com.safeguard.gateway\logs`, Linux `~/.local/share/com.safeguard.gateway/logs`).

**Build from source.** Install Rust (see above), Node 22.12 or later, and on Linux:

```bash
sudo apt install libwebkit2gtk-4.1-dev libgtk-3-dev libayatana-appindicator3-dev \
  librsvg2-dev libssl-dev libxdo-dev patchelf build-essential
```

Then, from the repository root (the sidecar step takes several minutes and about
1 GB of disk, mostly PyTorch; 1008 MiB measured on Linux x86-64 after the build prunes
PyTorch's test files):

```bash
(cd apps/web && npm ci --ignore-scripts && npm run build)        # the UI the sidecar carries
uv run --group sidecar python -m tools.build_sidecar              # PyInstaller, no models
cd apps/desktop && npm ci --ignore-scripts
npx tauri build --bundles deb       # or dmg on macOS, msi on Windows; add --debug for a faster build
```

Installers land in `apps/desktop/src-tauri/target/release/bundle/`. Rust unit tests:
`cargo test --manifest-path apps/desktop/src-tauri/Cargo.toml -j 2`. To run the shell
without bundling, build the sidecar once, then
`SAFEGUARD_SIDECAR=$PWD/apps/desktop/src-tauri/resources/safeguard-sidecar/safeguard-sidecar npx tauri dev`
(debug builds only).

Regenerate the attributions after any dependency change:
`uv run python -m tools.gen_notices` (writes `THIRD_PARTY_NOTICES.md`).

### The browser extension (Phase 7)

The extension masks what you send to ChatGPT (chatgpt.com, chat.openai.com), Claude
(claude.ai) and Gemini (gemini.google.com) through the gateway on this computer, and
restores the replies on the page (ADR 0026). It is not in any store yet (the Chrome
Web Store fee is on [APPROVAL_NEEDED.md](APPROVAL_NEEDED.md)), so pilots load it
unpacked. Build it once with Node 22.12 or later:

```bash
cd apps/extension && npm ci --ignore-scripts && npm run build && cd ../..
```

This writes `apps/extension/dist/chrome` (Chrome, Edge, Brave) and
`apps/extension/dist/firefox`.

**Chrome, Edge or Brave.** Open `chrome://extensions` (Edge: `edge://extensions`,
Brave: `brave://extensions`), switch on **Developer mode**, choose **Load unpacked**
and pick `apps/extension/dist/chrome`. The card shows the extension's **ID**, 32
letters from a to p. The id comes from the folder's path, so it stays the same while
you load it from the same folder.

**Firefox (128 or later).** Open `about:debugging#/runtime/this-firefox`, choose
**Load Temporary Add-on…** and pick `apps/extension/dist/firefox/manifest.json`. Copy
the **Internal UUID** shown for it. Then open `about:addons`, open SafeGuard Gateway,
and under **Permissions** allow access to `127.0.0.1` and the chat sites; Firefox
asks for host permissions separately. A temporary add-on is removed when Firefox
quits. Check the Internal UUID after each load and update `extension_ids` if it
changed.

**Allow the extension in the gateway.** The gateway refuses every browser origin it
was not told about. Add the id under `[proxy]` in `~/.safeguard/config.toml` and
restart the gateway:

```toml
[proxy]
extension_ids = [
  "abcdefghijklmnopabcdefghijklmnop",      # Chrome, Edge, Brave: the ID  # pragma: allowlist secret
  "0b5e7a4c-1d2f-4e3a-9b8c-7d6e5f4a3b2c",  # Firefox: the Internal UUID  # pragma: allowlist secret
]
```

The values above are examples; use your own. Each id allows exactly its own origin
(`chrome-extension://<id>` or `moz-extension://<uuid>`); the chat sites themselves
stay refused. If the id is missing, the extension blocks each send and its notice
shows the line to add.

**Use.** With `uv run safeguard serve` running, write in the site's box and send as
usual. The extension masks the draft first, puts the masked text in the box, then
sends it; a short notice says how many values were pseudonymised. If the gateway is
not running, nothing is sent and the notice says so. Restored values in replies have
a dotted underline; placeholders it could not restore, or that sit in a link, stay
visible with a red wavy underline. The toolbar badge counts the values masked in the
tab; the popup shows the gateway's status, the counts by type, **Clear this tab's
session**, and a link to the dashboard.

Masking reduces exposure; it is not a guarantee. Read the limits in
[SECURITY.md](SECURITY.md) before a pilot: the site's own page can read what you type
before it is masked, and a change to a site's page can stop the extension from
intercepting a send.

Browser tests (Chromium, local fixture pages, no live site):

```bash
SAFEGUARD_E2E=1 uv run pytest tests/e2e/test_extension.py -m e2e --no-cov -s
```

They need port 8787 free (the extension only talks to that port) and write
screenshots to `tests/e2e/artifacts/extension/`.

### Claude Code

Open the repository in Claude Code. It reads `CLAUDE.md`, the hooks in
`.claude/settings.json`, the subagents in `.claude/agents/` and the MCP servers
in `.mcp.json`. The GitHub MCP server reads a token from the `GITHUB_PAT`
environment variable:

```bash
export GITHUB_PAT=...   # a fine-grained token for this repository only
```

Never write the token into `.mcp.json` or any other file in the repository.

## Pilot installers (from Phase 6)

Pilot builds are unsigned until code-signing is approved (see
[APPROVAL_NEEDED.md](APPROVAL_NEEDED.md)), so each operating system warns on
first launch.

- macOS: right-click the app in Applications, choose Open, then Open again.
  This is needed once.
- Windows: on the SmartScreen screen, click "More info", then "Run anyway".
  This is needed once.

Tell pilot users about this step before they install, so the warning does not
look like an attack.
