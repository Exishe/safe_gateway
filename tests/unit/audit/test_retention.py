from pathlib import Path

import pytest

from gateway.audit.retention import retention_days


def test_retention_days_is_read_from_config(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text('provider = "ollama"\nretention_days = 90\n')
    assert retention_days(tmp_path) == 90


def test_no_config_or_no_key_means_no_retention(tmp_path: Path) -> None:
    assert retention_days(tmp_path) is None
    (tmp_path / "config.toml").write_text('provider = "ollama"\n')
    assert retention_days(tmp_path) is None


@pytest.mark.parametrize("value", ["0", '"90"', "1.5", "true", "-3"])
def test_a_bad_retention_value_is_refused(tmp_path: Path, value: str) -> None:
    (tmp_path / "config.toml").write_text(f"retention_days = {value}\n")
    with pytest.raises(ValueError, match="retention_days"):
        retention_days(tmp_path)


def test_a_broken_config_file_is_refused(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text("retention_days = = 3\n")
    with pytest.raises(ValueError, match=r"config\.toml"):
        retention_days(tmp_path)
