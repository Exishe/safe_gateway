"""Excel (.xlsx): cell values, comments and sheet titles in; a redacted copy out.

Read with `data_only=True`, so a formula is its cached value and the copy keeps no
formula (ADR 0018 point 3). One walk, `_events`, serves parse and write (ADR 0017).
"""

import datetime as dt
import re
import zipfile
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import openpyxl
from openpyxl.comments import Comment
from openpyxl.formatting.formatting import ConditionalFormattingList
from openpyxl.packaging.core import DocumentProperties
from openpyxl.packaging.custom import CustomPropertyList
from openpyxl.styles.differential import DifferentialStyleList
from openpyxl.styles.numbers import BUILTIN_FORMATS_MAX_SIZE, is_datetime, is_timedelta_format
from openpyxl.styles.table import TableStyleList
from openpyxl.utils.indexed_list import IndexedList
from openpyxl.workbook.workbook import Workbook
from openpyxl.worksheet.datavalidation import DataValidationList
from openpyxl.worksheet.filters import AutoFilter
from openpyxl.worksheet.header_footer import HeaderFooter
from openpyxl.worksheet.scenario import ScenarioList
from openpyxl.worksheet.worksheet import Worksheet

from gateway.parsers.document import CellPosition, Document, DocumentBuilder, DocumentError
from gateway.parsers.output import atomic_output
from gateway.parsers.redact import UnitMask
from gateway.parsers.zipcheck import check_archive, parse_errors

REDACTED_AUTHOR = "Redacted"
_MAX_TITLE = 31
_TITLE_FORBIDDEN = re.compile(r"[\[\]:*?/\\]")
# Built-in number formats that replace custom ones (ECMA-376 part 1, 18.8.30).
_BUILTIN_DATES = {"date": 14, "datetime": 22, "time": 21}
_BUILTIN_ELAPSED = 46  # [h]:mm:ss
# The only package members the copy may hold: anything else openpyxl writes is a
# construct this module has not reviewed, and the copy is refused (fail closed).
_KEPT_MEMBERS = re.compile(
    r"\[Content_Types\]\.xml|_rels/\.rels|docProps/(?:core|app)\.xml"
    r"|xl/(?:workbook|styles|sharedStrings)\.xml|xl/_rels/workbook\.xml\.rels"
    r"|xl/theme/theme1\.xml|xl/worksheets/sheet\d+\.xml|xl/worksheets/_rels/sheet\d+\.xml\.rels"
    r"|xl/comments/comment\d+\.xml|xl/drawings/commentsDrawing\d+\.vml"
)


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
    check_archive(path)
    try:
        return openpyxl.load_workbook(path, data_only=True)
    except (OSError, KeyError, ValueError, TypeError, *parse_errors()) as error:
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


def _general_number_formats(workbook: Workbook) -> None:
    """Every custom number format becomes a built-in one: a format code can hold literal
    text (`"Anna Petrova "0`) that is shown next to the value but is not a cell value.
    Dates and times keep a built-in date or time format, everything else is General."""
    codes = list(workbook._number_formats)  # noqa: SLF001  (no public API for styles)

    def built_in(number: int) -> int:
        index = number - BUILTIN_FORMATS_MAX_SIZE
        code = codes[index] if index < len(codes) else "General"
        if is_timedelta_format(code):
            return _BUILTIN_ELAPSED
        return _BUILTIN_DATES.get(is_datetime(code), 0)

    styles = list(workbook._cell_styles)  # noqa: SLF001  (no public API for styles)
    styles += [named._style for named in workbook._named_styles]  # noqa: SLF001
    for sheet in workbook.worksheets:
        styles += [cell._style for cell in sheet._cells.values()]  # noqa: SLF001
        dimensions = [*sheet.row_dimensions.values(), *sheet.column_dimensions.values()]
        styles += [dimension._style for dimension in dimensions]  # noqa: SLF001
    for style in styles:
        if style is not None and style.numFmtId >= BUILTIN_FORMATS_MAX_SIZE:
            style.numFmtId = built_in(style.numFmtId)
    workbook._number_formats = IndexedList()  # noqa: SLF001
    # Differential formats (conditional formats, table styles) carry number formats too.
    workbook._differential_styles = DifferentialStyleList()  # noqa: SLF001
    workbook._table_styles = TableStyleList()  # noqa: SLF001


def _rename_named_styles(workbook: Workbook) -> None:
    """A custom cell style's name is free text; built-in styles keep theirs."""
    number = 0
    for named in workbook._named_styles:  # noqa: SLF001
        if named.builtinId is None:
            number += 1
            named.name = f"Style {number}"


def _scrub_sheet(sheet: Worksheet) -> tuple[int, int]:
    """Clear one sheet's structures (ADR 0018 point 3); returns (images and charts, tables)."""
    removed = len(sheet._images) + len(sheet._charts)  # noqa: SLF001  (no public API)
    tables = len(sheet.tables)
    sheet._images = []  # noqa: SLF001
    sheet._charts = []  # noqa: SLF001
    sheet._pivots = []  # noqa: SLF001
    # A table repeats its header cells as column names and may hold totals labels and
    # formulas; dropping it keeps the cells and loses only the table styling.
    sheet.tables.clear()
    # Filter criteria and sort conditions hold cell values; the drop-down range stays.
    sheet.auto_filter = AutoFilter(ref=sheet.auto_filter.ref)
    sheet.scenarios = ScenarioList()  # what-if input values, user names, comments
    sheet.data_validations = DataValidationList()
    sheet.conditional_formatting = ConditionalFormattingList()
    sheet.HeaderFooter = HeaderFooter()
    sheet.defined_names.clear()
    for row in sheet.iter_rows():
        for cell in row:
            cell.hyperlink = None
            if cell.comment is not None and cell.comment.author != REDACTED_AUTHOR:
                cell.comment = Comment(cell.comment.text, REDACTED_AUTHOR)
    return removed, tables


def _scrub(workbook: Workbook) -> tuple[int, int]:
    """Remove what the copy must not carry (ADR 0018 point 3).

    Returns the number of images and charts, and of tables, removed.
    """
    removed = tables = 0
    for chartsheet in list(workbook.chartsheets):
        workbook.remove(chartsheet)
        removed += 1
    for sheet in workbook.worksheets:
        sheet_removed, sheet_tables = _scrub_sheet(sheet)
        removed += sheet_removed
        tables += sheet_tables
    _general_number_formats(workbook)
    _rename_named_styles(workbook)
    workbook.defined_names.clear()
    workbook._external_links = []  # noqa: SLF001
    workbook.loaded_theme = None  # openpyxl writes its default theme instead
    workbook.properties = DocumentProperties(creator="")
    workbook.custom_doc_props = CustomPropertyList()
    return removed, tables


def _check_members(path: Path, source: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        unexpected = [n for n in archive.namelist() if not _KEPT_MEMBERS.fullmatch(n)]
    if unexpected:
        msg = f"{source.name}: the copy would hold {unexpected[0]}, which is not redacted"
        raise DocumentError(msg)


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
    removed, tables = _scrub(workbook)
    taken = {sheet.title for sheet in workbook.worksheets if sheet.title not in renames}
    for index, sheet in enumerate(workbook.worksheets):
        if sheet.title in renames:
            sheet.title = _safe_title(renames[sheet.title], taken, index)
            taken.add(sheet.title)
    with atomic_output(output) as temporary:
        workbook.save(temporary)
        _check_members(temporary, source)
    notes = [f"removed {removed} image or chart"] if removed else []
    return notes + ([f"removed {tables} table (cells kept)"] if tables else [])
