"""Phase 2 exit criterion: streaming output equals non-streaming output for every chunking."""

from hypothesis import given
from hypothesis import strategies as st

from gateway.engine.mask import Mapping
from gateway.engine.placeholders import MAX_PARTIAL
from gateway.engine.rehydrate import StreamingRehydrator, rehydrate
from gateway.engine.types import EntityType
from tests.strategies import (
    chunkings,
    link_attacks,
    link_texts,
    placeholder_texts,
    texts_with_identifiers,
)

EMAIL = "x@example.org"


def sample_mapping() -> Mapping:
    mapping = Mapping()
    for surface, kind in [
        ("Anna Petrova", EntityType.PERSON),
        ("Boris", EntityType.PERSON),
        ("[PERSON_2]", EntityType.LITERAL),  # a restored value that looks like a token
        ("4 May 1980", EntityType.DATE_OF_BIRTH),
        ("x", EntityType.SSN),
        (EMAIL, EntityType.EMAIL),
        (
            "https://intranet.example/d?token=abc",
            EntityType.URL_WITH_TOKEN,
        ),  # a value that is a link
    ]:
        mapping.placeholder_for(surface, kind)
    return mapping


def stream(chunks: list[str], mapping: Mapping) -> tuple[str, list[str]]:
    rehydrator = StreamingRehydrator(mapping)
    out = []
    for chunk in chunks:
        out.append(rehydrator.feed(chunk))
        assert rehydrator.held <= MAX_PARTIAL
    out.append(rehydrator.close())
    return "".join(out), rehydrator.unresolved


@given(st.data())
def test_streaming_equals_rehydrate_for_every_chunking(data: st.DataObject) -> None:
    text = data.draw(st.one_of(placeholder_texts(), texts_with_identifiers(), link_texts()))
    chunks = data.draw(chunkings(text))
    mapping = sample_mapping()
    assert stream(chunks, mapping) == rehydrate(text, mapping)


@given(st.data())
def test_a_value_never_lands_in_a_link_the_answer_opened(data: st.DataObject) -> None:
    token = data.draw(st.sampled_from(["[EMAIL_1]", "EMAIL_1", "[email 1]", "EMAIL 1"]))
    text = data.draw(link_attacks(token))
    mapping = sample_mapping()
    restored, unresolved = rehydrate(text, mapping)
    assert EMAIL not in restored
    assert token in unresolved
    assert stream(data.draw(chunkings(text)), mapping) == (restored, unresolved)
