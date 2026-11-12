"""Line icons for Download Sorter's windows.

One consistent set drawn on a 24x24 grid with a 1.8px rounded stroke, so every
button and menu looks the same on every PC (emoji render differently on each
Windows version and font).  ``icon("home")`` returns a QIcon in the muted text
color; pass ``color=`` for another token color.
"""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from suite_theme import COLORS

_PATHS: dict[str, str] = {
    # navigation
    "home": '<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V20h14V9.5"/><path d="M10 20v-6h4v6"/>',
    "rules": '<path d="M4 6h16"/><path d="M7 12h10"/><path d="M10 18h4"/>',
    "cleanup": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>'
               '<path d="m9 13 2 2 4-4"/>',
    "settings": '<path d="M4 6h9"/><path d="M19 6h1"/><circle cx="16" cy="6" r="2.5"/>'
                '<path d="M4 12h2"/><path d="M12 12h8"/><circle cx="9" cy="12" r="2.5"/>'
                '<path d="M4 18h11"/><path d="M21 18h-1"/><circle cx="17.5" cy="18" r="2.5"/>',
    # actions
    "sort": '<path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M4 21h16"/>',
    "undo": '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
    "play": '<path d="M7 4.5v15l12-7.5z"/>',
    "pause": '<path d="M8 5v14"/><path d="M16 5v14"/>',
    "folder": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "reveal": '<path d="M14 4h6v6"/><path d="M20 4l-9 9"/>'
              '<path d="M18 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4"/>',
    "refresh": '<path d="M20 12a8 8 0 1 1-2.35-5.65"/><path d="M20 4v5h-5"/>',
    "plus": '<path d="M12 5v14"/><path d="M5 12h14"/>',
    "edit": '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m13.5 6.5 4 4"/>',
    "trash": '<path d="M4 7h16"/><path d="M9 7V4h6v3"/><path d="M6 7l1 13h10l1-13"/>',
    "up": '<path d="m6 14 6-6 6 6"/>',
    "down": '<path d="m6 10 6 6 6-6"/>',
    "more": '<circle cx="5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="19" cy="12" r="1.6"/>',
    "library": '<path d="M5 4h4v16H5z"/><path d="M11 4h4v16h-4z"/><path d="m17 5 3.5 1-3.5 14.5"/>',
    "import": '<path d="M12 4v11"/><path d="m7 10 5 5 5-5"/><path d="M5 20h14"/>',
    "export": '<path d="M12 15V4"/><path d="m7 9 5-5 5 5"/><path d="M5 20h14"/>',
    "reset": '<path d="M4 12a8 8 0 1 0 2.35-5.65"/><path d="M4 4v5h5"/>',
    "search": '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.4-4.4"/>',
    "check": '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    "close": '<path d="M6 6l12 12"/><path d="M18 6 6 18"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v6"/><path d="M12 7.5v.5"/>',
    "warning": '<path d="M12 4 2.8 19.5h18.4Z"/><path d="M12 10v4.5"/><path d="M12 17v.5"/>',
    "back": '<path d="M19 12H5"/><path d="m11 6-6 6 6 6"/>',
    "next": '<path d="M5 12h14"/><path d="m13 6 6 6-6 6"/>',
    "file": '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/>',
    "lock": '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7.5a4 4 0 0 1 8 0V11"/>',
    "shield": '<path d="M12 3 4.5 6v6c0 4.6 3.2 7.8 7.5 9 4.3-1.2 7.5-4.4 7.5-9V6z"/>',
    "copy": '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/>',
    "move": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>'
            '<path d="M8.5 13h6"/><path d="m12.5 10.5 2.5 2.5-2.5 2.5"/>',
    "rule": '<path d="M4 6h11"/><path d="M4 12h7"/><path d="M4 18h7"/><path d="M17.5 14v6"/><path d="M14.5 17h6"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "chevron_right": '<path d="m9 6 6 6-6 6"/>',
    "filter": '<path d="M4 5h16l-6.2 7.6V19l-3.6-1.8v-4.6z"/>',
    "help": '<circle cx="12" cy="12" r="9"/><path d="M9.6 9.4a2.5 2.5 0 1 1 3.6 2.2c-.8.4-1.2 1-1.2 1.9"/><path d="M12 17v.5"/>',
    # kinds of file (names match ui_categories.KINDS)
    "cat_cad": '<path d="M12 3 20 7.5v9L12 21l-8-4.5v-9z"/><path d="M4 7.5 12 12l8-4.5"/><path d="M12 12v9"/>',
    "cat_cnc": '<circle cx="12" cy="12" r="3"/><path d="M12 3v3"/><path d="M12 18v3"/><path d="M3 12h3"/>'
               '<path d="M18 12h3"/><path d="m5.6 5.6 2.1 2.1"/><path d="m16.3 16.3 2.1 2.1"/>'
               '<path d="m18.4 5.6-2.1 2.1"/><path d="m7.7 16.3-2.1 2.1"/>',
    "cat_print": '<path d="m12 4 8 4-8 4-8-4z"/><path d="m4 12 8 4 8-4"/><path d="m4 16 8 4 8-4"/>',
    "cat_pdf": '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/><path d="M9 15c1-2.2 2-2.2 3 0s2 2.2 3 0"/>',
    "cat_doc": '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/><path d="M9 12h6"/><path d="M9 15.5h6"/><path d="M9 19h3"/>',
    "cat_sheet": '<rect x="4" y="5" width="16" height="14" rx="2"/><path d="M4 10h16"/><path d="M4 14.5h16"/><path d="M10 5v14"/>',
    "cat_image": '<rect x="3.5" y="5" width="17" height="14" rx="2"/><circle cx="9" cy="10" r="1.6"/>'
                 '<path d="m4 17 5-4 4 3 3-2.5 4 3"/>',
    "cat_video": '<rect x="3" y="6" width="13" height="12" rx="2"/><path d="m16 10.5 5-3v9l-5-3z"/>',
    "cat_audio": '<path d="M9 18V6l10-2v12"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="16.5" cy="16" r="2.5"/>',
    "cat_archive": '<rect x="3" y="4" width="18" height="4" rx="1"/><path d="M5 8v11a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8"/>'
                   '<path d="M10 12.5h4"/>',
    "cat_installer": '<path d="M12 3v11"/><path d="m8 10 4 4 4-4"/><path d="M5 17v3h14v-3"/>',
    "cat_code": '<path d="m8 8-4 4 4 4"/><path d="m16 8 4 4-4 4"/><path d="m13.5 5-3 14"/>',
    "cat_default": '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/>',
    # profiles
    "profile_home": '<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V20h14V9.5"/><path d="M10 20v-6h4v6"/>',
    "profile_school": '<path d="M2.5 9 12 4.5 21.5 9 12 13.5z"/><path d="M6.5 11v5c1.5 1.6 3.4 2.5 5.5 2.5s4-.9 5.5-2.5v-5"/>'
                      '<path d="M21.5 9v5"/>',
    "profile_office": '<rect x="3" y="7" width="18" height="13" rx="2"/><path d="M8.5 7V5a2 2 0 0 1 2-2h3a2 2 0 0 1 2 2v2"/>'
                      '<path d="M3 13h18"/>',
    "profile_maker": '<path d="M12 3 20 7.5v9L12 21l-8-4.5v-9z"/><path d="M4 7.5 12 12l8-4.5"/><path d="M12 12v9"/>',
}

