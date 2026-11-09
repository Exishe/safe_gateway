"""The completion marker of a model install (ADR 0006, amended 2026-11-09).

An install counts only when its marker names the model and the digest pinned for it
now: an empty, half-unpacked or outdated directory is missing.
"""

import json
from pathlib import Path

import pytest

from gateway.engine.model_marker import MARKER, Pin, is_installed, write_marker

PIN = Pin("xx_fake_model-1.0.0", "a" * 64)


def test_a_written_marker_makes_the_directory_installed(tmp_path: Path) -> None:
    write_marker(tmp_path, PIN)

    assert is_installed(tmp_path, PIN)
    recorded = json.loads((tmp_path / MARKER).read_text(encoding="utf-8"))
    assert recorded["model_id"] == PIN.model_id
    assert recorded["sha256"] == PIN.sha256
    assert sorted(p.name for p in tmp_path.iterdir()) == [MARKER]  # no temporary file left


def test_a_missing_or_empty_directory_is_not_installed(tmp_path: Path) -> None:
    assert not is_installed(tmp_path / "absent", PIN)
    assert not is_installed(tmp_path, PIN)


def test_model_files_without_a_marker_are_not_installed(tmp_path: Path) -> None:
    # What an interrupted pull leaves: the files are there, the marker never was written.
    (tmp_path / "config.cfg").write_text("[nlp]\n", encoding="utf-8")
    (tmp_path / "weights.bin").write_bytes(b"\x00" * 16)

    assert not is_installed(tmp_path, PIN)


@pytest.mark.parametrize(
    "other",
    [
        Pin(PIN.model_id, "b" * 64),  # the pin moved on: the old install is outdated
        Pin("xx_other_model-1.0.0", PIN.sha256),  # another model's directory
    ],
)
def test_a_marker_for_another_pin_is_not_installed(tmp_path: Path, other: Pin) -> None:
    write_marker(tmp_path, other)

    assert not is_installed(tmp_path, PIN)


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"{not json",
        b"[]",
        b'"xx_fake_model-1.0.0"',
        b"\xff\xfe not utf-8",
        json.dumps({"model_id": PIN.model_id}).encode(),
        json.dumps({"model_id": PIN.model_id, "sha256": PIN.sha256.upper()}).encode(),
    ],
)
def test_a_damaged_marker_is_not_installed(tmp_path: Path, content: bytes) -> None:
    (tmp_path / MARKER).write_bytes(content)

    assert not is_installed(tmp_path, PIN)


def test_a_marker_that_is_a_directory_is_not_installed(tmp_path: Path) -> None:
    (tmp_path / MARKER).mkdir()

    assert not is_installed(tmp_path, PIN)


def test_a_file_where_the_directory_should_be_is_not_installed(tmp_path: Path) -> None:
    path = tmp_path / "model"
    path.write_bytes(b"not a directory")

    assert not is_installed(path, PIN)


def test_writing_a_marker_replaces_an_older_one(tmp_path: Path) -> None:
    write_marker(tmp_path, Pin(PIN.model_id, "b" * 64))
    write_marker(tmp_path, PIN)

    assert is_installed(tmp_path, PIN)


@pytest.mark.parametrize(
    ("model_id", "sha256"),
    [("", "a" * 64), ("x", "A" * 64), ("x", "a" * 63), ("x", "not-a-digest")],
)
def test_a_pin_needs_a_model_id_and_a_lower_case_sha256(model_id: str, sha256: str) -> None:
    with pytest.raises(ValueError, match=r"model id|sha256"):
        Pin(model_id, sha256)
