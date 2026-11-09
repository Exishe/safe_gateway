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


# ---- engine findings (dogfooding: the product's own recognizers, NER off)

IBAN = "DE89 3704 0044 0532 0130 00"  # pragma: allowlist secret
INN = "7707083893"  # pragma: allowlist secret
SNILS = "112-233-445 95"  # pragma: allowlist secret
NBSP = " "
ZWSP = "\u200b"


def _kinds(text: str) -> list[str]:
    return [finding.kind for finding in find_pii(text)]


def test_iban_is_flagged_by_the_engine() -> None:
    assert _kinds(f"pay to {IBAN}") == ["iban"]


def test_russian_inn_and_snils_are_flagged_as_national_ids() -> None:
    assert _kinds(f"inn {INN}") == ["national_id"]
    assert _kinds(f"snils {SNILS}") == ["national_id"]


@pytest.mark.parametrize("glue", [NBSP, ZWSP, f"{ZWSP}{NBSP}"])
def test_card_with_unicode_spaces_or_zero_width_characters_is_flagged(glue: str) -> None:
    # The shape regex does not see this; only the engine's cleaned view does.
    digits = "4111", "1111", "1111", "1111"  # pragma: allowlist secret
    assert _kinds(f"card {glue.join(digits)}") == ["credit_card"]


def test_a_shape_hit_is_not_reported_twice_by_the_engine() -> None:
    assert _kinds(f"card {CARD} ssn {SSN}") == ["ssn", "card"]


def test_pragma_exempts_engine_findings_on_that_line_only() -> None:
    text = f"iban {IBAN}  # pragma: allowlist secret\nnext line {IBAN}\n"
    assert find_pii(text) == [Finding("<text>", 2, "iban")]


def test_engine_finding_line_is_right_after_unicode_line_breaks() -> None:
    assert find_pii(f"a b\nc {IBAN}")[0].line == 3


def test_emails_phones_ips_and_urls_are_not_reported() -> None:
    text = "mail anna@example.com call +1 415 555 0132 host 192.168.10.20 https://example.com/a"
    assert find_pii(text) == []


def test_engine_finding_text_never_contains_the_value() -> None:
    (finding,) = find_pii(f"iban {IBAN}", "pay.txt")
    assert str(finding) == "pay.txt:1: possible iban (value hidden)"
    assert "3704" not in str(finding)


def test_lock_files_keep_shape_checks_but_skip_the_engine_pass() -> None:
    assert find_pii(f"pay {IBAN}", "uv.lock") == []
    assert [f.kind for f in find_pii(f"ssn {SSN}", "sub/uv.lock")] == ["ssn"]
