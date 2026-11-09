"""The retention period from `<SAFEGUARD_HOME>/config.toml` (ADR 0021 point 9).

Only the top-level `retention_days` key is read here. When Phase 4's config module
lands, the integration step should read it through that module instead.
"""

import tomllib
from pathlib import Path


def retention_days(home: Path) -> int | None:
    """Whole days to keep, or None when no config file or no `retention_days` key exists."""
    path = home / "config.toml"
    if not path.is_file():
        return None
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        msg = f"{path}: {error}"
        raise ValueError(msg) from error
    value = data.get("retention_days")
    if value is None:
        return None
    if type(value) is not int or value < 1:
        msg = f"retention_days in {path} must be a whole number of days, at least 1"
        raise ValueError(msg)
    return value
