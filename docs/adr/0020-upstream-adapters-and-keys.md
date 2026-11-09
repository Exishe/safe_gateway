# 0020. Upstream adapters, configuration and key handling

- Status: Accepted
- Date: 2026-11-09
- Refines: ADR 0006 point 1 (`gateway/cloud/` is the only package that makes outbound calls)

## Context

The proxy (ADR 0019) forwards masked requests to the model the customer chose:
Anthropic, OpenAI, Azure OpenAI, or a local Ollama. Hard rule 3 allows outbound
requests only from `gateway/cloud/`, and only to the upstream the user
configured. API keys must come from the OS keychain (docs/SECURITY.md, design
goal 4), never from files or the environment.

## Decision

### Adapters

1. `gateway/cloud/upstream.py` holds one `Upstream` class over
   `httpx.AsyncClient`, parameterised by provider:

   | provider | API shape | default `base_url` | path appended | key header | local |
   |----------|-----------|--------------------|---------------|------------|-------|
   | `anthropic` | Anthropic | `https://api.anthropic.com` | `/v1/messages` | `x-api-key`, plus `anthropic-version: 2023-06-01` | no |
   | `openai` | OpenAI | `https://api.openai.com/v1` | `/chat/completions` | `Authorization: Bearer` | no |
   | `azure_openai` | OpenAI | none, required | `/openai/deployments/{model}/chat/completions?api-version={api_version}` | `api-key` | no |
   | `ollama` | OpenAI | `http://127.0.0.1:11434/v1` | `/chat/completions` | none | yes |

   Ollama is reached through its OpenAI-compatible endpoint. `api_version`
   defaults to `2024-10-21` (Azure's GA version); Azure's newer `/openai/v1`
   API is not used yet.
2. **Only the configured base URL.** The URL is built from the configuration
   alone; nothing in a request body or header changes it. Redirects are not
   followed (`follow_redirects=False`): a 3xx answer is a 502, so an upstream
   cannot bounce a masked prompt, or a key, to another host. `trust_env=False`:
   proxy variables, `.netrc` and certificate variables in the environment are
   ignored, so the environment can neither reroute traffic nor add
   credentials. TLS certificates are verified.
3. **`base_url` rules.** `https` is required, except for a loopback host
   (`127.0.0.1`, `localhost`, `::1`), which may use `http` (local Ollama). No
   user info, query string or fragment, so a key cannot hide in the URL.
4. The adapter turns every httpx failure into its own `UpstreamError` with a
   status: 504 for a timeout, 502 for a network failure or a redirect. Its
   messages name the host, never a header or a key. Timeouts: 10 s to connect,
   300 s between bytes read (models think before streaming), 60 s to write.
5. The adapter sends only its own headers: content type, user agent, the key
   header and, for Anthropic, the API version.

### Keys

6. Keys live in the OS keychain through `keyring`, service `safeguard-gateway`,
   user name `api-key:<provider>`. They are set with the keyring command that
   ships with the dependency, which prompts for the value so it never lands in
   shell history: `uv run keyring set safeguard-gateway api-key:openai`. A
   `safeguard keys` command can come later.
7. `safeguard serve` reads the key once at start-up, holds it in memory in an
   `ApiKey` wrapper whose `repr` and `str` hide it, and refuses to start when a
   provider that needs one has none. Ollama needs no key, and none is read for
   it. A rotated key takes effect on restart.
8. Keys are never read from the configuration file, the environment, or a
   request: the client's own `Authorization` header is dropped (ADR 0019).

### Configuration

9. `~/.safeguard/config.toml`, or `$SAFEGUARD_HOME/config.toml`, read with the
   standard library's `tomllib`. Without the file the defaults apply: provider
   `ollama` on `http://127.0.0.1:11434/v1`, so nothing leaves the machine until
   the user chooses a cloud provider.

   ```toml
   provider = "openai"            # anthropic | openai | azure_openai | ollama
   base_url = "https://api.openai.com/v1"   # optional, the provider's default
   model = "gpt-4o-mini"          # optional; required for azure_openai (the deployment)
   api_version = "2024-10-21"     # azure_openai only
   gazetteer = "~/clients.csv"    # optional
   retention_days = 30            # optional: purge audit rows older than this (default: keep all)

   [thresholds]                   # optional, per entity type, 0..1
   PERSON = 0.6

   [proxy]
   max_body_bytes = 4194304
   cors_allow_origins = []        # Phase 7: the extension's origin
   ```

10. **The file never holds keys.** A field name containing `key`, `token`,
    `secret`, `password`, `passwd`, `credential`, `auth` or `bearer`, at any
    level (entity type names under `[thresholds]` excepted), is refused with an
    error that points at the keychain. So is a string value that starts like an
    API key (`sk-`). Every other unknown field is refused too, so a typo is an
    error rather than a silently ignored setting.

## Consequences

- A corporate HTTPS proxy, or a TLS-inspecting one with its own certificate,
  does not work until an explicit setting is added for it.
- An OpenAI-shaped client can only reach OpenAI-shaped providers (ADR 0019).
- The Ollama adapter is tested against a mocked endpoint; a run against a real
  Ollama is a manual check on the developer's machine.
