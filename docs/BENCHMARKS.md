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

Since leak hunt 1c, `tests/property/test_shape_detection.py` does the same for the shape
recognizers of ADR 0023 (each value found at its exact offsets by its recognizer, and left
with no letter or digit unmasked by `detect()`):

| Type | Generated formats | Assertion |
|------|-------------------|-----------|
| `+` phone | `+99` (unassigned) and groups of 1-4 digits, 10-15 digits in all; gaps of one space, two spaces, dot, hyphen, slash or spaced hyphen; an optional bracketed group | 100% |
| Passport shape | two letters (both capitals or both lower case) and seven digits | 100% |
| ID card shape | two capitals, five digits, two capitals | 100% |
| Card shape | 16 digits starting 2-6, 15 starting 3, 19 starting 3-6; contiguous, or grouped by four with each gap one space, two spaces, a dot or a hyphen, mixed | 100% |

The date property test also generates the forms added in 1c: a quoted Russian day, and
English and Russian month names between hyphens or slashes.

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
| Commit | 2721ae41d7518187b0cb0db8d2bcf2b800e87ec2 (tree clean; the leak hunt 1d fixes). Previous run, for the deltas: 077f54580a57d9369a229bb11f000103bfc4049a (engine as in cbdca9a, the leak hunt 1c fixes), the figures committed in fa75c3f, same command, sample and seed |
| Date | 2026-11-10 (previous run 2026-11-09) |
| Engine | `detect(text, "auto", EngineConfig(ner="auto"))`: structured recognizers (the date recognizer of ADR 0016 included), the shape and keyword recognizers of ADR 0023 as amended for leak hunts 1c and 1d, spaCy `en_core_web_lg` 3.8.0 through Presidio (and `ru_core_news_lg` 3.8.0 on lines of an English segment with a Cyrillic word), and GLiNER `gliner_multi_pii-v1` only on lines holding letters of a script without a spaCy model or, since 1d, Cyrillic words in an English segment (ADR 0016 amended). Default phone regions. |
| Run time | English 70.3 s (previous run 97.9 s); the other seven languages 34.5 s (previous 42.0 s); a shared 4-CPU container, single process, so times are rough and the decrease is not attributed to any one change (the clean view and the date recognizer did get faster, ADR 0016 amended for 1d, points 6 and 8) |

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

English, per dataset label (gold spans in the sample; the last column is the previous run, 077f545):

| Dataset label | Maps to | Gold spans | Masking recall | Typed recall | 077f545: masking / typed |
|---------------|---------|-----------:|---------------:|-------------:|--------------------------:|
| GIVENNAME | PERSON | 7977 | 83.6% | 66.3% | 83.6% / 66.3% |
| SURNAME | PERSON | 3061 | 85.4% | 70.9% | 85.4% / 70.9% |
| CITY | LOCATION | 1648 | 86.6% | 46.1% | 86.6% / 46.1% |
| STREET | ADDRESS/LOCATION | 1183 | 78.2% | 57.7% | 78.2% / 57.7% |
| BUILDINGNUM | ADDRESS/LOCATION | 1030 | 37.6% | 18.6% | 37.6% / 18.6% |
| ZIPCODE | ADDRESS/LOCATION | 377 | 30.2% | 2.3% | 30.2% / 2.3% |
| DATE | DATE/DATE_OF_BIRTH | 1491 | 88.1% | 83.7% | 88.1% / 83.7% |
| EMAIL | EMAIL | 1155 | 99.1% | 98.5% | 99.1% / 98.5% |
| TELEPHONENUM | PHONE | 1591 | 85.9% | 76.7% | 85.9% / 76.7% |
| CREDITCARDNUMBER | CREDIT_CARD | 179 | 84.3% | 65.9% | 84.3% / 65.9% |
| SOCIALNUM | NATIONAL_ID/SSN | 229 | 81.6% | 22.2% | 81.6% / 22.2% |
| TAXNUM | NATIONAL_ID | 259 | 83.7% | 11.1% | 83.7% / 11.1% |
| IDCARDNUM | NATIONAL_ID | 803 | 56.6% | 35.9% | 56.5% / 35.8% |
| PASSPORTNUM | PASSPORT | 367 | 98.3% | 45.2% | 98.3% / 45.2% |
| DRIVERLICENSENUM | DRIVER_LICENSE | 196 | 29.5% | 4.0% | 29.0% / 4.0% |

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
| PERSON | 6730 | 98.1% |
| LOCATION | 2093 | 95.8% |
| DATE | 2375 | 85.7% |
| ORG | 2577 | 73.2% |
| EMAIL | 1149 | 100.0% |
| PHONE | 1773 | 100.0% |
| CREDIT_CARD | 122 | 100.0% |
| SSN | 109 | 100.0% |
| NATIONAL_ID | 401 | 100.0% |
| PASSPORT | 166 | 100.0% |
| DRIVER_LICENSE | 9 | 100.0% |

