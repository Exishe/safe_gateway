"""Plain text: UTF-8, one unit per line, line breaks and a BOM kept (ADR 0017, 0018)."""

import codecs
import re
from collections.abc import Sequence
from pathlib import Path

from gateway.parsers.document import Document, DocumentBuilder, DocumentError, TextPosition
from gateway.parsers.output import atomic_output
from gateway.parsers.redact import UnitMask, masked_text

# Only real line breaks; str.splitlines() would also split on \v, \f and U+2028.
_LINE_BREAK = re.compile(r"(\r\n|\n|\r)")


def parse(path: Path) -> Document:
    data = path.read_bytes()
    bom = data.startswith(codecs.BOM_UTF8)
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        # Strict: replacing bytes would break the exact round trip back to the file.
        msg = f"{path.name} is not UTF-8 text ({error.reason} at byte {error.start})"
        raise DocumentError(msg) from error
    builder = DocumentBuilder()
    parts = _LINE_BREAK.split(text)
    for index, part in enumerate(parts):
        if index % 2:
            builder.separate(part)
        else:
            builder.add(part, TextPosition(index // 2 + 1))
    return builder.build("txt", {"bom": int(bom)})


def write_redacted(
    source: Path,  # noqa: ARG001  (uniform writer signature; the text is all in `document`)
    document: Document,
    masks: Sequence[UnitMask],
    output: Path,
) -> list[str]:
    data = masked_text(document, masks).encode("utf-8")
    if document.metadata.get("bom"):
        data = codecs.BOM_UTF8 + data
    with atomic_output(output) as temporary:
        temporary.write_bytes(data)
    return []
