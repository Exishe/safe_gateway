"""Leak-hunter round 3 (Phase 2): mask, rehydrate, the session store and `safeguard mask`.

Every test here failed when it was written. Each one names the trick; the
docstring says what the code did instead. Identifiers are fake by construction
(sequential digits, example.com); names are invented. Characters that are
invisible or easily confused are built with chr(), so the source holds none.
"""

import contextlib
import os
import stat
import threading
from pathlib import Path
from typing import IO

import pytest

import gateway.engine.mask as mask_module
from gateway.cli import main
from gateway.engine.detect import EngineConfig, detect
from gateway.engine.mask import Mapping, mask, mask_messages
from gateway.engine.rehydrate import rehydrate
from gateway.engine.session import SessionStore
from gateway.engine.types import EntityType

# Red-team findings (Phase 2), committed failing on purpose; each fix removes its marker.
FINDING = pytest.mark.xfail(strict=True, reason="mask/rehydrate red-team finding")

NO_NER = EngineConfig(ner="off")
SSN = "123-45-6780"  # pragma: allowlist secret
SSN_2 = "234-56-7890"  # pragma: allowlist secret
EMAIL = "zorvath.quillin@example.com"
NOTE = f"Client {EMAIL}, SSN {SSN}.\n"
FULLWIDTH_TWO = chr(0xFF12)  # a decimal digit (category Nd), so a word character


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def note(tmp_path: Path) -> Path:
    path = tmp_path / "note.txt"
    path.write_text(NOTE, encoding="utf-8")
    return path


# ------------------------------------------------------------------ (a) nothing detected leaks


@FINDING
def test_known_value_repeated_where_detection_misses_it() -> None:
    """The SSN is detected in message 1 and is in the mapping as [SSN_1], yet message 2
    went upstream as `Scan saved as ref123-45-6780.pdf`: mask only replaces the spans
    detect() returned for that text, never the surfaces the mapping already knows."""
    masked, mapping = mask_messages([f"SSN {SSN}.", f"Scan saved as ref{SSN}.pdf"], config=NO_NER)

    assert mapping.original("[SSN_1]") == SSN
    assert [text for text in masked if SSN in text] == []


@FINDING
def test_cli_output_and_json_map_on_the_same_path(note: Path, tmp_path: Path) -> None:
    """`safeguard mask note.txt -o out.txt --json-map out.txt` exited 0, and out.txt, the
    file meant to be sent upstream, held the map: every original value in clear. Only the
    input file is checked against the two outputs, so the map overwrote the masked text."""
    out = tmp_path / "note.masked.txt"
    main(["mask", "--no-ner", str(note), "-o", str(out), "--json-map", str(out)])

    written = out.read_text(encoding="utf-8") if out.exists() else ""
    assert SSN not in written
    assert EMAIL not in written


