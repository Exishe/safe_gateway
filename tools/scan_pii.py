"""Block commits that contain identifier-shaped personal data (ADR 0009).

This is a coarse net for the repository, not the product's detector: SSN-shaped
strings and Luhn-valid card numbers. Fake values in tests are allowed when the
line carries `pragma: allowlist secret`. Findings never print the value.

Run: `python -m tools.scan_pii FILE ...`, `--staged` (the git index) or `--tracked`
(every file git tracks; used by CI).
"""

import argparse
import codecs
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Kind = Literal["ssn", "card"]

ALLOWLIST_PRAGMA = "pragma: allowlist secret"
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
# 13 to 19 digits, optionally split by single spaces or dashes.
_CARD = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    kind: Kind

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: possible {self.kind} (value hidden)"


def _luhn_valid(digits: str) -> bool:
    total = 0
    for position, char in enumerate(reversed(digits)):
        value = int(char)
        if position % 2 == 1:
            value = value * 2 - 9 if value > 4 else value * 2  # noqa: PLR2004
        total += value
    return total % 10 == 0


def _has_card(line: str) -> bool:
    for match in _CARD.finditer(line):
        digits = re.sub(r"[ -]", "", match.group())
        if _luhn_valid(digits):
            return True
    return False


def find_pii(text: str, path: str = "<text>") -> list[Finding]:
    findings = []
    for number, line in enumerate(text.splitlines(), start=1):
        if ALLOWLIST_PRAGMA in line:
            continue
        if _SSN.search(line):
            findings.append(Finding(path, number, "ssn"))
        if _has_card(line):
            findings.append(Finding(path, number, "card"))
    return findings


def _git(repo: Path, *args: str) -> bytes:
    command = ["git", *args]
    return subprocess.run(command, cwd=repo, check=True, capture_output=True).stdout  # noqa: S603


def _staged_blobs(repo: Path) -> list[tuple[str, bytes]]:
    listing = _git(repo, "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
    names = [name for name in listing.decode().split("\0") if name]
    return [(name, _git(repo, "show", f":{name}")) for name in names]


def _tracked_blobs(repo: Path) -> list[tuple[str, bytes]]:
    listing = _git(repo, "ls-files", "-z")
    names = [name for name in listing.decode().split("\0") if name]
    return [(name, (repo / name).read_bytes()) for name in names if (repo / name).is_file()]


def _file_blobs(paths: list[Path]) -> list[tuple[str, bytes]]:
    return [(str(path), path.read_bytes()) for path in paths]


def _decode(blob: bytes) -> str | None:
    """Text for scanning, or None for binary. Never drops a file for its encoding."""
    if blob.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return blob.decode("utf-16", errors="replace")  # Excel "Unicode text" exports
    if b"\0" in blob:
        return None
    # cp1252 and other legacy exports: the digits we look for are ASCII either way.
    return blob.decode("utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Find identifier-shaped personal data.")
    parser.add_argument("paths", nargs="*", type=Path)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--staged", action="store_true", help="scan the git index")
    source.add_argument("--tracked", action="store_true", help="scan every tracked file")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)

    if args.staged:
        blobs = _staged_blobs(args.repo)
    elif args.tracked:
        blobs = _tracked_blobs(args.repo)
    else:
        blobs = _file_blobs(args.paths)
    findings: list[Finding] = []
    for name, blob in blobs:
        text = _decode(blob)
        if text is not None:
            findings.extend(find_pii(text, name))

    for finding in findings:
        print(finding)
    if findings:
        print(f"Remove the value, or mark a fake test value with `# {ALLOWLIST_PRAGMA}`.")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
