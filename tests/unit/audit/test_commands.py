import json
from datetime import UTC, datetime
from pathlib import Path

import keyring
import pytest
from keyring.backends.fail import Keyring as NoKeyring

from gateway.audit import commands
from gateway.audit.export import verify_export
from gateway.audit.store import AuditLog
from gateway.cli import main
from gateway.engine.session_disk import InMemoryKeyProvider
from gateway.secrets import AUDIT_KEY_ID, KeyringKeyProvider
from tests.audit_support import Clock, all_rows, fill, tamper

LONG_AGO = datetime(2020, 1, 1, tzinfo=UTC)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "sg"
    monkeypatch.setenv("SAFEGUARD_HOME", str(path))
    return path


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch) -> InMemoryKeyProvider:
    provider = InMemoryKeyProvider()
    monkeypatch.setattr(commands, "default_key_provider", lambda: provider)
    return provider


@pytest.fixture
def db(home: Path, keys: InMemoryKeyProvider) -> Path:
    path = home / "audit.db"
    with AuditLog(path, keys, clock=Clock(now=LONG_AGO)) as log:
        fill(log, 3)
    return path


def key_of(keys: InMemoryKeyProvider) -> bytes:
    key = keys.get(AUDIT_KEY_ID)
    assert key is not None
    return key


