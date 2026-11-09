"""Image resizing shared by the Mela writer and the LLM extractor."""

from __future__ import annotations

import io

from PIL import Image, ImageOps

try:  # iPhone photos are often HEIC; support them if the plugin is installed.
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:
    pass


def to_jpeg(data: bytes, max_edge: int, quality: int = 85) -> bytes | None:
    """Resize so the long edge is at most `max_edge` and re-encode as JPEG. None if unreadable."""
    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)
        img.thumbnail((max_edge, max_edge))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        return buf.getvalue()
    except Exception:
        return None
