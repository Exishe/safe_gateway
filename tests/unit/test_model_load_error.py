"""A broken NER stack (torch's DLL missing on Windows) is reported, not a bare HTTP 500."""

import builtins
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from gateway.cli import main
from gateway.cloud.providers import Provider
from gateway.engine import detect as detect_module
from gateway.engine import ner
from gateway.engine.detect import EngineConfig, check_ner_stack, detect
from gateway.engine.ner import ModelLoadError
from gateway.proxy import server
from gateway.proxy.app import create_app
from tests.proxy_fakes import FakeUpstream

VC_REDIST = "https://aka.ms/vs/17/release/vc_redist.x64.exe"
DLL_ERROR = r"[WinError 126] The specified module could not be found. torch\lib\c10.dll"
SECRET = "Zorblax Quendrick"  # noqa: S105  # pragma: allowlist secret

pytestmark = pytest.mark.usefixtures("broken_torch")


@pytest.fixture
def broken_torch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A model is "installed", and importing spaCy or GLiNER raises OSError as torch does."""
    model = tmp_path / "model"
    model.mkdir()
    (model / "config.cfg").write_text("", encoding="utf-8")
    monkeypatch.setattr(detect_module, "model_dir", lambda _lang: model)
    monkeypatch.setattr(detect_module, "_WARNED", set())
    ner.build_analyzer.cache_clear()
    real_import = builtins.__import__

    def failing_import(name: str, *args: Any, **kwargs: Any) -> object:  # noqa: ANN401
        if name in {"spacy", "gliner"}:
            raise OSError(DLL_ERROR)
        return real_import(name, *args, **kwargs)

    modules = dict(sys.modules)  # an import that fails half way leaves others half loaded
    monkeypatch.setattr(builtins, "__import__", failing_import)
    yield
    sys.modules.clear()
    sys.modules.update(modules)


def test_detect_raises_a_clear_error_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(ModelLoadError) as raised:
        detect(f"Meeting with {SECRET} today.", config=EngineConfig(gliner="off"))
    message = str(raised.value)
    assert "torch" in message
    assert VC_REDIST in message
    assert "safeguard serve" in message
    assert SECRET not in message


def test_the_fix_is_named_only_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(ModelLoadError) as raised:
        detect("Meeting with someone today.", config=EngineConfig(gliner="off"))
    assert "torch" in str(raised.value)
    assert VC_REDIST not in str(raised.value)


@pytest.mark.parametrize("path", ["/v1/chat/completions", "/mask"])
def test_proxy_answers_503_with_the_message_and_sends_nothing_upstream(
    path: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    fake = FakeUpstream(Provider.OLLAMA, lambda _r: pytest.fail("nothing may be sent upstream"))
    app = create_app(fake.upstream(), engine_config=EngineConfig(gliner="off"), model="m")
    client = TestClient(app, base_url="http://127.0.0.1:8787")
    if path == "/mask":
        body: dict[str, object] = {"text": f"Ask {SECRET} today"}
    else:
        body = {"model": "m", "messages": [{"role": "user", "content": f"Ask {SECRET} today"}]}

    response = client.post(path, json=body)

    assert response.status_code == 503
    assert VC_REDIST in response.text
    assert SECRET not in response.text
    assert fake.requests == []
    lines = [r for r in caplog.records if "torch" in r.getMessage()]
    assert len(lines) == 1
    assert SECRET not in lines[0].getMessage()


@pytest.mark.parametrize("command", ["scan", "mask"])
def test_cli_prints_the_message_and_exits_2(
    command: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    note = tmp_path / "note.txt"
    note.write_text(f"Ask {SECRET} today.", encoding="utf-8")

    assert main([command, str(note)]) == 2
    err = capsys.readouterr().err
    assert VC_REDIST in err
    assert SECRET not in err


def test_check_ner_stack_raises_when_the_stack_does_not_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(ModelLoadError):
        check_ner_stack()


def test_serve_warns_but_still_starts(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    started: list[bool] = []
    monkeypatch.setattr(server, "run", lambda *_a, **_k: started.append(True) or 0)

    assert main(["serve"]) == 0
    assert started == [True]
    err = capsys.readouterr().err
    assert "warning" in err
    assert VC_REDIST in err