def test_audit_alone_prints_help(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    del home
    assert main(["audit"]) == 0
    out = capsys.readouterr().out
    assert "verify" in out
    assert "export" in out
    assert "purge" in out


def test_verify_reports_ok(db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    del db
    assert main(["audit", "verify"]) == 0
    out = capsys.readouterr().out
    assert "OK" in out
    assert "3 rows" in out


def test_verify_names_the_tampered_row(db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tamper(db, "UPDATE audit_log SET bytes_out = 999 WHERE id = 2")
    assert main(["audit", "verify"]) == 1
    out = capsys.readouterr().out
    assert "row 2" in out
    assert "edited" in out


def test_verify_takes_another_file(db: Path, tmp_path: Path) -> None:
    moved = tmp_path / "kept-as-evidence.db"
    db.rename(moved)
    assert main(["audit", "verify", "--db", str(moved)]) == 0


def test_verify_without_a_log_exits_2_and_creates_nothing(
    home: Path, keys: InMemoryKeyProvider, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["audit", "verify"]) == 2
    assert "no audit log" in capsys.readouterr().err
    assert not (home / "audit.db").exists()
    assert keys.get(AUDIT_KEY_ID) is None


def test_verify_without_a_key_exits_2_and_creates_none(
    db: Path, keys: InMemoryKeyProvider, capsys: pytest.CaptureFixture[str]
) -> None:
    del db
    keys.delete(AUDIT_KEY_ID)
    assert main(["audit", "verify"]) == 2
    assert "device key" in capsys.readouterr().err
    assert keys.get(AUDIT_KEY_ID) is None


def test_verify_without_a_keychain_exits_2(
    db: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # As on this Linux container: keyring has no backend and raises NoKeyringError.
    del db
    monkeypatch.setattr(commands, "default_key_provider", KeyringKeyProvider)
    keyring.set_keyring(NoKeyring())  # the conftest fixture restores the previous one
    assert main(["audit", "verify"]) == 2
    assert "keychain" in capsys.readouterr().err


def test_export_writes_signed_json(
    db: Path, keys: InMemoryKeyProvider, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del db
    out = tmp_path / "export.json"
    assert main(["audit", "export", "-o", str(out)]) == 0
    document = json.loads(out.read_text(encoding="ascii"))
    assert [row["id"] for row in document["rows"]] == [1, 2, 3]
    assert verify_export(document, key_of(keys))
    assert not out.with_suffix(".pdf").exists()
    assert "export.json" in capsys.readouterr().out


def test_export_with_pdf_writes_both(db: Path, tmp_path: Path) -> None:
    del db
    out = tmp_path / "export.json"
    assert main(["audit", "export", "-o", str(out), "--pdf"]) == 0
    assert out.is_file()
    assert out.with_suffix(".pdf").read_bytes().startswith(b"%PDF-1.4")


def test_export_filters_by_day(db: Path, tmp_path: Path) -> None:
    del db
    out = tmp_path / "export.json"
    code = main(["audit", "export", "--from", "2020-01-01", "--to", "2020-01-01", "-o", str(out)])
    assert code == 0
    assert len(json.loads(out.read_text())["rows"]) == 3
    code = main(["audit", "export", "--from", "2020-01-02", "-o", str(out)])
    assert code == 0
    assert json.loads(out.read_text())["rows"] == []


def test_export_refuses_a_pdf_name_for_the_json(db: Path, tmp_path: Path) -> None:
    del db
    assert main(["audit", "export", "-o", str(tmp_path / "x.pdf")]) == 2
    assert not (tmp_path / "x.pdf").exists()


def test_export_refuses_bad_dates(db: Path, tmp_path: Path) -> None:
    del db
    out = str(tmp_path / "e.json")
    with pytest.raises(SystemExit) as exit_info:
        main(["audit", "export", "--from", "2026-13-01", "-o", out])
    assert exit_info.value.code == 2
    assert main(["audit", "export", "--from", "2026-11-03", "--to", "2026-11-02", "-o", out]) == 2


def test_export_of_a_broken_log_is_written_and_exits_1(
    db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tamper(db, "DELETE FROM audit_log WHERE id = 2")
    out = tmp_path / "export.json"
    assert main(["audit", "export", "-o", str(out)]) == 1
    assert json.loads(out.read_text())["verification"]["first_bad_id"] == 2
    assert "row 2" in capsys.readouterr().err


def test_purge_with_a_flag_keeps_the_log_verifiable(
    db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["audit", "purge", "--older-than-days", "30"]) == 0
    assert "purged 3 rows" in capsys.readouterr().out
    assert [row["kind"] for row in all_rows(db)] == ["checkpoint"]
    assert main(["audit", "verify"]) == 0


def test_purge_reads_retention_days_from_config(db: Path, home: Path) -> None:
    (home / "config.toml").write_text("retention_days = 30\n")
    assert main(["audit", "purge"]) == 0
    assert [row["kind"] for row in all_rows(db)] == ["checkpoint"]


def test_purge_needs_a_retention_period(db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    del db
    assert main(["audit", "purge"]) == 2
    assert "retention_days" in capsys.readouterr().err


def test_purge_refuses_zero_days(db: Path) -> None:
    del db
    with pytest.raises(SystemExit) as exit_info:
        main(["audit", "purge", "--older-than-days", "0"])
    assert exit_info.value.code == 2


def test_purge_without_a_log_has_nothing_to_do(home: Path) -> None:
    assert main(["audit", "purge", "--older-than-days", "30"]) == 0
    assert not (home / "audit.db").exists()


def test_purge_refuses_a_broken_log(db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tamper(db, "UPDATE audit_log SET status = 500 WHERE id = 1")
    assert main(["audit", "purge", "--older-than-days", "30"]) == 1
    assert "row 1" in capsys.readouterr().err
    assert len(all_rows(db)) == 3


def test_purge_reads_the_full_gateway_config(db: Path, home: Path) -> None:
    (home / "config.toml").write_text(
        'provider = "ollama"\nretention_days = 30\n\n[proxy]\nmax_body_bytes = 1024\n'
    )
    assert main(["audit", "purge"]) == 0
    assert [row["kind"] for row in all_rows(db)] == ["checkpoint"]


def test_purge_refuses_a_config_the_gateway_would_refuse(
    db: Path, home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (home / "config.toml").write_text("retention_days = 30\nretention_dayz = 1\n")
    assert main(["audit", "purge"]) == 2
    assert "retention_dayz" in capsys.readouterr().err
    assert len(all_rows(db)) == 3
