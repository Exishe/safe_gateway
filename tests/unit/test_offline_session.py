"""The test session cannot install packages or download models (tests/conftest.py)."""

import importlib.util
import os
import subprocess
import sys

import pytest


def test_package_installers_are_offline_for_the_whole_session() -> None:
    assert os.environ.get("PIP_NO_INDEX") == "1"
    assert os.environ.get("UV_OFFLINE") == "1"


@pytest.mark.skipif(importlib.util.find_spec("spacy") is None, reason="spaCy is not installed")
def test_spacy_model_download_raises_even_when_spacy_is_imported_inside_a_test() -> None:
    import spacy.cli  # noqa: PLC0415  (imported here on purpose: after the fixtures ran)

    with pytest.raises(RuntimeError, match="download"):
        spacy.cli.download("en_core_web_lg")


def test_the_conftest_itself_does_not_import_spacy() -> None:
    # A fresh interpreter that loads only the conftest: spaCy takes seconds to import.
    code = "import sys, tests.conftest; print('spacy' in sys.modules)"
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "False"
