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
