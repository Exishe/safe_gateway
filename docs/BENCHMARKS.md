# Benchmarks

No dataset measurements yet. Names, organisations, addresses and dates need the
NER step (Phase 1b); recall on ai4privacy OpenPII is measured in Phase 1c.

## Structured identifiers (asserted, not measured)

Since Phase 1a, the property tests in `tests/property/test_structured_detection.py`
assert that every generated value is detected at its exact offsets. This is a
test result on synthetic data, not a recall figure:

| Type | Generated formats | Assertion |
|------|-------------------|-----------|
| Card | Visa 13/16/19, Mastercard 51-55 and 2-series, Amex, Discover; space, hyphen, dot or no separator; ASCII, Arabic-Indic, full-width and Devanagari digits | 100% |
| US SSN | valid area/group/serial; 3-2-4 with space, hyphen, dot or none | 100% |
| IBAN | all 127 registry countries; grouped by 4 or compact | 100% |
| RU INN | 10 and 12 digits | 100% |
| RU SNILS | 123-456-789 01, spaced, or plain | 100% |
| Email | Faker addresses (en_US, ru_RU) | 100% |
| IP address | any IPv4 or IPv6 except the unspecified address | 100% |
| URL with token | http(s) URLs with a secret query parameter | 100% |
| Phone | example numbers of US, GB, DE, FR, IT, ES, NL, RU, AM, mobile and fixed line; E.164, international and national formats; ASCII, Arabic-Indic and full-width digits | 100% |

CI runs these with the `ci` Hypothesis profile (500 derandomized examples each).

Rules for this file:

- A number goes here only after it was measured. Each row names the dataset
  and its version, the exact command, the commit, and the date.
- Structured identifiers must reach 100% on generated data; that is asserted
  by the property tests, not just reported here.
- Names, organisations, addresses and health details are measured and
  reported per language (English, Russian, Armenian), never rounded up.
- ai4privacy OpenPII is licensed CC-BY-4.0; cite it wherever these numbers are
  shown to customers.

## NER throughput (measured, not asserted)

Measured 2026-11-09 in a 4-CPU Linux container, spaCy 3.8.18, models
3.8.0 already loaded, NER component only (Phase 1b):

| Model | Load | Per 100 KB | 1 MB |
|-------|------|------------|------|
| en_core_web_lg | 2.1 s | 0.84 s | 9.8 s |
| ru_core_news_lg | 1.9 s | 0.77 s | 8.2 s |

With NER off, 1 MB of structured-only scanning takes about 0.5 s.

## Recall per entity type

| Entity type | Language | Dataset (version) | Recall | Precision | Command | Commit | Date |
|-------------|----------|-------------------|--------|-----------|---------|--------|------|
