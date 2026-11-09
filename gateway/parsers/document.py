"""What a parser returns: text, the source unit of every character, metadata (ADR 0017)."""

from bisect import bisect_right
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal


class DocumentError(ValueError):
    """A file we cannot read, or cannot write back, completely. The CLI exits 2."""


@dataclass(frozen=True)
class TextPosition:
    line: int  # 1-based


@dataclass(frozen=True)
class ParagraphPosition:
    part: str  # e.g. /word/document.xml
    index: int  # 0-based, among the part's paragraphs in document order


@dataclass(frozen=True)
class CellPosition:
    sheet: str
    cell: str  # "A1"; empty for a sheet title
    field: Literal["value", "comment", "title"] = "value"


@dataclass(frozen=True)
class EmailPosition:
    field: str  # "From", "To", "Cc", "Subject", or "body"
    index: int  # occurrence of the header, or number of the text part


@dataclass(frozen=True)
class PagePosition:
    page: int  # 1-based
    line: int  # 0-based, on that page
    x: float  # baseline origin in PDF points, page's displayed orientation
    y: float
    size: float  # font size in points
    ocr: bool = False


Source = TextPosition | ParagraphPosition | CellPosition | EmailPosition | PagePosition


@dataclass(frozen=True)
class Segment:
    """text[start:end] is the whole text of one source unit."""

    start: int
    end: int
    source: Source


@dataclass(frozen=True)
class Document:
    format: str
    text: str
    segments: tuple[Segment, ...]
    # Structural facts only (page count, items removed); never document content.
    metadata: Mapping[str, str | int] = field(default_factory=dict)

    def segment_text(self, index: int) -> str:
        segment = self.segments[index]
        return self.text[segment.start : segment.end]

    def source_at(self, offset: int) -> Source | None:
        """The unit holding text[offset], or None for a separator we added."""
        index = bisect_right([s.start for s in self.segments], offset) - 1
        while index >= 0:
            segment = self.segments[index]
            if segment.start <= offset < segment.end:
                return segment.source
            if segment.end <= offset and segment.end > segment.start:
                return None
            index -= 1  # an empty unit at the same start
        return None


class DocumentBuilder:
    """Appends units and separators in order, keeping offsets."""

    def __init__(self) -> None:
        self._pieces: list[str] = []
        self._segments: list[Segment] = []
        self._length = 0

    def add(self, text: str, source: Source) -> None:
        self._segments.append(Segment(self._length, self._length + len(text), source))
        self._append(text)

    def separate(self, text: str) -> None:
        """Text that is not from the source: line breaks, tabs, labels."""
        self._append(text)

    def _append(self, text: str) -> None:
        self._pieces.append(text)
        self._length += len(text)

    def build(self, format_name: str, metadata: Mapping[str, str | int] | None = None) -> Document:
        return Document(
            format=format_name,
            text="".join(self._pieces),
            segments=tuple(self._segments),
            metadata=dict(metadata or {}),
        )
