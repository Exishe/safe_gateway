"""Checks shared by the zip-based formats (.docx, .xlsx): size caps and parse errors."""

import zipfile
from pathlib import Path
from xml.etree.ElementTree import ParseError

from lxml.etree import XMLSyntaxError  # ty: ignore[import-not-found]  (no type stubs)

from gateway.parsers.document import DocumentError

# A 332 KB .docx inflated to 630 MB of memory (security review M6). Office documents
# for small firms are far below these caps; anything above is refused, not truncated.
MAX_UNCOMPRESSED = 200 * 1024 * 1024  # bytes, all members together
MAX_MEMBERS = 10_000


def parse_errors() -> tuple[type[Exception], ...]:
    """What the XML parsers raise on a malformed part: neither is a ValueError."""
    return (XMLSyntaxError, ParseError)


def check_archive(path: Path) -> None:
    """Refuse an archive that is not a zip, has too many members, or inflates too far.

    The sizes are the ones the archive declares. That is enough: Python's zipfile stops
    reading a member at its declared size and then fails its CRC check, so a member
    cannot inflate past what is declared here.
    """
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
    except (zipfile.BadZipFile, zipfile.LargeZipFile) as error:
        msg = f"{path.name} is not a readable zip archive ({error})"
        raise DocumentError(msg) from error
    if len(members) > MAX_MEMBERS:
        msg = f"{path.name} has {len(members)} members; the limit is {MAX_MEMBERS}"
        raise DocumentError(msg)
    total = sum(member.file_size for member in members)
    if total > MAX_UNCOMPRESSED:
        size, limit = total // (1024 * 1024), MAX_UNCOMPRESSED // (1024 * 1024)
        msg = f"{path.name} is {size} MB uncompressed; the limit is {limit} MB"
        raise DocumentError(msg)
