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
