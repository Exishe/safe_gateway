"""Build the desktop app's Python sidecar with PyInstaller (ADR 0027).

`uv run --group sidecar python -m tools.build_sidecar` writes a one-folder build of
`safeguard serve` to `apps/desktop/src-tauri/resources/safeguard-sidecar/`, with the
web UI from `apps/web/dist` inside it. Models are not bundled: the first run pulls
them with `safeguard models pull` (ADR 0006). PyInstaller is a build-time tool in
its own dependency group, so the runtime license check never sees it.
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

NAME = "safeguard-sidecar"
# PyInstaller's --add-data separator is the platform's PATH separator.
DATA_SEPARATOR = os.pathsep
# Data files and plugin modules that PyInstaller cannot find by reading imports.
_COLLECT_ALL = ("spacy", "presidio_analyzer", "phonenumbers", "gliner")
_COLLECT_SUBMODULES = ("uvicorn", "keyring.backends", "gateway")
_COPY_METADATA = ("safeguard-gateway", "keyring", "pydantic")
_EXCLUDE = ("tkinter", "matplotlib", "IPython", "pytest")


# Removed from the finished folder, relative to `_internal/`. None of it is imported by
# `safeguard serve`: PyTorch's C++ test binaries and test libraries, its headers, GPU
# runtime libraries and static archives (a CPU-only build has most of these absent), and
# packages' own test suites. Measured in ADR 0027.
_PRUNE_PATHS = ("torch/test", "torch/bin", "torch/include", "nvidia", "triton")
_PRUNE_GLOBS = (
    "torch/lib/*_test.so",
    "torch/lib/libbackend_with_compiler.so",
    "torch/lib/libaoti_custom_ops.so",
    "torch/lib/libcu*",
    "torch/lib/libnv*",
    "torch/lib/libnccl*",
    "**/tests",
    "**/*.a",
)


class BuildError(Exception):
    """The build cannot start; the message says what to do."""


def pyinstaller_command(root: Path) -> list[str]:
    ui = root / "apps" / "web" / "dist"
    if not (ui / "index.html").is_file():
        msg = f"no web UI build at {ui}; run `npm ci && npm run build` in apps/web first"
        raise BuildError(msg)
    out = root / "apps" / "desktop" / "src-tauri" / "resources"
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--name",
        NAME,
        "--distpath",
        str(out),
        "--workpath",
        str(root / "build" / "pyinstaller"),
        "--specpath",
        str(root / "build" / "pyinstaller"),
        "--add-data",
        f"{ui}{DATA_SEPARATOR}ui",
    ]
    for option, names in (
        ("--collect-all", _COLLECT_ALL),
        ("--collect-submodules", _COLLECT_SUBMODULES),
        ("--copy-metadata", _COPY_METADATA),
        ("--exclude-module", _EXCLUDE),
    ):
        for name in names:
            command += [option, name]
    command.append(str(root / "tools" / "sidecar_entry.py"))
    return command


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def prune_bundle(bundle: Path) -> int:
    """Delete files the sidecar never imports; return the bytes freed."""
    internal = bundle / "_internal"
    if not internal.is_dir():
        msg = f"no _internal folder in {bundle}; is this a PyInstaller one-folder build?"
        raise BuildError(msg)
    targets = {internal / name for name in _PRUNE_PATHS}
    for pattern in _PRUNE_GLOBS:
        targets.update(internal.glob(pattern))
    freed = 0
    for target in sorted(targets):
        if target.exists() or target.is_symlink():
            freed += _size(target)
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink()
    return freed


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    try:
        command = pyinstaller_command(root)
    except BuildError as error:
        print(error, file=sys.stderr)
        return 2
    # A fixed argument list, no shell: nothing user-supplied reaches it.
    code = subprocess.run(command, check=False, cwd=root).returncode  # noqa: S603
    if code != 0:
        return code
    bundle = root / "apps" / "desktop" / "src-tauri" / "resources" / NAME
    try:
        freed = prune_bundle(bundle)
    except BuildError as error:
        print(error, file=sys.stderr)
        return 2
    print(f"pruned {freed / 1_000_000:.0f} MB of unused files from {bundle}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
