"""The `safeguard` command line. Subcommands arrive with their phases (scan, mask, serve, audit)."""

import argparse

from gateway import __version__


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="safeguard",
        description="Local-first privacy gateway for cloud AI models.",
    )
    parser.add_argument("--version", action="version", version=f"safeguard {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    parser.parse_args(argv)
    parser.print_help()
    return 0
