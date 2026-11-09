import email
import email.policy
from email.message import EmailMessage
from pathlib import Path

import pytest

from gateway.engine.detect import EngineConfig, detect
from gateway.engine.rehydrate import rehydrate
from gateway.parsers import eml
from gateway.parsers.document import EmailPosition
from gateway.parsers.redact import mask_units, masked_text

SENDER = "anna.petrova@example.org"
RECIPIENT = "boris.ivanov@example.com"
COPY = "clerk@example.net"
CARD = "4111 1111 1111 1111"  # pragma: allowlist secret
IBAN = "DE89370400440532013000"  # pragma: allowlist secret
ATTACHED_ONLY = "attached.only@example.org"
STRUCTURED = EngineConfig(ner="off")
VALUES = (SENDER, RECIPIENT, COPY, CARD, IBAN)


def _message(tmp_path: Path) -> Path:
    message = EmailMessage()
    message["Received"] = "from mail.example.org (10.1.2.3) by mx.example.com"
    message["Message-ID"] = f"<123.{SENDER}>"
    message["From"] = f"Anna Petrova <{SENDER}>"
    message["To"] = RECIPIENT
    message["Cc"] = COPY
    message["Reply-To"] = SENDER
    message["X-Mailer"] = "Test Mailer"
    message["Subject"] = f"Платёж по карте {CARD}"
    message.set_content(f"Hello,\nplease pay to {IBAN}.\nCard {CARD}\n")
    message.add_alternative(
        f'<html><body><p>Hello,</p><p>pay to <a href="mailto:{SENDER}">{IBAN}</a></p>'
        "</body></html>",
        subtype="html",
    )
    message.add_attachment(
        f"notes {ATTACHED_ONLY}".encode(),
        maintype="application",
        subtype="octet-stream",
        filename=f"{SENDER}.bin",
    )
    path = tmp_path / "letter.eml"
    path.write_bytes(message.as_bytes())
    return path


def _redact(tmp_path: Path) -> tuple[Path, Path]:
    source = _message(tmp_path)
    document = eml.parse(source)
    masks, _ = mask_units(document, detect(document.text, config=STRUCTURED))
    out = tmp_path / "out.eml"
    eml.write_redacted(source, document, masks, out)
    return source, out


def test_headers_and_text_parts_are_units(tmp_path: Path) -> None:
    document = eml.parse(_message(tmp_path))

    assert document.format == "eml"
    assert f"From: Anna Petrova <{SENDER}>\n" in document.text
    assert f"Subject: Платёж по карте {CARD}\n" in document.text
    assert f"please pay to {IBAN}." in document.text
    assert f"Hello,\npay to {IBAN}" in document.text  # the html part, as text
    assert document.source_at(document.text.index(RECIPIENT)) == EmailPosition("To", 0)
    assert document.source_at(document.text.index("please")) == EmailPosition("body", 0)
    assert ATTACHED_ONLY not in document.text
    assert "Received" not in document.text
    assert document.metadata["attachments"] == 1


def test_redacted_copy_has_no_value_anywhere(tmp_path: Path) -> None:
    _, out = _redact(tmp_path)
    data = out.read_bytes()
    message = email.message_from_bytes(data, policy=email.policy.default)
    decoded = [str(value) for _, value in message.items()]
    decoded.extend(part.get_content() for part in message.walk() if not part.is_multipart())
    everything = data.decode("ascii", "replace") + "".join(decoded)
    for value in (*VALUES, ATTACHED_ONLY, "10.1.2.3"):
        assert value not in everything


def test_redacted_copy_keeps_masked_headers_and_drops_the_rest(tmp_path: Path) -> None:
    _, out = _redact(tmp_path)
    message = email.message_from_bytes(out.read_bytes(), policy=email.policy.default)

    kept = {name.lower() for name in message}
    assert kept <= {"from", "to", "cc", "subject", "mime-version", "content-type"}
    raw = dict(message.raw_items())
    assert raw["From"] == "Anna Petrova <[EMAIL_1]>"
    assert "[CREDIT_CARD_1]" in str(message["Subject"])
    types = [part.get_content_type() for part in message.walk()]
    assert "application/octet-stream" not in types
    html = message.get_body(("html",))
    assert html is not None
    assert "<pre>" in html.get_content()
    assert "mailto" not in html.get_content()


def test_redacted_copy_reparses_to_the_masked_text_and_restores(tmp_path: Path) -> None:
    source = _message(tmp_path)
    document = eml.parse(source)
    masks, mapping = mask_units(document, detect(document.text, config=STRUCTURED))
    out = tmp_path / "out.eml"
    notes = eml.write_redacted(source, document, masks, out)

    again = eml.parse(out)
    assert again.text == masked_text(document, masks)
    assert rehydrate(again.text, mapping) == (document.text, [])
    assert notes == ["removed 1 attachment or non-text part"]


def test_a_changed_source_is_refused(tmp_path: Path) -> None:
    source = _message(tmp_path)
    document = eml.parse(source)
    masks, _ = mask_units(document, [])
    source.write_bytes(b"Subject: other\n\nbody\n")
    with pytest.raises(ValueError, match="changed"):
        eml.write_redacted(source, document, masks, tmp_path / "out.eml")


def test_a_message_that_is_only_an_attachment_keeps_none_of_it(tmp_path: Path) -> None:
    message = EmailMessage()
    message["Subject"] = "scan"
    message.set_content(f"secret {SENDER}".encode(), maintype="image", subtype="png")
    source = tmp_path / "scan.eml"
    source.write_bytes(message.as_bytes())
    document = eml.parse(source)
    masks, _ = mask_units(document, [])
    out = tmp_path / "out.eml"
    assert eml.write_redacted(source, document, masks, out) == [
        "removed 1 attachment or non-text part"
    ]
    assert SENDER.encode() not in out.read_bytes()
    assert b"c2VjcmV0" not in out.read_bytes()  # base64 of "secret"
