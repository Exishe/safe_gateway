# Phase 1: Detection engine

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

Phase 1 runs in three steps with one tag (ADR 0011). This file plans **1a** in
full; 1b and 1c are outlined at the end and get their own plan when they start.

## Phase 1a: structured recognizers, merge, `detect()`, `safeguard scan`

**Goal:** `safeguard scan notes.txt` lists every structured identifier in the file, with no model download.

**Architecture:** `gateway/engine/` holds `Span` and `EntityType`, checksum helpers, one module per recognizer, a merge step, and `detect()`. Each recognizer has `name` and `find(text) -> list[Span]` and reads the original text, so offsets never shift. The CLI reads a text file and prints the merged spans.

**Tech Stack:** Python 3.12, `phonenumbers` (runtime, Apache-2.0), Hypothesis and Faker (tests only).

**Spec:** the brief (Phase 1 and "Functional spec / Engine"), `docs/adr/0001`, `0006`, `0011`.

## Global Constraints

- `gateway/engine/` imports no network, process or dynamic-import module (checked on every edit).
- Every structured type reaches 100% detection on its Hypothesis strategy, embedded in Faker sentences.
- Offsets refer to the original text; `text[span.start:span.end]` is always the detected value.
- Fake data only; literal identifiers in tests carry `# pragma: allowlist secret`.
- No recall figure for names or addresses in 1a: there is no NER yet.

## Review Focus

1. A card number written next to another number (`qty 2 4111 1111 1111 1111`) is still found. Test in Task 3.
2. Arabic-Indic and full-width digits are found, and the span covers the original characters. Tests in Tasks 3 and 4.
3. A Luhn-valid 16-digit order number with an implausible prefix (`0000 ...`, `9...`) is not reported as a card. Test in Task 3.
4. An IBAN followed by an upper-case word (`... 3000 BANK`) is found at its exact length. Test in Task 3.
5. Empty text, whitespace only and a 1 MB text return without error, and the 1 MB text takes under 2 seconds. Test in Task 6.

---

### Task 1: Types and merge

**Files:** create `gateway/engine/types.py`, `gateway/engine/merge.py`; test `tests/unit/engine/test_merge.py`.

**Interfaces (produces):**
- `class EntityType(StrEnum)`: PERSON, ORG, LOCATION, ADDRESS, EMAIL, PHONE, DATE, DATE_OF_BIRTH, SSN, PASSPORT, DRIVER_LICENSE, NATIONAL_ID, CREDIT_CARD, IBAN, BANK_ACCOUNT, IP_ADDRESS, MEDICAL_RECORD_NUMBER, HEALTH_CONDITION, CASE_NUMBER, URL_WITH_TOKEN, CODENAME, LITERAL.
- `@dataclass(frozen=True) class Span: start: int; end: int; entity_type: EntityType; score: float; recognizer: str`, with `__post_init__` rejecting `start >= end` or a score outside 0..1.
- `class Recognizer(Protocol): name: str; def find(self, text: str) -> list[Span]`.
- `merge(spans: Iterable[Span], thresholds: Mapping[EntityType, float] | None = None, priority: Sequence[str] = ()) -> list[Span]`: drop spans below their threshold (default 0.5), resolve overlaps by length, then score, then recognizer position in `priority`; return sorted by start.

- [ ] Tests: below-threshold dropped; disjoint kept; nested keeps outer; equal length keeps higher score; equal length and score keeps earlier priority; touching spans both kept; output sorted; invalid Span raises ValueError.
- [ ] Run, see FAIL; implement; run, see PASS; commit `feat(engine): spans and merge`.

### Task 2: Checksums

**Files:** create `gateway/engine/checksums.py`; test `tests/unit/engine/test_checksums.py`.

**Interfaces (produces):** `digits_of(text: str) -> str` (ASCII digits from any Unicode decimal digits, other characters dropped); `luhn_valid(digits: str) -> bool`; `iban_valid(iban: str) -> bool` (mod-97 and per-country length table); `inn_valid(digits: str) -> bool` (RU, 10 or 12 digits); `snils_valid(digits: str) -> bool` (RU, 11 digits; numbers up to 001-001-998 have no checksum and are invalid).

- [ ] Tests: known-valid and one-digit-changed pairs for each; `digits_of("٤١١١ １１")` == `"411111"`; property: appending the computed check digit makes `luhn_valid` true.
- [ ] Run, FAIL; implement; PASS; commit `feat(engine): checksum helpers`.

### Task 3: Digit-group and IBAN recognizers

**Files:** create `gateway/engine/digits.py` (`digit_runs`, `windows`), `gateway/engine/recognizers/{credit_card,us_ssn,ru_inn,ru_snils,iban}.py`; create `tests/strategies.py`; test `tests/unit/engine/recognizers/test_*.py`, `tests/property/test_structured_detection.py`.

