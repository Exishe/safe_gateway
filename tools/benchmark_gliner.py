"""Throughput and peak memory of `detect()` with GLiNER, on CPU, warm (ADR 0015).

Run once per language, each in a fresh process so the peak is that run's own:

    uv run python -m tools.benchmark_gliner --lang hy
    uv run python -m tools.benchmark_gliner --lang en

`hy` uses the default config (Armenian goes to GLiNER); `en` uses `gliner="all"`, so
spaCy and GLiNER both read the text. The text is Faker prose with names and addresses
(seeded), about 100 KB of UTF-8. The models are loaded and warmed on a short text
first; the timed figure is one pass over the whole text. No network: the models come
from `safeguard models pull`.
"""

import argparse
import platform
import resource
import time

from faker import Faker

from gateway.engine.detect import EngineConfig, detect

TARGET_BYTES = 100_000
LOCALES = {"hy": "hy_AM", "en": "en_US"}


def sample_text(lang: str, seed: int, target_bytes: int = TARGET_BYTES) -> str:
    """Seeded Faker paragraphs with names and addresses, just over `target_bytes` of UTF-8."""
    fake = Faker(LOCALES[lang])
    fake.seed_instance(seed)
    paragraphs: list[str] = []
    size = 0
    while size < target_bytes:
        paragraph = f"{fake.name()}, {fake.address()}. {fake.paragraph(nb_sentences=6)}"
        paragraphs.append(paragraph)
        size += len(paragraph.encode()) + 2
    return "\n\n".join(paragraphs)


def _peak_mib() -> float:
    # Linux reports ru_maxrss in KiB.
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lang", choices=sorted(LOCALES), required=True)
    parser.add_argument("--seed", type=int, default=20261109)
    args = parser.parse_args(argv)

    config = EngineConfig(gliner="all") if args.lang == "en" else EngineConfig()
    text = sample_text(args.lang, args.seed)
    size_kb = len(text.encode()) / 1000

    started = time.perf_counter()
    detect(text[:2000], lang="auto", config=config)  # loads and warms the models
    load_s = time.perf_counter() - started
    rss_after_load = _peak_mib()

    started = time.perf_counter()
    spans = detect(text, lang="auto", config=config)
    run_s = time.perf_counter() - started

    per_100kb = run_s / (size_kb / 100)
    gliner = sum(1 for span in spans if span.recognizer == "gliner")
    print(f"lang={args.lang} config.gliner={config.gliner} text={size_kb:.1f} KB UTF-8")
    print(f"cpu={platform.processor() or platform.machine()} python={platform.python_version()}")
    print(f"load+warm {load_s:.1f} s; peak RSS after load {rss_after_load:.0f} MiB")
    print(
        f"timed pass {run_s:.1f} s = {per_100kb:.1f} s per 100 KB; peak RSS {_peak_mib():.0f} MiB"
    )
    print(f"spans {len(spans)} (gliner {gliner})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