How to read this.

- The dataset's values are synthetic and often invalid by construction. A one-off check of
  the full English validation shard (not part of the committed command) found 513 of 575
  gold card numbers failing the Luhn check. Since ADR 0023, Luhn-invalid numbers of an
  exact card shape, `+` numbers that libphonenumber rejects, and numbers after a phone
  keyword are masked at lower scores (0.55 to 0.85), so they are found; a customer who
  raises a type's threshold above those scores gets the validated-only behaviour back. The
  property tests in `tests/property/` still assert 100% on valid generated identifiers;
  this benchmark does not show how real documents behave.
- PASSPORT, DRIVER_LICENSE and a general NATIONAL_ID are found only by shape (`AA9999999`,
  `AA99999AA`) or after a document keyword in English or Russian (ADR 0023), so typed
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
places and addresses are not detected and are not tabulated here (GIVENNAME, SURNAME,
CITY and STREET read 0.0% or close to it in every one of these languages; every row is in
`docs/benchmarks/openpii-500k.md`). Dates come from the date recognizer alone. Cells are
masking recall / typed recall (gold spans). Records used: de, es, fr, it 10,000 each; nl 5,347, hi 6,938,
te 5,434 (all the shard has for them).

| Dataset label | de | es | fr | it | nl | hi | te |
|---|---:|---:|---:|---:|---:|---:|---:|
| EMAIL | 97.5% / 97.5% (1306) | 99.2% / 99.2% (1116) | 98.8% / 98.8% (1311) | 99.3% / 99.3% (1152) | 98.5% / 98.5% (602) | 99.0% / 99.0% (715) | 98.9% / 98.9% (566) |
| TELEPHONENUM | 80.4% / 78.7% (1641) | 82.8% / 80.8% (1468) | 83.3% / 81.7% (1695) | 84.5% / 82.7% (1595) | 84.3% / 82.2% (787) | 78.1% / 74.8% (1162) | 78.1% / 75.8% (903) |
| CREDITCARDNUMBER | 79.4% / 79.0% (253) | 73.8% / 73.3% (218) | 74.5% / 74.0% (216) | 76.9% / 76.1% (260) | 78.6% / 78.6% (136) | 74.2% / 74.2% (136) | 61.8% / 60.6% (165) |
| SOCIALNUM | 0.0% / 0.0% (275) | 47.7% / 6.5% (199) | 28.8% / 3.2% (218) | 0.0% / 0.0% (107) | 97.6% / 48.2% (85) | 64.6% / 8.0% (150) | 66.0% / 7.8% (203) |
| TAXNUM | 11.9% / 0.8% (335) | 34.2% / 4.3% (277) | 31.0% / 4.6% (238) | 0.4% / 0.4% (250) | 95.9% / 0.0% (148) | 67.5% / 8.3% (216) | 66.9% / 9.3% (224) |
| IDCARDNUM | 30.4% / 5.5% (833) | 3.1% / 3.1% (848) | 13.6% / 6.5% (793) | 7.5% / 7.5% (902) | 96.9% / 6.4% (421) | 1.1% / 1.1% (537) | 1.4% / 1.4% (556) |
| PASSPORTNUM | 0.4% / 0.0% (432) | 50.8% / 50.8% (393) | 29.7% / 29.4% (363) | 51.3% / 50.7% (333) | 0.0% / 0.0% (192) | 99.5% / 99.1% (238) | 92.2% / 91.1% (259) |
| DRIVERLICENSENUM | 38.2% / 0.0% (235) | 0.5% / 0.0% (191) | 20.5% / 0.0% (195) | 72.7% / 0.0% (158) | 1.1% / 1.1% (87) | 0.0% / 0.0% (163) | 0.0% / 0.0% (171) |
| DATE | 50.2% / 50.2% (989) | 31.4% / 31.4% (993) | 32.9% / 32.9% (1112) | 32.1% / 32.1% (1249) | 33.9% / 33.9% (509) | 68.5% / 68.5% (716) | 65.0% / 64.9% (762) |

