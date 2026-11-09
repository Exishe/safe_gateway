# Benchmarks

Recall on ai4privacy OpenPII is measured below (English with NER; other languages
structured only). Russian and Armenian have no figure: no labelled dataset covers them.

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

Measured by the Phase 1b build agent at commit 84783e2 with an ad-hoc timing
script (not in the repo); re-measure with a committed script before quoting.

### GLiNER (Phase 1c, measured, not asserted)

Measured 2026-11-09 in the same 4-CPU Linux container, CPU only (torch 2.15.0+cpu,
gliner 0.2.30, transformers 5.22.2), `urchade/gliner_multi_pii-v1` at commit 1fcf13e,
engine at commit e6e28e4, windows of 128 words. Whole `detect()`, models loaded and
warmed first, one timed pass; peak RSS of the process (`ru_maxrss`). Text: seeded Faker
paragraphs with names and addresses (seed 20261109), about 100 KB of UTF-8 (an Armenian
letter is 2 bytes, so the Armenian text has about half as many characters). Commands, each
in a fresh process:

    uv run python -m tools.benchmark_gliner --lang hy
    uv run python -m tools.benchmark_gliner --lang en

| Text | Config | Models | Load + warm | Per 100 KB | Peak RSS |
|------|--------|--------|-------------|------------|----------|
| Armenian (hy_AM) | default (`gliner="auto"`) | GLiNER | 29.9 s | 75.7 s | 2802 MiB |
| English (en_US) | `gliner="all"` | spaCy en + GLiNER | 19.1 s | 136.4 s | 3475 MiB |

GLiNER is about two orders of magnitude slower than spaCy on the CPU: a 1 MB Armenian
document would take over 12 minutes. `gliner="all"` is therefore not a default.


## Recall per entity type

### Run: ai4privacy OpenPII 500k, English (measured)

| Item | Value |
|------|-------|
| Dataset | ai4privacy/open-pii-masking-500k-ai4privacy, validation shard `data/validation/test.jsonl` (SHA-256 4e908e60d8d88f90015301e1ab4a8b7899ec713f1fc903860e1d9e0b91677ebf) |
| Revision | 506996d625ed970a0063432daf6007cf4a3a48e3 (HEAD of the dataset repository on 2026-11-09) |
| License | CC-BY-4.0 (card: `license: other`, `license_name: cc-by-4.0`). Attribution: "Open PII Masking 500k", Ai4Privacy, https://huggingface.co/datasets/ai4privacy/open-pii-masking-500k-ai4privacy |
| Sample | 10,000 English records of 30,160 in the shard, seed 20261109 (`random.Random(seed).sample`) |
| Command | `uv run python -m tools.benchmark_openpii --sample 10000 --seed 20261109 --json docs/benchmarks/openpii-500k.json --markdown docs/benchmarks/openpii-500k.md` (the committed `.md` adds a header to the tables the command writes) |
| Commit | 6b27eb43b422894f049e89932b32d2778a0a7e12 (tree clean). Previous run: 79d82b15215a812d69ad4e70b2e46b5a971e8b12 (harness), same engine as bc8662e |
| Date | 2026-11-09 |
| Engine | `detect(text, "auto", EngineConfig(ner="auto"))`: structured recognizers, the shape and keyword recognizers of ADR 0016, and spaCy `en_core_web_lg` 3.8.0 through Presidio. No GLiNER yet. Default phone regions. |
| Run time | English 39.1 s; all eight languages 70 s in a 4-CPU container, single process |

Definitions. Masking recall: share of gold spans with at least 90% of their characters
inside the union of any detected spans (what matters for privacy: the value was masked,
whatever we called it). Typed recall: the same, counting only spans of the EntityType the
label maps to. Precision: share of detected spans of that type that overlap any gold span.
It is a lower bound, because the dataset labels no organisations and no dates of the kind
the NER finds in running text, so an unmatched span is not necessarily a false alarm.
Percentages are truncated to one decimal, never rounded up.

Label mapping (dataset label to EntityType). The engine has no type for the labels in the
second table; they are reported, not dropped.

