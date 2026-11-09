"""Recall benchmark of `detect()` on ai4privacy Open PII Masking 500k (CC-BY-4.0).

Run: `uv run python -m tools.benchmark_openpii --sample 10000 --seed 20261109`

Network is allowed here (this is a tool, not the engine) and used for one thing: the
pinned validation shard is downloaded once into `.cache/benchmarks/` (gitignored) and
checked against its SHA-256. Dataset text is never written anywhere else; the outputs
hold aggregate counts only.

Three figures per dataset label and language:
- masking recall: share of gold spans with at least 90% of their characters inside the
  union of ANY detected spans. This is what matters for privacy: the value left the
  machine masked, whatever we called it.
- typed recall: the same, counting only spans of the EntityType(s) the label maps to.
- precision (per detected EntityType): share of detected spans that overlap any gold
  span. The dataset labels no organisations and few other types, so a detected span
  with no gold partner is not necessarily wrong; read precision as a lower bound.
"""

import argparse
import hashlib
import json
import platform
import random
import subprocess
import sys
import time
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx

from gateway.engine.detect import EngineConfig, detect
from gateway.engine.ner import SPACY_MODEL_DIRS
from gateway.engine.types import EntityType, Span

DATASET_ID = "ai4privacy/open-pii-masking-500k-ai4privacy"
# Pinned: HEAD of the dataset repository when this harness was written (2026-11-09).
REVISION = "506996d625ed970a0063432daf6007cf4a3a48e3"  # pragma: allowlist secret
SHARD_PATH = "data/validation/test.jsonl"
# Hugging Face's own LFS object id for the shard, which is its SHA-256.
SHARD_SHA256 = (
    "4e908e60d8d88f90015301e1ab4a8b7899ec713f1fc903860e1d9e0b91677ebf"  # pragma: allowlist secret
)
SHARD_URL = f"https://huggingface.co/datasets/{DATASET_ID}/resolve/{REVISION}/{SHARD_PATH}"
CACHE_DIR = Path(".cache/benchmarks")
COVERAGE_THRESHOLD = 0.9
# Languages with a spaCy model get NER (the product's "auto" routing); the rest run
# structured recognizers only, and the report says so.
NER_LANGUAGES = frozenset(SPACY_MODEL_DIRS)

E = EntityType
# Dataset label -> the EntityTypes that count as a typed hit. An empty set means we have
# no type for it: reported as "not covered", never dropped. Names are labelled as given
# name and surname separately; a PERSON span covering both counts for each.
LABEL_MAP: dict[str, frozenset[EntityType]] = {
    "GIVENNAME": frozenset({E.PERSON}),
    "SURNAME": frozenset({E.PERSON}),
    "CITY": frozenset({E.LOCATION}),
    "STREET": frozenset({E.ADDRESS, E.LOCATION}),
    "BUILDINGNUM": frozenset({E.ADDRESS, E.LOCATION}),
    "ZIPCODE": frozenset({E.ADDRESS, E.LOCATION}),
    "TELEPHONENUM": frozenset({E.PHONE}),
    "EMAIL": frozenset({E.EMAIL}),
    "DATE": frozenset({E.DATE, E.DATE_OF_BIRTH}),
    "IDCARDNUM": frozenset({E.NATIONAL_ID}),
    "PASSPORTNUM": frozenset({E.PASSPORT}),
    "TAXNUM": frozenset({E.NATIONAL_ID}),
    "SOCIALNUM": frozenset({E.SSN, E.NATIONAL_ID}),
    "CREDITCARDNUMBER": frozenset({E.CREDIT_CARD}),
    "DRIVERLICENSENUM": frozenset({E.DRIVER_LICENSE}),
    "TIME": frozenset(),
    "TITLE": frozenset(),
    "AGE": frozenset(),
    "SEX": frozenset(),
    "GENDER": frozenset(),
}


@dataclass(frozen=True)
class GoldSpan:
    label: str
    start: int
    end: int


@dataclass(frozen=True)
class Record:
    text: str
    gold: list[GoldSpan]


@dataclass
class GoldRow:
    total: int = 0
    masked: int = 0
    typed: int = 0


@dataclass
class DetectedRow:
    total: int = 0
    hit: int = 0


