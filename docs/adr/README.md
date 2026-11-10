# Architecture decision records

One file per decision, numbered in order. Format: Status, Context, Decision,
Consequences. A decision is changed by a new ADR that supersedes the old one,
never by editing history.

| # | Decision | Status |
|---|----------|--------|
| [0001](0001-placeholder-format.md) | Placeholder format and how values map to placeholders | Accepted |
| [0002](0002-mapping-lifetime.md) | Where the placeholder map lives and for how long | Accepted |
| [0003](0003-fuzzy-rehydration.md) | Limits of tolerant rehydration | Accepted |
| [0004](0004-long-documents-and-streaming.md) | Long documents, chunking and streamed rehydration | Accepted |
| [0005](0005-build-order.md) | Proxy before extension | Accepted |
| [0006](0006-no-egress-enforcement.md) | Enforcing "no network in the engine" and offline model loading | Accepted |
| [0007](0007-ci-minutes.md) | CI matrix and the free-minutes budget | Accepted |
| [0008](0008-naming-and-license.md) | Package names and the product's own license | Accepted |
| [0009](0009-dev-tooling.md) | MCP servers, hooks and scanners used while building | Accepted |
| [0010](0010-guard-hardening.md) | Guards fail closed, and every layer is checked by the next | Accepted |
| [0011](0011-detection-engine-shape.md) | Shape of the detection engine | Accepted |
| [0012](0012-ner-through-presidio-offline.md) | NER through Presidio and spaCy, strictly offline | Accepted |
| [0013](0013-fail-safe-detection.md) | Fail-safe detection: clean text, digit runs, nothing left uncovered | Accepted |
| [0014](0014-placeholder-grammar.md) | One placeholder grammar for mask and rehydrate | Accepted |
| [0015](0015-gliner-offline.md) | GLiNER for Armenian and other text, strictly offline | Accepted |
| [0016](0016-ner-input-normalisation.md) | What the NER models read, and a recognizer for dates | Accepted |
| [0017](0017-parser-design-and-source-mapping.md) | Document parsers: one walk per format, text units and source positions | Accepted |
| [0018](0018-redacted-write-back.md) | Redacted copies: what each format keeps, replaces and drops | Accepted |
| [0019](0019-proxy-api-and-request-flow.md) | Proxy API surface and request flow | Accepted |
| [0020](0020-upstream-adapters-and-keys.md) | Upstream adapters, configuration and key handling | Accepted |
| [0021](0021-audit-chain-and-retention.md) | Audit log: hash chain, device key and retention | Accepted |
| [0022](0022-audit-export-format.md) | Audit export: signed JSON and a text PDF summary | Accepted |
| [0023](0023-shape-and-keyword-recognizers.md) | Shape and keyword recognizers for values that fail validation | Accepted |
| [0024](0024-web-ui.md) | Web UI: mask first, send through the gateway, restore in the browser | Accepted |
| [0025](0025-session-aware-chat.md) | Session-aware chat routes: the model and the audit log keep the real types | Accepted |
