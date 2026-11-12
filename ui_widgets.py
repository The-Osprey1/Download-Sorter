"""Small reusable widgets: a step indicator, file-kind tiles, the "..." row menu,
a clickable frame and the window's message bar.

They sit on top of ui_style / ui_icons / ui_categories and use only suite_theme
colors, so every page that needs one looks the same.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Callable, Iterable, Sequence

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QMenu,
    QSizePolicy,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import ui_categories
import ui_icons
import ui_motion
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


@lru_cache(maxsize=64)
def _padded_pixmap(name: str, color: str | None, glyph: int, size: int, ratio: float) -> QPixmap:
    pixmap = QPixmap(int(size * ratio), int(size * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.drawPixmap(QPointF((size - glyph) / 2, (size - glyph) / 2), ui_icons.pixmap(name, color, glyph, ratio))
    painter.end()
    return pixmap


def padded_icon(name: str, color: str | None = None, glyph: int = 16, size: int = 28) -> QIcon:
    """A small icon centered in a larger transparent square.

    A table has one icon size for every cell; this keeps a 16 px glyph looking
    16 px in a column whose other icons are 28 px tiles.
    """
    return QIcon(_padded_pixmap(name, color, glyph, size, 2.0))


def category_tile_icon(kind: str, size: int = 28) -> QIcon:
    """The same tile as an icon, for list and table cells."""
    return QIcon(_tile_pixmap(kind, size, 2.0))


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
        fill_menu(self._menu, actions)


def fill_menu(menu: QMenu, actions: Sequence["MenuEntry | None"]) -> None:
    """Add ``(text, icon name or None, callback, enabled)`` entries to a menu; ``None`` is a separator."""
    for entry in actions:
        if entry is None:
            menu.addSeparator()
            continue
        text, icon_name, callback, enabled = entry
        action = menu.addAction(ui_icons.icon(icon_name) if icon_name else QIcon(), text)
        action.setEnabled(bool(enabled))
        action.triggered.connect(lambda _checked=False, run=callback: run())


# ---------------------------------------------------------------------------
# a badge at the end of a table cell
# ---------------------------------------------------------------------------

BADGE_ROLE = Qt.ItemDataRole.UserRole + 1  # a cell's badge, as ("Learned", "success")

_BADGE_COLORS = {  # kind: (text, background, border) token names, as in ui_style's role="badge"
    "success": ("success", "successSubtle", "successBorder"),
    "info": ("info", "infoSubtle", "infoBorder"),
    "warning": ("warning", "warningSubtle", "warningBorder"),
    "critical": ("critical", "criticalSubtle", "criticalBorder"),
    "neutral": ("textMuted", "surfaceRaised", "border"),
}


class BadgeDelegate(QStyledItemDelegate):
    """Draws a small badge at the right end of a table cell.

    A cell gets one by storing ``(text, kind)`` under ``BADGE_ROLE``; the cell's own
    text is shortened to leave room for it. Cells without one are drawn as usual.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._hide_text = False

    def initStyleOption(self, option, index) -> None:
        super().initStyleOption(option, index)
        if self._hide_text:
            option.text = ""

    @staticmethod
    def _badge(index) -> tuple[str, str] | None:
        data = index.data(BADGE_ROLE)
        try:
            text, kind = data
        except (TypeError, ValueError):
            return None
        return (str(text), str(kind)) if text else None

    def paint(self, painter, option, index) -> None:
        badge = self._badge(index)
        if badge is None:
            super().paint(painter, option, index)
            return
        text, kind = badge
        font = QFont(option.font)
        font.setPixelSize(11)
        font.setWeight(QFont.Weight.DemiBold)
        width = QFontMetrics(font).horizontalAdvance(text) + 14

        # The cell's background and icon over its whole width...
        self._hide_text = True
        try:
            super().paint(painter, option, index)
        finally:
            self._hide_text = False
        # ...then its text again, shortened to leave room for the badge.
        narrowed = QStyleOptionViewItem(option)
        narrowed.rect = option.rect.adjusted(0, 0, -(width + 14), 0)
        super().paint(painter, narrowed, index)

        fg, bg, border = (COLORS[name] for name in _BADGE_COLORS.get(kind, _BADGE_COLORS["neutral"]))
        box = QRectF(option.rect.right() - width - 8, option.rect.center().y() - 9 + 0.5, width, 18)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(border), 1))
        painter.setBrush(QColor(bg))
        painter.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
        painter.setPen(QColor(fg))
        painter.setFont(font)
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()