**Interfaces (produces):**
- `digit_runs(text) -> list[list[tuple[int, int]]]`: each run is its digit groups as (start, end), groups joined by exactly one space, hyphen or dot, no word character on either side of the run.
- `windows(run, min_digits, max_digits) -> list[tuple[int, int]]`: every contiguous group window whose digit count fits, longest first.
- Recognizer instances `CREDIT_CARD`, `US_SSN`, `RU_INN`, `RU_SNILS`, `IBAN`, each with `name` and `find`.
- `tests/strategies.py`: `credit_cards()`, `us_ssns()`, `ru_inns()`, `ru_snilses()`, `ibans()`, `formatted(digits, group_sizes)` with separators " ", "-", "." or none, `with_unicode_digits(s)`, and `embedded(value)` placing a value inside a Faker sentence.

Rules: cards 13 to 19 digits, Luhn, first digit 2 to 6. SSN: area not 000, 666 or 900-999, group not 00, serial not 0000; 3-2-4 with separators scores 0.85, nine bare digits 0.6. INN: 10 or 12 digits, checksum. SNILS: 11 digits (3-3-3 then 2), checksum. IBAN: two letters, two digits, then letters, digits and single spaces up to the country's length; mod-97.

- [ ] Tests (examples): each recognizer finds a valid example and rejects its one-digit-changed twin; card after another number found (Review Focus 1); `0000`-prefix and `9`-prefix Luhn-valid numbers not cards (3); IBAN followed by `BANK` found at exact length (4); Arabic-Indic card found, span covers the original characters (2).
- [ ] Property tests: for each strategy, `embedded(value)` yields a span of the right type whose text equals the value.
- [ ] Run, FAIL; implement; PASS; commit `feat(engine): card, SSN, INN, SNILS and IBAN recognizers`.

### Task 4: Email, IP, URL-with-token and phone recognizers

**Files:** create `gateway/engine/recognizers/{email,ip_address,url_token,phone}.py`; extend `tests/strategies.py`; tests alongside.

