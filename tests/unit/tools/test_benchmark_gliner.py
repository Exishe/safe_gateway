"""The GLiNER throughput benchmark's input: seeded, sized, in the asked alphabet."""

import pytest

from gateway.engine.script import segments
from tools.benchmark_gliner import main, sample_text


@pytest.mark.parametrize("lang", ["hy", "en"])
def test_sample_text_is_seeded_sized_and_in_its_script(lang: str) -> None:
    text = sample_text(lang, seed=3, target_bytes=5_000)
    assert text == sample_text(lang, seed=3, target_bytes=5_000)
    assert 5_000 <= len(text.encode()) < 6_500
    assert {piece_lang for _, _, piece_lang in segments(text)} == {lang}


def test_an_unknown_language_is_refused() -> None:
    with pytest.raises(SystemExit):
        main(["--lang", "de"])