# ---------------------------------------------------------------------------
# text that shortens itself instead of making its row wider
# ---------------------------------------------------------------------------

class ElidedLabel(QLabel):
    """A label that ends in "..." when it has less room than its text needs; hover shows all of it.

    ``text()`` is what is showing and ``full_text()`` is everything. A long folder
    name or reason can't push the rest of a row (or the window) wider, because the
    label is allowed to shrink below the width of its text.
    """

    def __init__(self, text: str = "", parent: QWidget | None = None,
                 mode: Qt.TextElideMode = Qt.TextElideMode.ElideRight) -> None:
        super().__init__(parent)
        self._full = ""
        self._mode = mode
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setText(text)

    def full_text(self) -> str:
        return self._full

    def setText(self, text: str) -> None:  # noqa: N802 (Qt's name)
        self._full = str(text or "")
        self.setAccessibleName(self._full)
        self._elide()
        self.updateGeometry()

    def _chrome(self) -> int:
        """The width the style adds around the text (padding and border)."""
        return max(0, QLabel.sizeHint(self).width() - self.fontMetrics().horizontalAdvance(QLabel.text(self)))

    def _elide(self) -> None:
        metrics = self.fontMetrics()
        if self.width() <= 1:  # not laid out yet: show it whole, and shorten it once it has a size
            shown = self._full
        else:
            shown = metrics.elidedText(self._full, self._mode, max(0, self.width() - self._chrome()))
        if shown != QLabel.text(self):
            QLabel.setText(self, shown)
        self.setToolTip(self._full if shown != self._full else "")

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.fontMetrics().horizontalAdvance(self._full) + self._chrome(), QLabel.sizeHint(self).height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(self._chrome() + self.fontMetrics().horizontalAdvance("…"), QLabel.minimumSizeHint(self).height())

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._elide()

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._elide()


# ---------------------------------------------------------------------------
# a frame that acts like a button
# ---------------------------------------------------------------------------

class ClickableFrame(QFrame):
    """A frame you can click, or focus and press Enter or Space. Style it with ``role="chip"``."""

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            return
        super().keyPressEvent(event)


# ---------------------------------------------------------------------------
# the window's message bar
# ---------------------------------------------------------------------------

class NoticeBar(QWidget):
    """A message bar for a whole window: a ``ui_motion.Banner`` that takes no room while it is empty."""

    def __init__(self, parent: QWidget | None = None, margins: tuple[int, int, int, int] = (32, 16, 32, 0)) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*margins)
        layout.setSpacing(0)
        self.banner = ui_motion.Banner(self)
        layout.addWidget(self.banner)
        self.banner.installEventFilter(self)
        self.setVisible(False)

    def eventFilter(self, obj, event) -> bool:
        if obj is self.banner and event.type() in (QEvent.Type.Show, QEvent.Type.Hide):
            self.setVisible(self.banner.isVisible())
        return False

    def show_message(
        self,
        text: str,
        kind: str = "info",
        action_text: str = "",
        action: Callable[[], None] | None = None,
        timeout_ms: int = 7000,
    ) -> None:
        self.setVisible(True)  # first: a banner inside a hidden bar could never become visible
        self.banner.show_message(text, kind, action_text, action, timeout_ms)

    def dismiss(self) -> None:
        self.banner.dismiss()
