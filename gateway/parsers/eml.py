"""E-mail (.eml): From/To/Cc/Subject and text parts in; a redacted copy out (ADR 0017, 0018).

Headers are read raw and decoded as unstructured text: the address parser rewrites a
value it cannot parse (a masked `[PERSON_1] <[EMAIL_1]>` reads as `<>`), so the same
unstructured header class is used to read and to write. Attachments are not read and
are removed from the copy, with every header outside the MIME structure.
"""

import html
import re
from collections.abc import Iterator, Sequence
from email import policy
from email.headerregistry import HeaderRegistry
from email.message import EmailMessage, MIMEPart
from email.parser import BytesParser
from pathlib import Path
from typing import Literal

from gateway.parsers.document import Document, DocumentBuilder, DocumentError, EmailPosition
from gateway.parsers.html_text import to_text
from gateway.parsers.output import atomic_output
from gateway.parsers.redact import UnitMask

HEADERS = ("From", "To", "Cc", "Subject")
# Kept on every part of the copy; everything else (Received, Message-ID, Reply-To,
# X-..., Content-Disposition with its file name) is dropped.
_STRUCTURE_HEADERS = frozenset({"mime-version", "content-type", "content-transfer-encoding"})
# RFC 5322 unfolding: a line break followed by a space or tab is not part of the value.
_FOLD = re.compile(r"\r?\n(?=[ \t])")
_POLICY = policy.default.clone(header_factory=HeaderRegistry(use_default_map=False))

Kind = Literal["plain", "html", "other"]


def _read(path: Path) -> EmailMessage:
    message = BytesParser(policy=_POLICY).parsebytes(path.read_bytes())
    if not isinstance(message, EmailMessage):  # pragma: no cover  (policy.default gives one)
        msg = f"{path.name}: not an e-mail message"
        raise DocumentError(msg)
    return message


def _header_values(message: EmailMessage) -> Iterator[tuple[str, int, str]]:
    """(name, occurrence, decoded value) for each wanted header, in HEADERS order."""
    for name in HEADERS:
        values = [value for key, value in message.raw_items() if key.lower() == name.lower()]
        for index, raw in enumerate(values):
            yield name, index, str(_POLICY.header_factory(name, _FOLD.sub("", raw)))


def _leaves(part: MIMEPart) -> Iterator[tuple[MIMEPart, MIMEPart | None, Kind]]:
    """(part, its parent, kind) for every non-multipart part, in order.

    An attachment is a leaf of kind "other" even when it is itself a message.
    """

    def walk(
        current: MIMEPart, parent: MIMEPart | None
    ) -> Iterator[tuple[MIMEPart, MIMEPart | None, Kind]]:
        attachment = current.get_content_disposition() == "attachment"
        if current.is_multipart() and not attachment:
            for child in current.iter_parts():
                yield from walk(child, current)
            return
        content_type = current.get_content_type()
        if attachment or content_type not in {"text/plain", "text/html"}:
            yield current, parent, "other"
        else:
            yield current, parent, "plain" if content_type == "text/plain" else "html"

    yield from walk(part, None)


def _body_text(part: MIMEPart, kind: Kind) -> str:
    content = part.get_content()
    text = content if isinstance(content, str) else ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return to_text(text) if kind == "html" else text.rstrip("\n")


def parse(path: Path) -> Document:
    message = _read(path)
    builder = DocumentBuilder()
    for name, index, value in _header_values(message):
        builder.separate(f"{name}: ")
        builder.add(value, EmailPosition(name, index))
        builder.separate("\n")
    removed = 0
    number = 0
    for part, _, kind in _leaves(message):
        if kind == "other":
            removed += 1
            continue
        builder.separate("\n")
        builder.add(_body_text(part, kind), EmailPosition("body", number))
        number += 1
    return builder.build("eml", {"attachments": removed})


def _strip_headers(part: MIMEPart) -> None:
    for name in set(part.keys()):
        if name.lower() not in _STRUCTURE_HEADERS:
            del part[name]
    if part.is_multipart():
        part.preamble = None  # free text a mail client never shows
        part.epilogue = None


def write_redacted(
    source: Path, document: Document, masks: Sequence[UnitMask], output: Path
) -> list[str]:
    message = _read(source)
    headers = list(_header_values(message))
    leaves = list(_leaves(message))
    text_parts = [(part, kind) for part, _, kind in leaves if kind != "other"]
    if len(headers) + len(text_parts) != len(document.segments) or len(masks) != len(
        document.segments
    ):
        msg = f"{source.name} changed since it was read; mask it again"
        raise DocumentError(msg)

    for part, parent, kind in leaves:
        payload = parent.get_payload() if parent is not None else None
        if kind == "other" and isinstance(payload, list):
            payload.remove(part)
    for part in message.walk():
        _strip_headers(part)
    for (name, _, _), unit in zip(headers, masks, strict=False):
        message[name] = unit.text
    for (part, kind), unit in zip(text_parts, masks[len(headers) :], strict=True):
        if kind == "plain":
            part.set_content(unit.text, subtype="plain", charset="utf-8")
        else:
            page = f"<html><body><pre>{html.escape(unit.text)}</pre></body></html>"
            part.set_content(page, subtype="html", charset="utf-8")
    if leaves and leaves[0][2] == "other" and leaves[0][1] is None:
        message.set_content("", subtype="plain", charset="utf-8")  # the message was one attachment

    with atomic_output(output) as temporary:
        temporary.write_bytes(message.as_bytes(policy=_POLICY))
    removed = sum(1 for _, _, kind in leaves if kind == "other")
    return [f"removed {removed} attachment or non-text part"] if removed else []
