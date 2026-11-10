"""Mode-bit assertions that stay meaningful on Windows.

Windows has no POSIX modes: stat() reports 0o666 for files and 0o777 for directories, and
protection comes from the user profile ACL (docs/SECURITY.md). There the check falls back
to what can be observed: the owner can read and write the path.
"""

import os
import stat
import sys
from pathlib import Path

import pytest

ON_WINDOWS = sys.platform == "win32"

posix_only = pytest.mark.skipif(
    ON_WINDOWS, reason="POSIX mode bits and umask do not exist on Windows"
)


def assert_mode_bits(actual: int, expected: int) -> None:
    """Assert a recorded S_IMODE; on Windows the bits are not meaningful, so nothing is checked."""
    if not ON_WINDOWS:
        assert actual == expected


def assert_mode(path: Path, expected: int) -> None:
    """Assert `path` has the POSIX mode `expected`; on Windows assert the owner can use it."""
    if ON_WINDOWS:
        assert os.access(path, os.R_OK | os.W_OK)
    else:
        assert stat.S_IMODE(path.stat().st_mode) == expected
