import pytest

from gateway.engine.placeholders import (
    MAX_COUNTER,
    MAX_PARTIAL,
    TOKEN,
    canonical,
    canonical_of,
    could_become_token,
)
from gateway.engine.types import EntityType


def tokens(text: str) -> list[str]:
    return [match.group() for match in TOKEN.finditer(text)]


def test_canonical_form() -> None:
    assert canonical(EntityType.SSN, 1) == "[SSN_1]"
    assert canonical(EntityType.DATE_OF_BIRTH, 12) == "[DATE_OF_BIRTH_12]"


@pytest.mark.parametrize("number", [0, -1, MAX_COUNTER + 1])
def test_canonical_rejects_numbers_out_of_range(number: int) -> None:
    with pytest.raises(ValueError, match="counter"):
        canonical(EntityType.PERSON, number)


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("see [PERSON_1] now", "[PERSON_1]"),
        ("see [person 1] now", "[person 1]"),
        ("see [Person-1] now", "[Person-1]"),
        ("see PERSON_1 now", "PERSON_1"),
        ("see PERSON 1, now", "PERSON 1"),
        ("born [DATE_OF_BIRTH_2]", "[DATE_OF_BIRTH_2]"),
        ("born DATE_OF_BIRTH_2.", "DATE_OF_BIRTH_2"),
        ("x[LITERAL_3]y", "[LITERAL_3]"),
    ],
)
def test_token_finds_each_shape(text: str, found: str) -> None:
    assert tokens(text) == [found]


@pytest.mark.parametrize(
    "text",
    [
        "only person 1 of 3 signed",
        "person_1",
        "XPERSON_1",
        "PERSON_1x",
        "PERSON_1234567",
        "[PERSON_1234567]",
        "[FOO_1]",
        "[PERSON_1",
        "PERSON-1",
        "[PERSON 1 ]",
        "[PERSON1]",
    ],
)
def test_token_ignores_what_is_not_placeholder_shaped(text: str) -> None:
    assert tokens(text) == []


def test_unbracketed_token_touching_a_bracket_is_not_a_token() -> None:
    # In masked text, the neighbours of an original segment are placeholder brackets.
    assert tokens("]PERSON_1") == []
    assert tokens("PERSON_1[") == []


def test_canonical_of_upper_cases_and_normalises_the_separator() -> None:
    keys = [canonical_of(match) for match in TOKEN.finditer("[person-2] PERSON 3 [Ssn_4]")]
    assert keys == ["[PERSON_2]", "[PERSON_3]", "[SSN_4]"]


@pytest.mark.parametrize(
    "tail",
    [
        "[",
        "[pe",
        "[PERSON",
        "[PERSON_",
        "[person-12",
        "P",
        "PERS",
        "PERSON_",
        "PERSON 1",
        "DATE_OF",
    ],
)
def test_could_become_token(tail: str) -> None:
    assert could_become_token(tail)


@pytest.mark.parametrize(
    "tail", ["[PERSON_1]", "person", "x", "PERSON_1 ", "[x", "PERSON_1234567", "]", ""]
)
def test_could_not_become_token(tail: str) -> None:
    assert not could_become_token(tail)


def test_max_partial_is_the_longest_partial_token() -> None:
    longest = max(len(t.value) for t in EntityType)
    assert 1 + longest + 1 + len(str(MAX_COUNTER)) == MAX_PARTIAL
    assert could_become_token("[" + "MEDICAL_RECORD_NUMBER" + "_" + "9" * 6)
