"""Phase 2 exit criterion: streaming output equals non-streaming output for every chunking."""

from hypothesis import given
from hypothesis import strategies as st

from gateway.engine.mask import Mapping
from gateway.engine.placeholders import MAX_PARTIAL
from gateway.engine.rehydrate import StreamingRehydrator, rehydrate
from gateway.engine.types import EntityType
from tests.strategies import chunkings, placeholder_texts, texts_with_identifiers


def sample_mapping() -> Mapping:
    mapping = Mapping()
    for surface, kind in [
        ("Anna Petrova", EntityType.PERSON),
        ("Boris", EntityType.PERSON),
        ("[PERSON_2]", EntityType.LITERAL),  # a restored value that looks like a token
        ("4 May 1980", EntityType.DATE_OF_BIRTH),
        ("x", EntityType.SSN),
    ]:
        mapping.placeholder_for(surface, kind)
    return mapping


@given(st.data())
def test_streaming_equals_rehydrate_for_every_chunking(data: st.DataObject) -> None:
    text = data.draw(st.one_of(placeholder_texts(), texts_with_identifiers()))
    chunks = data.draw(chunkings(text))
    mapping = sample_mapping()

    rehydrator = StreamingRehydrator(mapping)
    out = []
    for chunk in chunks:
        out.append(rehydrator.feed(chunk))
        assert rehydrator.held <= MAX_PARTIAL
    out.append(rehydrator.close())

    assert ("".join(out), rehydrator.unresolved) == rehydrate(text, mapping)