def coverage(start: int, end: int, detected: Iterable[tuple[int, int]]) -> float:
    """Share of characters in [start, end) inside the union of `detected` intervals."""
    covered = 0
    cursor = start
    for left, right in sorted(detected):
        left, right = max(left, cursor), min(right, end)  # noqa: PLW2901
        if right > left:
            covered += right - left
            cursor = right
    return covered / (end - start)


def _overlaps(span: Span, gold: Sequence[GoldSpan]) -> bool:
    return any(span.start < g.end and g.start < span.end for g in gold)


@dataclass
class Tally:
    gold: dict[tuple[str, str], GoldRow] = field(default_factory=lambda: defaultdict(GoldRow))
    detected: dict[tuple[str, str], DetectedRow] = field(
        default_factory=lambda: defaultdict(DetectedRow)
    )

    def add_record(self, language: str, gold: Sequence[GoldSpan], found: Sequence[Span]) -> None:
        for g in gold:
            accepted = LABEL_MAP[g.label]  # KeyError on a label nobody mapped: fail loudly
            row = self.gold[language, g.label]
            row.total += 1
            if coverage(g.start, g.end, [(s.start, s.end) for s in found]) >= COVERAGE_THRESHOLD:
                row.masked += 1
            typed = [(s.start, s.end) for s in found if s.entity_type in accepted]
            if accepted and coverage(g.start, g.end, typed) >= COVERAGE_THRESHOLD:
                row.typed += 1
        for s in found:
            drow = self.detected[language, s.entity_type.value]
            drow.total += 1
            drow.hit += _overlaps(s, gold)

    def not_covered_labels(self) -> set[str]:
        return {label for _, label in self.gold if not LABEL_MAP[label]}


def sample_indices(population: int, size: int, seed: int) -> list[int]:
    """`size` distinct indices of range(population), sorted, reproducible from `seed`."""
    if size >= population:
        return list(range(population))
    return sorted(random.Random(seed).sample(range(population), size))  # noqa: S311


def verify_sha256(path: Path, expected: str) -> None:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    if digest.hexdigest() != expected:
        msg = f"SHA-256 of {path} is {digest.hexdigest()}, expected {expected}"
        raise ValueError(msg)


def fetch_shard(cache_dir: Path = CACHE_DIR) -> Path:
    """The pinned shard in `cache_dir`, downloaded and verified if not already there."""
    target = cache_dir / f"openpii-500k-validation-{REVISION[:7]}.jsonl"
    if not target.exists():
        cache_dir.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(".part")
        with (
            httpx.stream("GET", SHARD_URL, follow_redirects=True, timeout=300) as response,
            partial.open("wb") as out,
        ):
            response.raise_for_status()
            for chunk in response.iter_bytes(1 << 20):
                out.write(chunk)
        partial.rename(target)
    verify_sha256(target, SHARD_SHA256)
    return target


def load_records(path: Path) -> tuple[dict[str, list[Record]], dict[str, int]]:
    """Records by language, and per language the count of records skipped because a gold
    span's offsets do not reproduce its own `value` (so its position cannot be trusted)."""
    records: dict[str, list[Record]] = defaultdict(list)
    skipped: dict[str, int] = defaultdict(int)
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            data = json.loads(line)
            text, language = data["source_text"], data["language"]
            mask = data["privacy_mask"]
            if any(text[m["start"] : m["end"]] != m["value"] for m in mask):
                skipped[language] += 1
                continue
            gold = [GoldSpan(m["label"], m["start"], m["end"]) for m in mask]
            records[language].append(Record(text, gold))
    return dict(records), dict(skipped)


def pct(part: int, whole: int) -> str:
    """One decimal, truncated rather than rounded, so a figure is never flattered."""
    if whole == 0:
        return "n/a"
    return f"{part * 1000 // whole / 10:.1f}%"


NOT_COVERED_NOTE = (
    "Labels with no EntityType (not covered; typed recall does not apply,"
    " the masking figure is incidental):"
)


