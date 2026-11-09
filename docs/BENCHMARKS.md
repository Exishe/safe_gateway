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


## Recall per entity type

### Run: ai4privacy OpenPII 500k, English (measured)

| Item | Value |
|------|-------|
| Dataset | ai4privacy/open-pii-masking-500k-ai4privacy, validation shard `data/validation/test.jsonl` (SHA-256 4e908e60d8d88f90015301e1ab4a8b7899ec713f1fc903860e1d9e0b91677ebf) |
| Revision | 506996d625ed970a0063432daf6007cf4a3a48e3 (HEAD of the dataset repository on 2026-11-09) |
| License | CC-BY-4.0 (card: `license: other`, `license_name: cc-by-4.0`). Attribution: "Open PII Masking 500k", Ai4Privacy, https://huggingface.co/datasets/ai4privacy/open-pii-masking-500k-ai4privacy |
| Sample | 10,000 English records of 30,160 in the shard, seed 20261109 (`random.Random(seed).sample`) |
| Command | `uv run python -m tools.benchmark_openpii --sample 10000 --seed 20261109 --json docs/benchmarks/openpii-500k.json --markdown docs/benchmarks/openpii-500k.md` (the committed `.md` adds a header to the tables the command writes) |
| Commit | 79d82b15215a812d69ad4e70b2e46b5a971e8b12 (harness; tree clean) |
| Date | 2026-11-09 |
| Engine | `detect(text, "auto", EngineConfig(ner="auto"))`: structured recognizers plus spaCy `en_core_web_lg` 3.8.0 through Presidio. No GLiNER yet. Default phone regions. |
| Run time | English 37.6 s; all eight languages 70 s in a 4-CPU container, single process |

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

English, per dataset label (gold spans in the sample):

| Dataset label | Maps to | Gold spans | Masking recall | Typed recall |
|---------------|---------|-----------:|---------------:|-------------:|
| GIVENNAME | PERSON | 7977 | 83.6% | 66.2% |
| SURNAME | PERSON | 3061 | 85.4% | 70.8% |
| CITY | LOCATION | 1648 | 86.6% | 46.1% |
| STREET | ADDRESS/LOCATION | 1183 | 78.2% | 57.7% |
| BUILDINGNUM | ADDRESS/LOCATION | 1030 | 37.6% | 18.6% |
| ZIPCODE | ADDRESS/LOCATION | 377 | 30.2% | 2.3% |
| DATE | DATE/DATE_OF_BIRTH | 1491 | 64.3% | 59.8% |
| EMAIL | EMAIL | 1155 | 99.1% | 98.5% |
| TELEPHONENUM | PHONE | 1591 | 43.4% | 30.7% |
| CREDITCARDNUMBER | CREDIT_CARD | 179 | 27.3% | 8.9% |
| SOCIALNUM | NATIONAL_ID/SSN | 229 | 81.2% | 21.8% |
| TAXNUM | NATIONAL_ID | 259 | 83.3% | 10.8% |
| IDCARDNUM | NATIONAL_ID | 803 | 21.1% | 0.3% |
| PASSPORTNUM | PASSPORT | 367 | 53.1% | 0.0% |
| DRIVERLICENSENUM | DRIVER_LICENSE | 196 | 20.4% | 0.0% |

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
| PERSON | 6731 | 98.2% |
| LOCATION | 2103 | 95.9% |
| DATE | 2108 | 84.1% |
| ORG | 2593 | 73.5% |
| EMAIL | 1149 | 100.0% |
| PHONE | 1227 | 100.0% |
| CREDIT_CARD | 21 | 100.0% |
| SSN | 144 | 100.0% |
| NATIONAL_ID | 127 | 100.0% |

How to read this.

- These are the engine as shipped in Phase 1b, on a dataset whose values are synthetic and
  often invalid by construction. The structured recognizers validate (Luhn for cards,
  area rules for SSNs, libphonenumber for phones), so synthetic numbers that fail those
  checks are not detected. A one-off check of the full English validation shard (not part
  of the committed command) found 513 of 575 gold card numbers failing the Luhn check. The
  property tests in `tests/property/` still assert 100% on valid generated identifiers;
  this benchmark does not contradict them, and it does not show how real documents behave.
- Types with no recognizer yet (PASSPORT, DRIVER_LICENSE, a general NATIONAL_ID, ADDRESS
  parts) have 0% or near-0% typed recall by construction; any masking recall there comes
  from other recognizers matching part of the value.
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
| TELEPHONENUM | 46.0% / 43.2% (1641) | 37.5% / 35.0% (1468) | 38.9% / 36.1% (1695) | 37.8% / 34.7% (1595) | 44.3% / 41.2% (787) | 34.8% / 30.4% (1162) | 39.0% / 36.4% (903) |
| CREDITCARDNUMBER | 12.2% / 11.8% (253) | 9.6% / 9.1% (218) | 10.1% / 9.7% (216) | 12.6% / 11.9% (260) | 7.3% / 7.3% (136) | 11.7% / 11.7% (136) | 10.9% / 9.6% (165) |
| SOCIALNUM | 0.0% / 0.0% (275) | 47.7% / 6.5% (199) | 27.5% / 3.2% (218) | 0.0% / 0.0% (107) | 97.6% / 48.2% (85) | 64.6% / 8.0% (150) | 66.0% / 7.8% (203) |
| TAXNUM | 11.6% / 0.5% (335) | 34.2% / 4.3% (277) | 30.2% / 3.7% (238) | 0.0% / 0.0% (250) | 95.9% / 0.0% (148) | 67.5% / 8.3% (216) | 66.9% / 9.3% (224) |
| IDCARDNUM | 0.1% / 0.0% (833) | 0.0% / 0.0% (848) | 7.1% / 0.0% (793) | 0.0% / 0.0% (902) | 96.4% / 0.0% (421) | 0.0% / 0.0% (537) | 0.0% / 0.0% (556) |
| PASSPORTNUM | 0.0% / 0.0% (432) | 0.0% / 0.0% (393) | 0.2% / 0.0% (363) | 0.0% / 0.0% (333) | 0.0% / 0.0% (192) | 0.0% / 0.0% (238) | 0.0% / 0.0% (259) |
| DRIVERLICENSENUM | 37.8% / 0.0% (235) | 0.0% / 0.0% (191) | 20.5% / 0.0% (195) | 72.7% / 0.0% (158) | 0.0% / 0.0% (87) | 0.0% / 0.0% (163) | 0.0% / 0.0% (171) |

The high masking figures for nl (SOCIALNUM, TAXNUM, IDCARDNUM) sit next to typed recall
of 48.2%, 0.0% and 0.0%: the values were covered by spans of other types, which this run
does not break down. Read typed recall as the figure for the type itself.

### Russian and Armenian: not measured

- Russian. No Open PII release has Russian. The 500k release covers en, fr, de, es, it,
  nl, hi and te. The newer releases list 23 languages (pii-masking-openpii-1m, revision
  ecfdc547f4a0955600cfe6ab98ba2a162207fcc0) and 30 languages (pii-masking-openpii-1.5m,
  revision a785eb528e28be2693c3718a27e066970de5dadb), and neither lists Russian. The
  `ru_core_news_lg` model is installed and used by `detect()`, but it has no recall
  figure here. No number is given for Russian until a labelled Russian dataset with a
  permitted license is found and approved (hard rules 1 and 5).
- Armenian. Not in any Open PII release either. There is no NER model for it and no
  figure.
- GLiNER is not part of the engine yet, so none of the figures above include it.
