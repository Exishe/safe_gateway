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


def test_rule_3_left_edge_sees_text_before_the_scan_start() -> None:
    mapping = Mapping()
    mapping.placeholder_for("Anna", EntityType.PERSON)
    assert rehydrate("XPERSON_1", mapping) == ("XPERSON_1", [])


def test_empty_mapping_lists_every_token() -> None:
    assert rehydrate("[PERSON_1] PERSON 2", Mapping()) == (
        "[PERSON_1] PERSON 2",
        ["[PERSON_1]", "PERSON 2"],
    )


# ------------------------------------------------------------------ streaming (ADR 0004 point 3)

from gateway.engine.placeholders import MAX_PARTIAL  # noqa: E402
from gateway.engine.rehydrate import StreamingRehydrator  # noqa: E402

ANSWER = (
    "Dear [PERSON_1], PERSON 2 wrote to [email-1]. [PERSON_7] is unknown; "
    "only person 1 of 3 signed. Born [DATE_OF_BIRTH_1], PERSON_12 and XPERSON_1."
)


def stream(chunks: list[str], mapping: Mapping) -> tuple[str, list[str]]:
    rehydrator = StreamingRehydrator(mapping)
    out = "".join(rehydrator.feed(chunk) for chunk in chunks) + rehydrator.close()
    return out, rehydrator.unresolved


def test_a_split_placeholder_is_held_then_restored(mapping: Mapping) -> None:
    rehydrator = StreamingRehydrator(mapping)
    assert rehydrator.feed("Dear [PER") == "Dear "
    assert rehydrator.feed("SON_1], hi") == "Anna Petrova, hi"
    assert rehydrator.close() == ""


def test_the_next_chunk_decides_an_unbracketed_token(mapping: Mapping) -> None:
    assert stream(["see PERSON_1", "2 now"], mapping) == ("see PERSON_12 now", ["PERSON_12"])
    assert stream(["see PERSON_1", "x now"], mapping) == ("see PERSON_1x now", [])
    assert stream(["see PERSON_1", ". now"], mapping) == ("see Anna Petrova. now", [])


def test_plain_text_is_emitted_at_once(mapping: Mapping) -> None:
    rehydrator = StreamingRehydrator(mapping)
    assert rehydrator.feed("nothing to hold here. ") == "nothing to hold here. "
    assert rehydrator.feed("lower case person") == "lower case person"


def test_the_held_tail_never_exceeds_the_longest_partial_token(mapping: Mapping) -> None:
    rehydrator = StreamingRehydrator(mapping)
    for char in "x [MEDICAL_RECORD_NUMBER_123456 and on [PERSON 1" * 3:
        rehydrator.feed(char)
        assert rehydrator.held <= MAX_PARTIAL
    rehydrator.feed("[MEDICAL_RECORD_NUMBER_123456")
    assert rehydrator.held == MAX_PARTIAL


def test_close_flushes_an_unfinished_token(mapping: Mapping) -> None:
    assert stream(["the end [PERSON_"], mapping) == ("the end [PERSON_", [])
    assert stream(["the end PERSON_1"], mapping) == ("the end Anna Petrova", [])


def test_every_two_chunk_split_matches_rehydrate(mapping: Mapping) -> None:
    expected = rehydrate(ANSWER, mapping)
    for cut in range(len(ANSWER) + 1):
        assert stream([ANSWER[:cut], ANSWER[cut:]], mapping) == expected, cut


def test_one_character_chunks_match_rehydrate(mapping: Mapping) -> None:
    assert stream(list(ANSWER), mapping) == rehydrate(ANSWER, mapping)


# ------------------------------------------------------------------ Unicode edges (leak hunt 2)

FULLWIDTH_TWO = chr(0xFF12)
ARABIC_INDIC_ONE = chr(0x661)
CYRILLIC_ZHE = chr(0x436)


@pytest.mark.parametrize(
    "text",
    [
        f"PERSON_1{FULLWIDTH_TWO} signed",
        f"{FULLWIDTH_TWO}PERSON_1 signed",
        f"PERSON 1{ARABIC_INDIC_ONE} signed",
        f"{CYRILLIC_ZHE}PERSON_1 signed",
        f"PERSON_1{CYRILLIC_ZHE} signed",
    ],
)
def test_a_unicode_word_character_touching_an_unbracketed_token_blocks_it(
    mapping: Mapping, text: str
) -> None:
    assert rehydrate(text, mapping) == (text, [])


def test_the_token_grammar_stays_ascii(mapping: Mapping) -> None:
    long_s = f"[PER{chr(0x17F)}ON_1]"  # Unicode case folding reads the long s as `s`
    fullwidth_number = f"[PERSON_{FULLWIDTH_TWO}]"
    assert rehydrate(f"{long_s} {fullwidth_number}", mapping) == (
        f"{long_s} {fullwidth_number}",
        [],
    )


def test_a_non_word_unicode_character_does_not_block(mapping: Mapping) -> None:
    assert rehydrate("\u00abPERSON_1\u00bb", mapping) == ("\u00abAnna Petrova\u00bb", [])


def test_streaming_holds_an_unbracketed_token_for_a_unicode_digit(mapping: Mapping) -> None:
    text = f"see PERSON_1{FULLWIDTH_TWO} now"
    assert stream(["see PERSON_1", f"{FULLWIDTH_TWO} now"], mapping) == (text, [])
    assert stream(list(text), mapping) == rehydrate(text, mapping)
