"""The `safeguard` command line: `scan` now; `mask`, `serve`, `audit` come with their phases."""

import argparse
import json
import sys
from pathlib import Path

from gateway import __version__
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

    spans = detect(text, config=EngineConfig(gazetteer=gazetteer))
    if args.json:
        _print_json(text, spans)
    else:
        _print_table(text, spans, args.file)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "scan":
        return _scan(args)
    parser.print_help()
    return 0