| Dataset label | EntityType accepted for typed recall |
|---------------|--------------------------------------|
| GIVENNAME, SURNAME | PERSON (labelled separately; one PERSON span over both counts for each) |
| CITY | LOCATION |
| STREET, BUILDINGNUM, ZIPCODE | ADDRESS or LOCATION |
| TELEPHONENUM | PHONE |
| EMAIL | EMAIL |
| DATE | DATE or DATE_OF_BIRTH |
| IDCARDNUM, TAXNUM | NATIONAL_ID |
| SOCIALNUM | SSN or NATIONAL_ID |
| PASSPORTNUM | PASSPORT |
| CREDITCARDNUMBER | CREDIT_CARD |
| DRIVERLICENSENUM | DRIVER_LICENSE |
| TIME, TITLE, AGE, SEX, GENDER | none: not covered |

English, per dataset label (gold spans in the sample; the last column is the previous run):

| Dataset label | Maps to | Gold spans | Masking recall | Typed recall | Previous: masking / typed |
|---------------|---------|-----------:|---------------:|-------------:|--------------------------:|
| GIVENNAME | PERSON | 7977 | 83.6% | 66.2% | 83.6% / 66.2% |
| SURNAME | PERSON | 3061 | 85.4% | 70.8% | 85.4% / 70.8% |
| CITY | LOCATION | 1648 | 86.6% | 46.1% | 86.6% / 46.1% |
| STREET | ADDRESS/LOCATION | 1183 | 78.2% | 57.7% | 78.2% / 57.7% |
| BUILDINGNUM | ADDRESS/LOCATION | 1030 | 37.6% | 18.6% | 37.6% / 18.6% |
| ZIPCODE | ADDRESS/LOCATION | 377 | 30.2% | 2.3% | 30.2% / 2.3% |
| DATE | DATE/DATE_OF_BIRTH | 1491 | 64.3% | 59.8% | 64.3% / 59.8% |
| EMAIL | EMAIL | 1155 | 99.1% | 98.5% | 99.1% / 98.5% |
| TELEPHONENUM | PHONE | 1591 | 85.9% | 76.7% | 43.4% / 30.7% |
| CREDITCARDNUMBER | CREDIT_CARD | 179 | 84.3% | 65.9% | 27.3% / 8.9% |
| SOCIALNUM | NATIONAL_ID/SSN | 229 | 81.6% | 22.2% | 81.2% / 21.8% |
| TAXNUM | NATIONAL_ID | 259 | 83.7% | 11.1% | 83.3% / 10.8% |
| IDCARDNUM | NATIONAL_ID | 803 | 56.5% | 35.8% | 21.1% / 0.3% |
| PASSPORTNUM | PASSPORT | 367 | 98.3% | 45.2% | 53.1% / 0.0% |
| DRIVERLICENSENUM | DRIVER_LICENSE | 196 | 28.0% | 3.5% | 20.4% / 0.0% |

Not covered (no EntityType; the masking figure is incidental, from spans of other types):

| Dataset label | Gold spans | Incidentally masked |
|---------------|-----------:|--------------------:|
| TITLE | 1098 | 32.3% |
| TIME | 1630 | 6.1% |
| AGE | 535 | 44.4% |
| SEX | 248 | 11.6% |
| GENDER | 199 | 6.0% |

English, precision per detected type:

| Detected type | Detected spans | Precision (lower bound) |
|---------------|---------------:|------------------------:|
| PERSON | 6726 | 98.2% |
| LOCATION | 2093 | 95.8% |
| DATE | 2054 | 83.6% |
| ORG | 2576 | 73.3% |
| EMAIL | 1149 | 100.0% |
| PHONE | 1773 | 100.0% |
| CREDIT_CARD | 121 | 100.0% |
| SSN | 109 | 100.0% |
| NATIONAL_ID | 399 | 100.0% |
| PASSPORT | 166 | 100.0% |
| DRIVER_LICENSE | 8 | 100.0% |

How to read this.

- The dataset's values are synthetic and often invalid by construction. A one-off check of
  the full English validation shard (not part of the committed command) found 513 of 575
  gold card numbers failing the Luhn check. Since ADR 0016, Luhn-invalid numbers of an
  exact card shape, `+` numbers that libphonenumber rejects, and numbers after a phone
  keyword are masked at lower scores (0.55 to 0.85), so they are found; a customer who
  raises a type's threshold above those scores gets the validated-only behaviour back. The
  property tests in `tests/property/` still assert 100% on valid generated identifiers;
  this benchmark does not show how real documents behave.
