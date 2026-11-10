import shutil
import stat
import tempfile
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader

from gateway.engine.detect import EngineConfig, detect
from gateway.parsers import ocr, pdf
from gateway.parsers.document import PagePosition
from gateway.parsers.redact import mask_units
from tests.posix import assert_mode_bits

EMAIL = "anna.petrova@example.org"
CARD = "4111 1111 1111 1111"  # pragma: allowlist secret
STRUCTURED = EngineConfig(ner="off")

needs_tesseract = pytest.mark.skipif(
    shutil.which("tesseract") is None,
    reason="Tesseract is not installed (apt-get install tesseract-ocr tesseract-ocr-rus)",
)


def _scan(tmp_path: Path) -> Path:
    """A page with no text layer: one image, as a scanner writes it (A4 at 150 dpi)."""
    image = Image.new("L", (1240, 1754), 255)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=32)
    draw.text((100, 150), f"Email: {EMAIL}", fill=0, font=font)
    draw.text((100, 225), f"Card {CARD} on file", fill=0, font=font)
    path = tmp_path / "scan.pdf"
    image.save(path, "PDF", resolution=150)
    return path


@needs_tesseract
def test_a_scanned_page_is_read_with_ocr(tmp_path: Path) -> None:
    document = pdf.parse(_scan(tmp_path))

    assert EMAIL in document.text
    assert CARD in document.text
    assert document.metadata["ocr_pages"] == 1
    position = document.source_at(document.text.index(EMAIL))
    assert isinstance(position, PagePosition)
    assert position.ocr
    assert position.page == 1
    assert 30 < position.x < 70  # 100 px of 1240 across 595 pt
    assert 700 < position.y < 790


@needs_tesseract
def test_the_redacted_copy_of_a_scan_holds_placeholders_and_no_image(tmp_path: Path) -> None:
    source = _scan(tmp_path)
    document = pdf.parse(source)
    masks, _ = mask_units(document, detect(document.text, config=STRUCTURED))
    out = tmp_path / "out.pdf"
    pdf.write_redacted(source, document, masks, out)

    reader = PdfReader(out)
    text = reader.pages[0].extract_text()
    assert "[EMAIL_1]" in text
    assert "[CREDIT_CARD_1]" in text
    assert EMAIL not in text
    assert not reader.pages[0].images


@needs_tesseract
def test_installed_languages_are_used() -> None:
    assert "eng" in ocr.languages().split("+")


def test_a_scan_without_tesseract_is_refused_with_the_page_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ocr, "available", lambda: False)
    with pytest.raises(ocr.OcrUnavailableError, match="page 1"):
        pdf.parse(_scan(tmp_path))


def test_a_blank_page_needs_no_ocr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ocr, "available", lambda: False)
    blank = tmp_path / "blank.pdf"
    pdf.write_pdf([pdf.Page(200, 200, [])], blank)
    document = pdf.parse(blank)
    assert document.text == ""
    assert document.metadata["pages"] == 1


# ------------------------------------------------------------------ security review M5

_NO_WORDS = ("text", "block_num", "par_num", "line_num", "left", "top", "width", "height")


def _fake_tesseract(
    monkeypatch: pytest.MonkeyPatch, *, fail: bool = False
) -> list[tuple[Path, int]]:
    """Replace the Tesseract call; record the temporary directory it would write to."""
    seen: list[tuple[Path, int]] = []

    def fake_image_to_data(*_args: object, **_kwargs: object) -> dict[str, list[object]]:
        directory = Path(tempfile.gettempdir())
        seen.append((directory, stat.S_IMODE(directory.stat().st_mode)))
        (directory / "tess_page_input.PNG").write_bytes(b"unmasked page")
        if fail:
            msg = "tesseract was killed"
            raise RuntimeError(msg)
        return {key: [] for key in _NO_WORDS}

    monkeypatch.setattr(ocr, "available", lambda: True)
    monkeypatch.setattr(ocr, "languages", lambda: "eng")
    monkeypatch.setattr(ocr.pytesseract, "image_to_data", fake_image_to_data)
    return seen


def test_ocr_writes_only_to_an_owner_only_directory_that_is_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _fake_tesseract(monkeypatch)
    default = tempfile.gettempdir()

    ocr.read_lines(Image.new("L", (10, 10), 255))

    assert len(seen) == 1
    directory, mode = seen[0]
    assert str(directory) != default
    assert directory.name.startswith("safeguard-ocr-")
    assert_mode_bits(mode, 0o700)
    assert not directory.exists()
    assert tempfile.gettempdir() == default


def test_the_private_directory_is_removed_when_tesseract_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _fake_tesseract(monkeypatch, fail=True)
    default = tempfile.gettempdir()
    with pytest.raises(RuntimeError, match="killed"):
        ocr.read_lines(Image.new("L", (10, 10), 255))
    assert not seen[0][0].exists()
    assert tempfile.gettempdir() == default


@needs_tesseract
def test_real_tesseract_files_land_in_the_private_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = ocr.pytesseract.pytesseract.run_tesseract
    outputs: list[tuple[Path, int]] = []

    def spy(
        input_filename: str, output_filename_base: str, *args: object, **kwargs: object
    ) -> None:
        run(input_filename, output_filename_base, *args, **kwargs)
        directory = Path(output_filename_base).parent
        outputs.append((directory, stat.S_IMODE(directory.stat().st_mode)))

    monkeypatch.setattr(ocr.pytesseract.pytesseract, "run_tesseract", spy)
    ocr.read_lines(Image.new("L", (60, 20), 255))
    assert len(outputs) == 1
    directory, mode = outputs[0]
    assert directory.name.startswith("safeguard-ocr-")
    assert_mode_bits(mode, 0o700)
    assert not directory.exists()