The high masking figures for nl (SOCIALNUM, TAXNUM, IDCARDNUM) sit next to typed recall
of 48.2%, 0.0% and 6.4%: the values were covered by spans of other types, which this run
does not break down. Read typed recall as the figure for the type itself. The document
keywords are English, Russian and Armenian only, so passports, ID cards and driving
licences in these languages are found by shape alone.

### Changes since the previous run (leak hunt 1d)

Previous run: commit 077f54580a57d9369a229bb11f000103bfc4049a, 2026-11-09 (the figures committed in fa75c3f). This run:
commit 2721ae41d7518187b0cb0db8d2bcf2b800e87ec2, 2026-11-10, same command, sample and seed. What changed: the 21 fixes of
leak hunt 1d (ADR 0014 amendment 2, ADR 0016 and ADR 0023 amended 2026-11-09). Every
label whose figures moved, masking recall / typed recall (counts in brackets), previous
-> this run:

| Language | Dataset label | Gold spans | Masking recall | Typed recall |
|---|---|---:|---:|---:|
| de | DATE | 989 | 34.1% -> 50.2% (338 -> 497) | 34.1% -> 50.2% (338 -> 497) |
| fr | DATE | 1112 | 31.4% -> 32.9% (350 -> 366) | 31.4% -> 32.9% (350 -> 366) |
| en | DRIVERLICENSENUM | 196 | 29.0% -> 29.5% (57 -> 58) | 4.0% -> 4.0% (8 -> 8) |
| en | IDCARDNUM | 803 | 56.5% -> 56.6% (454 -> 455) | 35.8% -> 35.9% (288 -> 289) |
| es | CREDITCARDNUMBER | 218 | 73.3% -> 73.8% (160 -> 161) | 72.9% -> 73.3% (159 -> 160) |
| hi | CREDITCARDNUMBER | 136 | 73.5% -> 74.2% (100 -> 101) | 73.5% -> 74.2% (100 -> 101) |
| te | CREDITCARDNUMBER | 165 | 61.2% -> 61.8% (101 -> 102) | 60.0% -> 60.6% (99 -> 100) |
| te | BUILDINGNUM | 598 | 0.8% -> 1.0% (5 -> 6) | 0.0% -> 0.0% (0 -> 0) |
| te | TELEPHONENUM | 903 | 78.1% -> 78.1% (706 -> 706) | 75.9% -> 75.8% (686 -> 685) |

The German gain is the German month names and the day followed by a dot
(`12. März 1985`); this run does not break the gains down further. One loss: a Telugu
phone number is still masked but no longer typed PHONE (te PHONE spans 1044 -> 1043, and
te CREDIT_CARD 106 -> 108): a span of another type now wins the overlap, most likely a
card read with the looser separators; the run does not isolate it. Every label not
listed is unchanged.

Precision (lower bound) stays 100.0% for every structured type in every language, on more
spans: DATE de 515 -> 674 and fr 546 -> 562; CREDIT_CARD en 121 -> 122, es 163 -> 164,
hi 106 -> 107, te 106 -> 108; NATIONAL_ID en 400 -> 401. Precision dropped for one NER
type: English PERSON 98.2% -> 98.1% (6729 -> 6730 spans, 6608 overlapping a gold span both
times), so the one new PERSON span overlaps no gold span; English ORG gained one span
(2576 -> 2577) that overlaps none either (73.2% both times). Both are probably the
mixed-script routing (a line of an English record with a Cyrillic word now also reaches the
Russian model and GLiNER); the run does not isolate them. As before, this benchmark has no
Russian or Armenian text, so most 1d fixes (the clean view folds, Russian dates and
documents, routing) cannot show here; they are asserted by
`tests/unit/engine/test_leak_hunt_1d.py` and the property tests, not measured.