- PASSPORT, DRIVER_LICENSE and a general NATIONAL_ID are found only by shape (`AA9999999`,
  `AA99999AA`) or after a document keyword in English or Russian (ADR 0016), so typed
  recall stays low where the dataset writes a bare code with no keyword. ADDRESS parts
  have no recognizer; any masking recall there comes from other recognizers.
- The gap between masking and typed recall for phones and cards means those values were
  covered, at least in part, by spans of another type; this run does not break down which.
- Names and places are NER results from a general-purpose model, measured here for the
  first time. The gap between masking and typed recall for PERSON, CITY and STREET means
  those values were covered by spans of another type; this run does not break down which.

### Other OpenPII languages: structured recognizers only (measured)

The same run, commit and revision, for the other languages in the shard. There is no NER
model for these languages in the engine, so run with `EngineConfig(ner="off")`: names,
places, dates and addresses are not detected and are not tabulated here (GIVENNAME,
SURNAME, CITY, DATE and STREET read 0.0% in every one of these languages; every row is in
`docs/benchmarks/openpii-500k.md`). Cells are masking recall / typed
recall (gold spans). Records used: de, es, fr, it 10,000 each; nl 5,347, hi 6,938,
te 5,434 (all the shard has for them).

| Dataset label | de | es | fr | it | nl | hi | te |
|---|---:|---:|---:|---:|---:|---:|---:|
| EMAIL | 97.5% / 97.5% (1306) | 99.2% / 99.2% (1116) | 98.8% / 98.8% (1311) | 99.3% / 99.3% (1152) | 98.5% / 98.5% (602) | 99.0% / 99.0% (715) | 98.9% / 98.9% (566) |
| TELEPHONENUM | 80.3% / 78.6% (1641) | 82.8% / 80.8% (1468) | 83.3% / 81.7% (1695) | 84.5% / 82.7% (1595) | 84.3% / 82.2% (787) | 78.1% / 74.8% (1162) | 72.7% / 70.4% (903) |
| CREDITCARDNUMBER | 79.4% / 79.0% (253) | 73.3% / 72.9% (218) | 74.5% / 74.0% (216) | 76.9% / 76.1% (260) | 78.6% / 78.6% (136) | 73.5% / 73.5% (136) | 61.2% / 60.0% (165) |
| SOCIALNUM | 0.0% / 0.0% (275) | 47.7% / 6.5% (199) | 28.8% / 3.2% (218) | 0.0% / 0.0% (107) | 97.6% / 48.2% (85) | 64.6% / 8.0% (150) | 66.0% / 7.8% (203) |
| TAXNUM | 11.9% / 0.8% (335) | 34.2% / 4.3% (277) | 31.0% / 4.6% (238) | 0.4% / 0.4% (250) | 95.9% / 0.0% (148) | 67.5% / 8.3% (216) | 66.9% / 9.3% (224) |
| IDCARDNUM | 26.5% / 1.5% (833) | 2.2% / 2.2% (848) | 7.9% / 0.7% (793) | 4.3% / 4.3% (902) | 96.9% / 6.4% (421) | 1.1% / 1.1% (537) | 1.4% / 1.4% (556) |
| PASSPORTNUM | 0.4% / 0.0% (432) | 50.8% / 50.8% (393) | 29.7% / 29.4% (363) | 51.0% / 50.7% (333) | 0.0% / 0.0% (192) | 99.5% / 99.1% (238) | 92.2% / 91.1% (259) |
| DRIVERLICENSENUM | 38.2% / 0.0% (235) | 0.5% / 0.0% (191) | 20.5% / 0.0% (195) | 72.7% / 0.0% (158) | 1.1% / 1.1% (87) | 0.0% / 0.0% (163) | 0.0% / 0.0% (171) |

The high masking figures for nl (SOCIALNUM, TAXNUM, IDCARDNUM) sit next to typed recall
of 48.2%, 0.0% and 6.4%: the values were covered by spans of other types, which this run
does not break down. Read typed recall as the figure for the type itself. The document
keywords are English and Russian only, so passports, ID cards and driving licences in
these languages are found by shape alone.

