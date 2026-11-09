"""Phase 2 exit criteria: the round trip is exact and masking touches only what it must."""

import re

from hypothesis import assume, given
from hypothesis import strategies as st

from gateway.engine.detect import EngineConfig, detect
from gateway.engine.mask import Mapping, mask, mask_messages
from gateway.engine.placeholders import TOKEN
from gateway.engine.rehydrate import rehydrate
from gateway.engine.types import EntityType, Span
from tests.strategies import any_spans, placeholder_texts, texts_with_identifiers, us_ssns

NO_NER = EngineConfig(ner="off")
WRITTEN = re.compile(r"\[[A-Z_]+_[0-9]+\]")  # a placeholder as mask writes it


@given(texts_with_identifiers())
def test_round_trip_with_detected_spans(text: str) -> None:
    masked, mapping = mask(text, detect(text, config=NO_NER))
    assert rehydrate(masked, mapping) == (text, [])


@st.composite
def texts_and_spans(draw: st.DrawFn, *, disjoint: bool) -> tuple[str, list[Span]]:
    text = draw(st.one_of(placeholder_texts(), texts_with_identifiers(), st.text()))
    return text, draw(any_spans(text, disjoint=disjoint))


@given(texts_and_spans(disjoint=False))
def test_round_trip_with_any_spans(sample: tuple[str, list[Span]]) -> None:
    text, spans = sample
    masked, mapping = mask(text, spans)
    assert rehydrate(masked, mapping) == (text, [])


@given(st.data())
def test_masked_text_holds_no_token_but_its_own_placeholders(data: st.DataObject) -> None:
    text, spans = data.draw(texts_and_spans(disjoint=False))
    masked, mapping = mask(text, spans)
    for match in TOKEN.finditer(masked):
        assert match.group() in mapping


@st.composite
def plain_texts_and_spans(draw: st.DrawFn) -> tuple[str, list[Span]]:
    text = draw(st.text().filter(lambda t: "[" not in t and TOKEN.search(t) is None))
    return text, draw(any_spans(text, disjoint=True))


def occurrences(text: str, value: str) -> set[int]:
    return {i for i in range(len(text)) if text.startswith(value, i)}


@given(plain_texts_and_spans())
def test_text_outside_spans_is_unchanged_where_no_span_value_recurs(
    sample: tuple[str, list[Span]],
) -> None:
    # Mask also replaces other occurrences of a span's value (ADR 0014 amendment 1);
    # where there are none, nothing outside the spans changes.
    text, spans = sample
    for span in spans:
        surface = text[span.start : span.end]
        assume(
            occurrences(text, surface)
            <= {s.start for s in spans if text[s.start : s.end] == surface}
        )
    masked, _ = mask(text, spans)
    bounds = [0, *[i for s in spans for i in (s.start, s.end)], len(text)]
    outside = [text[a:b] for a, b in zip(bounds[0::2], bounds[1::2], strict=True)]
    assert WRITTEN.split(masked) == outside


@given(st.lists(st.sampled_from(["Anna", "anna", "Boris", "4111", "Anna "]), min_size=1))
def test_same_surface_same_placeholder(surfaces: list[str]) -> None:
    text, spans, cursor = "", [], 0
    for surface in surfaces:
        spans.append(Span(cursor, cursor + len(surface), EntityType.PERSON, 0.9, "test"))
        text += surface + "|"
        cursor = len(text)
    masked, mapping = mask(text, spans)

    placeholders = WRITTEN.findall(masked)
    assert [mapping.original(p) for p in placeholders] == surfaces
    for surface, placeholder in zip(surfaces, placeholders, strict=True):
        same = [p for s, p in zip(surfaces, placeholders, strict=True) if s == surface]
        assert set(same) == {placeholder}
    assert len(mapping) == len(set(surfaces))


@given(st.lists(texts_with_identifiers(), max_size=4))
def test_one_mapping_shared_by_messages_round_trips_each(texts: list[str]) -> None:
    mapping = Mapping()
    masked = [mask(text, detect(text, config=NO_NER), mapping)[0] for text in texts]
    assert [rehydrate(m, mapping) for m in masked] == [(text, []) for text in texts]


@given(us_ssns(), st.sampled_from(["ref", "x", "_", "("]), st.sampled_from([".pdf", "", "th", ")"]))
def test_a_detected_value_never_survives_glued_into_another_message(
    ssn: str, before: str, after: str
) -> None:
    texts = [f"{before}{ssn}{after} was scanned", f"SSN {ssn}."]
    masked, mapping = mask_messages(texts, config=NO_NER)
    assert all(ssn not in m for m in masked)
    assert [rehydrate(m, mapping) for m in masked] == [(t, []) for t in texts]