**Interfaces (produces):** `EMAIL`, `IP_ADDRESS`, `URL_WITH_TOKEN` instances; `PhoneRecognizer(regions: Sequence[str] = DEFAULT_REGIONS)` with `DEFAULT_REGIONS = ("US", "GB", "DE", "FR", "IT", "ES", "NL", "RU", "AM")`. Strategies `emails()` (Faker), `ip_addresses()` (Hypothesis), `token_urls()`, `phones()` (each region's example mobile and fixed-line numbers with varied subscriber digits, kept only if `phonenumbers.is_valid_number`, formatted E.164, international or national).

Rules: IP via the `ipaddress` module (v4 and v6), not inside a longer dotted number (version strings like `1.2.3.4.5`). URL with token: an http(s) URL whose query has a parameter named token, access_token, api_key, apikey, key, sig, signature, auth, password, secret or session (case-insensitive); the span is the whole URL.

- [ ] Tests: examples and near-misses for each; version string `1.2.3.4.5` is not an IP; Russian national, Armenian national and E.164 phones found; Arabic-Indic phone found (Review Focus 2).
- [ ] Property tests for all four strategies.
- [ ] Run, FAIL; implement; PASS; commit `feat(engine): email, IP, token URL and phone recognizers`.

### Task 5: Gazetteer

**Files:** create `gateway/engine/recognizers/gazetteer.py`; test alongside.

**Interfaces (produces):** `Gazetteer(entries: Mapping[str, EntityType])`, `load_gazetteer(path: Path) -> Gazetteer` (CSV with header `value,entity_type`; blank type means CODENAME; unknown type raises ValueError naming the line).

- [ ] Tests: case-insensitive whole-word match; no match inside a longer word; multi-word entries; longest entry wins when entries overlap; CSV loader with blank and invalid types; non-UTF-8 CSV raises a clear error.
- [ ] Run, FAIL; implement; PASS; commit `feat(engine): customer gazetteer`.

### Task 6: `detect()` and `safeguard scan`

**Files:** create `gateway/engine/detect.py`; modify `gateway/cli.py`; tests `tests/unit/engine/test_detect.py`, `tests/unit/test_cli.py`.

**Interfaces (produces):** `@dataclass(frozen=True) class EngineConfig: thresholds: Mapping[EntityType, float] = {}; phone_regions: Sequence[str] = DEFAULT_REGIONS; gazetteer: Gazetteer | None = None`; `detect(text: str, lang: str = "auto", config: EngineConfig | None = None) -> list[Span]` (runs every recognizer, then `merge`; `lang` is accepted and unused until 1b). CLI: `safeguard scan FILE [--json] [--gazetteer CSV]`; the table shows line:column, type, score, recognizer and the value with 20 characters of context; `--json` prints a list of objects with start, end, entity_type, score, recognizer, text. Exit 0 whether or not anything is found; exit 2 for an unreadable file.

- [ ] Tests: mixed text with one of each type returns each once with exact offsets; empty and whitespace-only text returns []; 1 MB of Faker text with 50 identifiers finishes under 2 seconds and finds all 50 (Review Focus 5); CLI table and JSON output on a temp file; missing file exits 2 with a message.
- [ ] Run, FAIL; implement; PASS; run `uv run safeguard scan` on a sample file and read the output; commit `feat: detect() and safeguard scan`.

### Task 7: Red team and close-out

- [ ] Dispatch the `leak-hunter` agent on the engine. Every bypass becomes a failing test; fix each, or record it in `docs/SECURITY.md` limits with a reason.
- [ ] `docs/BENCHMARKS.md`: note that structured types are asserted at 100% by the property tests (no recall figure; nothing measured on a dataset yet).
- [ ] Full verification (every CI command), commit, push.

---

## Phase 1b: names, organisations, places and dates (NER), strictly offline

**Goal:** `safeguard scan` also finds PERSON, ORG, LOCATION and DATE in English and Russian text, using spaCy through Presidio, with no network path at run time (ADR 0012).

**Global Constraints (in addition to 1a):** the engine never passes Presidio a model *name*, only an existing local directory; a missing model raises `ModelMissing` naming `safeguard models pull`; only `gateway/cloud/` downloads anything; tests that need a model are marked `models` and skip with a reason when it is absent.

**Review Focus:** (1) a missing model must never trigger a download (socket-disabled test); (2) mixed English/Russian text sends each paragraph to the right model; (3) a 1 MB text stays within spaCy's `max_length` via windows and finds names on both sides of a window edge; (4) a zip entry with `../` in `models pull` must not escape the models directory; (5) Armenian text returns structured and gazetteer hits without error.

### Task 1: Model registry and `safeguard models pull`
Files: `gateway/cloud/models.py`, `gateway/cli.py`; tests `tests/unit/cloud/test_models.py`. Adds `httpx` (BSD) at run time.
Interfaces: `ModelSpec(name, version, url, sha256)`; `SPACY_MODELS` for `en_core_web_lg` and `ru_core_news_lg` 3.8.0 (explosion/spacy-models GitHub release wheels, SHA-256 pinned after first verified download); `models_home() -> Path` (`$SAFEGUARD_HOME` or `~/.safeguard`) / `models`; `pull(spec, client: httpx.Client) -> Path` downloads, checks SHA-256, unpacks the wheel's model directory to `models/spacy/<name>-<version>/`. CLI `safeguard models pull [--only en|ru]` and `safeguard models list`.
Tests (httpx `MockTransport`, no network): good archive unpacks to the expected path; wrong SHA-256 raises and leaves nothing behind; `../` entry rejected; existing install is not downloaded again.

### Task 2: Offline NER recognizer
Files: `gateway/engine/ner.py`; tests `tests/unit/engine/test_ner.py`. Adds `presidio-analyzer` (MIT) and `spacy` (MIT) at run time.
Interfaces: `ModelMissing(Exception)`; `model_dir(lang) -> Path` (raises `ModelMissing` if absent); `SpacyNerRecognizer(lang, model_dir)` with `name = f"ner_{lang}"` and `find(text)`; builds a Presidio `AnalyzerEngine` lazily with an NLP engine configured from the *path* and a registry holding only Presidio's spaCy recognizer; maps labels per ADR 0012 point 5; splits text into windows below `max_length` with an overlap.
Tests: label mapping and windowing with a stub NLP engine (always run); `ModelMissing` raised before Presidio is constructed when the directory is absent, with sockets disabled; `models`-marked tests with the real models: an English and a Russian sentence each yield PERSON/ORG/LOCATION at exact offsets.

### Task 3: Script routing and `detect()`
Files: `gateway/engine/script.py`, `gateway/engine/detect.py`; tests alongside.
Interfaces: `segments(text) -> list[tuple[int, int, str]]` (paragraph ranges with the dominant script's language: Latin `en`, Cyrillic `ru`, Armenian `hy`, other `und`); `EngineConfig.ner: Literal["auto", "off"] = "auto"`; `detect(text, lang="auto")`: `lang` forces one language; with `auto`, each segment goes to its model; `hy` and `und` get no NER; models that are not installed are skipped with one warning per process (`auto`) rather than failing the scan.
Tests: routing of a mixed en/ru/hy text; `lang="ru"` forces Russian; NER off when configured; detection still works with no models installed.

### Task 4: CI, docs and red team
CI downloads the models once with `safeguard models pull` (cached by version) and runs `-m models`; `docs/INSTALL.md` gains the `models pull` step; `docs/SECURITY.md` states that Armenian names are found only through the gazetteer until 1c; leak-hunter round on names (declension, initials, transliteration, all-caps headers) with every bypass a failing test, fixed or recorded as a limit; recall numbers still wait for 1c.

## Phase 1c (outline)

Confirm the GLiNER2-PII model id (the brief's `fastino/gliner2-pii` does not resolve), else `urchade/gliner_multi_pii-v1` (Apache-2.0); `safeguard models pull` in `gateway/cloud/`; offline loading from `~/.safeguard/models` with `HF_HUB_OFFLINE=1`; CPU-only torch on Linux CI; recall per entity type on ai4privacy OpenPII in `docs/BENCHMARKS.md`; the commit hook's PII scan switches to the engine; tag `v0.1.0`.
