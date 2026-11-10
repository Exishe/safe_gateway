"""Block commits that contain identifier-shaped personal data (ADR 0009).

Two layers. Shape checks (SSN-shaped strings, Luhn-valid card numbers) are
deliberately stricter than the engine, for example they flag the well-known fake
SSN used in every tutorial. On top of them the product's own structured recognizers run with
NER off (dogfooding), so cards split by no-break spaces or zero-width characters,
IBANs and Russian INN/SNILS are found too. Emails, phones, IPs and URLs are not
reported: they are far too common in a code repository. Fake values in tests are
allowed when the line carries `pragma: allowlist secret`. Findings never print the
value.

Run: `python -m tools.scan_pii FILE ...`, `--staged` (the git index) or `--tracked`
(every file git tracks; used by CI).
"""

import argparse
import codecs
import html
import io
import re
import subprocess
import sys
import zipfile
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from gateway.engine.detect import EngineConfig, detect
from gateway.engine.types import EntityType

Kind = Literal["ssn", "card", "credit_card", "iban", "national_id"]

# Engine types that are reported, and the shape kind that already covers each.
_ENGINE_KINDS: dict[EntityType, tuple[Kind, Kind | None]] = {
    EntityType.CREDIT_CARD: ("credit_card", "card"),
    EntityType.IBAN: ("iban", None),
    EntityType.SSN: ("ssn", "ssn"),
    EntityType.NATIONAL_ID: ("national_id", None),
}
# Checksum-validated values only (score 1.0, ADR 0011). The shape-only and keyword-gated
# rules (ADR 0023) are for masking, where a miss leaks; in a commit gate every order
# number and "ID 123456789" in a comment would block the commit.
_VALIDATED = 1.0
_STRUCTURED_ONLY = EngineConfig(
    ner="off",
    thresholds={EntityType.CREDIT_CARD: _VALIDATED, EntityType.NATIONAL_ID: _VALIDATED},
)
# Generated lock files are pages of hashes and versions (uv.lock is 450 KB, about 5 s
# in the engine, 90% of a full scan). They keep the shape checks, not the engine pass.
# npm's package-lock.json is the same: its sha512 integrity hashes hold IBAN-shaped runs.
_SHAPE_ONLY_FILES = frozenset({"uv.lock", "package-lock.json"})

# Office files are zip containers of XML. Caps keep a zip bomb from eating the commit.
_OFFICE_PART = re.compile(r"^(?:word|xl|ppt)/.+\.xml$")
_MAX_ZIP_BYTES = 50 * 1024 * 1024
_MAX_ZIP_MEMBERS = 2000
# Closing tags that end a line (paragraph, row, slide paragraph) or a cell.
_LINE_END = re.compile(r"</(?:w:p|row|a:p)>")
_CELL_END = re.compile(r"</(?:c|w:tc|a:tc|si)>")
_TAG = re.compile(r"<[^>]*>")

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


def _line_starts(lines: list[str]) -> list[int]:
    starts = []
    offset = 0
    for line in lines:
        starts.append(offset)
        offset += len(line)
    return starts


def _inside_a_word(text: str, start: int, end: int) -> bool:
    """Digits glued to letters are hashes or ids, as in the shape regex's `\\b`."""
    return (start > 0 and text[start - 1].isalpha()) or (end < len(text) and text[end].isalpha())


def _engine_findings(text: str, path: str, shape_hits: set[tuple[int, Kind]]) -> list[Finding]:
    # keepends=True so offsets add up; splitlines() splits on the same breaks as the
    # shape pass, which keeps the line numbers of both layers aligned.
    lines = text.splitlines(keepends=True)
    starts = _line_starts(lines)
    findings = []
    for span in detect(text, config=_STRUCTURED_ONLY):
        reported = _ENGINE_KINDS.get(span.entity_type)
        if reported is None:
            continue
        kind, shape_kind = reported
        index = bisect_right(starts, span.start) - 1
        if ALLOWLIST_PRAGMA in lines[index] or (index + 1, shape_kind) in shape_hits:
            continue
        if span.entity_type != EntityType.IBAN and _inside_a_word(text, span.start, span.end):
            continue
        findings.append(Finding(path, index + 1, kind))
    return findings


def find_pii(text: str, path: str = "<text>") -> list[Finding]:
    findings = []
    for number, line in enumerate(text.splitlines(), start=1):
        if ALLOWLIST_PRAGMA in line:
            continue
        if _SSN.search(line):
            findings.append(Finding(path, number, "ssn"))
        if _has_card(line):
            findings.append(Finding(path, number, "card"))
    shape_hits = {(finding.line, finding.kind) for finding in findings}
    if Path(path).name not in _SHAPE_ONLY_FILES:
        findings.extend(_engine_findings(text, path, shape_hits))
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


def _xml_to_text(xml: bytes) -> str:
    """Visible text of an Office XML part. Runs are joined so split numbers re-form."""
    text = xml.decode("utf-8", errors="replace")
    text = _LINE_END.sub("\n", text)
    text = _CELL_END.sub(" ", text)
    return html.unescape(_TAG.sub("", text))


def _office_texts(blob: bytes, name: str) -> list[tuple[str, str]] | None:
    """(label, text) per Office XML part, or None when the blob is not an Office zip."""
    if not blob.startswith(b"PK") or not zipfile.is_zipfile(io.BytesIO(blob)):
        return None
    try:
        archive = zipfile.ZipFile(io.BytesIO(blob))
        members = [info for info in archive.infolist() if _OFFICE_PART.match(info.filename)]
        if not members:
            return None
        texts: list[tuple[str, str]] = []
        budget = _MAX_ZIP_BYTES
        if len(members) > _MAX_ZIP_MEMBERS:
            print(f"{name}: not fully scanned (too many parts)", file=sys.stderr)
            members = members[:_MAX_ZIP_MEMBERS]
        for info in members:
            # file_size is only the header's claim, so the read itself is capped too.
            if info.file_size > budget:
                print(f"{name}: not fully scanned (size cap)", file=sys.stderr)
                break
            with archive.open(info) as part:
                data = part.read(budget + 1)
            if len(data) > budget:
                print(f"{name}: not fully scanned (size cap)", file=sys.stderr)
                break
            budget -= len(data)
            texts.append((f"{name}:{info.filename}", _xml_to_text(data)))
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, OSError):
        print(f"{name}: unreadable archive, skipped", file=sys.stderr)
        return []
    return texts


def _scannable_texts(name: str, blob: bytes) -> list[tuple[str, str]]:
    office = _office_texts(blob, name)
    if office is not None:
        return office
    text = _decode(blob)
    return [] if text is None else [(name, text)]


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
        for label, text in _scannable_texts(name, blob):
            findings.extend(find_pii(text, label))

    for finding in findings:
        print(finding)
    if findings:
        print(f"Remove the value, or mark a fake test value with `# {ALLOWLIST_PRAGMA}`.")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
