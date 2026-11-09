# Phase 1: Detection engine (outline)

> Outline only. At the start of the phase run `superpowers:brainstorming`, then
> `superpowers:writing-plans` to turn this into a task-by-task plan in this file.

**Goal:** `safeguard scan file.txt` prints every detected entity with type, span, score and recognizer.

**Scope**
- `gateway/engine/types.py`: `Span(start, end, entity_type, score, recognizer)`, the `EntityType` enum from the brief (PERSON, ORG, LOCATION, ADDRESS, EMAIL, PHONE, DATE_OF_BIRTH, SSN, PASSPORT, DRIVER_LICENSE, NATIONAL_ID, CREDIT_CARD, IBAN, BANK_ACCOUNT, IP_ADDRESS, MEDICAL_RECORD_NUMBER, HEALTH_CONDITION, CASE_NUMBER, URL_WITH_TOKEN), plus `LITERAL` (ADR 0001). Add DATE and small geographic units before anything is called "PHI" (HIPAA Safe Harbor).
- `gateway/engine/recognizers/`: one file per structured type. Validation: Luhn for cards, mod-97 for IBAN, SSN area/group rules, E.164 and national phone formats for US, UK, EU, RU, AM, RU INN/SNILS checksums, AM passport and social card formats. Accept separators, spaces, Unicode digits, and OCR noise (`O` for `0`).
- Presidio analyzer with spaCy `en_core_web_lg` and `ru_core_news_lg`; GLiNER2-PII (fallback `urchade/gliner_multi_pii-v1`) as an extra recognizer, loaded lazily from `~/.safeguard/models`, offline (ADR 0006). NER windows per ADR 0004.
- `gateway/engine/merge.py`: union, longest span wins, then max score; per-type thresholds from config.
- Customer gazetteer: CSV of names and codenames that are always masked.
- `gateway/cloud/models.py`: `safeguard models pull` (the only place models are downloaded).
- `detect(text, lang="auto") -> list[Span]`; CLI `safeguard scan FILE`.

**Exit criteria**
- Hypothesis strategies for every structured format; 100% detection asserted on generated data.
- Recall per entity type measured on ai4privacy OpenPII and recorded in `docs/BENCHMARKS.md` with dataset version and date (attribution, CC-BY-4.0).
- `leak-hunter` run once; every bypass it finds is a failing test, then fixed.
- The commit hook's PII scan switches to the engine itself (dogfooding).
- Tag `v0.1.0`.

**Open questions for the phase brainstorm:** GLiNER2-PII vs `gliner_multi_pii-v1` as the default given 8 GB RAM; how to detect language for mixed en/ru/hy text; threshold defaults per type.