### Earlier change: leak hunt 1c

Previous run: commit 050ff8c5dd7d3ccd4369a2fe56e658411adf114d, measured on 2026-11-09 from
an unmodified export of that commit with the same command, sample and seed. The figures
committed before this run were older (commit 6b27eb4, before the date recognizer), so they
are not the baseline here: at 050ff8c English DATE already read 88.1% / 83.7%. What
changed: the 15 fixes of leak hunt 1c (ADR 0016 and ADR 0023, amended 2026-11-09).
Every label whose figures moved, masking recall / typed recall, 050ff8c -> this run:

| Language | Dataset label | Gold spans | Masking recall | Typed recall |
|---|---|---:|---:|---:|
| en | DRIVERLICENSENUM | 196 | 28.0% -> 29.0% | 3.5% -> 4.0% |
| en | GIVENNAME | 7977 | 83.6% -> 83.6% | 66.2% -> 66.3% |
| en | SURNAME | 3061 | 85.4% -> 85.4% | 70.8% -> 70.9% |
| de | IDCARDNUM | 833 | 26.5% -> 30.4% | 1.5% -> 5.5% |
| de | TELEPHONENUM | 1641 | 80.3% -> 80.4% | 78.6% -> 78.7% |
| es | IDCARDNUM | 848 | 2.2% -> 3.1% | 2.2% -> 3.1% |
| es | BUILDINGNUM | 899 | 0.3% -> 0.4% | 0.0% -> 0.0% |
| fr | IDCARDNUM | 793 | 7.9% -> 13.6% | 0.7% -> 6.5% |
| it | IDCARDNUM | 902 | 4.3% -> 7.5% | 4.3% -> 7.5% |
| it | PASSPORTNUM | 333 | 51.0% -> 51.3% | 50.7% -> 50.7% |
| te | TELEPHONENUM | 903 | 72.7% -> 78.1% | 70.4% -> 75.9% |

No label lost masking or typed recall in any language; every label not listed is
unchanged. Precision (lower bound) stays 100.0% for every structured type in every
language, on more spans: NATIONAL_ID de 84 -> 117, es 77 -> 86, fr 95 -> 141, it 100 -> 130
and te 75 -> 71; te PHONE 1007 -> 1044. Precision did drop
for one NER type: English ORG 73.3% -> 73.2% (2576 spans both times, 1890 -> 1888 overlapping
a gold span); this run does not isolate which change caused it. English PERSON gained three
spans (6726 -> 6729), all overlapping gold spans, and DATE lost one (2376 -> 2375, precision
85.7% both times). This benchmark has no Russian or Armenian text, so the Russian series
and number, the Armenian keywords and the mixed-script routing cannot show here; those
fixes are asserted by `tests/unit/engine/test_leak_hunt_1c.py` and the property tests,
not measured.

### Earlier change: the ADR 0023 recognizers

Previous run: commit 79d82b15215a812d69ad4e70b2e46b5a971e8b12, 2026-11-09 (engine
unchanged up to bc8662e). This run: commit 6b27eb43b422894f049e89932b32d2778a0a7e12,
2026-11-09, same command, sample and seed. What changed: the six shape and keyword
recognizers of ADR 0023 (`+` phones, keyword-gated phones, Luhn-invalid card shapes,
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
- With the default `gliner="auto"`, GLiNER reads Latin-script text only on lines that
  also hold letters of a script without a spaCy model (ADR 0016 amended); the English run
  above includes that. Hindi and Telugu text (Devanagari, Telugu script) is now
  routed to GLiNER when NER is on; their rows above were run with NER off and have not
  been re-measured with GLiNER.
