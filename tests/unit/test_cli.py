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


def test_scan_table_keeps_the_longest_recognizer_name_apart_from_the_text(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    note = tmp_path / "note.txt"
    licence = "driver's license: KELDA 770032 K9 706\n"  # pragma: allowlist secret
    note.write_text(licence, encoding="utf-8")

    assert main(["scan", "--no-ner", str(note)]) == 0
    assert "id_document_context " in capsys.readouterr().out


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


# ------------------------------------------------------------------ safeguard models (Phase 1b)

import hashlib  # noqa: E402
import io  # noqa: E402
import zipfile  # noqa: E402
from dataclasses import replace  # noqa: E402

import httpx  # noqa: E402

from gateway.cloud.hub import HubFile, HubModelSpec  # noqa: E402
from gateway.cloud.models import ModelSpec  # noqa: E402


def _fake_wheel(name: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(f"{name}/{name}-1.0.0/config.cfg", b"[nlp]\n")
    return buffer.getvalue()


GLINER_REVISION = "0123456789abcdef0123456789abcdef01234567"  # pragma: allowlist secret
GLINER_FILES = {
    ("example/gliner", "gliner_config.json"): b'{"model_name": "example/backbone"}',
    ("example/gliner", "pytorch_model.bin"): b"weights",
    ("example/backbone", "config.json"): b'{"model_type": "deberta-v2"}',
    ("example/backbone", "tokenizer_config.json"): b"{}",
}


def _fake_gliner() -> tuple[HubModelSpec, dict[str, bytes]]:
    files = tuple(
        HubFile(repo, GLINER_REVISION, path, hashlib.sha256(data).hexdigest())
        for (repo, path), data in GLINER_FILES.items()
    )
    served = {
        f"https://huggingface.co/{repo}/resolve/{GLINER_REVISION}/{path}": data
        for (repo, path), data in GLINER_FILES.items()
    }
    return HubModelSpec("xx_gliner", GLINER_REVISION, "example/backbone", files), served


@pytest.fixture
def fake_models(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, ModelSpec]:
    """Small fake wheels and GLiNER files served by a mock transport, under an empty home."""
    monkeypatch.setenv("SAFEGUARD_HOME", str(tmp_path / "sg"))
    # Invented paths on an allowed host: downloads go only to ALLOWED_HOSTS, and none is sent.
    releases = "https://github.com/example/models/releases/download"
    wheels = {f"{releases}/{n}.whl": _fake_wheel(n) for n in ("xx_en", "xx_ru")}
    specs = {
        lang: ModelSpec(name, "1.0.0", url, hashlib.sha256(wheels[url]).hexdigest())
        for lang, name, url in (
            ("en", "xx_en", f"{releases}/xx_en.whl"),
            ("ru", "xx_ru", f"{releases}/xx_ru.whl"),
        )
    }
    gliner, served = _fake_gliner()
    payloads = {**wheels, **served}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = payloads.get(str(request.url))
        return httpx.Response(200, content=payload) if payload else httpx.Response(404)

    monkeypatch.setattr("gateway.cli.SPACY_MODELS", specs)
    monkeypatch.setattr("gateway.cli.GLINER_MODEL", gliner)
    monkeypatch.setattr(
        "gateway.cli.open_client", lambda: httpx.Client(transport=httpx.MockTransport(handler))
    )
    return specs


@pytest.mark.usefixtures("fake_models")
def test_models_list_shows_each_model_as_missing_then_installed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["models", "list"]) == 0
    out = capsys.readouterr().out
    assert "xx_en" in out
    assert "xx_ru" in out
    assert "xx_gliner" in out
    assert out.count("missing") == 3

    # A directory alone is not an install: a pull may have been cut short (M3).
    (tmp_path / "sg" / "models" / "spacy" / "xx_en-1.0.0").mkdir(parents=True)
    (tmp_path / "sg" / "models" / "gliner" / f"xx_gliner-{GLINER_REVISION}").mkdir(parents=True)
    assert main(["models", "list"]) == 0
    assert capsys.readouterr().out.count("missing") == 3

    assert main(["models", "pull", "--only", "en"]) == 0
    capsys.readouterr()
    assert main(["models", "list"]) == 0
    out = capsys.readouterr().out
    assert out.count("missing") == 2
    assert "installed" in out
    assert str(tmp_path / "sg" / "models" / "spacy" / "xx_en-1.0.0") in out


def test_models_list_keeps_long_model_names_apart_from_the_next_column(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from gateway.cloud.hub import GLINER_MODEL  # noqa: PLC0415
    from gateway.cloud.models import SPACY_MODELS  # noqa: PLC0415

    assert main(["models", "list"]) == 0
    rows = capsys.readouterr().out.splitlines()[1:]
    names = [spec.name for spec in SPACY_MODELS.values()] + [GLINER_MODEL.name]
    assert [row.split()[1] for row in rows] == names


@pytest.mark.usefixtures("fake_models")
def test_models_pull_only_en_installs_english_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["models", "pull", "--only", "en"]) == 0

    spacy_dir = tmp_path / "sg" / "models" / "spacy"
    assert (spacy_dir / "xx_en-1.0.0" / "config.cfg").is_file()
    assert not (spacy_dir / "xx_ru-1.0.0").exists()
    assert str(spacy_dir / "xx_en-1.0.0") in capsys.readouterr().out


@pytest.mark.usefixtures("fake_models")
def test_models_pull_installs_every_model_and_skips_what_is_there(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["models", "pull"]) == 0
    assert main(["models", "pull"]) == 0

    spacy_dir = tmp_path / "sg" / "models" / "spacy"
    assert sorted(p.name for p in spacy_dir.iterdir()) == ["xx_en-1.0.0", "xx_ru-1.0.0"]
    gliner_dir = tmp_path / "sg" / "models" / "gliner"
    assert [p.name for p in gliner_dir.iterdir()] == [f"xx_gliner-{GLINER_REVISION}"]
    assert "already installed" in capsys.readouterr().out


@pytest.mark.usefixtures("fake_models")
def test_models_pull_replaces_directories_left_without_a_marker(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spacy_dir = tmp_path / "sg" / "models" / "spacy" / "xx_en-1.0.0"
    gliner_dir = tmp_path / "sg" / "models" / "gliner" / f"xx_gliner-{GLINER_REVISION}"
    for leftover in (spacy_dir, gliner_dir):
        leftover.mkdir(parents=True)
        (leftover / "partial.bin").write_bytes(b"\x00")

    assert main(["models", "pull"]) == 0

    out = capsys.readouterr().out
    assert "already installed" not in out
    assert out.count("verified and installed") == 3
    assert (spacy_dir / "config.cfg").is_file()
    assert (gliner_dir / "pytorch_model.bin").read_bytes() == b"weights"
    assert not (spacy_dir / "partial.bin").exists()
    assert not (gliner_dir / "partial.bin").exists()


@pytest.mark.usefixtures("fake_models")
def test_models_pull_only_gliner_installs_gliner_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["models", "pull", "--only", "gliner"]) == 0

    target = tmp_path / "sg" / "models" / "gliner" / f"xx_gliner-{GLINER_REVISION}"
    assert (target / "pytorch_model.bin").read_bytes() == b"weights"
    assert not (tmp_path / "sg" / "models" / "spacy").exists()
    assert str(target) in capsys.readouterr().out


def test_models_pull_reports_a_bad_gliner_file_and_exits_1(
    fake_models: dict[str, ModelSpec],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    del fake_models
    good, _ = _fake_gliner()
    bad = HubModelSpec(
        good.name,
        good.revision,
        good.backbone,
        (*good.files[:-1], replace(good.files[-1], sha256="0" * 64)),
    )
    monkeypatch.setattr("gateway.cli.GLINER_MODEL", bad)

    assert main(["models", "pull", "--only", "gliner"]) == 1
    assert "SHA-256 mismatch" in capsys.readouterr().err


def test_models_pull_reports_a_bad_download_and_exits_1(
    fake_models: dict[str, ModelSpec],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    broken = ModelSpec("xx_en", "1.0.0", fake_models["en"].url, "0" * 64)
    monkeypatch.setattr("gateway.cli.SPACY_MODELS", {"en": broken})

    assert main(["models", "pull"]) == 1
    assert "SHA-256 mismatch" in capsys.readouterr().err


def test_models_without_a_subcommand_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["models"]) == 0
    assert "pull" in capsys.readouterr().out


# ------------------------------------------------------------------ safeguard scan --no-ner

import logging  # noqa: E402

import gateway.engine.detect as detect_module  # noqa: E402


def test_scan_without_models_warns_and_still_lists_identifiers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(detect_module, "_WARNED", set())
    note = tmp_path / "note.txt"
    note.write_text("Zorvath Quillin, anna.petrova@example.org", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        assert main(["scan", str(note)]) == 0

    assert "EMAIL" in capsys.readouterr().out
    assert any("safeguard models pull --only en" in r.getMessage() for r in caplog.records)


def test_scan_no_ner_skips_the_models_without_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(detect_module, "_WARNED", set())
    note = tmp_path / "note.txt"
    note.write_text("Zorvath Quillin, anna.petrova@example.org", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        assert main(["scan", "--no-ner", str(note)]) == 0

    assert caplog.records == []


# ------------------------------------------------------------------ safeguard mask (Phase 2)

import os  # noqa: E402
import stat  # noqa: E402

from gateway.engine.mask import Mapping  # noqa: E402
from gateway.engine.rehydrate import rehydrate  # noqa: E402

CARD = "4111 1111 1111 1111"  # pragma: allowlist secret


@pytest.fixture
def note(tmp_path: Path) -> Path:
    path = tmp_path / "note.txt"
    path.write_text(NOTE, encoding="utf-8")
    return path


def test_mask_writes_placeholders_to_the_output_file(
    note: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "note.masked.txt"
    assert main(["mask", "--no-ner", str(note), "-o", str(out)]) == 0

    masked = out.read_text(encoding="utf-8")
    assert masked == "Client email [EMAIL_1]\ncard [CREDIT_CARD_1] on file.\n"
    assert "anna.petrova" not in masked
    assert CARD not in masked
    assert capsys.readouterr().out == ""


def test_mask_prints_to_stdout_without_an_output_file(
    note: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["mask", "--no-ner", str(note)]) == 0
    assert capsys.readouterr().out == "Client email [EMAIL_1]\ncard [CREDIT_CARD_1] on file.\n"


def test_mask_json_map_restores_the_original_and_is_private(
    note: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out, map_path = tmp_path / "masked.txt", tmp_path / "map.json"
    assert main(["mask", "--no-ner", str(note), "-o", str(out), "--json-map", str(map_path)]) == 0

    mapping = Mapping.from_dict(json.loads(map_path.read_text(encoding="utf-8")))
    assert rehydrate(out.read_text(encoding="utf-8"), mapping) == (NOTE, [])
    assert stat.S_IMODE(map_path.stat().st_mode) == 0o600
    assert "personal data" in capsys.readouterr().err


def test_mask_refuses_to_overwrite_its_input(
    note: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["mask", "--no-ner", str(note), "-o", str(note)]) == 2
    assert note.read_text(encoding="utf-8") == NOTE
    assert main(["mask", "--no-ner", str(note), "--json-map", str(note)]) == 2
    assert note.read_text(encoding="utf-8") == NOTE
    assert "input file" in capsys.readouterr().err


def test_mask_refuses_one_path_for_output_and_map(
    note: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out.txt"
    assert main(["mask", "--no-ner", str(note), "-o", str(out), "--json-map", str(out)]) == 2
    assert not out.exists()
    out.write_text("", encoding="utf-8")
    hard_link = tmp_path / "link.txt"
    hard_link.hardlink_to(out)
    assert main(["mask", "--no-ner", str(note), "-o", str(out), "--json-map", str(hard_link)]) == 2
    err = capsys.readouterr().err
    assert "same file" in err
    assert "anna.petrova" not in err
    assert out.read_text(encoding="utf-8") == ""


@pytest.mark.parametrize("kind", ["file", "symlink", "dangling symlink"])
def test_mask_never_writes_the_map_over_an_existing_path(
    note: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], kind: str
) -> None:
    target = tmp_path / "elsewhere.txt"
    if kind != "dangling symlink":
        target.write_text("keep", encoding="utf-8")
    map_path = tmp_path / "map.json"
    if kind == "file":
        map_path.write_text("keep", encoding="utf-8")
    else:
        map_path.symlink_to(target)
    out = tmp_path / "out.txt"

    assert main(["mask", "--no-ner", str(note), "-o", str(out), "--json-map", str(map_path)]) == 2

    assert "already exists" in capsys.readouterr().err
    assert not out.exists()
    if kind != "dangling symlink":
        assert target.read_text(encoding="utf-8") == "keep"
    else:
        assert not target.exists()


def test_mask_map_is_0600_even_under_a_permissive_umask(note: Path, tmp_path: Path) -> None:
    map_path = tmp_path / "map.json"
    old = os.umask(0)
    try:
        assert main(["mask", "--no-ner", str(note), "--json-map", str(map_path)]) == 0
    finally:
        os.umask(old)
    assert stat.S_IMODE(map_path.stat().st_mode) == 0o600


def test_a_failed_map_write_leaves_no_partial_map_and_no_output(
    note: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def disk_full(*_args: object, **_kwargs: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "fdopen", disk_full)
    map_path, out = tmp_path / "map.json", tmp_path / "out.txt"
    assert main(["mask", "--no-ner", str(note), "-o", str(out), "--json-map", str(map_path)]) == 2
    assert not map_path.exists()
    assert not out.exists()


def test_mask_of_a_missing_or_non_utf8_file_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["mask", str(tmp_path / "missing.txt")]) == 2
    legacy = tmp_path / "legacy.txt"
    legacy.write_bytes("Café".encode("cp1252"))
    assert main(["mask", str(legacy)]) == 2
    assert "cannot read" in capsys.readouterr().err


def test_mask_uses_a_gazetteer(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    note = tmp_path / "note.txt"
    note.write_text("Project Bluebird moves to Q3.", encoding="utf-8")
    clients = tmp_path / "clients.csv"
    clients.write_text("value,entity_type\nBluebird,\n", encoding="utf-8")

    assert main(["mask", "--no-ner", "--gazetteer", str(clients), str(note)]) == 0
    assert capsys.readouterr().out == "Project [CODENAME_1] moves to Q3."


# ---- security review 2 (M9): `python -m` runs the CLI

import subprocess  # noqa: E402
import sys  # noqa: E402


@pytest.mark.parametrize("module", ["gateway.cli", "gateway"])
def test_python_dash_m_runs_the_cli(tmp_path: Path, module: str) -> None:
    note = tmp_path / "note.txt"
    note.write_text("nothing to see here\n")

    result = subprocess.run(
        [sys.executable, "-m", module, "scan", str(note)],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()


# ------------------------------------------------------------------ mask documents (Phase 3)

import email  # noqa: E402
import email.policy  # noqa: E402
from email.message import EmailMessage  # noqa: E402

import docx  # noqa: E402
import openpyxl  # noqa: E402
from pypdf import PdfReader  # noqa: E402

from gateway.parsers import pdf as pdf_parser  # noqa: E402

DOC_EMAIL = "anna.petrova@example.org"
DOC_CARD = "4111 1111 1111 1111"  # pragma: allowlist secret


def test_mask_docx_writes_a_redacted_docx(tmp_path: Path) -> None:
    source = tmp_path / "contract.docx"
    document = docx.Document()
    document.add_paragraph(f"Client {DOC_EMAIL}, card {DOC_CARD}")
    document.save(str(source))
    out = tmp_path / "out.docx"
    map_file = tmp_path / "map.json"

    assert main(["mask", "--no-ner", str(source), "-o", str(out), "--json-map", str(map_file)]) == 0
    text = "\n".join(p.text for p in docx.Document(str(out)).paragraphs)
    assert text == "Client [EMAIL_1], card [CREDIT_CARD_1]"
    mapping = Mapping.from_dict(json.loads(map_file.read_text(encoding="utf-8")))
    assert rehydrate(text, mapping)[0] == f"Client {DOC_EMAIL}, card {DOC_CARD}"


def test_mask_xlsx_pdf_and_eml(tmp_path: Path) -> None:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet["A1"] = DOC_EMAIL
    workbook.save(tmp_path / "a.xlsx")
    pdf_parser.write_pdf(
        [pdf_parser.Page(300, 300, [pdf_parser.Line(f"to {DOC_EMAIL}", 20, 200, 12)])],
        tmp_path / "b.pdf",
    )
    message = EmailMessage()
    message["From"] = DOC_EMAIL
    message["Subject"] = "hello"
    message.set_content(f"card {DOC_CARD}\n")
    (tmp_path / "c.eml").write_bytes(message.as_bytes())

    for name in ("a.xlsx", "b.pdf", "c.eml"):
        out = tmp_path / f"out-{name}"
        assert main(["mask", "--no-ner", str(tmp_path / name), "-o", str(out)]) == 0, name

    cell = openpyxl.load_workbook(tmp_path / "out-a.xlsx").worksheets[0]["A1"].value
    assert cell == "[EMAIL_1]"
    assert "to [EMAIL_1]" in PdfReader(tmp_path / "out-b.pdf").pages[0].extract_text()
    mail = email.message_from_bytes(
        (tmp_path / "out-c.eml").read_bytes(), policy=email.policy.default
    )
    assert dict(mail.raw_items())["From"] == "[EMAIL_1]"
    body = mail.get_body(("plain",))
    assert body is not None
    assert body.get_content() == "card [CREDIT_CARD_1]\n"


def test_mask_of_a_document_without_output_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "contract.docx"
    docx.Document().save(str(source))
    assert main(["mask", str(source)]) == 2
    assert "-o" in capsys.readouterr().err


def test_mask_of_an_unreadable_document_exits_2_with_a_reason(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "broken.pdf"
    source.write_bytes(b"not a pdf")
    out = tmp_path / "out.pdf"
    assert main(["mask", str(source), "-o", str(out)]) == 2
    assert "broken.pdf" in capsys.readouterr().err
    assert not out.exists()


def test_mask_prints_what_the_copy_lost(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "b.pdf"
    pdf_parser.write_pdf([pdf_parser.Page(300, 300, [pdf_parser.Line("x", 20, 200, 12)])], source)
    assert main(["mask", "--no-ner", str(source), "-o", str(tmp_path / "o.pdf")]) == 0
    assert "rebuilt 1 pages as text only" in capsys.readouterr().err


def test_mask_of_other_suffixes_keeps_the_text_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "notes.md"
    source.write_text(f"# {DOC_EMAIL}\n", encoding="utf-8")
    assert main(["mask", "--no-ner", str(source)]) == 0
    assert capsys.readouterr().out == "# [EMAIL_1]\n"


# ------------------------------------------------------------------ safeguard serve (Phase 4)

from keyring.backends.fail import Keyring as NoKeyring  # noqa: E402

from gateway.proxy import server  # noqa: E402
from tests.proxy_fakes import MemoryKeyring, keyring_backend  # noqa: E402


def test_serve_passes_host_port_and_audit_to_the_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, int, bool]] = []

    def fake_run(host: str, port: int, *, audit: bool) -> int:
        calls.append((host, port, audit))
        return 0

    monkeypatch.setattr(server, "run", fake_run)
    assert main(["serve"]) == 0
    assert main(["serve", "--port", "9000"]) == 0
    assert main(["serve", "--no-audit"]) == 0
    assert calls == [
        ("127.0.0.1", 8787, True),
        ("127.0.0.1", 9000, True),
        ("127.0.0.1", 8787, False),
    ]


def test_serve_without_a_keychain_names_no_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SAFEGUARD_HOME", str(tmp_path))
    started: list[object] = []
    monkeypatch.setattr(server.uvicorn, "run", lambda *a, **k: started.append((a, k)))
    with keyring_backend(NoKeyring()):
        assert main(["serve"]) == 2
    assert "--no-audit" in capsys.readouterr().err
    assert started == []


def test_serve_no_audit_warns_that_nothing_is_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SAFEGUARD_HOME", str(tmp_path))
    monkeypatch.setattr(server.uvicorn, "run", lambda *_a, **_k: None)
    with keyring_backend(NoKeyring()):
        assert main(["serve", "--no-audit"]) == 0
    assert "no audit log" in capsys.readouterr().err


def test_serve_refuses_a_non_loopback_host(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    started: list[object] = []
    monkeypatch.setattr(server.uvicorn, "run", lambda *a, **k: started.append((a, k)))
    assert main(["serve", "--host", "0.0.0.0"]) == 2
    assert "127.0.0.1" in capsys.readouterr().err
    assert started == []


def test_serve_reports_a_missing_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SAFEGUARD_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text('provider = "openai"\n', encoding="utf-8")
    monkeypatch.setattr(server, "read_api_key", lambda _: None)
    with keyring_backend(MemoryKeyring()):
        assert main(["serve"]) == 2
    assert "keyring set safeguard-gateway api-key:openai" in capsys.readouterr().err
