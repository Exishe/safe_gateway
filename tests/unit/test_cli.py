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
    wheels = {f"https://models.example.org/{n}.whl": _fake_wheel(n) for n in ("xx_en", "xx_ru")}
    specs = {
        lang: ModelSpec(name, "1.0.0", url, hashlib.sha256(wheels[url]).hexdigest())
        for lang, name, url in (
            ("en", "xx_en", "https://models.example.org/xx_en.whl"),
            ("ru", "xx_ru", "https://models.example.org/xx_ru.whl"),
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

    (tmp_path / "sg" / "models" / "spacy" / "xx_en-1.0.0").mkdir(parents=True)
    assert main(["models", "list"]) == 0
    out = capsys.readouterr().out
    assert out.count("missing") == 2
    assert "installed" in out
    assert str(tmp_path / "sg" / "models" / "spacy" / "xx_en-1.0.0") in out


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
