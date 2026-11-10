"""Writers never leave a partial file: write next to the target, rename on success."""

import os
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def atomic_output(output: Path) -> Generator[Path]:
    """A temporary path in `output`'s directory, renamed to `output` if the block succeeds."""
    descriptor, name = tempfile.mkstemp(prefix=".safeguard-", dir=output.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        yield temporary
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
