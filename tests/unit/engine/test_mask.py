import copy
import pickle
import threading

import pytest

import gateway.engine.mask as mask_module
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
        {"[PERSON_2]": "x"},  # a counter gap: mask numbers from 1
        {"[PERSON_1]": "x", "[PERSON_3]": "y"},
        {"[LITERAL_1]": "Anna"},  # a literal only ever holds a placeholder-shaped token
        {"[LITERAL_1]": "[PERSON_1] and more"},
        {1: "x"},
    ],
)
def test_from_dict_rejects_what_mask_never_writes(data: dict[object, object]) -> None:
    with pytest.raises(ValueError, match="map"):
        Mapping.from_dict(data)


def test_counter_overflow_raises_instead_of_writing_an_ambiguous_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # from_dict refuses gaps, so the limit is lowered rather than a million entries made.
    monkeypatch.setattr(mask_module, "MAX_COUNTER", 2)
    mapping = Mapping()
    mapping.placeholder_for("Anna", EntityType.PERSON)
    mapping.placeholder_for("Boris", EntityType.PERSON)
    with pytest.raises(ValueError, match="counter"):
        mapping.placeholder_for("Vera", EntityType.PERSON)


def test_the_real_counter_limit_is_six_digits() -> None:
    assert MAX_COUNTER == 999_999


@pytest.mark.parametrize("data", [[], [("[PERSON_1]", "x")], "x", None])
def test_from_dict_of_a_non_object_raises_value_error(data: object) -> None:
    with pytest.raises(ValueError, match="map"):
        Mapping.from_dict(data)


def test_from_dict_accepts_a_literal_of_each_token_shape() -> None:
    data = {"[LITERAL_1]": "[person-1]", "[LITERAL_2]": "PERSON 2", "[LITERAL_3]": "[SSN_1]"}
    assert Mapping.from_dict(data).to_dict() == data


def test_a_mapping_pickles_and_copies_without_its_lock() -> None:
    mapping = Mapping()
    mapping.placeholder_for("Anna", EntityType.PERSON)
    pickled = pickle.loads(pickle.dumps(mapping))  # noqa: S301  (bytes made on the line)
    for clone in (pickled, copy.deepcopy(mapping), copy.copy(mapping)):
        assert clone.to_dict() == {"[PERSON_1]": "Anna"}
        assert clone.placeholder_for("Boris", EntityType.PERSON) == "[PERSON_2]"
    assert len(mapping) == 1


