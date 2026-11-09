from pathlib import Path

import pytest

from tools.check_no_egress import Violation, check_paths, check_source, main, rules_for_path

ROOT = Path("/repo")
REPO = Path(__file__).resolve().parents[3]


def violations(source: str, relpath: str) -> list[Violation]:
    rules = rules_for_path(ROOT / relpath, ROOT)
    assert rules is not None, f"{relpath} should have rules"
    return check_source(source, relpath, rules)


@pytest.mark.parametrize(
    "source",
    [
        "import socket",
        "from urllib.request import urlopen",
        "from http import client",
        "import urllib.request as u",
        "import requests",
        "import huggingface_hub",
        '__import__("socket")',
        "import subprocess",
        "import importlib",
        "import ctypes",
        'import os\nos.system("curl example.com")',
        'from os import popen\npopen("curl example.com")',
        'eval("1")',
    ],
)
def test_engine_rejects_network_process_and_dynamic_code(source: str) -> None:
    found = violations(source, "gateway/engine/detect.py")
    assert len(found) == 1
    assert found[0].path == "gateway/engine/detect.py"


def test_engine_reports_the_offending_line() -> None:
    found = violations("x = 1\n\nimport socket\n", "gateway/engine/detect.py")
    assert found[0].line == 3
    assert "socket" in found[0].detail


def test_engine_allows_plain_standard_library() -> None:
    source = "import re\nimport os\nfrom dataclasses import dataclass\nos.path.join('a', 'b')\n"
    assert violations(source, "gateway/engine/detect.py") == []


def test_mentions_in_comments_and_strings_are_not_imports() -> None:
    source = '# socket and httpx are banned here\nNOTE = "import socket"\n'
    assert violations(source, "gateway/engine/detect.py") == []


def test_relative_imports_are_not_flagged() -> None:
    assert violations("from . import requests", "gateway/engine/detect.py") == []


def test_parsers_may_run_subprocesses_but_not_open_connections() -> None:
    assert violations("import subprocess", "gateway/parsers/pdf.py") == []
    assert len(violations("import httpx", "gateway/parsers/pdf.py")) == 1


def test_proxy_must_go_through_cloud_for_outbound_calls() -> None:
    assert len(violations("import httpx", "gateway/proxy/app.py")) == 1


def test_cloud_may_make_requests(tmp_path: Path) -> None:
    cloud = tmp_path / "gateway" / "cloud" / "openai.py"
    cloud.parent.mkdir(parents=True)
    cloud.write_text("import httpx\nimport socket\n")

    assert rules_for_path(cloud, tmp_path) is None
    assert check_paths([tmp_path / "gateway"], tmp_path) == []


def test_cli_module_has_network_rules() -> None:
    assert len(violations("import requests", "gateway/cli.py")) == 1


def test_syntax_error_fails_closed() -> None:
    found = violations("def broken(:\n", "gateway/engine/detect.py")
    assert len(found) == 1
    assert "parse" in found[0].detail


def test_paths_outside_gateway_have_no_rules() -> None:
    assert rules_for_path(ROOT / "tools/check_no_egress.py", ROOT) is None
    assert rules_for_path(ROOT / "tests/unit/test_cli.py", ROOT) is None


def test_violation_text_is_greppable() -> None:
    found = violations("import socket", "gateway/engine/detect.py")
    assert str(found[0]).startswith("gateway/engine/detect.py:1: ")


def test_check_paths_scans_directories(tmp_path: Path) -> None:
    engine = tmp_path / "gateway" / "engine"
    engine.mkdir(parents=True)
    (engine / "bad.py").write_text("import socket\n")
    (engine / "good.py").write_text("import re\n")

    found = check_paths([tmp_path / "gateway"], tmp_path)

    assert [v.path for v in found] == ["gateway/engine/bad.py"]


def test_main_returns_1_and_prints_violations(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "gateway" / "engine" / "bad.py"
    bad.parent.mkdir(parents=True)
    bad.write_text("import socket\n")

    assert main(["--root", str(tmp_path), str(bad)]) == 1
    assert "gateway/engine/bad.py:1:" in capsys.readouterr().out


def test_main_returns_0_when_clean(tmp_path: Path) -> None:
    good = tmp_path / "gateway" / "engine" / "good.py"
    good.parent.mkdir(parents=True)
    good.write_text("import re\n")

    assert main(["--root", str(tmp_path), str(good)]) == 0


def test_repository_is_clean() -> None:
    assert check_paths([REPO / "gateway"], REPO) == []
