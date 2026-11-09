import pytest

import gateway
from gateway.cli import main


def test_version_flag_prints_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])

    assert exit_info.value.code == 0
    assert gateway.__version__ in capsys.readouterr().out


def test_no_arguments_prints_help_and_returns_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert "usage: safeguard" in capsys.readouterr().out


def test_unknown_command_returns_2() -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["no-such-command"])

    assert exit_info.value.code == 2


# ------------------------------------------------------------------ safeguard scan (Phase 1a)

import json  # noqa: E402
from pathlib import Path  # noqa: E402

NOTE = (
    "Client email anna.petrova@example.org\n"
    "card 4111 1111 1111 1111 on file.\n"  # pragma: allowlist secret
)


def test_scan_prints_a_table_with_position_type_and_context(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    note = tmp_path / "note.txt"
    note.write_text(NOTE, encoding="utf-8")

    assert main(["scan", str(note)]) == 0
    out = capsys.readouterr().out
    assert "1:14" in out
    assert "EMAIL" in out
    assert "2:6" in out
    assert "CREDIT_CARD" in out
    assert "2 detections" in out


def test_scan_json_lists_offsets_and_text(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    note = tmp_path / "note.txt"
    note.write_text(NOTE, encoding="utf-8")

    assert main(["scan", "--json", str(note)]) == 0
    items = json.loads(capsys.readouterr().out)
    assert [i["entity_type"] for i in items] == ["EMAIL", "CREDIT_CARD"]
    assert all(NOTE[i["start"] : i["end"]] == i["text"] for i in items)


def test_scan_uses_a_gazetteer(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    note = tmp_path / "note.txt"
    note.write_text("Project Bluebird moves to Q3.", encoding="utf-8")
    clients = tmp_path / "clients.csv"
    clients.write_text("value,entity_type\nBluebird,\n", encoding="utf-8")

    assert main(["scan", "--gazetteer", str(clients), str(note)]) == 0
    assert "CODENAME" in capsys.readouterr().out


def test_scan_says_so_when_nothing_is_found(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    note = tmp_path / "note.txt"
    note.write_text("Nothing personal here.", encoding="utf-8")

    assert main(["scan", str(note)]) == 0
    assert "No detections" in capsys.readouterr().out


def test_scan_of_a_missing_file_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["scan", str(tmp_path / "missing.txt")]) == 2
    assert "cannot read" in capsys.readouterr().err