def test_concurrent_allocation_gives_each_value_its_own_number() -> None:
    mapping = Mapping()
    values = [f"value-{n}" for n in range(2000)]

    def allocate(chunk: list[str]) -> None:
        for value in chunk:
            mapping.placeholder_for(value, EntityType.PERSON)

    threads = [threading.Thread(target=allocate, args=(values[i::8],)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(mapping.to_dict()) == sorted(f"[PERSON_{n}]" for n in range(1, 2001))
    assert sorted(mapping.to_dict().values()) == sorted(values)


def test_known_values_leaves_out_literals() -> None:
    mapping = Mapping()
    mapping.placeholder_for("[PERSON_9]", EntityType.LITERAL)
    mapping.placeholder_for("Anna", EntityType.PERSON)
    assert mapping.known_values() == {"Anna": EntityType.PERSON}


def test_clear_forgets_everything() -> None:
    mapping = Mapping()
    mapping.placeholder_for("Anna", EntityType.PERSON)
    mapping.clear()
    assert len(mapping) == 0
    assert mapping.placeholder_for("Boris", EntityType.PERSON) == "[PERSON_1]"


def test_an_empty_value_is_refused() -> None:
    with pytest.raises(ValueError, match="empty"):
        Mapping().placeholder_for("", EntityType.PERSON)


# ------------------------------------------------------------------ mask and mask_messages

from gateway.engine.detect import EngineConfig, detect  # noqa: E402
from gateway.engine.mask import mask, mask_messages  # noqa: E402
from gateway.engine.rehydrate import rehydrate  # noqa: E402
from gateway.engine.types import Span  # noqa: E402

NO_NER = EngineConfig(ner="off")
SSN_DASHES = "234-56-7890"  # pragma: allowlist secret
SSN_SPACES = "234 56 7890"  # pragma: allowlist secret


def span(start: int, end: int, kind: EntityType = EntityType.PERSON) -> Span:
    return Span(start, end, kind, 0.9, "test")


def mask_detected(text: str) -> tuple[str, Mapping]:
    return mask(text, detect(text, config=NO_NER))


def test_one_value_written_twice_gets_one_placeholder() -> None:
    masked, mapping = mask_detected(f"SSN {SSN_DASHES}, again {SSN_DASHES}.")
    assert masked == "SSN [SSN_1], again [SSN_1]."
    assert mapping.to_dict() == {"[SSN_1]": SSN_DASHES}


def test_two_spellings_of_one_value_get_two_placeholders() -> None:
    masked, mapping = mask_detected(f"SSN {SSN_DASHES} or {SSN_SPACES}.")
    assert masked == "SSN [SSN_1] or [SSN_2]."
    assert mapping.original("[SSN_2]") == SSN_SPACES


def test_placeholder_shaped_input_is_masked_as_literal() -> None:
    text = "Earlier answer: [PERSON_1] met PERSON 2 and [literal-1]."
    masked, mapping = mask(text, [])
    assert masked == "Earlier answer: [LITERAL_1] met [LITERAL_2] and [LITERAL_3]."
    assert rehydrate(masked, mapping) == (text, [])


def test_lower_case_unbracketed_text_is_left_alone() -> None:
    masked, mapping = mask("only person 1 of 3 signed", [])
    assert masked == "only person 1 of 3 signed"
    assert len(mapping) == 0


def test_overlapping_spans_are_united_never_dropped() -> None:
    masked, mapping = mask("abcdef!", [span(0, 4), span(2, 6, EntityType.EMAIL)])
    assert masked == "[PERSON_1]!"
    assert mapping.to_dict() == {"[PERSON_1]": "abcdef"}


def test_touching_spans_stay_separate() -> None:
    masked, _ = mask("abcdef", [span(0, 3), span(3, 6, EntityType.EMAIL)])
    assert masked == "[PERSON_1][EMAIL_1]"


def test_a_label_before_a_masked_value_is_kept() -> None:
    # "SSN 234" alone would read as a rule 3 token; once the number is masked it cannot.
    masked, _ = mask_detected(f"SSN {SSN_DASHES} and PHONE 2 here")
    assert masked == "SSN [SSN_1] and [LITERAL_1] here"


def test_a_span_inside_a_placeholder_shaped_token_round_trips() -> None:
    text = "x [PERSON_1] y"
    masked, mapping = mask(text, [span(10, 11, EntityType.SSN)])
    assert masked == "x [PERSON_[SSN_1]] y"
    assert rehydrate(masked, mapping) == (text, [])


@pytest.mark.parametrize(
    ("text", "spans"),
    [
        ("XPERSON_1 y", [span(0, 1, EntityType.CODENAME)]),
        ("PERSON_12 y", [span(8, 9, EntityType.SSN)]),
        ("[PERSONx_1]", [span(7, 8, EntityType.CODENAME)]),
        ("[PERSON x]", [span(8, 9)]),
    ],
)
def test_a_span_next_to_placeholder_like_text_round_trips(text: str, spans: list[Span]) -> None:
    masked, mapping = mask(text, spans)
    assert rehydrate(masked, mapping) == (text, [])


def test_the_mapping_holds_originals_only() -> None:
    text = f"Mail anna.petrova@example.org, SSN {SSN_DASHES}."
    masked, mapping = mask_detected(text)
    assert masked == "Mail [EMAIL_1], SSN [SSN_1]."
    assert sorted(mapping.to_dict().values()) == sorted(["anna.petrova@example.org", SSN_DASHES])


def test_a_given_mapping_is_extended_and_numbering_continues() -> None:
    mapping = Mapping()
    first, _ = mask("Anna", [span(0, 4)], mapping)
    second, same = mask("Boris and Anna", [span(0, 5), span(10, 14)], mapping)
    assert same is mapping
    assert (first, second) == ("[PERSON_1]", "[PERSON_2] and [PERSON_1]")


def test_a_span_outside_the_text_raises() -> None:
    with pytest.raises(ValueError, match="outside"):
        mask("abc", [span(1, 4)])


def test_mask_messages_shares_one_mapping_in_order() -> None:
    texts = ["Mail anna@example.org", "Again anna@example.org and bob@example.com"]
    masked, mapping = mask_messages(texts, config=NO_NER)
    assert masked == ["Mail [EMAIL_1]", "Again [EMAIL_1] and [EMAIL_2]"]
    assert mapping.original("[EMAIL_2]") == "bob@example.com"
