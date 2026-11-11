"""Shared look for Download Sorter's windows.

Every window applies ``stylesheet()`` once at its top level, and widgets pick a
look by role instead of carrying their own inline styles:

* buttons: default (secondary), ``role="primary"`` (one per screen),
  ``role="ghost"`` (low-emphasis), ``role="nav"`` (sidebar)
* frames: ``objectName="card"`` for a raised panel; ``tone="success"``,
  ``"warning"``, ``"info"`` or ``"critical"`` tints it (``set_tone``), and
  ``hero="true"`` gives the main status card its soft gradient
* labels: ``role="title"``, ``"section"``, ``"muted"``, ``"caption"``, and
  ``role="badge"`` with ``kind="neutral"|"success"|"info"|"warning"|"critical"``
  (``badge()``)
* checkboxes: ``role="switch"`` draws an on/off switch (``switch_row()``)
* radio buttons: ``role="segment"`` draws a button-style choice

All colors and sizes come from suite_theme tokens.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import re
import tempfile
from typing import Any

from PySide6.QtCore import QEvent, QObject, QSize, Qt
from PySide6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

import ui_icons
from suite_theme import ACCENTS, COLORS, GRADIENT, RADII, TYPE

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


@lru_cache(maxsize=1)
def _switch_images() -> dict[str, str]:
    """Write the on/off switch pictures to small SVG files (style sheets can only load images from files)."""
    c = COLORS

    def draw(track: str, stroke: str, knob: str, knob_x: float, ring: bool = False) -> str:
        width = 2 if ring else 1
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" width="34" height="20" viewBox="0 0 34 20">'
            f'<rect x="{width / 2}" y="{width / 2}" width="{34 - width}" height="{20 - width}"'
            f' rx="{(20 - width) / 2}" fill="{track}" stroke="{stroke}" stroke-width="{width}"/>'
            f'<circle cx="{knob_x}" cy="10" r="6" fill="{knob}"/></svg>'
        )

    pictures = {
        "off": draw(c["canvas"], c["borderStrong"], c["textMuted"], 10),
        "on": draw(ACCENT, ACCENT, c["canvas"], 24),
        "off_focus": draw(c["canvas"], c["focus"], c["textMuted"], 10, ring=True),
        "on_focus": draw(ACCENT, c["focus"], c["canvas"], 24, ring=True),
        "off_disabled": draw(c["surface"], c["border"], c["textFaint"], 10),
        "on_disabled": draw(c["successBorder"], c["successBorder"], c["textFaint"], 24),
    }
    try:
        folder = Path(tempfile.gettempdir()) / "download-sorter-ui"
        folder.mkdir(parents=True, exist_ok=True)
        paths: dict[str, str] = {}
        for name, body in pictures.items():
            target = folder / f"switch-{name}.svg"
            if not target.exists() or target.read_text(encoding="utf-8") != body:
                target.write_text(body, encoding="utf-8")
            paths[name] = target.as_posix()
        return paths
    except OSError:
        return {}


def _switch_rules() -> str:
    images = _switch_images()
    if not images:  # no temp folder to write to: a plain pill without the knob still reads as on/off
        return (
            'QCheckBox[role="switch"]::indicator { width: 34px; height: 20px; border-radius: 10px;'
            f' border: 1px solid {COLORS["borderStrong"]}; background: {COLORS["canvas"]}; }}\n'
            f'QCheckBox[role="switch"]::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}\n'
        )
    rules = (
        'QCheckBox[role="switch"] { spacing: 0; }\n'
        'QCheckBox[role="switch"]::indicator { width: 34px; height: 20px; border: none;'
        ' border-radius: 0; background: transparent; }\n'
    )
    for state, name in (
        ("unchecked", "off"), ("checked", "on"),
        ("unchecked:focus", "off_focus"), ("checked:focus", "on_focus"),
        ("unchecked:disabled", "off_disabled"), ("checked:disabled", "on_disabled"),
    ):
        rules += (f'QCheckBox[role="switch"]::indicator:{state} {{ border: none; background: transparent;'
                  f' image: url({images[name]}); }}\n')
    return rules


def _qt_gradient(css: str) -> str:
    """Turn a CSS token like "linear-gradient(180deg, <top> 0%, <bottom> 100%)" into a Qt style sheet gradient."""
    stops = re.findall(r"(#[0-9A-Fa-f]{6})\s+(\d+(?:\.\d+)?)%", css)
    if len(stops) < 2:
        return COLORS["surface"]
    body = ", ".join(f"stop:{float(position) / 100:.2f} {color}" for color, position in stops)
    return f"qlineargradient(x1:0, y1:0, x2:0, y2:1, {body})"


def stylesheet() -> str:
    c = COLORS
    check = _check_image()
    check_rule = f"image: url({check});" if check else ""
    switch_rules = _switch_rules()
    hero_bg = _qt_gradient(GRADIENT["surfaceCard"])
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
    /* captions are small, so they use the muted color (6:1 on surface); textFaint is only for disabled text */
    QLabel[role="caption"] {{ color: {c['textMuted']}; font-size: 12px; }}
    QLabel[role="mono"] {{ font-family: '{MONO}'; }}

    /* small rounded tags: Learned, Left alone, ... */
    QLabel[role="badge"] {{
        color: {c['textMuted']};
        background: {c['surfaceRaised']};
        border: 1px solid {c['border']};
        border-radius: 4px;
        padding: 2px 8px;
        font-size: 12px;
        font-weight: 600;
    }}
    QLabel[role="badge"][kind="success"] {{ color: {c['success']}; background: {c['successSubtle']}; border-color: {c['successBorder']}; }}
    QLabel[role="badge"][kind="info"] {{ color: {c['info']}; background: {c['infoSubtle']}; border-color: {c['infoBorder']}; }}
    QLabel[role="badge"][kind="warning"] {{ color: {c['warning']}; background: {c['warningSubtle']}; border-color: {c['warningBorder']}; }}
    QLabel[role="badge"][kind="critical"] {{ color: {c['critical']}; background: {c['criticalSubtle']}; border-color: {c['criticalBorder']}; }}

    /* ---- panels ---- */
    QFrame#card {{
        background: {c['surface']};
        border: 1px solid {c['border']};
        border-radius: {RADII['card']}px;
    }}
    QFrame#card[hero="true"] {{ background: {hero_bg}; }}
    QFrame#card[tone="success"] {{ background: {c['successSubtle']}; border-color: {c['successBorder']}; }}
    QFrame#card[tone="warning"] {{ background: {c['warningSubtle']}; border-color: {c['warningBorder']}; }}
    QFrame#card[tone="info"] {{ background: {c['infoSubtle']}; border-color: {c['infoBorder']}; }}
    QFrame#card[tone="critical"] {{ background: {c['criticalSubtle']}; border-color: {c['criticalBorder']}; }}
    QFrame#sidebar {{
        background: {c['surface']};
        border-right: 1px solid {c['border']};
    }}
    /* chip: a small clickable status area (the sidebar's "Sorting is on") */
    QFrame[role="chip"] {{ background: transparent; border: 1px solid transparent; border-radius: {RADII['control']}px; }}
    QFrame[role="chip"]:hover {{ background: {c['surfaceHover']}; }}
    QFrame[role="chip"]:focus {{ border-color: {c['focus']}; }}
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
    QToolButton[role="ghost"] {{ padding: 0; }}  /* icon-only: the usual padding would squash the icon */
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

    /* keyboard focus ring on checkboxes and radio buttons */
    QCheckBox::indicator:unchecked:focus, QCheckBox::indicator:checked:focus {{ border-color: {c['focus']}; }}
    QRadioButton::indicator:unchecked:focus {{ border-color: {c['focus']}; }}

    /* switch: a checkbox drawn as an on/off toggle */
    {switch_rules}
    /* segment: a radio button drawn as a button, for "pick one of a few" */
    QRadioButton[role="segment"] {{
        background: {c['surfaceRaised']};
        border: 1px solid {c['border']};
        border-radius: {RADII['control']}px;
        color: {c['textMuted']};
        font-weight: 600;
        padding: 6px 14px;
        spacing: 0;
    }}
    QRadioButton[role="segment"]::indicator,
    QRadioButton[role="segment"]::indicator:checked,
    QRadioButton[role="segment"]::indicator:hover {{
        width: 0px; height: 0px; border: none; background: transparent; image: none;
    }}
    QRadioButton[role="segment"]:hover {{ background: {c['surfaceHover']}; border-color: {c['borderStrong']}; color: {c['text']}; }}
    QRadioButton[role="segment"]:checked {{ background: {c['successSubtle']}; border-color: {ACCENT}; color: {ACCENT}; }}
    QRadioButton[role="segment"]:focus {{ border-color: {c['focus']}; }}

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
    QTableWidget::item:hover, QListWidget::item:hover {{ background: {c['surfaceRaised']}; }}
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
    QProgressBar {{ background: {c['surfaceHover']}; border: none; border-radius: 2px; text-align: center; }}
    QProgressBar::chunk {{ background: {ACCENT}; border-radius: 2px; }}
    QProgressBar[tone="info"]::chunk {{ background: {c['info']}; }}

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


def badge(text: str, kind: str = "neutral", parent: QWidget | None = None) -> QLabel:
    """A small rounded tag ("Learned", "Left alone"). ``kind``: neutral, success, info, warning or critical."""
    tag = QLabel(text, parent)
    tag.setProperty("role", "badge")
    tag.setProperty("kind", kind)
    tag.setTextFormat(Qt.TextFormat.PlainText)
    return tag


def set_tone(widget: QWidget, tone: str | None) -> None:
    """Tint a card: ``tone`` is success, warning, info, critical, or None for the plain card."""
    widget.setProperty("tone", tone or "")
    polish(widget)


class _ClickToToggle(QObject):
    """Lets a click on a row's text flip its switch, like clicking the label beside a checkbox."""

    def __init__(self, switch: QCheckBox) -> None:
        super().__init__(switch)
        self._switch = switch

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.MouseButtonRelease and self._switch.isEnabled():
            self._switch.click()
            return True
        return False


