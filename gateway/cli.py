"""The `safeguard` command line: `scan`, `mask` and `models`; `serve`, `audit` come later."""

import argparse
import json
import os
import sys
from pathlib import Path

from gateway import __version__
from gateway.audit import commands as audit_commands
from gateway.cloud import hub
from gateway.cloud.hub import GLINER_MODEL
from gateway.cloud.models import (
    SPACY_MODELS,
    ModelDownloadError,
    install_dir,
    installed,
    open_client,
    pull,
)
from gateway.engine.detect import EngineConfig, detect
from gateway.engine.mask import Mapping, mask
from gateway.engine.recognizers.gazetteer import load_gazetteer
from gateway.engine.types import Span
from gateway.parsers import formats

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
        help=(
            "skip names, organisations and places (no spaCy or GLiNER model is loaded; "
            "fixed date formats stay)"
        ),
    )

    mask_parser = commands.add_parser(
        "mask",
        help="replace the personal data in a file with placeholders (text, pdf, docx, xlsx, eml)",
    )
    mask_parser.add_argument("file", type=Path)
    mask_parser.add_argument(
        "-o", "--output", type=Path, help="write the masked text here instead of stdout"
    )
    mask_parser.add_argument(
        "--json-map",
        type=Path,
        help="also write the placeholder -> original map as JSON (personal data; mode 0600)",
    )
    mask_parser.add_argument(
        "--gazetteer", type=Path, help="CSV of names and codenames to always find"
    )
    mask_parser.add_argument(
        "--no-ner",
        action="store_true",
        help="skip names, organisations and places (no model is loaded; fixed date formats stay)",
    )

    models = commands.add_parser("models", help="download and list the local NER models")
    model_commands = models.add_subparsers(dest="models_command", metavar="ACTION")
    pull_parser = model_commands.add_parser("pull", help="download the pinned models (network)")
    pull_parser.add_argument(
        "--only",
        choices=[*sorted(SPACY_MODELS), "gliner"],
        help="pull one model only: a spaCy language, or gliner (Armenian and other text)",
    )
    model_commands.add_parser("list", help="show which models are installed")
    models.set_defaults(models_parser=models)
    audit_commands.register(commands.add_parser)
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
    # Wide enough for the longest recognizer name, "id_document_context".
    print(f"{'LINE:COL':<10}{'TYPE':<16}{'SCORE':<7}{'RECOGNIZER':<21}TEXT")
    for span in spans:
        line, column = _line_and_column(text, span.start)
        print(
            f"{f'{line}:{column}':<10}{span.entity_type:<16}{span.score:<7.2f}"
            f"{span.recognizer:<21}{_in_context(text, span)}"
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

    if args.no_ner:
        config = EngineConfig(gazetteer=gazetteer, ner="off", gliner="off")
    else:
        config = EngineConfig(gazetteer=gazetteer)
    spans = detect(text, config=config)
    if args.json:
        _print_json(text, spans)
    else:
        _print_table(text, spans, args.file)
    return 0


def _same_file(a: Path, b: Path) -> bool:
    return a.resolve() == b.resolve() or (a.exists() and b.exists() and a.samefile(b))


# Windows has no O_NOFOLLOW; there O_EXCL alone refuses an existing path.
_NEW_PRIVATE_FILE = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)


