"""Small reusable widgets: a step indicator, file-kind tiles, the "..." row menu,
a clickable frame and the window's message bar.

They sit on top of ui_style / ui_icons / ui_categories and use only suite_theme
colors, so every page that needs one looks the same.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Callable, Iterable, Sequence

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
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
# a row that wraps
# ---------------------------------------------------------------------------

class FlowLayout(QLayout):
    """A row of widgets that starts a second row when the first one is full.

    A plain row never gets narrower than all of its buttons side by side, and a
    window that is allowed to be narrower than that cuts the last button off.
    This one asks only for its widest widget and grows downwards instead, so
    every label stays whole. The order (and so the Tab order) never changes.

    With ``push_last`` the last widget sits at the right edge for as long as
    everything fits on one row, the way a stretch before it would place it.
    """

    def __init__(self, parent: QWidget | None = None, spacing: int = 8, push_last: bool = False) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._push_last = push_last
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)

    # -- the QLayout contract --
    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802 (Qt's name)
        self._items.append(item)
        self.invalidate()

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        if 0 <= index < len(self._items):
            item = self._items.pop(index)
            self.invalidate()
            return item
        return None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._arrange(QRect(0, 0, width, 0), move=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._arrange(rect, move=True)

    def sizeHint(self) -> QSize:  # noqa: N802
        """Everything on one row."""
        shown = self._shown()
        margins = self.contentsMargins()
        width = sum(item.sizeHint().width() for item in shown) + self.spacing() * max(0, len(shown) - 1)
        height = max((item.sizeHint().height() for item in shown), default=0)
        return QSize(width + margins.left() + margins.right(), height + margins.top() + margins.bottom())

    def minimumSize(self) -> QSize:  # noqa: N802
        """One widget per row is the narrowest this can get."""
        shown = self._shown()
        margins = self.contentsMargins()
        width = max((item.minimumSize().width() for item in shown), default=0)
        height = max((item.minimumSize().height() for item in shown), default=0)
        return QSize(width + margins.left() + margins.right(), height + margins.top() + margins.bottom())

    # -- placing --
    def _shown(self) -> list[QLayoutItem]:
        return [item for item in self._items if not item.isEmpty()]  # a hidden widget takes no room

    def _arrange(self, rect: QRect, move: bool) -> int:
        """Place the widgets (when ``move``) and return the height the rows need."""
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        gap = self.spacing()
        rows: list[list[QLayoutItem]] = [[]]
        used = 0
        for item in self._shown():
            width = item.sizeHint().width()
            if rows[-1] and used + gap + width > area.width():
                rows.append([])
                used = 0
            used += (gap if rows[-1] else 0) + width
            rows[-1].append(item)

        y = area.y()
        for row in rows:
            height = max((item.sizeHint().height() for item in row), default=0)
            if move:
                x = area.x()
                for index, item in enumerate(row):
                    size = item.sizeHint()
                    width = min(size.width(), area.width())  # never wider than the row itself
                    if self._push_last and len(rows) == 1 and len(row) > 1 and index == len(row) - 1:
                        x = max(x, area.right() + 1 - width)
                    item.setGeometry(QRect(QPoint(x, y + (height - size.height()) // 2), QSize(width, size.height())))
                    x += width + gap
            y += height + gap
        return y - gap - rect.y() + margins.bottom() if rows[0] else margins.top() + margins.bottom()


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


class WrappingCheckBox(QWidget):
    """CheckBox whose text wraps onto multiple lines inside narrow panels.

    Exposes the QCheckBox API callers use (``isChecked``, ``setChecked``,
    ``toggle``, ``click``, ``text``, ``setText``, ``setToolTip``, ``toggled``,
    ``clicked``) while letting the label wrap without widening its container.
    """

    toggled = Signal(bool)
    clicked = Signal(bool)

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._label_pressed = False
        self._self_pressed = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        # QSS gives QCheckBox spacing: 10px even with empty text, so its indicator sits at
        # x=0..18 and reserves x=18..28 before the label — matching native QCheckBox alignment.
        layout.setSpacing(0)

        self._box = QCheckBox("", self)
        self._box.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._box.toggled.connect(self._on_box_toggled)
        self._box.clicked.connect(self._on_box_clicked)

        self._label = QLabel(text, self)
        self._label.setWordWrap(True)
        self._label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self._label.installEventFilter(self)

        layout.addWidget(self._box, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self._label, 1)

        policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFocusProxy(self._box)
        if text:
            self.setAccessibleName(text)

    def _on_box_toggled(self, checked: bool) -> None:
        self.toggled.emit(checked)

    def _on_box_clicked(self, checked: bool) -> None:
        self.clicked.emit(checked)

    def isChecked(self) -> bool:
        return self._box.isChecked()

    def setChecked(self, checked: bool) -> None:
        self._box.setChecked(checked)

    def checkState(self) -> Qt.CheckState:
        return self._box.checkState()

    def setCheckState(self, state: Qt.CheckState) -> None:
        self._box.setCheckState(state)

    def toggle(self) -> None:
        self._box.toggle()

    def click(self) -> None:
        self._box.click()

    def text(self) -> str:
        return self._label.text()

    def setText(self, text: str) -> None:
        self._label.setText(text)
        self.setAccessibleName(text)
        self.updateGeometry()

    def setToolTip(self, tip: str) -> None:
        super().setToolTip(tip)
        self._box.setToolTip(tip)
        self._label.setToolTip(tip)

    def setAccessibleName(self, name: str) -> None:
        super().setAccessibleName(name)
        self._box.setAccessibleName(name)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        box_sz = self._box.sizeHint()
        label_w = max(1, width - box_sz.width())
        label_h = self._label.heightForWidth(label_w)
        if label_h < 0:
            label_h = self._label.sizeHint().height()
        return max(box_sz.height(), label_h)

    def minimumSizeHint(self) -> QSize:
        box_sz = self._box.sizeHint()
        # Keep min width narrow enough to wrap, without letting QLabel inflate min height.
        return QSize(box_sz.width() + self._label.minimumSizeHint().width(), box_sz.height())

    def sizeHint(self) -> QSize:
        box_sz = self._box.sizeHint()
        lbl_sz = self._label.sizeHint()
        return QSize(box_sz.width() + lbl_sz.width(), max(box_sz.height(), lbl_sz.height()))

    def eventFilter(self, obj: object, event: QEvent) -> bool:
        if obj is self._label:
            etype = event.type()
            if etype == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                if self.isEnabled():
                    self._label_pressed = True
                    return True
            elif etype == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                if self._label_pressed:
                    self._label_pressed = False
                    if self.isEnabled() and self._label.rect().contains(event.position().toPoint()):
                        self._box.setFocus(Qt.FocusReason.MouseFocusReason)
                        self._box.click()
                    return True
        return super().eventFilter(obj, event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self._self_pressed = True
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._self_pressed:
            self._self_pressed = False
            if self.isEnabled() and self.rect().contains(event.position().toPoint()):
                self._box.setFocus(Qt.FocusReason.MouseFocusReason)
                self._box.click()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat() and self.isEnabled():
            self._box.click()
            event.accept()
            return
        super().keyPressEvent(event)

