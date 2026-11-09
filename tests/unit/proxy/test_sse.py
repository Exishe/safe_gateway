import pytest

from gateway.proxy.sse import SSEDecoder, SSEEvent, encode


def _decode(*chunks: bytes) -> list[SSEEvent]:
    decoder = SSEDecoder()
    events = [event for chunk in chunks for event in decoder.feed(chunk)]
    return events + decoder.close()


STREAM = (
    b'event: message_start\ndata: {"a": 1}\n\n'
    b"data: line one\ndata: line two\n\n"
    b": a comment\n\n"
    b"id: 7\nretry: 100\nunknown: x\ndata: [DONE]\n\n"
)
EXPECTED = [
    SSEEvent('{"a": 1}', event="message_start"),
    SSEEvent("line one\nline two"),
    SSEEvent("[DONE]", id="7", retry="100"),
]


def test_events_in_one_chunk() -> None:
    assert _decode(STREAM) == EXPECTED


def test_split_at_every_byte_gives_the_same_events() -> None:
    for cut in range(len(STREAM) + 1):
        assert _decode(STREAM[:cut], STREAM[cut:]) == EXPECTED, cut


def test_one_byte_at_a_time() -> None:
    assert _decode(*(STREAM[i : i + 1] for i in range(len(STREAM)))) == EXPECTED


@pytest.mark.parametrize("newline", [b"\r\n", b"\r", b"\n"])
def test_line_endings(newline: bytes) -> None:
    stream = b"data: a" + newline + b"data: b" + newline + newline + b"data: c" + newline * 2
    assert _decode(stream) == [SSEEvent("a\nb"), SSEEvent("c")]


def test_cr_and_lf_split_across_chunks_is_one_line_break() -> None:
    assert _decode(b"data: a\r", b"\n\r", b"\ndata: b\r\n\r\n") == [
        SSEEvent("a"),
        SSEEvent("b"),
    ]


def test_multibyte_character_split_across_chunks() -> None:
    raw = "data: Ереван ✓\n\n".encode()
    for cut in range(len(raw) + 1):
        assert _decode(raw[:cut], raw[cut:]) == [SSEEvent("Ереван ✓")]


def test_field_without_space_and_empty_data() -> None:
    assert _decode(b"data:x\n\ndata\n\n") == [SSEEvent("x"), SSEEvent("")]


def test_blank_line_without_data_dispatches_nothing() -> None:
    assert _decode(b"\n\nevent: ping\n\n") == []


def test_unterminated_last_event_is_returned_by_close() -> None:
    decoder = SSEDecoder()
    assert decoder.feed(b"data: tail") == []
    assert decoder.close() == [SSEEvent("tail")]


@pytest.mark.parametrize("event", EXPECTED)
def test_encode_round_trips(event: SSEEvent) -> None:
    assert _decode(encode(event)) == [event]


def test_encode_format() -> None:
    assert encode(SSEEvent('{"x":1}', event="ping")) == b'event: ping\ndata: {"x":1}\n\n'
