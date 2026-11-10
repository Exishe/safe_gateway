"""Build the desktop app's Python sidecar with PyInstaller (ADR 0027).

`uv run --group sidecar python -m tools.build_sidecar` writes a one-folder build of
`safeguard serve` to `apps/desktop/src-tauri/resources/safeguard-sidecar/`, with the
web UI from `apps/web/dist` inside it. Models are not bundled: the first run pulls
them with `safeguard models pull` (ADR 0006). PyInstaller is a build-time tool in
its own dependency group, so the runtime license check never sees it.
"""

import argparse
import os
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


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    try:
        command = pyinstaller_command(root)
    except BuildError as error:
        print(error, file=sys.stderr)
        return 2
    # A fixed argument list, no shell: nothing user-supplied reaches it.
    return subprocess.run(command, check=False, cwd=root).returncode  # noqa: S603


if __name__ == "__main__":
    sys.exit(main())
