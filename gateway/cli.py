"""The `safeguard` command line: `scan` and `models`; `mask`, `serve`, `audit` come later."""

import argparse
import json
import sys
from pathlib import Path

from gateway import __version__
from gateway.cloud.models import (
    SPACY_MODELS,
    ModelDownloadError,
    install_dir,
    open_client,
    pull,
)
from gateway.engine.detect import EngineConfig, detect
from gateway.engine.recognizers.gazetteer import load_gazetteer
from gateway.engine.types import Span

CONTEXT = 20  # characters shown on each side of a detection


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="safeguard",
        description="Local-first privacy gateway for cloud AI models.",
    )
    parser.add_argument("--version", action="version", version=f"safeguard {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")

    scan = commands.add_parser("scan", help="list the personal data found in a text file")
    scan.add_argument("file", type=Path)
    scan.add_argument("--json", action="store_true", help="print JSON instead of a table")
    scan.add_argument("--gazetteer", type=Path, help="CSV of names and codenames to always find")
    scan.add_argument(
        "--no-ner",
        action="store_true",
        help="skip names, organisations, places and dates (no model is loaded)",
    )

    models = commands.add_parser("models", help="download and list the local NER models")
    model_commands = models.add_subparsers(dest="models_command", metavar="ACTION")
    pull_parser = model_commands.add_parser("pull", help="download the pinned models (network)")
    pull_parser.add_argument(
        "--only", choices=sorted(SPACY_MODELS), help="pull one language's model only"
    )
    model_commands.add_parser("list", help="show which models are installed")
    models.set_defaults(models_parser=models)
    return parser


def _line_and_column(text: str, offset: int) -> tuple[int, int]:
    line = text.count("\n", 0, offset) + 1
    column = offset - (text.rfind("\n", 0, offset) + 1) + 1
    return line, column


def _in_context(text: str, span: Span) -> str:
    before = text[max(span.start - CONTEXT, 0) : span.start]
    after = text[span.end : span.end + CONTEXT]
    snippet = f"{before}«{text[span.start : span.end]}»{after}"
    return " ".join(snippet.split())  # one line, whatever the file's line breaks


def _print_table(text: str, spans: list[Span], file: Path) -> None:
    if not spans:
        print(f"No detections in {file}.")
        return
    print(f"{'LINE:COL':<10}{'TYPE':<16}{'SCORE':<7}{'RECOGNIZER':<13}TEXT")
    for span in spans:
        line, column = _line_and_column(text, span.start)
        print(
            f"{f'{line}:{column}':<10}{span.entity_type:<16}{span.score:<7.2f}"
            f"{span.recognizer:<13}{_in_context(text, span)}"
        )
    print(f"{len(spans)} detections in {file}.")


def _print_json(text: str, spans: list[Span]) -> None:
    items = [
        {
            "start": s.start,
            "end": s.end,
            "entity_type": str(s.entity_type),
            "score": s.score,
            "recognizer": s.recognizer,
            "text": text[s.start : s.end],
        }
        for s in spans
    ]
    print(json.dumps(items, ensure_ascii=False, indent=2))


def _scan(args: argparse.Namespace) -> int:
    try:
        # Not UTF-8 (an old Windows export, say): the digits we look for survive replacement.
        text = args.file.read_bytes().decode("utf-8", errors="replace")
        gazetteer = load_gazetteer(args.gazetteer) if args.gazetteer else None
    except (OSError, ValueError) as error:
        print(f"safeguard: cannot read input: {error}", file=sys.stderr)
        return 2

    config = EngineConfig(gazetteer=gazetteer, ner="off" if args.no_ner else "auto")
    spans = detect(text, config=config)
    if args.json:
        _print_json(text, spans)
    else:
        _print_table(text, spans, args.file)
    return 0


def _models_list() -> int:
    print(f"{'LANG':<6}{'MODEL':<18}{'VERSION':<9}{'STATUS':<11}PATH")
    for lang, spec in SPACY_MODELS.items():
        path = install_dir(spec)
        status = "installed" if path.is_dir() else "missing"
        print(f"{lang:<6}{spec.name:<18}{spec.version:<9}{status:<11}{path}")
    return 0


def _models_pull(only: str | None) -> int:
    # The one command that reaches the network (ADR 0006): pinned URLs, checked digests.
    wanted = [SPACY_MODELS[only]] if only else list(SPACY_MODELS.values())
    with open_client() as client:
        for spec in wanted:
            if install_dir(spec).is_dir():
                print(f"{spec.name} {spec.version}: already installed at {install_dir(spec)}")
                continue
            print(f"{spec.name} {spec.version}: downloading {spec.url}", flush=True)
            try:
                path = pull(spec, client)
            except (ModelDownloadError, OSError) as error:
                print(f"safeguard: {error}", file=sys.stderr)
                return 1
            print(f"{spec.name} {spec.version}: verified and installed at {path}")
    return 0


def _models(args: argparse.Namespace) -> int:
    if args.models_command == "pull":
        return _models_pull(args.only)
    if args.models_command == "list":
        return _models_list()
    args.models_parser.print_help()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "scan":
        return _scan(args)
    if args.command == "models":
        return _models(args)
    parser.print_help()
    return 0
