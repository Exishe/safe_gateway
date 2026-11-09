# 0012. NER through Presidio and spaCy, strictly offline

- Status: Accepted
- Date: 2026-11-09

## Context

Phase 1b adds names, organisations, locations and dates. The brief fixes the
stack: presidio-analyzer with spaCy `en_core_web_lg` and `ru_core_news_lg`.
Reading presidio-analyzer 2.2.366 (MIT) showed two network paths that the
static no-egress check cannot see, because they live inside the library:

- `SpacyNlpEngine` calls `spacy.cli.download(name)` when the model name is not
  an installed package or an existing path. There is no switch to turn this
  off; `SlimSpacyNlpEngine` has `auto_download`, defaulting to True.
- Presidio's own email recognizer calls `tldextract`, which fetches the public
  suffix list on first use.

## Decision

1. **Presidio is the NER backend, restricted to spaCy NER.** The analyzer gets
   a registry holding only its spaCy recognizer. Presidio's regex recognizers
   are not loaded: ours validate more (ADR 0011), and this keeps `tldextract`
   out of the call path.
2. **Only local model paths reach Presidio.** The engine passes a directory
   under `~/.safeguard/models/spacy/<model>-<version>/`. If it does not exist
   the engine raises `ModelMissing` naming `safeguard models pull`, before
   Presidio is constructed, so the download branch can never run.
3. **`safeguard models pull` lives in `gateway/cloud/`** and arrives in 1b, not
   1c: it downloads the pinned spaCy wheels from the explosion/spacy-models
   GitHub releases, checks their SHA-256, and unpacks the model directory. 1c
   adds GLiNER to the same command.
4. **Language by script, not by a detector.** English, Russian and Armenian use
   different alphabets, so each paragraph goes to the model of its dominant
   script: Latin to `en`, Cyrillic to `ru`. Armenian has no spaCy model; it
   relies on the gazetteer and the structured recognizers until GLiNER (1c).
   No language-detection library is added.
5. **Label mapping.** en: PERSON, ORG; GPE, LOC and FAC become LOCATION; DATE.
   ru: PER becomes PERSON, ORG, LOC becomes LOCATION. Other labels are dropped.
6. **Long texts** are split per ADR 0004 into windows below spaCy's
   `max_length`, cut at paragraph or whitespace boundaries with an overlap;
   the merge step removes duplicates from the overlap.
7. **Models load lazily,** once per language per process. Tests that need a
   model are marked `models` and skipped with a clear reason when it is
   absent; CI downloads the models once and caches them.

## Consequences

- presidio-analyzer pulls in `regex` (Apache-2.0 AND CNRI-Python). CNRI-Python
  is not on the hard-rule-1 list; the project owner approved it as a reasoned
  exception on 2026-11-09 (`tools/license_policy.toml`). It must be attributed in
  `THIRD_PARTY_NOTICES.md` when packaging starts.

- A missing model is an error the user can act on, never a silent download.
- The socket-disabled test run also covers the NER path: any network call
  from inside Presidio or spaCy fails the suite.
- Armenian names are found only through the gazetteer until 1c. The UI and
  SECURITY.md must say so.
- The `lg` models are about 1 GB of RAM each when loaded; on an 8 GB machine,
  loading only the languages a text needs matters.
