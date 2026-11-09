"""Local text recognition for photos and PDFs, so most scans never need the API.

On a Mac this uses Apple's Vision framework (the same engine as Live Text in Photos), via the
`ocrmac` package. It handles printed pages well and reads neat handwriting. Elsewhere it falls
back to Tesseract, which is fine for printed text and poor on handwriting.
"""

from __future__ import annotations

import io
import shutil
import sys
from functools import lru_cache

from PIL import Image, ImageOps

from . import images  # noqa: F401  (registers the HEIC opener if installed)


@lru_cache(maxsize=1)
def backend() -> str | None:
    if sys.platform == "darwin":
        try:
            import ocrmac.ocrmac  # noqa: F401

            return "vision"
        except ImportError:
            pass
    try:
        import pytesseract  # noqa: F401

        if shutil.which("tesseract"):
            return "tesseract"
    except ImportError:
        pass
    return None


def _load(data: bytes) -> Image.Image | None:
    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
        return img.convert("RGB")
    except Exception:
        return None


def order_boxes(boxes: list[tuple[str, float, float, float, float]]) -> list[str]:
    """Reading order for (text, x, y_top, w, h) boxes in 0-1 coords, y measured from the top.

    Cookbook pages are often two columns. If the lines split cleanly into a left and a right
    group, read the left column top to bottom, then the right.
    """
    if not boxes:
        return []
    left = [b for b in boxes if b[1] + b[3] <= 0.52]
    right = [b for b in boxes if b[1] >= 0.48]
    spanning = len(boxes) - len(left) - len(right)
    columns = [boxes]
    if len(left) >= 3 and len(right) >= 3 and spanning <= max(1, len(boxes) // 10):
        rest = [b for b in boxes if b not in left and b not in right]
        columns = [sorted(rest, key=lambda b: b[2]), left, right]
    out = []
    for col in columns:
        out += [b[0] for b in sorted(col, key=lambda b: (round(b[2], 2), b[1]))]
    return out


def _vision(img: Image.Image) -> list[str]:
    from ocrmac import ocrmac

    results = ocrmac.text_from_image(img, recognition_level="accurate", detail=True)
    boxes = []
    for text, _conf, (x, y, w, h) in results:
        boxes.append((text, x, 1 - (y + h), w, h))  # Vision's origin is bottom-left
    return order_boxes(boxes)


def _tesseract(img: Image.Image) -> list[str]:
    import pytesseract

    gray = ImageOps.grayscale(img)
    if max(gray.size) < 1800:  # Tesseract does better on larger text
        scale = 1800 / max(gray.size)
        gray = gray.resize((int(gray.width * scale), int(gray.height * scale)))
    text = pytesseract.image_to_string(gray)
    return [" ".join(line.split()) for line in text.splitlines() if line.strip()]


def image_lines(data: bytes) -> list[str]:
    engine = backend()
    img = _load(data)
    if engine is None or img is None:
        return []
    try:
        return _vision(img) if engine == "vision" else _tesseract(img)
    except Exception:
        return []


def pdf_lines(data: bytes) -> list[str]:
    """Text layer of a PDF. Scanned PDFs without one return []."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception:
        return []
    return [" ".join(line.split()) for line in text.splitlines() if line.strip()]
