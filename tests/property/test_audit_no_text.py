"""No stored column and no byte of the audit file holds the text it was given (ADR 0021).

Faker writes a note with personal data; the engine masks it; the log records only
the masked payload's hash and per-type counts, and even gets the raw note as a
session id, which it must store hashed. Then every cell of every table, and the
file's raw bytes, are searched for the note, the masked text and each value.
"""

import hashlib
import re
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path

from faker import Faker
from hypothesis import given, settings
from hypothesis import strategies as st

from gateway.audit.store import AuditLog
from gateway.engine.detect import EngineConfig, detect
from gateway.engine.mask import mask
from gateway.engine.session_disk import InMemoryKeyProvider
from tests.audit_support import Clock

FAKE = Faker(["en_US", "de_DE", "ru_RU"])
NO_MODELS = EngineConfig(ner="off", gliner="off")
MIN_NEEDLE = 6  # shorter strings occur by chance in binary data and say nothing


def note(seed: int) -> tuple[str, list[str]]:
    FAKE.seed_instance(seed)
    values = [
        FAKE.name(),
        FAKE.email(),
        FAKE.phone_number(),
        FAKE.street_address(),
        FAKE.company(),
    ]
    text = (
        f"Client {values[0]} ({values[1]}, {values[2]}) lives at {values[3]}.\n"
        f"Employer: {values[4]}. {FAKE.sentence(nb_words=12)}\n"
    )
    return text, values


def pieces(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"[.\n]", text) if len(p.strip()) > 12]


def cells(db: Path) -> list[str]:
    connection = sqlite3.connect(db)
    try:
        tables = [name for (name,) in connection.execute("SELECT name FROM sqlite_master")]
        found: list[str] = []
        for table in tables:
            rows = connection.execute(f'SELECT * FROM "{table}"')  # noqa: S608  (names from sqlite_master)
            found.extend(str(cell) for row in rows for cell in row)
        return found
    finally:
        connection.close()


@settings(deadline=None, max_examples=40)  # each example writes a SQLite file
@given(st.integers(0, 2**32 - 1))
def test_no_cell_and_no_file_byte_holds_the_text(seed: int) -> None:
    raw, values = note(seed)
    spans = detect(raw, config=NO_MODELS)
    masked, _mapping = mask(raw, spans)
    counts = Counter(str(span.entity_type) for span in spans)

    with tempfile.TemporaryDirectory() as directory:
        db = Path(directory) / "audit.db"
        with AuditLog(db, InMemoryKeyProvider(), clock=Clock()) as log:
            log.record(
                session_id=raw,
                upstream_host="api.example.com",
                model="model-a",
                entity_counts=dict(counts),
                masked_payload_sha256=hashlib.sha256(masked.encode()).hexdigest(),
                bytes_out=len(masked.encode()),
                status=200,
                local=False,
            )
        stored = cells(db)
        data = db.read_bytes()
        assert [p.name for p in db.parent.iterdir()] == ["audit.db"]  # no journal or WAL beside it

    needles = {raw, masked, *pieces(raw), *pieces(masked), *values}
    for needle in (n for n in needles if len(n) >= MIN_NEEDLE):
        assert not any(needle in cell for cell in stored), f"stored: {needle!r}"
        assert needle.encode() not in data, f"in the file bytes: {needle!r}"