def render_markdown(tally: Tally, languages: Sequence[str]) -> str:
    covered_rows, other_rows, precision_rows = [], [], []
    for lang in languages:
        for (row_lang, label), row in sorted(tally.gold.items()):
            if row_lang != lang:
                continue
            types = "/".join(sorted(t.value for t in LABEL_MAP[label]))
            if types:
                covered_rows.append(
                    f"| {lang} | {label} | {types} | {row.total} | "
                    f"{pct(row.masked, row.total)} | {pct(row.typed, row.total)} |"
                )
            else:
                other_rows.append(
                    f"| {lang} | {label} | {row.total} | {pct(row.masked, row.total)} |"
                )
        for (row_lang, etype), drow in sorted(tally.detected.items()):
            if row_lang == lang:
                precision_rows.append(
                    f"| {lang} | {etype} | {drow.total} | {pct(drow.hit, drow.total)} |"
                )
    return "\n".join(
        [
            "| Language | Dataset label | Maps to | Gold spans | Masking recall | Typed recall |",
            "|----------|---------------|---------|-----------:|---------------:|-------------:|",
            *covered_rows,
            "",
            NOT_COVERED_NOTE,
            "",
            "| Language | Dataset label | Gold spans | Incidentally masked |",
            "|----------|---------------|-----------:|--------------------:|",
            *other_rows,
            "",
            "| Language | Detected type | Detected spans | Precision (lower bound) |",
            "|----------|---------------|---------------:|------------------------:|",
            *precision_rows,
            "",
        ]
    )


def _git(*args: str) -> str:
    return subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def run(sample: int, seed: int, languages: Sequence[str] | None, cache_dir: Path) -> dict:
    records, skipped = load_records(fetch_shard(cache_dir))
    chosen = [lang for lang in sorted(records) if languages is None or lang in languages]
    tally = Tally()
    sizes: dict[str, int] = {}
    seconds: dict[str, float] = {}
    for lang in chosen:
        ner_on = lang in NER_LANGUAGES
        config = EngineConfig(ner="auto" if ner_on else "off")
        started = time.perf_counter()
        indices = sample_indices(len(records[lang]), sample, seed)
        sizes[lang] = len(indices)
        for i in indices:
            record = records[lang][i]
            tally.add_record(lang, record.gold, detect(record.text, "auto", config))
        seconds[lang] = round(time.perf_counter() - started, 1)
        print(f"{lang}: {len(indices)} records in {seconds[lang]} s", file=sys.stderr)
    return {
        "meta": {
            "dataset": DATASET_ID,
            "revision": REVISION,
            "shard": SHARD_PATH,
            "shard_sha256": SHARD_SHA256,
            "license": "CC-BY-4.0",
            "sample_per_language": sample,
            "seed": seed,
            "records_used": sizes,
            "records_skipped_bad_offsets": skipped,
            "ner_languages": sorted(NER_LANGUAGES & set(chosen)),
            "ner_models": dict(SPACY_MODEL_DIRS),
            "coverage_threshold": COVERAGE_THRESHOLD,
            "commit": _git("rev-parse", "HEAD"),
            "tree_dirty": bool(_git("status", "--porcelain", "--untracked-files=no")),
            "date": datetime.now(UTC).date().isoformat(),
            "python": platform.python_version(),
            "seconds": seconds,
        },
        "languages": chosen,
        "gold": [
            {"language": lang, "label": label, **vars(row)}
            for (lang, label), row in sorted(tally.gold.items())
        ],
        "detected": [
            {"language": lang, "type": etype, **vars(row)}
            for (lang, etype), row in sorted(tally.detected.items())
        ],
        "label_map": {k: sorted(t.value for t in v) for k, v in LABEL_MAP.items()},
        "_tally": tally,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sample", type=int, default=10000, help="records per language")
    parser.add_argument("--seed", type=int, default=20261109)
    parser.add_argument("--languages", nargs="*", help="default: every language in the shard")
    parser.add_argument("--cache-dir", type=Path, default=CACHE_DIR)
    parser.add_argument("--json", type=Path, help="write aggregate results here")
    parser.add_argument("--markdown", type=Path, help="write the tables here")
    args = parser.parse_args(argv)

    result = run(args.sample, args.seed, args.languages, args.cache_dir)
    tally: Tally = result.pop("_tally")
    markdown = render_markdown(tally, result["languages"])
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", "utf-8")
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(markdown, encoding="utf-8")
    print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