def switch_row(title: str, description: str = "", parent: QWidget | None = None) -> tuple[QWidget, QCheckBox]:
    """A settings row: title and one-line description on the left, an on/off switch on the right.

    Returns ``(row, switch)``; the switch is an ordinary ``QCheckBox``, so code
    reads and connects it like any checkbox. It takes keyboard focus with Tab
    only, which keeps the focus ring for keyboard users.
    """
    row = QWidget(parent)
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 6, 0, 6)
    layout.setSpacing(16)

    text = QVBoxLayout()
    text.setSpacing(2)
    title_label = label(title)
    text.addWidget(title_label)
    description_label = None
    if description:
        description_label = label(description, "muted", wrap=True)
        text.addWidget(description_label)
    layout.addLayout(text, 1)

    switch = QCheckBox()
    switch.setProperty("role", "switch")
    switch.setFocusPolicy(Qt.FocusPolicy.TabFocus)
    switch.setCursor(Qt.CursorShape.PointingHandCursor)
    switch.setAccessibleName(title)
    if description:
        switch.setAccessibleDescription(description)
    layout.addWidget(switch, 0, Qt.AlignmentFlag.AlignVCenter)

    toggler = _ClickToToggle(switch)
    for text_label in (title_label, description_label):
        if text_label is not None:
            text_label.installEventFilter(toggler)
    return row, switch


def polish(widget: QWidget) -> None:
    """Re-apply the stylesheet after a dynamic property change."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def set_role(widget: QWidget, role: Any, icon_name: str | None = None) -> None:
    """Change a widget's style (e.g. a button to "primary"); recolor its icon to match if named."""
    widget.setProperty("role", role)
    if icon_name and isinstance(widget, QPushButton):
        color = COLORS["canvas"] if role == "primary" else COLORS["textMuted"]
        widget.setIcon(ui_icons.icon(icon_name, color))
    polish(widget)
