import pytest

from gateway.engine.mask import Mapping
from gateway.engine.placeholders import MAX_COUNTER
from gateway.engine.types import EntityType

# ------------------------------------------------------------------ Mapping


def test_same_surface_gets_the_same_placeholder() -> None:
    mapping = Mapping()
    first = mapping.placeholder_for("Anna Petrova", EntityType.PERSON)
    again = mapping.placeholder_for("Anna Petrova", EntityType.PERSON)
    assert first == again == "[PERSON_1]"
    assert len(mapping) == 1


def test_new_surfaces_count_per_type_in_order_of_first_call() -> None:
    mapping = Mapping()
    assert mapping.placeholder_for("Anna", EntityType.PERSON) == "[PERSON_1]"
    assert mapping.placeholder_for("a@example.org", EntityType.EMAIL) == "[EMAIL_1]"
    assert mapping.placeholder_for("Boris", EntityType.PERSON) == "[PERSON_2]"
    assert mapping.original("[PERSON_2]") == "Boris"
    assert mapping.original("[PERSON_3]") is None
    assert "[EMAIL_1]" in mapping
    assert "[EMAIL_2]" not in mapping


def test_two_spellings_get_two_placeholders() -> None:
    mapping = Mapping()
    assert mapping.placeholder_for("anna", EntityType.PERSON) == "[PERSON_1]"
    assert mapping.placeholder_for("Anna", EntityType.PERSON) == "[PERSON_2]"


def test_a_surface_keeps_its_first_placeholder_whatever_type_comes_later() -> None:
    mapping = Mapping()
    assert mapping.placeholder_for("4951234567", EntityType.NATIONAL_ID) == "[NATIONAL_ID_1]"
    assert mapping.placeholder_for("4951234567", EntityType.PHONE) == "[NATIONAL_ID_1]"


def test_to_dict_and_from_dict_round_trip_and_continue_numbering() -> None:
    mapping = Mapping()
    mapping.placeholder_for("Anna", EntityType.PERSON)
    mapping.placeholder_for("Boris", EntityType.PERSON)
    data = mapping.to_dict()
    assert data == {"[PERSON_1]": "Anna", "[PERSON_2]": "Boris"}

    restored = Mapping.from_dict(data)
    assert restored.to_dict() == data
    assert restored.placeholder_for("Anna", EntityType.PERSON) == "[PERSON_1]"
    assert restored.placeholder_for("Vera", EntityType.PERSON) == "[PERSON_3]"


@pytest.mark.parametrize(
    "data",
    [
        {"PERSON_1": "x"},
        {"[person_1]": "x"},
        {"[FOO_1]": "x"},
        {"[PERSON_01]": "x"},
        {"[PERSON_1]": "x", "[PERSON_2]": "x"},
        {"[PERSON_1]": 3},
    ],
)
def test_from_dict_rejects_what_mask_never_writes(data: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="map"):
        Mapping.from_dict(data)


def test_counter_overflow_raises_instead_of_writing_an_ambiguous_token() -> None:
    mapping = Mapping.from_dict({f"[PERSON_{MAX_COUNTER}]": "Anna"})
    with pytest.raises(ValueError, match="counter"):
        mapping.placeholder_for("Boris", EntityType.PERSON)


def test_clear_forgets_everything() -> None:
    mapping = Mapping()
    mapping.placeholder_for("Anna", EntityType.PERSON)
    mapping.clear()
    assert len(mapping) == 0
    assert mapping.placeholder_for("Boris", EntityType.PERSON) == "[PERSON_1]"
