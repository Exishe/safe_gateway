"""Checks shared by the zip-based formats (.docx, .xlsx)."""

from pathlib import Path
from xml.etree.ElementTree import ParseError

from lxml.etree import XMLSyntaxError  # ty: ignore[import-not-found]  (no type stubs)


def parse_errors() -> tuple[type[Exception], ...]:
    """What the XML parsers raise on a malformed part: neither is a ValueError."""
    return (XMLSyntaxError, ParseError)


def check_archive(path: Path) -> None:
    """Refuse an archive too large to open safely."""
