"""Small reusable widgets: a step indicator, file-kind tiles and the "..." row menu.

They sit on top of ui_style / ui_icons / ui_categories and use only suite_theme
colors, so every page that needs one looks the same.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Callable, Iterable, Sequence

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QLabel, QMenu, QSizePolicy, QToolButton, QWidget

import ui_categories
import ui_icons
from suite_theme import ACCENTS, COLORS, RADII

ACCENT = ACCENTS["downloadSorter"]


# ---------------------------------------------------------------------------
# step indicator
# ---------------------------------------------------------------------------

class Stepper(QWidget):
    """"1 Your folders - 2 How it works - 3 Finish": done steps get a tick, the current one is filled."""

    CIRCLE = 22
    GAP = 8
    LINK = 22  # length of the line between two steps

    def __init__(self, labels: Iterable[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._labels = [str(text) for text in labels]
        self._current = 0
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._announce()

    def labels(self) -> list[str]:
        return list(self._labels)

    def current(self) -> int:
        return self._current

    def set_current(self, index: int) -> None:
        index = max(0, min(int(index), len(self._labels) - 1)) if self._labels else 0
        if index != self._current:
            self._current = index
            self._announce()
            self.update()

    def _announce(self) -> None:
        if self._labels:
            self.setAccessibleName(f"Step {self._current + 1} of {len(self._labels)}: {self._labels[self._current]}")

    def _bold(self) -> QFont:
        font = QFont(self.font())
        font.setPixelSize(13)
        font.setWeight(QFont.Weight.DemiBold)
        return font

    def sizeHint(self) -> QSize:
        metrics = QFontMetrics(self._bold())
        width = 0
        for position, text in enumerate(self._labels):
            width += self.CIRCLE + self.GAP + metrics.horizontalAdvance(text)
            if position < len(self._labels) - 1:
                width += self.LINK + 2 * self.GAP
        return QSize(width + 2, self.CIRCLE + 6)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bold = self._bold()
        regular = QFont(bold)
        regular.setWeight(QFont.Weight.Normal)
        centre_y = self.height() / 2
        x = 1.0
        for position, text in enumerate(self._labels):
            done = position < self._current
            current = position == self._current
            circle = QRectF(x, centre_y - self.CIRCLE / 2, self.CIRCLE, self.CIRCLE)

            if current:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(ACCENT))
            else:
                painter.setPen(QPen(QColor(ACCENT if done else COLORS["borderStrong"]), 1.5))
                painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(circle)

            if done:
                tick = ui_icons.pixmap("check", ACCENT, 12)
                painter.drawPixmap(QPointF(circle.center().x() - 6, circle.center().y() - 6), tick)
            else:
                painter.setFont(bold)
                painter.setPen(QColor(COLORS["canvas"] if current else COLORS["textMuted"]))
                painter.drawText(circle, Qt.AlignmentFlag.AlignCenter, str(position + 1))

            x += self.CIRCLE + self.GAP
            painter.setFont(bold if current else regular)
            painter.setPen(QColor(COLORS["text"] if current else COLORS["textMuted"]))
            text_width = QFontMetrics(bold).horizontalAdvance(text)
            painter.drawText(QRectF(x, 0, text_width, self.height()),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
            x += text_width

            if position < len(self._labels) - 1:
                x += self.GAP
                painter.setPen(QPen(QColor(COLORS["successBorder"] if done else COLORS["border"]), 2))
                painter.drawLine(QPointF(x, centre_y), QPointF(x + self.LINK, centre_y))
                x += self.LINK + self.GAP


# ---------------------------------------------------------------------------
# file-kind tiles
# ---------------------------------------------------------------------------

def tile_color(kind: str) -> str:
    return ui_categories.COLOR_BY_KIND.get(kind) or ui_categories.COLOR_BY_KIND["default"]


@lru_cache(maxsize=64)
def _tile_pixmap(kind: str, size: int, ratio: float) -> QPixmap:
    color = QColor(tile_color(kind))
    pixmap = QPixmap(int(size * ratio), int(size * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    tint = QColor(color)
    tint.setAlphaF(0.14)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(tint)
    painter.drawRoundedRect(QRectF(0, 0, size, size), RADII["small"], RADII["small"])
    glyph_size = max(12, round(size * 0.57))
    glyph = ui_icons.pixmap(ui_icons.category_icon_name(kind), color.name(), glyph_size, ratio)
    painter.drawPixmap(QPointF((size - glyph_size) / 2, (size - glyph_size) / 2), glyph)
    painter.end()
    return pixmap


def category_tile(kind: str, size: int = 28, parent: QWidget | None = None) -> QLabel:
    """A rounded tile with the icon for a kind of file, tinted in that kind's color."""
    tile = QLabel(parent)
    tile.setFixedSize(size, size)
    tile.setPixmap(_tile_pixmap(kind, size, 2.0))
    tile.setAccessibleName(ui_categories.LABEL_BY_KIND.get(kind, "File"))
    return tile


# ---------------------------------------------------------------------------
# row menu
# ---------------------------------------------------------------------------

# (text, icon name or None, callback, enabled)
MenuEntry = tuple[str, "str | None", Callable[[], None], bool]


class RowMenuButton(QToolButton):
    """The "..." button at the end of a list row. Fill its menu with ``set_actions``."""

    def __init__(self, tooltip: str = "More actions", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "ghost")
        self.setIcon(ui_icons.icon("more"))
        self.setIconSize(QSize(18, 18))
        self.setFixedSize(32, 32)
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._menu = QMenu(self)
        self.setMenu(self._menu)

    def set_actions(self, actions: Sequence["MenuEntry | None"]) -> None:
        """Rebuild the menu. ``None`` adds a separator."""
        self._menu.clear()
        for entry in actions:
            if entry is None:
                self._menu.addSeparator()
                continue
            text, icon_name, callback, enabled = entry
            action = self._menu.addAction(ui_icons.icon(icon_name) if icon_name else QIcon(), text)
            action.setEnabled(bool(enabled))
            action.triggered.connect(lambda _checked=False, run=callback: run())
