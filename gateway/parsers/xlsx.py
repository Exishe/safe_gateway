"""Excel (.xlsx): cell values, comments and sheet titles in; a redacted copy out.

Read with `data_only=True`, so a formula is its cached value and the copy keeps no
formula (ADR 0018 point 3). One walk, `_events`, serves parse and write (ADR 0017).
"""

import datetime as dt
import re
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import openpyxl
from openpyxl.comments import Comment
from openpyxl.formatting.formatting import ConditionalFormattingList
from openpyxl.packaging.core import DocumentProperties
from openpyxl.packaging.custom import CustomPropertyList
from openpyxl.workbook.workbook import Workbook
from openpyxl.worksheet.datavalidation import DataValidationList
from openpyxl.worksheet.header_footer import HeaderFooter
from openpyxl.worksheet.worksheet import Worksheet

from gateway.parsers.document import CellPosition, Document, DocumentBuilder, DocumentError
from gateway.parsers.output import atomic_output
from gateway.parsers.redact import UnitMask

REDACTED_AUTHOR = "Redacted"
_MAX_TITLE = 31
_TITLE_FORBIDDEN = re.compile(r"[\[\]:*?/\\]")


@dataclass(frozen=True)
class _Unit:
    text: str
    source: CellPosition
    write: Callable[[str], None]


_Event = str | _Unit  # a separator, or a unit


def _cell_text(value: object) -> str | None:
    """The text a cell shows; None for an empty or boolean cell."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e16:  # noqa: PLR2004
        return str(int(value))
    if isinstance(value, dt.datetime) and value.time() == dt.time():
        return value.date().isoformat()  # Excel stores every date as a date and time
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat()
    return str(value)


def _set_value(cell: object, text: str) -> None:
    cell.value = text  # ty: ignore[unresolved-attribute]
    cell.data_type = "s"  # ty: ignore[unresolved-attribute]  (never a formula, even "=...")


def _set_comment(cell: object, text: str) -> None:
    cell.comment = Comment(text, REDACTED_AUTHOR)  # ty: ignore[unresolved-attribute]


def _sheet_events(sheet: Worksheet, renames: dict[str, str]) -> Iterator[_Event]:
    title = sheet.title
    yield _Unit(title, CellPosition(title, "", "title"), lambda text: renames.update({title: text}))
    comments = []
    for row in sheet.iter_rows():
        yield "\n"
        column = 0
        for cell in row:
            text = _cell_text(cell.value)
            if cell.comment is not None:
                comments.append(cell)
            if text is None:
                continue
            yield "\t" * (cell.column - 1 - column)
            column = cell.column - 1
            yield _Unit(
                text,
                CellPosition(title, cell.coordinate),
                lambda new, cell=cell: _set_value(cell, new),
            )
    for cell in comments:
        yield "\n"
        yield _Unit(
            cell.comment.text,
            CellPosition(title, cell.coordinate, "comment"),
            lambda new, cell=cell: _set_comment(cell, new),
        )


def _events(workbook: Workbook, renames: dict[str, str]) -> Iterator[_Event]:
    for index, sheet in enumerate(workbook.worksheets):
        if index:
            yield "\n\n"
        yield from _sheet_events(sheet, renames)


def _load(path: Path) -> Workbook:
    try:
        return openpyxl.load_workbook(path, data_only=True)
    except (OSError, KeyError, ValueError, TypeError) as error:
        msg = f"{path.name} is not a readable .xlsx workbook ({error})"
        raise DocumentError(msg) from error


def parse(path: Path) -> Document:
    workbook = _load(path)
    builder = DocumentBuilder()
    for event in _events(workbook, {}):
        if isinstance(event, str):
            builder.separate(event)
        else:
            builder.add(event.text, event.source)
    return builder.build("xlsx", {"sheets": len(workbook.worksheets)})


def _safe_title(masked: str, taken: set[str], index: int) -> str:
    """A legal, unique sheet title: placeholders lose their brackets (`SSN_1`)."""
    title = _TITLE_FORBIDDEN.sub("", masked).strip(" '")
    if not title or len(title) > _MAX_TITLE or title in taken:
        title = f"Sheet{index + 1}"
    return title


def _scrub(workbook: Workbook) -> int:
    """Remove what the copy must not carry (ADR 0018 point 3); returns images and charts."""
    removed = 0
    for chartsheet in list(workbook.chartsheets):
        workbook.remove(chartsheet)
        removed += 1
    for sheet in workbook.worksheets:
        removed += len(sheet._images) + len(sheet._charts)  # noqa: SLF001  (no public API)
        sheet._images = []  # noqa: SLF001
        sheet._charts = []  # noqa: SLF001
        sheet._pivots = []  # noqa: SLF001
        sheet.data_validations = DataValidationList()
        sheet.conditional_formatting = ConditionalFormattingList()
        sheet.HeaderFooter = HeaderFooter()
        sheet.defined_names.clear()
        for row in sheet.iter_rows():
            for cell in row:
                cell.hyperlink = None
                if cell.comment is not None and cell.comment.author != REDACTED_AUTHOR:
                    cell.comment = Comment(cell.comment.text, REDACTED_AUTHOR)
    workbook.defined_names.clear()
    workbook._external_links = []  # noqa: SLF001
    workbook.properties = DocumentProperties(creator="")
    workbook.custom_doc_props = CustomPropertyList()
    return removed


def write_redacted(
    source: Path, document: Document, masks: Sequence[UnitMask], output: Path
) -> list[str]:
    workbook = _load(source)
    renames: dict[str, str] = {}
    units = [event for event in _events(workbook, renames) if isinstance(event, _Unit)]
    if len(units) != len(document.segments) or len(masks) != len(units):
        msg = f"{source.name} changed since it was read; mask it again"
        raise DocumentError(msg)
    for unit, mask in zip(units, masks, strict=True):
        if mask.text != unit.text:
            unit.write(mask.text)
    removed = _scrub(workbook)
    taken = {sheet.title for sheet in workbook.worksheets if sheet.title not in renames}
    for index, sheet in enumerate(workbook.worksheets):
        if sheet.title in renames:
            sheet.title = _safe_title(renames[sheet.title], taken, index)
            taken.add(sheet.title)
    with atomic_output(output) as temporary:
        workbook.save(temporary)
    return [f"removed {removed} image or chart"] if removed else []
