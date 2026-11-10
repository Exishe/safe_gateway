# Approval needed

Nothing here is needed to build or pilot the product. Each item has a free path
that works until it is approved. Claude Code adds a row whenever a step would
cost money, and keeps working on the free path. Items are approved one at a
time.

| Item | Typical cost | When it matters | Free path until approved | Status |
|------|--------------|-----------------|--------------------------|--------|
| Anthropic / OpenAI API usage at runtime | per token | when the product talks to a real cloud model | Customers bring their own key; development and tests use local Ollama | not needed (BYOK) |
| Apple Developer Program (signing and notarisation) | $99 a year | shipping a `.dmg` that opens without a Gatekeeper warning | Unsigned pilot builds; right-click, Open once (INSTALL.md) | not approved |
| Windows code-signing certificate | $200 to $400 a year (OV), more for EV | SmartScreen warning on first run of the `.msi` | Unsigned pilot builds with the "More info, Run anyway" step; Azure Trusted Signing later | not approved |
| Chrome Web Store developer account | $5 once | publishing the extension | Load unpacked for pilots; Firefox AMO is free | not approved |
| Domain name | about $10 a year | marketing site, download links | GitHub Pages or a `*.vercel.app` subdomain | not approved |
| PyMuPDF commercial license | quote only | only if PyMuPDF were used | Not needed: pypdf, pdfplumber and Tesseract | not needed |
| Commercial PII services (AWS Comprehend, Azure PII, Google DLP) | per request | never, by design | Local Presidio and GLiNER | not needed |
| Larger local model for a fully offline tier | customer hardware | customers who refuse any cloud | Ollama with 7B+ models on the customer's machine | customer's cost |
| Legal review of GDPR and HIPAA wording | lawyer hours | before the first regulated-industry contract | Conservative wording and a `legal:compliance-check` first pass | not approved |
