"""Entry point of the frozen sidecar: `safeguard-sidecar` is `safeguard serve` (ADR 0027).

PyInstaller needs a script, not a console-script name. With no arguments the sidecar
serves on the default loopback port; the desktop shell passes none.
"""

import multiprocessing
import sys

from gateway.cli import main


def run(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    return main(arguments or ["serve"])


if __name__ == "__main__":
    # torch and spaCy can start worker processes; a frozen build needs this call first.
    multiprocessing.freeze_support()
    sys.exit(run())
