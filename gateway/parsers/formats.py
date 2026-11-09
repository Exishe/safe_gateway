"""Which parser reads which file, and the one call the CLI makes (ADR 0017 point 7)."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from gateway.engine.detect import EngineConfig, detect
from gateway.engine.mask import Mapping
from gateway.parsers import docx, eml, pdf, txt, xlsx
from gateway.parsers.document import Document
from gateway.parsers.redact import UnitMask, mask_units, masked_text


@dataclass(frozen=True)
class Handler:
    parse: Callable[[Path], Document]
    write: Callable[[Path, Document, Sequence[UnitMask], Path], list[str]]


SUFFIXES: dict[str, Handler] = {
    ".pdf": Handler(pdf.parse, pdf.write_redacted),
    ".docx": Handler(docx.parse, docx.write_redacted),
    ".xlsx": Handler(xlsx.parse, xlsx.write_redacted),
    ".eml": Handler(eml.parse, eml.write_redacted),
    ".txt": Handler(txt.parse, txt.write_redacted),
}


@dataclass(frozen=True)
class MaskResult:
    text: str  # the masked document text, as the model would read it
    mapping: Mapping
    notes: list[str]  # what the copy does not carry (ADR 0018)


def handles(path: Path) -> bool:
    return path.suffix.lower() in SUFFIXES


def is_text(path: Path) -> bool:
    """Only plain text can be written to stdout; every other format needs a file."""
    return path.suffix.lower() == ".txt"


def mask_file(source: Path, output: Path | None, config: EngineConfig) -> MaskResult:
    """Parse, detect, mask unit by unit with one mapping, and write the copy if asked."""
    handler = SUFFIXES[source.suffix.lower()]
    document = handler.parse(source)
    masks, mapping = mask_units(document, detect(document.text, config=config))
    notes = handler.write(source, document, masks, output) if output is not None else []
    return MaskResult(masked_text(document, masks), mapping, notes)