def _write_private(path: Path, content: str) -> None:
    """Write `content` to a new file readable by the owner only: the map holds the values.

    O_EXCL makes the create atomic and refuses any existing path, a symlink included
    (so it is never followed), and the file is 0600 from the moment it exists: an old
    file's wider mode never applies. A failed write leaves no partial map behind.
    """
    descriptor = os.open(path, _NEW_PRIVATE_FILE, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _mask_targets_conflict(args: argparse.Namespace) -> str | None:
    """Why the input, output and map paths cannot be used, or None. Names no values."""
    for target in (args.output, args.json_map):
        if target is not None and _same_file(target, args.file):
            return f"{target} is the input file; refusing to overwrite it"
    both = args.output is not None and args.json_map is not None
    if both and _same_file(args.output, args.json_map):
        # The map would replace the masked text: every value sent upstream in clear.
        return "--output and --json-map are the same file; refusing to write either"
    if args.json_map is not None and os.path.lexists(args.json_map):
        return (
            f"{args.json_map} already exists; the map is never written over an existing "
            "file. Delete it or choose another path."
        )
    return None


def _mask_document(args: argparse.Namespace, config: EngineConfig) -> tuple[str, Mapping]:
    if args.output is None and not formats.is_text(args.file):
        msg = f"{args.file.suffix} needs an output file: add -o OUT"
        raise ValueError(msg)
    result = formats.mask_file(args.file, args.output, config)
    for note in result.notes:
        print(f"safeguard: note: {note}", file=sys.stderr)
    return result.text, result.mapping


def _mask(args: argparse.Namespace) -> int:
    conflict = _mask_targets_conflict(args)
    if conflict is not None:
        print(f"safeguard: {conflict}", file=sys.stderr)
        return 2
    try:
        gazetteer = load_gazetteer(args.gazetteer) if args.gazetteer else None
        config = EngineConfig(gazetteer=gazetteer, ner="off" if args.no_ner else "auto")
        if formats.handles(args.file):  # .pdf .docx .xlsx .eml .txt (ADR 0017)
            masked, mapping = _mask_document(args, config)
        else:
            # Strict UTF-8: replacing bytes would break the exact round trip back to the file.
            text = args.file.read_bytes().decode("utf-8")
            masked, mapping = mask(text, detect(text, config=config))
            if args.output is not None:
                args.output.write_text(masked, encoding="utf-8", newline="")
    except (OSError, ValueError) as error:  # DocumentError is a ValueError
        print(f"safeguard: cannot read input: {error}", file=sys.stderr)
        return 2
    if args.json_map is not None:
        try:
            _write_private(
                args.json_map, json.dumps(mapping.to_dict(), ensure_ascii=False, indent=2)
            )
        except OSError as error:
            # A masked copy is never left behind without its map.
            if args.output is not None:
                args.output.unlink(missing_ok=True)
            print(f"safeguard: cannot write the map: {error.strerror}", file=sys.stderr)
            return 2
        print(
            f"safeguard: warning: {args.json_map} holds the original values, so it is personal "
            "data. Keep it on this device and delete it when you no longer need it.",
            file=sys.stderr,
        )
    if args.output is None:
        print(masked, end="")
    return 0


def _models_list() -> int:
    gliner = GLINER_MODEL
    # The column fits the longest name, so a name never runs into the version after it.
    width = max(len(name) for name in [gliner.name, *(s.name for s in SPACY_MODELS.values())]) + 2
    print(f"{'LANG':<6}{'MODEL':<{width}}{'VERSION':<9}{'STATUS':<11}PATH")
    # "installed" only with a marker for the current pin: a bare directory may be a cut-short pull.
    for lang, spec in SPACY_MODELS.items():
        status = "installed" if installed(spec) else "missing"
        print(f"{lang:<6}{spec.name:<{width}}{spec.version:<9}{status:<11}{install_dir(spec)}")
    status = "installed" if hub.installed(gliner) else "missing"
    path = hub.install_dir(gliner)
    print(f"{'other':<6}{gliner.name:<{width}}{gliner.revision[:7]:<9}{status:<11}{path}")
    return 0


def _models_pull(only: str | None) -> int:
    # The one command that reaches the network (ADR 0006): pinned URLs, checked digests.
    if only is None:
        wanted = list(SPACY_MODELS.values())
    elif only == "gliner":
        wanted = []
    else:
        wanted = [SPACY_MODELS[only]]
    with open_client() as client:
        for spec in wanted:
            if installed(spec):
                print(f"{spec.name} {spec.version}: already installed at {install_dir(spec)}")
                continue
            print(f"{spec.name} {spec.version}: downloading {spec.url}", flush=True)
            try:
                path = pull(spec, client)
            except (ModelDownloadError, OSError) as error:
                print(f"safeguard: {error}", file=sys.stderr)
                return 1
            print(f"{spec.name} {spec.version}: verified and installed at {path}")
        if only not in {None, "gliner"}:
            return 0
        gliner = GLINER_MODEL
        if hub.installed(gliner):
            print(f"{gliner.name}: already installed at {hub.install_dir(gliner)}")
            return 0
        print(f"{gliner.name}: downloading {len(gliner.files)} pinned files", flush=True)
        try:
            path = hub.pull(gliner, client)
        except (ModelDownloadError, OSError, ValueError) as error:
            print(f"safeguard: {error}", file=sys.stderr)
            return 1
        print(f"{gliner.name}: verified and installed at {path}")
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
    if args.command == "mask":
        return _mask(args)
    if args.command == "models":
        return _models(args)
    if args.command == "audit":
        return audit_commands.run(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
