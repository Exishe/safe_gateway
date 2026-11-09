"""OCR through Tesseract: the one module allowed to import pytesseract (ADR 0017 point 6).

pytesseract runs the local `tesseract` binary in a subprocess; nothing here opens a
network connection. Called only for PDF pages that have no text layer.
"""

import os
import shutil
from dataclasses import dataclass
from functools import cache

import pytesseract
from PIL import Image

from gateway.parsers.document import DocumentError

_WANTED_LANGUAGES = ("eng", "rus", "hye")
_TIMEOUT_SECONDS = 300


class OcrUnavailableError(DocumentError):
    """A page needs OCR and Tesseract is not installed."""


@dataclass(frozen=True)
class OcrLine:
    text: str
    left: int  # pixels in the image, origin top left
    top: int
    width: int
    height: int


def available() -> bool:
    return shutil.which(pytesseract.pytesseract.tesseract_cmd) is not None


@cache
def languages() -> str:
    """The installed subset of eng, rus and hye, as Tesseract's `-l` value."""
    installed = set(pytesseract.get_languages(config=""))
    chosen = [language for language in _WANTED_LANGUAGES if language in installed]
    return "+".join(chosen) or "eng"


def read_lines(image: Image.Image) -> list[OcrLine]:
    if not available():
        msg = "Tesseract is not installed (apt-get install tesseract-ocr tesseract-ocr-rus)"
        raise OcrUnavailableError(msg)
    # Tesseract's OpenMP threads thrash on a busy machine (64 s against 0.4 s for one
    # page under load, measured 2026-11-09); one thread per page is Tesseract's own
    # advice. An explicit setting in the environment wins.
    os.environ.setdefault("OMP_THREAD_LIMIT", "1")
    if image.mode not in {"L", "RGB"}:
        image = image.convert("RGB")
    data = pytesseract.image_to_data(
        image,
        lang=languages(),
        output_type=pytesseract.Output.DICT,
        timeout=_TIMEOUT_SECONDS,
    )
    groups: dict[tuple[int, int, int], list[int]] = {}
    for index, word in enumerate(data["text"]):
        if word.strip():
            key = (data["block_num"][index], data["par_num"][index], data["line_num"][index])
            groups.setdefault(key, []).append(index)
    lines = []
    for indexes in groups.values():
        left = min(data["left"][i] for i in indexes)
        top = min(data["top"][i] for i in indexes)
        right = max(data["left"][i] + data["width"][i] for i in indexes)
        bottom = max(data["top"][i] + data["height"][i] for i in indexes)
        text = " ".join(data["text"][i].strip() for i in indexes)
        lines.append(OcrLine(text, left, top, right - left, bottom - top))
    return lines
