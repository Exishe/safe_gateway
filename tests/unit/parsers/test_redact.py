import pytest

from gateway.engine.mask import Mapping, Replacement, mask
from gateway.engine.types import EntityType, Span
from gateway.parsers.document import DocumentBuilder, DocumentError, TextPosition
from gateway.parsers.redact import distribute, mask_units, masked_text

EMAIL = "anna@example.org"


def _document(*units: str):  # noqa: ANN202  (test helper)
    builder = DocumentBuilder()
    for index, unit in enumerate(units):
        if index:
            builder.separate("\n")
        builder.add(unit, TextPosition(index + 1))
    return builder.build("txt")


def _email_spans(text: str) -> list[Span]:
    spans, start = [], text.find(EMAIL)
    while start != -1:
        spans.append(Span(start, start + len(EMAIL), EntityType.EMAIL, 1.0, "email"))
        start = text.find(EMAIL, start + 1)
    return spans


def test_each_unit_is_masked_with_one_mapping() -> None:
    document = _document(f"to {EMAIL}", "nothing", f"again {EMAIL}")
    masks, mapping = mask_units(document, _email_spans(document.text))

    assert [m.text for m in masks] == ["to [EMAIL_1]", "nothing", "again [EMAIL_1]"]
    assert masks[0].replacements == (Replacement(3, 19, "[EMAIL_1]"),)
    assert masks[1].replacements == ()
    assert mapping.to_dict() == {"[EMAIL_1]": EMAIL}


def test_masked_text_equals_masking_the_whole_text() -> None:
    document = _document(f"to {EMAIL} [PERSON_1]", "x", EMAIL)
    spans = _email_spans(document.text)
    masks, _ = mask_units(document, spans)
    assert masked_text(document, masks) == mask(document.text, spans)[0]


def test_a_span_crossing_a_separator_is_masked_in_each_unit() -> None:
    document = _document("card 4111 1111", "1111 1111 end")  # pragma: allowlist secret
    span = Span(5, 24, EntityType.CREDIT_CARD, 1.0, "credit_card")
    masks, mapping = mask_units(document, [span])

    assert masks[0].text == "card [CREDIT_CARD_1]"
    assert masks[1].text == "[CREDIT_CARD_2] end"
    assert set(mapping.to_dict().values()) == {"4111 1111", "1111 1111"}


def test_a_given_mapping_is_extended() -> None:
    mapping = Mapping()
    mapping.placeholder_for("other@example.org", EntityType.EMAIL)
    document = _document(EMAIL)
    masks, same = mask_units(document, _email_spans(document.text), mapping)
    assert same is mapping
    assert masks[0].text == "[EMAIL_2]"


# ------------------------------------------------------------------ distribute


def test_distribute_puts_the_placeholder_in_the_first_piece_of_its_region() -> None:
    # "Anna " | "Pet" | "rova!" with "Anna Petrova" replaced.
    pieces = [("Anna ", True), ("Pet", True), ("rova!", True)]
    found = [Replacement(0, 12, "[PERSON_1]")]
    assert distribute(pieces, found) == ["[PERSON_1]", "", "!"]


def test_distribute_keeps_text_outside_regions_in_its_own_piece() -> None:
    pieces = [("Dear ", True), ("Anna", True), (", hi", True)]
    found = [Replacement(5, 9, "[PERSON_1]")]
    assert distribute(pieces, found) == ["Dear ", "[PERSON_1]", ", hi"]


def test_distribute_drops_a_fixed_piece_inside_a_region_and_keeps_others() -> None:
    pieces = [("a", True), ("\t", False), ("b", True), ("\t", False), ("c", True)]
    found = [Replacement(0, 3, "[X_1]")]
    assert distribute(pieces, found) == ["[X_1]", None, "", "\t", "c"]


def test_distribute_uses_a_later_piece_when_the_region_starts_in_a_fixed_one() -> None:
    pieces = [("\t", False), ("ab", True)]
    assert distribute(pieces, [Replacement(0, 2, "[X_1]")]) == [None, "[X_1]b"]


def test_distribute_refuses_a_region_with_no_writable_piece() -> None:
    with pytest.raises(DocumentError):
        distribute([("\t", False)], [Replacement(0, 1, "[X_1]")])
