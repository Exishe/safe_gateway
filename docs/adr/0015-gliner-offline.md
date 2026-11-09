# 0015. GLiNER for Armenian and other text, strictly offline

- Status: Accepted
- Date: 2026-11-09

## Context

Phase 1b sends Latin text to spaCy `en_core_web_lg` and Cyrillic text to
`ru_core_news_lg` (ADR 0012). Armenian, and any other alphabet, got no names,
organisations or places: only structured identifiers and the gazetteer.

The brief named `fastino/gliner2-pii`, which does not exist on the Hugging Face
Hub. `fastino/gliner2-multi-v1` exists, but it is a general GLiNER2 model, not a
PII one, and needs the separate `gliner2` library. GLiNER v0/v1 *base* models are
CC-BY-NC and forbidden (hard rule 1).

GLiNER's `from_pretrained` downloads from the Hub whenever a name does not
resolve locally. Reading `gliner` 0.2.30 showed a second network path that a
local model directory does not close by itself: the model's config names its
backbone (`microsoft/mdeberta-v3-base`), and GLiNER resolves that name with
`AutoTokenizer.from_pretrained` when the directory holds no
`tokenizer_config.json`, and with `AutoConfig.from_pretrained` when the config
holds no `encoder_config`.

## Decision

1. **Model.** `urchade/gliner_multi_pii-v1` at commit
   `1fcf13e85f4eef5394e1fcd406cf2ca9ea82351d`; model card license apache-2.0.
   It was fine-tuned from `urchade/gliner_multi-v2.1` (apache-2.0). The backbone
   `microsoft/mdeberta-v3-base` (commit `a0484667b22365f84929a935b5e50a51f71f159d`)
   is MIT. Runtime packages added: `gliner` (Apache-2.0), `torch` (owner-approved
   exception, `tools/license_policy.toml`), `transformers`, `protobuf` (needed to
   convert the backbone's sentencepiece model to a tokenizer).
2. **CPU only.** On Linux `torch` comes from PyTorch's CPU index through an
   explicit uv index, so no CUDA packages are installed.
3. **Pull.** `safeguard models pull [--only gliner]` (in `gateway/cloud/hub.py`)
   fetches five files from `huggingface.co/<repo>/resolve/<commit>/<file>`, each
   checked against a pinned SHA-256, into
   `~/.safeguard/models/gliner/gliner_multi_pii-v1-<commit>/`. Nothing is
   installed unless every file verifies. The backbone's weights are not fetched:
   GLiNER's own `pytorch_model.bin` holds the fine-tuned encoder.
4. **The backbone stays offline.** The install holds the backbone's
   `config.json`, `spm.model` and `tokenizer_config.json`, and the
   `gliner_config.json` that GLiNER reads embeds the backbone config as
   `encoder_config`. So neither of GLiNER's backbone lookups runs. The pinned
   original config is kept byte for byte as `gliner_config.source.json`, and
   `pull` refuses a config that names any other backbone.
5. **Loading.** `gateway/engine/gliner_ner.py` raises `ModelMissing` before
   importing `gliner` unless the directory is a complete install (point 4).
   It sets `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` before the import and
   loads only that path, with `local_files_only=True`, once per process. A
   `models` test loads the real model under the socket-disabled run with every
   Hub download function replaced by a tripwire.
6. **Labels.** person, organization, location, address and date of birth become
   PERSON, ORG, LOCATION, ADDRESS and DATE_OF_BIRTH. Threshold
   `EngineConfig.gliner_threshold`, default 0.5.
7. **Windows.** Text is cut into windows of 128 GLiNER words (its own word
   splitter) overlapping by 32 (ADR 0004), and offsets are mapped back to the
   original text. The limit that binds is the backbone's 512 positions, not
   GLiNER's `max_len` of 384 words. Armenian averages about 2.4 sub-word tokens
   per word. 192 words of dense Faker `hy_AM` text reached 502 positions with the
   label prompt, and 128 words reach 344. A `models` test keeps the worst window
   at or below 448 positions.
8. **Routing.** In `detect()`, paragraphs whose script is Armenian (`hy`) or
   undetermined (`und`) go to GLiNER. `EngineConfig.gliner` is `"auto"` (that),
   `"all"` (GLiNER also reads `en` and `ru` paragraphs beside spaCy, for ADDRESS
   and DATE_OF_BIRTH, which spaCy does not label) or `"off"`. `ner="off"` and
   `safeguard scan --no-ner` turn GLiNER off too. A missing GLiNER model gives
   one warning per process and never raises, even for `lang="hy"`. Without it,
   the text still gets every structured recognizer and the gazetteer.

## Consequences

- Armenian names, organisations, places and addresses are detected without a
  gazetteer entry, but how well is unknown. The model was fine-tuned on six
  Western European languages, and no labelled Armenian dataset exists to
  measure it. `docs/SECURITY.md` says so, and the gazetteer remains the
  dependable path for known clients.
- The install is about 1.2 GB on disk. Speed and memory are in
  `docs/BENCHMARKS.md`. With `gliner="all"`, English and Russian text pay for
  both models.
- `torch` and `transformers` are large dependencies. `transformers` also
  converts the tokenizer at load time and prints a spurious warning about a
  "Mistral regex" when it does.
- The derived `gliner_config.json` is not byte-identical to a pinned file. It is
  a deterministic function of two pinned files, and the pinned original sits
  beside it.