# Icons that read better filled (small dots and the play triangle).
_FILLED = {"more", "play"}

PERSONA_ICONS = {
    "custom": "folder",
    "everyday_home": "profile_home",
    "academic_student": "profile_school",
    "office_admin": "profile_office",
    "maker_3d": "profile_maker",
}


def names() -> list[str]:
    return sorted(_PATHS)


def svg(name: str, color: str) -> str:
    body = _PATHS[name]
    fill = color if name in _FILLED else "none"
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'fill="{fill}" stroke="{color}" stroke-width="1.8" '
        f'stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
    )


@lru_cache(maxsize=256)
def pixmap(name: str, color: str | None = None, size: int = 16, scale: float = 2.0) -> QPixmap:
    """Render an icon to a crisp pixmap (drawn at ``scale`` for high-DPI screens)."""
    renderer = QSvgRenderer(QByteArray(svg(name, color or COLORS["textMuted"]).encode("utf-8")))
    px = QPixmap(int(size * scale), int(size * scale))
    px.fill(Qt.GlobalColor.transparent)
    painter = QPainter(px)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, size * scale, size * scale))
    painter.end()
    px.setDevicePixelRatio(scale)
    return px


def icon(name: str, color: str | None = None, size: int = 16) -> QIcon:
    return QIcon(pixmap(name, color, size))


def category_icon_name(kind: str) -> str:
    """The icon for a kind of file ("pdf" -> "cat_pdf"); unknown kinds get the plain file icon."""
    name = f"cat_{kind}"
    return name if name in _PATHS else "cat_default"


def category_icon(kind: str, color: str | None = None, size: int = 16) -> QIcon:
    return icon(category_icon_name(kind), color, size)