@FINDING
def test_cli_json_map_written_into_an_existing_path(
    note: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The map is opened with O_CREAT|O_TRUNC and chmod-ed only after it is written. An
    existing 0644 file therefore held the original values while readable by everyone
    (mode seen at write time: 0o644); a symlink at the map path was followed and the
    values landed in its target."""
    stale_map = tmp_path / "map.json"
    stale_map.write_text("{}", encoding="utf-8")
    stale_map.chmod(0o644)
    shared = tmp_path / "shared.txt"
    shared.write_text("", encoding="utf-8")
    shared.chmod(0o644)
    planted = tmp_path / "planted.json"
    planted.symlink_to(shared)

    modes_at_write: list[int] = []
    real_fdopen = os.fdopen

    def fdopen_noting_the_mode(fd: int, mode: str, encoding: str | None = None) -> IO[str]:
        modes_at_write.append(stat.S_IMODE(os.fstat(fd).st_mode))
        return real_fdopen(fd, mode, encoding=encoding)

    monkeypatch.setattr(os, "fdopen", fdopen_noting_the_mode)
    main(
        ["mask", "--no-ner", str(note), "-o", str(tmp_path / "a.txt"), "--json-map", str(stale_map)]
    )
    main(["mask", "--no-ner", str(note), "-o", str(tmp_path / "b.txt"), "--json-map", str(planted)])
    monkeypatch.undo()

    assert [oct(mode) for mode in modes_at_write if mode & 0o077] == []
    assert SSN not in shared.read_text(encoding="utf-8")


# ------------------------------------------------------------------ (b, c, e) concurrency


def test_two_masks_into_one_session_mapping_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two requests mask into the same session's mapping from two worker threads. The
    store's lock guards only its dict; Mapping.placeholder_for reads the counter, builds
    the token, then writes, with no lock. Interleaved there, both SSNs became [SSN_1],
    the mapping kept one of them, and the other text rehydrated to the wrong number."""
    store = SessionStore()
    session_id = store.create()
    both_inside = threading.Barrier(2)
    real_canonical = mask_module.canonical

    def canonical_once_both_have_read(entity_type: EntityType, number: int) -> str:
        # Holds each thread between reading and writing the counter until the other gets
        # there too. If placeholder_for is serialised, the wait times out and both run alone.
        with contextlib.suppress(threading.BrokenBarrierError):
            both_inside.wait(timeout=2)
        return real_canonical(entity_type, number)

    texts = (f"SSN {SSN}", f"SSN {SSN_2}")
    detect(texts[0], config=NO_NER)  # warm up, so neither thread is slow to reach the counter
    monkeypatch.setattr(mask_module, "canonical", canonical_once_both_have_read)
    masked: dict[str, str] = {}

    def request(text: str) -> None:
        masked[text] = mask(text, detect(text, config=NO_NER), store.get(session_id))[0]

    threads = [threading.Thread(target=request, args=(text,)) for text in texts]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    mapping = store.get(session_id)
    assert {text: rehydrate(masked[text], mapping)[0] for text in texts} == {t: t for t in texts}


# ------------------------------------------------------------------ (e) expiry forgets


def test_session_past_its_ttl_is_forgotten_without_a_get() -> None:
    """Expiry is checked only by get() and purge_expired(). Past the TTL the store still
    answered `session_id in store` with True, counted it in len(), and its mapping still
    held the name: nothing forgets until someone happens to call one of those two."""
    clock = Clock()
    store = SessionStore(ttl_seconds=60, clock=clock)
    mapping = Mapping()
    mapping.placeholder_for("Zorvath Quillin", EntityType.PERSON)
    session_id = store.create(mapping)

    clock.now += 61

    assert session_id not in store
    assert len(store) == 0
    assert len(mapping) == 0


def test_nan_session_ttl_is_refused() -> None:
    """`ttl_seconds <= 0` is the only check, and NaN compares false with everything, so
    SessionStore(ttl_seconds=nan) was accepted and its sessions never expired: get()
    still returned the mapping 10**9 seconds later and purge_expired() removed nothing."""
    with pytest.raises(ValueError, match="ttl"):
        SessionStore(ttl_seconds=float("nan"))


# ------------------------------------------------------------------ (c) restore only what was meant


def test_unicode_digit_touching_an_unbracketed_token() -> None:
    """ADR 0014 point 3: rule 3 does not match when a word character touches either end.
    TOKEN is compiled with re.ASCII, so a full-width digit is not a word character to it:
    the model's `PERSON_1` + full-width 2 (PERSON_12) came back as PERSON_1's name
    followed by the digit, a real value where the model meant a different placeholder."""
    # Built by allocation: from_dict refuses the counter gap a two-entry dict would have.
    mapping = Mapping()
    for name in ["Zorvath Quillin", *(f"Person {n}" for n in range(2, 12)), "Brenna Holloway"]:
        mapping.placeholder_for(name, EntityType.PERSON)
    assert mapping.original("[PERSON_12]") == "Brenna Holloway"

    restored, _ = rehydrate(f"PERSON_1{FULLWIDTH_TWO} signed the lease.", mapping)

    assert "Zorvath Quillin" not in restored