### Changes since the previous run

Previous run: commit 79d82b15215a812d69ad4e70b2e46b5a971e8b12, 2026-11-09 (engine
unchanged up to bc8662e). This run: commit 6b27eb43b422894f049e89932b32d2778a0a7e12,
2026-11-09, same command, sample and seed. What changed: the six shape and keyword
recognizers of ADR 0016 (`+` phones, keyword-gated phones, Luhn-invalid card shapes,
`AA9999999` passports, `AA99999AA` ID cards, keyword-gated document numbers). Masking
recall, previous -> this run:

| Dataset label | en | de | es | fr | it | nl | hi | te |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| TELEPHONENUM | 43.4% -> 85.9% | 46.0% -> 80.3% | 37.5% -> 82.8% | 38.9% -> 83.3% | 37.8% -> 84.5% | 44.3% -> 84.3% | 34.8% -> 78.1% | 39.0% -> 72.7% |
| CREDITCARDNUMBER | 27.3% -> 84.3% | 12.2% -> 79.4% | 9.6% -> 73.3% | 10.1% -> 74.5% | 12.6% -> 76.9% | 7.3% -> 78.6% | 11.7% -> 73.5% | 10.9% -> 61.2% |
| PASSPORTNUM | 53.1% -> 98.3% | 0.0% -> 0.4% | 0.0% -> 50.8% | 0.2% -> 29.7% | 0.0% -> 51.0% | 0.0% -> 0.0% | 0.0% -> 99.5% | 0.0% -> 92.2% |
| IDCARDNUM | 21.1% -> 56.5% | 0.1% -> 26.5% | 0.0% -> 2.2% | 7.1% -> 7.9% | 0.0% -> 4.3% | 96.4% -> 96.9% | 0.0% -> 1.1% | 0.0% -> 1.4% |
| DRIVERLICENSENUM | 20.4% -> 28.0% | 37.8% -> 38.2% | 0.0% -> 0.5% | 20.5% -> 20.5% | 72.7% -> 72.7% | 0.0% -> 1.1% | 0.0% -> 0.0% | 0.0% -> 0.0% |

No label lost masking or typed recall in any language. Smaller gains: en SOCIALNUM
81.2% -> 81.6% and TAXNUM 83.3% -> 83.7%; de TAXNUM 11.6% -> 11.9%; fr SOCIALNUM
27.5% -> 28.8% and TAXNUM 30.2% -> 31.0%; it TAXNUM 0.0% -> 0.4%. Every other label,
names, places, dates and e-mail included, is unchanged. Precision (lower bound) stays
100.0% for every structured type in every language; the en NER types moved by at most
0.5 points (DATE 84.1% -> 83.6%) because longer structured spans now win some overlaps.
The prototype rules of the miss analysis were not run as part of this command; the
figures above are the shipped recognizers.

### Russian and Armenian: not measured

- Russian. No Open PII release has Russian. The 500k release covers en, fr, de, es, it,
  nl, hi and te. The newer releases list 23 languages (pii-masking-openpii-1m, revision
  ecfdc547f4a0955600cfe6ab98ba2a162207fcc0) and 30 languages (pii-masking-openpii-1.5m,
  revision a785eb528e28be2693c3718a27e066970de5dadb), and neither lists Russian. The
  `ru_core_news_lg` model is installed and used by `detect()`, but it has no recall
  figure here. No number is given for Russian until a labelled Russian dataset with a
  permitted license is found and approved (hard rules 1 and 5).
- Armenian. Not in any Open PII release either. Since Phase 1c, Armenian text goes to
  GLiNER (ADR 0015), but no labelled Armenian dataset exists, so there is no recall
  figure; only throughput is measured (below).
- The recall figures above were measured before GLiNER joined the engine and do not
  include it. With the default `gliner="auto"`, GLiNER reads no Latin-script text, so
  the English figures stand. Hindi and Telugu text (Devanagari, Telugu script) is now
  routed to GLiNER when NER is on; their rows above were run with NER off and have not
  been re-measured with GLiNER.
