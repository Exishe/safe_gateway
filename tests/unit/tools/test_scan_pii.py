import subprocess
from pathlib import Path

import pytest

from tools.scan_pii import Finding, find_pii, main

# Fake identifiers only. Each literal line carries the allowlist pragma so the
# repository's own pre-commit scan lets this file through.
SSN = "123-45-6789"  # pragma: allowlist secret
CARD = "4111 1111 1111 1111"  # pragma: allowlist secret
CARD_BAD_CHECKSUM = "4111 1111 1111 1112"  # pragma: allowlist secret


def test_ssn_shape_is_flagged() -> None:
    assert find_pii(f"client ssn {SSN}\n") == [Finding("<text>", 1, "ssn")]


def test_luhn_valid_card_with_spaces_is_flagged() -> None:
    assert find_pii(f"line one\ncard {CARD}\n") == [Finding("<text>", 2, "card")]


def test_card_with_dashes_and_no_separators_is_flagged() -> None:
    assert len(find_pii(CARD.replace(" ", "-"))) == 1
    assert len(find_pii(CARD.replace(" ", ""))) == 1


def test_luhn_invalid_number_is_not_flagged() -> None:
    assert find_pii(f"order {CARD_BAD_CHECKSUM}") == []


def test_allowlist_pragma_skips_the_line() -> None:
    assert find_pii(f"x = '{SSN}'  # pragma: allowlist secret") == []


def test_iso_dates_and_short_numbers_are_not_flagged() -> None:
    assert find_pii("released 2026-11-09, build 12345, port 8787") == []


def test_digits_inside_hex_hashes_are_not_flagged() -> None:
    assert find_pii("sha256:ab4111111111111111cd") == []


def test_finding_text_never_contains_the_value() -> None:
    (finding,) = find_pii(f"card {CARD}", "notes.txt")
    text = str(finding)
    assert text.startswith("notes.txt:1: ")
    assert "1111" not in text


def test_main_scans_files_and_returns_1_on_findings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    leaky = tmp_path / "leaky.txt"
    leaky.write_text(f"ssn {SSN}\n")
    clean = tmp_path / "clean.txt"
    clean.write_text("nothing here\n")

    assert main([str(leaky), str(clean)]) == 1
    output = capsys.readouterr().out
    assert "leaky.txt:1: possible ssn" in output
    assert "6789" not in output


def test_main_skips_binary_files(tmp_path: Path) -> None:
    blob = tmp_path / "image.png"
    blob.write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe\x00")
    assert main([str(blob)]) == 0


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)  # noqa: S607


def test_staged_mode_reads_the_index_not_the_working_tree(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    notes = tmp_path / "notes.txt"
    notes.write_text(f"ssn {SSN}\n")
    _git(tmp_path, "add", "notes.txt")
    notes.write_text("cleaned in the working tree but not staged\n")

    assert main(["--staged", "--repo", str(tmp_path)]) == 1

    _git(tmp_path, "add", "notes.txt")
    assert main(["--staged", "--repo", str(tmp_path)]) == 0


# ---- review fixes (Important #2, #4)


def test_windows_1252_export_is_scanned(tmp_path: Path) -> None:
    export = tmp_path / "clients.csv"
    export.write_bytes(f"José,{SSN}\n".encode("cp1252"))
    assert main([str(export)]) == 1


def test_utf16_export_with_bom_is_scanned(tmp_path: Path) -> None:
    export = tmp_path / "clients.csv"
    export.write_bytes(f"name,ssn\nAnna,{SSN}\n".encode("utf-16"))
    assert main([str(export)]) == 1


def test_staged_mode_handles_non_ascii_file_names(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    (tmp_path / "Договор.txt").write_text(f"ssn {SSN}\n")
    _git(tmp_path, "add", "Договор.txt")
    assert main(["--staged", "--repo", str(tmp_path)]) == 1


def test_tracked_mode_scans_every_file_in_the_repository(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    (tmp_path / "notes.txt").write_text(f"ssn {SSN}\n")
    _git(tmp_path, "add", "notes.txt")
    assert main(["--tracked", "--repo", str(tmp_path)]) == 1
