"""Camera photos and screenshots, told apart (#25).

A screenshot is named by the operating system ("Screenshot ...", "Screen Shot ...") and has no
camera make or model; a phone or camera photo has a camera make or model in its EXIF data.
Pillow is only needed to read EXIF, and a file that can't be read is simply left alone.
"""

from __future__ import annotations

import re
from pathlib import Path

PHOTO_EXTENSIONS = frozenset({".jpg", ".jpeg", ".heic", ".heif", ".png", ".webp", ".tif", ".tiff"})
_SCREENSHOT_NAME = re.compile(r"^(screenshot|screen shot|screen_shot|snip)\b", re.IGNORECASE)
_EXIF_MAKE, _EXIF_MODEL = 271, 272


def image_kind(path: Path) -> str | None:
    """"camera", "screenshot", or None (not a photo we can tell apart)."""
    path = Path(path)
    if path.suffix.lower() not in PHOTO_EXTENSIONS:
        return None
    if _SCREENSHOT_NAME.match(path.name):
        return "screenshot"
    if path.suffix.lower() not in (".jpg", ".jpeg", ".heic", ".heif", ".tif", ".tiff"):
        return None
    try:
        from PIL import Image
        with Image.open(path) as image:
            exif = image.getexif()
            if exif.get(_EXIF_MAKE) or exif.get(_EXIF_MODEL):
                return "camera"
    except Exception:
        return None
    return None
