import io
import subprocess
import zipfile
from collections.abc import Mapping
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


@pytest.mark.parametrize(
    "line",
    [
        "card HF50607EJ on file",  # pragma: allowlist secret  (ID card shape)
        "ID number: XT58ACI3SX",  # pragma: allowlist secret  (keyword-gated)
        "# The keyword itself (ID 123456789) is not part of the value.",  # pragma: allowlist secret
    ],
)
def test_shape_only_and_keyword_gated_identifiers_do_not_block_a_commit(line: str) -> None:
    # Masking reports them (ADR 0023); a commit gate keeps to checksum-validated values.
    assert find_pii(line) == []


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
    # npm's lock files are the same kind of page; a sha512 integrity hash can hold an
    # IBAN-shaped run (apps/extension/package-lock.json did).
    assert find_pii(f"pay {IBAN}", "apps/extension/package-lock.json") == []
    assert [f.kind for f in find_pii(f"ssn {SSN}", "package-lock.json")] == ["ssn"]
    # Cargo.lock holds sha256 checksums of crates, shaped the same way.
    assert find_pii(f"pay {IBAN}", "apps/desktop/src-tauri/Cargo.lock") == []
    assert [f.kind for f in find_pii(f"ssn {SSN}", "Cargo.lock")] == ["ssn"]
    assert [f.kind for f in find_pii(f"pay {IBAN}", "package.json")] == ["iban"]


# ---- Office files (zip containers with XML parts)


def _zip_bytes(members: Mapping[str, str | bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _docx(body: str) -> bytes:
    xml = f"<w:document><w:body>{body}</w:body></w:document>"
    return _zip_bytes({"[Content_Types].xml": "<Types/>", "word/document.xml": xml})


def test_docx_text_is_scanned_with_member_and_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The number is split over two runs, as Word does after an edit.
    body = (
        "<w:p><w:r><w:t>Intro</w:t></w:r></w:p>"
        f"<w:p><w:r><w:t>card {CARD[:9]}</w:t></w:r><w:r><w:t>{CARD[9:]}</w:t></w:r></w:p>"
    )
    path = tmp_path / "brief.docx"
    path.write_bytes(_docx(body))
    assert main([str(path)]) == 1
    output = capsys.readouterr().out
    assert "brief.docx:word/document.xml:2: possible card" in output
    assert "4111" not in output


def test_xlsx_cell_values_are_scanned(tmp_path: Path) -> None:
    sheet = f"<worksheet><row><c><v>{SSN}</v></c></row></worksheet>"
    path = tmp_path / "book.xlsx"
    path.write_bytes(_zip_bytes({"xl/worksheets/sheet1.xml": sheet}))
    assert main([str(path)]) == 1


def test_clean_office_file_passes(tmp_path: Path) -> None:
    path = tmp_path / "clean.docx"
    path.write_bytes(_docx("<w:p><w:r><w:t>nothing here</w:t></w:r></w:p>"))
    assert main([str(path)]) == 0


def test_zip_without_office_parts_is_still_skipped(tmp_path: Path) -> None:
    path = tmp_path / "data.zip"
    path.write_bytes(_zip_bytes({"notes.txt": f"ssn {SSN}"}))
    assert main([str(path)]) == 0


def test_office_scan_stops_at_the_size_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("tools.scan_pii._MAX_ZIP_BYTES", 100)
    path = tmp_path / "bomb.docx"
    path.write_bytes(_docx("<w:t>" + "a" * 5000 + "</w:t>"))
    assert main([str(path)]) == 0
    assert "not fully scanned" in capsys.readouterr().err


def test_office_scan_stops_at_the_member_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("tools.scan_pii._MAX_ZIP_MEMBERS", 2)
    members = {f"word/part{i}.xml": "<w:t>x</w:t>" for i in range(5)}
    path = tmp_path / "many.docx"
    path.write_bytes(_zip_bytes(members))
    assert main([str(path)]) == 0
    assert "not fully scanned" in capsys.readouterr().err


def test_corrupt_zip_is_skipped(tmp_path: Path) -> None:
    path = tmp_path / "broken.docx"
    path.write_bytes(b"PK\x03\x04\x00" + b"\x00" * 40)
    assert main([str(path)]) == 0
