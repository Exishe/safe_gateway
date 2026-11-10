from gateway.parsers.document import (
    CellPosition,
    DocumentBuilder,
    DocumentError,
    TextPosition,
)


def test_builder_records_each_unit_and_its_source() -> None:
    builder = DocumentBuilder()
    builder.add("Name\tSSN", CellPosition("Sheet1", "A1"))
    builder.separate("\n")
    builder.add("Anna", TextPosition(2))
    document = builder.build("test", {"units": 2})

    assert document.text == "Name\tSSN\nAnna"
    assert document.segment_text(0) == "Name\tSSN"
    assert document.segment_text(1) == "Anna"
    assert document.segments[1].start == 9
    assert document.metadata == {"units": 2}
    assert document.format == "test"


def test_source_at_maps_an_offset_back_and_separators_to_none() -> None:
    builder = DocumentBuilder()
    builder.add("first", TextPosition(1))
    builder.separate("\n")
    builder.add("second", TextPosition(2))
    document = builder.build("txt")

    assert document.source_at(0) == TextPosition(1)
    assert document.source_at(4) == TextPosition(1)
    assert document.source_at(5) is None  # the separator
    assert document.source_at(6) == TextPosition(2)
    assert document.source_at(99) is None


def test_empty_units_are_kept_so_the_walk_stays_aligned() -> None:
    builder = DocumentBuilder()
    builder.add("", TextPosition(1))
    builder.separate("\n")
    builder.add("x", TextPosition(2))
    document = builder.build("txt")
    assert len(document.segments) == 2
    assert document.source_at(1) == TextPosition(2)


def test_document_error_is_a_value_error() -> None:
    assert issubclass(DocumentError, ValueError)
