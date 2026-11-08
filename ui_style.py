"""Shared look for Download Sorter's windows.

Every window applies ``stylesheet()`` once at its top level, and widgets pick a
look by role instead of carrying their own inline styles:

* buttons: default (secondary), ``role="primary"`` (one per screen),
  ``role="ghost"`` (low-emphasis), ``role="nav"`` (sidebar)
* frames: ``objectName="card"`` for a raised panel
* labels: ``role="title"``, ``"section"``, ``"muted"``, ``"caption"``

All colors and sizes come from suite_theme tokens.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import tempfile
from typing import Any

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

import ui_icons
from suite_theme import ACCENTS, COLORS, RADII, TYPE

ACCENT = ACCENTS["downloadSorter"]
FONT = TYPE["ui"]
MONO = TYPE["mono"]


@lru_cache(maxsize=1)
def _check_image() -> str:
    """Write the checkbox tick to a small SVG file (style sheets can only load images from files)."""
    try:
        folder = Path(tempfile.gettempdir()) / "download-sorter-ui"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / "check.svg"
        body = ui_icons.svg("check", COLORS["canvas"]).replace('stroke-width="1.8"', 'stroke-width="3"')
        if not target.exists() or target.read_text(encoding="utf-8") != body:
            target.write_text(body, encoding="utf-8")
        return target.as_posix()
    except OSError:
        return ""


def stylesheet() -> str:
    c = COLORS
    check = _check_image()
    check_rule = f"image: url({check});" if check else ""
    return f"""
    QWidget {{
        color: {c['text']};
        font-family: '{FONT}';
        font-size: 13px;
    }}
    QDialog, QStackedWidget, QStackedWidget > QWidget {{
        background: {c['canvas']};
    }}
    QToolTip {{
        background: {c['surfaceRaised']};
        color: {c['text']};
        border: 1px solid {c['border']};
        padding: 4px 8px;
    }}

    /* ---- text ---- */
    QLabel {{ background: transparent; }}
    QLabel[role="title"] {{ font-size: 20px; font-weight: 600; }}
    QLabel[role="section"] {{ font-size: 14px; font-weight: 600; }}
    QLabel[role="muted"] {{ color: {c['textMuted']}; }}
    QLabel[role="caption"] {{ color: {c['textFaint']}; font-size: 12px; }}
    QLabel[role="mono"] {{ font-family: '{MONO}'; }}

    /* ---- panels ---- */
    QFrame#card {{
        background: {c['surface']};
        border: 1px solid {c['border']};
        border-radius: {RADII['card']}px;
    }}
    QFrame#sidebar {{
        background: {c['surface']};
        border-right: 1px solid {c['border']};
    }}
    QFrame#divider {{
        background: {c['border']};
        max-height: 1px;
        min-height: 1px;
    }}

    /* ---- buttons ---- */
    QPushButton, QToolButton {{
        background: {c['surfaceRaised']};
        border: 1px solid {c['border']};
        border-radius: {RADII['control']}px;
        padding: 7px 14px;
        font-weight: 600;
    }}
    QPushButton:hover, QToolButton:hover {{
        background: {c['surfaceHover']};
        border-color: {c['borderStrong']};
    }}
    QPushButton:pressed, QToolButton:pressed {{ background: {c['surface']}; }}
    QPushButton:disabled, QToolButton:disabled {{
        color: {c['textFaint']};
        border-color: {c['border']};
        background: {c['surface']};
    }}
    QPushButton:focus, QToolButton:focus {{ outline: none; border-color: {c['focus']}; }}
    QPushButton[role="primary"] {{
        background: {ACCENT};
        border-color: {ACCENT};
        color: {c['canvas']};
    }}
    QPushButton[role="primary"]:hover {{ border-color: {c['text']}; }}
    QPushButton[role="primary"]:disabled {{
        background: {c['successSubtle']};
        border-color: {c['successBorder']};
        color: {c['textFaint']};
    }}
    QPushButton[role="ghost"], QToolButton[role="ghost"] {{
        background: transparent;
        border-color: transparent;
        color: {c['textMuted']};
    }}
    QPushButton[role="ghost"]:hover, QToolButton[role="ghost"]:hover {{
        background: {c['surfaceHover']};
        color: {c['text']};
    }}
    QToolButton::menu-indicator {{ image: none; width: 0; }}
    QPushButton[role="nav"] {{
        background: transparent;
        border: none;
        border-radius: {RADII['control']}px;
        color: {c['textMuted']};
        text-align: left;
        padding: 9px 12px;
    }}
    QPushButton[role="nav"]:hover {{ background: {c['surfaceHover']}; color: {c['text']}; }}
    QPushButton[role="nav"]:checked {{
        background: {c['surfaceRaised']};
        color: {c['text']};
    }}

    /* ---- inputs ---- */
    QLineEdit, QComboBox, QSpinBox, QDateTimeEdit {{
        background: {c['canvas']};
        border: 1px solid {c['border']};
        border-radius: {RADII['control']}px;
        padding: 6px 10px;
        selection-background-color: {c['successBorder']};
    }}
    QLineEdit:focus, QComboBox:focus {{ border-color: {c['focus']}; }}
    QLineEdit[role="mono"] {{ font-family: '{MONO}'; }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    QComboBox QAbstractItemView {{
        background: {c['surfaceRaised']};
        border: 1px solid {c['border']};
        selection-background-color: {c['surfaceHover']};
        outline: none;
    }}
    QCheckBox {{ spacing: 10px; background: transparent; }}
    QCheckBox::indicator {{
        width: 16px; height: 16px;
        border-radius: 4px;
        border: 1px solid {c['borderStrong']};
        background: {c['canvas']};
    }}
    QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; {check_rule} }}
    QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
    QRadioButton {{ spacing: 8px; background: transparent; }}
    QRadioButton::indicator {{
        width: 14px; height: 14px;
        border-radius: 8px;
        border: 1px solid {c['borderStrong']};
        background: {c['canvas']};
    }}
    QRadioButton::indicator:hover {{ border-color: {ACCENT}; }}
    QRadioButton::indicator:checked {{ border: 4px solid {ACCENT}; background: {c['canvas']}; width: 8px; height: 8px; }}
    QTableWidget::indicator {{
        width: 16px; height: 16px;
        border-radius: 4px;
        border: 1px solid {c['borderStrong']};
        background: {c['canvas']};
    }}
    QTableWidget::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; {check_rule} }}

    /* ---- tables and lists ---- */
    QTableWidget, QListWidget, QTreeWidget {{
        background: {c['surface']};
        alternate-background-color: {c['surface']};
        border: 1px solid {c['border']};
        border-radius: {RADII['card']}px;
        gridline-color: transparent;
        selection-background-color: {c['surfaceHover']};
        selection-color: {c['text']};
        outline: none;
    }}
    QTableWidget::item, QListWidget::item {{ padding: 6px 8px; border-bottom: 1px solid {c['surfaceRaised']}; }}
    QHeaderView {{ background: transparent; }}
    QHeaderView::section {{
        background: {c['surface']};
        color: {c['textMuted']};
        border: none;
        border-bottom: 1px solid {c['border']};
        padding: 8px;
        font-size: 12px;
        font-weight: 600;
    }}
    QTableCornerButton::section {{ background: {c['surface']}; border: none; }}
    QScrollArea {{ background: transparent; border: none; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}

    /* ---- tabs (underline style) ---- */
    QTabWidget::pane {{ border: none; top: -1px; }}
    QTabBar {{ background: transparent; }}
    QTabBar::tab {{
        background: transparent;
        border: none;
        border-bottom: 2px solid transparent;
        color: {c['textMuted']};
        padding: 8px 2px;
        margin-right: 18px;
        font-weight: 600;
    }}
    QTabBar::tab:hover {{ color: {c['text']}; }}
    QTabBar::tab:selected {{ color: {c['text']}; border-bottom-color: {ACCENT}; }}

    /* ---- menus and scrollbars ---- */
    QMenu {{
        background: {c['surfaceRaised']};
        border: 1px solid {c['border']};
        border-radius: {RADII['control']}px;
        padding: 4px;
    }}
    QMenu::item {{ padding: 7px 18px 7px 10px; border-radius: 6px; }}
    QMenu::item:selected {{ background: {c['surfaceHover']}; }}
    QMenu::separator {{ height: 1px; background: {c['border']}; margin: 4px 6px; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 4px; min-height: 30px; }}
    QScrollBar::handle:vertical:hover {{ background: {c['borderStrong']}; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle:horizontal {{ background: {c['border']}; border-radius: 4px; min-width: 30px; }}
    QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
        background: none; border: none; width: 0; height: 0;
    }}
    """


# ---------------------------------------------------------------------------
# small builders so screens read as structure, not styling
# ---------------------------------------------------------------------------

def label(text: str = "", role: str | None = None, wrap: bool = False, parent: QWidget | None = None) -> QLabel:
    lbl = QLabel(text, parent)
    if role:
        lbl.setProperty("role", role)
    lbl.setWordWrap(wrap)
    return lbl


def button(
    text: str,
    role: str | None = None,
    icon_name: str | None = None,
    tooltip: str = "",
    parent: QWidget | None = None,
) -> QPushButton:
    """A push button. ``&`` in text is shown literally (not a shortcut marker)."""
    btn = QPushButton(text.replace("&", "&&"), parent)
    if role:
        btn.setProperty("role", role)
    if icon_name:
        color = COLORS["canvas"] if role == "primary" else COLORS["textMuted"]
        btn.setIcon(ui_icons.icon(icon_name, color))
        btn.setIconSize(QSize(16, 16))
    if tooltip:
        btn.setToolTip(tooltip)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    return btn


def icon_button(icon_name: str, tooltip: str, parent: QWidget | None = None) -> QPushButton:
    btn = QPushButton(parent)
    btn.setProperty("role", "ghost")
    btn.setIcon(ui_icons.icon(icon_name))
    btn.setIconSize(QSize(16, 16))
    btn.setToolTip(tooltip)
    btn.setAccessibleName(tooltip)
    btn.setFixedSize(32, 32)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    return btn


def card(parent: QWidget | None = None, margins: tuple[int, int, int, int] = (18, 16, 18, 16), spacing: int = 12) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame(parent)
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)
    return frame, layout


def divider(parent: QWidget | None = None) -> QFrame:
    line = QFrame(parent)
    line.setObjectName("divider")
    return line


def page_header(title: str, subtitle: str = "", parent: QWidget | None = None) -> tuple[QWidget, QHBoxLayout]:
    """Title and subtitle on the left; returns the row so callers can add actions on the right."""
    box = QWidget(parent)
    row = QHBoxLayout(box)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(8)
    text = QVBoxLayout()
    text.setSpacing(4)
    text.addWidget(label(title, "title"))
    if subtitle:
        text.addWidget(label(subtitle, "muted", wrap=True))
    row.addLayout(text, 1)
    return box, row


def status_dot(color: str, size: int = 8) -> QLabel:
    dot = QLabel()
    dot.setFixedSize(size, size)
    dot.setStyleSheet(f"background: {color}; border-radius: {size // 2}px;")
    return dot


def set_status_dot(dot: QLabel, color: str) -> None:
    size = dot.width()
    dot.setStyleSheet(f"background: {color}; border-radius: {size // 2}px;")


def polish(widget: QWidget) -> None:
    """Re-apply the stylesheet after a dynamic property change."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def set_role(widget: QWidget, role: Any) -> None:
    widget.setProperty("role", role)
    polish(widget)
