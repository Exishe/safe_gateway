import pytest

from gateway.engine.mask import Mapping
from gateway.engine.rehydrate import rehydrate
from gateway.engine.types import EntityType


@pytest.fixture
def mapping() -> Mapping:
    mapping = Mapping()
    mapping.placeholder_for("Anna Petrova", EntityType.PERSON)
    mapping.placeholder_for("Boris Ivanov", EntityType.PERSON)
    mapping.placeholder_for("anna@example.org", EntityType.EMAIL)
    mapping.placeholder_for("4 May 1980", EntityType.DATE_OF_BIRTH)
    return mapping


def test_rule_1_exact_placeholder(mapping: Mapping) -> None:
    assert rehydrate("Dear [PERSON_1], see [EMAIL_1].", mapping) == (
        "Dear Anna Petrova, see anna@example.org.",
        [],
    )


@pytest.mark.parametrize("token", ["[person_2]", "[Person-2]", "[PERSON 2]", "[pErSoN-2]"])
def test_rule_2_bracketed_any_case_any_separator(mapping: Mapping, token: str) -> None:
    assert rehydrate(f"Ask {token} today.", mapping) == ("Ask Boris Ivanov today.", [])


@pytest.mark.parametrize("token", ["PERSON_2", "PERSON 2"])
def test_rule_3_unbracketed_upper_case(mapping: Mapping, token: str) -> None:
    assert rehydrate(f"Ask {token} today.", mapping) == ("Ask Boris Ivanov today.", [])


def test_multi_word_type_restores_whole(mapping: Mapping) -> None:
    assert rehydrate("Born [date_of_birth-1].", mapping) == ("Born 4 May 1980.", [])


@pytest.mark.parametrize(
    "text",
    [
        "only person 1 of 3 signed",
        "only Person 1 of 3 signed",
        "person_1 is lower case",
        "PERSON-1 has a dash and no brackets",
        "XPERSON_1 sits inside a word",
        "PERSON_1x sits inside a word",
        "the client signed",
    ],
)
def test_never_restored_and_not_listed(mapping: Mapping, text: str) -> None:
    assert rehydrate(text, mapping) == (text, [])


def test_type_and_number_not_in_the_mapping_stay_visible_and_are_listed(
    mapping: Mapping,
) -> None:
    text = "[PERSON_7] and [SSN_1] and PERSON_9 and [PERSON_7] and [PERSON_01]"
    assert rehydrate(text, mapping) == (
        text,
        ["[PERSON_7]", "[SSN_1]", "PERSON_9", "[PERSON_01]"],
    )


def test_a_restored_value_is_never_scanned_again() -> None:
    mapping = Mapping()
    mapping.placeholder_for("[PERSON_2]", EntityType.LITERAL)
    mapping.placeholder_for("Boris", EntityType.PERSON)
    assert rehydrate("[LITERAL_1] [PERSON_1]", mapping) == ("[PERSON_2] Boris", [])


def test_empty_mapping_lists_every_token() -> None:
    assert rehydrate("[PERSON_1] PERSON 2", Mapping()) == (
        "[PERSON_1] PERSON 2",
        ["[PERSON_1]", "PERSON 2"],
    )
