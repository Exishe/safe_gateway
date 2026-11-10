"""The sidecar build's command line: what goes in, and what must stay out (ADR 0027)."""

import sys
from pathlib import Path

import pytest

from tools import build_sidecar
from tools.build_sidecar import BuildError, pyinstaller_command


def _tree(tmp_path: Path) -> Path:
    (tmp_path / "apps" / "web" / "dist").mkdir(parents=True)
    (tmp_path / "apps" / "web" / "dist" / "index.html").write_text("<title>ui</title>")
    return tmp_path


def test_the_command_builds_a_one_folder_app_with_the_ui_and_no_models(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    command = pyinstaller_command(root)
    assert command[:3] == [sys.executable, "-m", "PyInstaller"]
    assert "--onedir" in command
    assert command[command.index("--name") + 1] == "safeguard-sidecar"
    data = [command[i + 1] for i, word in enumerate(command) if word == "--add-data"]
    assert len(data) == 1
    source, _, target = data[0].rpartition(build_sidecar.DATA_SEPARATOR)
    assert Path(source) == root / "apps" / "web" / "dist"
    assert target == "ui"
    # Models are pulled on first run (ADR 0006), never bundled.
    assert not any("model" in word.lower() for word in command if word != "--name")


def test_the_output_goes_under_the_desktop_resources_folder(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    command = pyinstaller_command(root)
    expected = root / "apps" / "desktop" / "src-tauri" / "resources"
    assert Path(command[command.index("--distpath") + 1]) == expected


def test_a_missing_ui_build_is_an_error_not_a_silent_gap(tmp_path: Path) -> None:
    with pytest.raises(BuildError, match="npm run build"):
        pyinstaller_command(tmp_path)


def test_the_distribution_metadata_is_copied_for_the_version_lookup(tmp_path: Path) -> None:
    command = pyinstaller_command(_tree(tmp_path))
    copied = [command[i + 1] for i, word in enumerate(command) if word == "--copy-metadata"]
    assert "safeguard-gateway" in copied


def _touch(base: Path, *names: str) -> None:
    for name in names:
        path = base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 10)


def test_prune_removes_build_only_files_and_keeps_what_runs(tmp_path: Path) -> None:
    bundle = tmp_path / "safeguard-sidecar"
    internal = bundle / "_internal"
    gone = (
        "torch/test/test_nn.py",
        "torch/bin/test_api",
        "torch/include/ATen/ATen.h",
        "torch/lib/libtorchbind_test.so",
        "torch/lib/libjitbackend_test.so",
        "torch/lib/libcudart.so.12",
        "nvidia/cudnn/lib/libcudnn.so",
        "triton/__init__.py",
        "spacy/tests/test_x.py",
        "somepkg/libstatic.a",
    )
    kept = (
        "torch/__init__.py",
        "torch/lib/libtorch_cpu.so",
        "torch/lib/libc10.so",
        "torch/testing/_comparison.py",
        "spacy/lang/ru/__init__.py",
        "ui/index.html",
    )
    _touch(internal, *gone, *kept)
    _touch(bundle, "safeguard-sidecar")
    removed = build_sidecar.prune_bundle(bundle)
    for name in gone:
        assert not (internal / name).exists(), name
    for name in kept:
        assert (internal / name).is_file(), name
    assert (bundle / "safeguard-sidecar").is_file()
    assert removed >= 10 * len(gone)


def test_prune_without_an_internal_folder_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(BuildError, match="_internal"):
        build_sidecar.prune_bundle(tmp_path)
