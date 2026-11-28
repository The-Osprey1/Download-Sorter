"""The one-screen Folders designer.

Left: a "Where would a file go?" tester, the folder list (drag a folder onto
another to nest it) and the things the app handles for you. Right: the selected
folder, described as one sentence you can click to change. Bottom: the files
still sitting loose in Downloads, with where each would go.

Every edit goes through :mod:`download_sorter.folder_designer`, which refuses
edits that cannot work, so the screen never offers a broken choice. Each change
shows a message with Undo. Moving files on disk is handed to the main window,
which runs it off the GUI thread and records it so it can be undone.
"""

from __future__ import annotations

import copy
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer, Signal
import shiboken6
from PySide6.QtGui import QAction, QColor, QFont, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleFactory,
    QStyleOptionViewItem,
    QTreeWidget,
    QTreeWidgetItem,
    QTreeWidgetItemIterator,
    QVBoxLayout,
    QWidget,
    QWidgetItem,
)

from download_sorter import folder_designer as fd
from download_sorter.config import Config
from download_sorter.router import FileCandidate, RouteDecision, candidate_from_path, evaluate
from download_sorter.ui.motion import crossfade_stacked_widget, fade_in, make_vector_icon
from download_sorter.ui.settings_widgets import MacToggle
from download_sorter.ui.workers import run_async

ROLE_PATH = Qt.ItemDataRole.UserRole + 1

ACCENT = "#0A84FF"
AMBER = "#FF9F0A"
MUTED = "#98989D"

PAGE_FOLDER = 0
PAGE_OTHER = 1
PAGE_DUPES = 2
PAGE_WAITING = 3
PAGE_EMPTY = 4

_STYLE = """
QFrame#FoldersSidebar { background: #161618; border-right: 1px solid #2C2C2E; }
QLabel#SideEyebrow { color: #8E8E93; font-size: 11px; font-weight: 600; letter-spacing: 0.4px; }
QLineEdit#TesterField { background: #1C1C1E; border: 1px solid #2C2C2E; border-radius: 8px; padding: 6px 10px; }
QLineEdit#TesterField:focus { border-color: #0A84FF; }
QLabel#TesterResult { color: #98989D; font-size: 12px; padding: 2px 2px; }
QTreeWidget#FolderList { background: transparent; border: none; outline: 0; font-size: 13px; }
QLabel#DropZone { color: #98989D; font-size: 12px; border: 1px dashed #3D3D41; border-radius: 8px; padding: 8px; }
QLabel#DropZone[active="true"] { color: #F5F5F7; border: 2px solid #0A84FF; background: rgba(10,132,255,0.18); }
QLabel#DragBanner { color: #F5F5F7; background: rgba(10,132,255,0.16); border-radius: 8px; padding: 6px 8px; font-size: 12px; }
QTreeWidget#FolderList::item { height: 30px; border-radius: 6px; padding-left: 2px; }
QTreeWidget#FolderList { show-decoration-selected: 0; }
QTreeWidget#FolderList::item:selected, QTreeWidget#FolderList::item:hover { background: transparent; color: #F5F5F7; }
QTreeWidget#FolderList::branch, QTreeWidget#FolderList::branch:selected,
QTreeWidget#FolderList::branch:hover { background: transparent; image: none; border-image: none; }
QPushButton#SideRow { text-align: left; background: transparent; border: none; border-radius: 6px;
    padding: 6px 8px; color: #E5E5EA; }
QPushButton#SideRow:hover { background: #232326; }
QPushButton#SideRow:checked { background: rgba(10,132,255,0.22); }
QLabel#SideRowTitle { color: #E5E5EA; font-size: 13px; font-weight: 600; background: transparent; }
QLabel#SideRowSub { color: #8E8E93; font-size: 11px; background: transparent; }
QLabel#SideBadge { color: #AEAEB2; background: #2C2C2E; border-radius: 8px; padding: 1px 7px; font-size: 11px;
    font-weight: 600; }
QPushButton#SideTool { background: transparent; border: none; border-radius: 6px; padding: 3px 8px;
    color: #0A84FF; font-weight: 600; }
QPushButton#SideTool:hover { background: #232326; }
QLineEdit#FolderTitle { background: transparent; border: 1px solid transparent; border-radius: 8px;
    font-size: 24px; font-weight: 700; padding: 2px 6px; }
QLineEdit#FolderTitle:hover { border-color: #2C2C2E; }
QLineEdit#FolderTitle:focus { border-color: #0A84FF; background: #1C1C1E; }
QLabel#Breadcrumb { color: #8E8E93; font-size: 12px; }
QLabel#NameProblem { color: #FF453A; font-size: 12px; }
QLabel#SentenceWord { font-size: 16px; color: #F5F5F7; }
QPushButton#Token { font-size: 15px; font-weight: 600; color: #0A84FF; background: rgba(10,132,255,0.12);
    border: 1px solid rgba(10,132,255,0.35); border-radius: 9px; padding: 4px 10px; }
QPushButton#Token:hover { background: rgba(10,132,255,0.22); }
QPushButton#Token[empty="true"] { color: #98989D; background: #1C1C1E; border: 1px dashed #3D3D41; }
QFrame#Card { background: #1C1C1E; border: 1px solid #2C2C2E; border-radius: 12px; }
QFrame#ClashNote { background: rgba(255,159,10,0.10); border: 1px solid rgba(255,159,10,0.40); border-radius: 10px; }
QFrame#HintNote { background: rgba(10,132,255,0.10); border: 1px solid rgba(10,132,255,0.40); border-radius: 10px; }
QLabel#CardTitle { font-size: 13px; font-weight: 600; }
QLabel#Muted { color: #98989D; font-size: 12px; }
QFrame#Tile { background: #232326; border: 1px solid #2C2C2E; border-radius: 10px; }
QLabel#TileName { font-size: 12px; }
QPushButton#Primary { background: #0A84FF; color: white; border: none; border-radius: 8px; padding: 6px 14px;
    font-weight: 600; }
QPushButton#Primary:hover { background: #3395FF; }
QPushButton#Primary:disabled { background: #2C2C2E; color: #636366; }
QPushButton#Quiet { background: #232326; border: 1px solid #2C2C2E; border-radius: 8px; padding: 5px 12px; }
QPushButton#Quiet:hover { background: #2C2C2E; }
QPushButton#Danger { background: transparent; color: #FF453A; border: 1px solid rgba(255,69,58,0.45);
    border-radius: 8px; padding: 5px 12px; }
QPushButton#Danger:hover { background: rgba(255,69,58,0.12); }
QPushButton#Disclosure { background: transparent; border: none; text-align: left; color: #98989D;
    font-weight: 600; padding: 4px 0; }
QFrame#Tray { background: #161618; border-top: 1px solid #2C2C2E; }
QListWidget#TrayList, QListWidget#PageList { background: transparent; border: none; outline: 0; }
QListWidget#TrayList::item, QListWidget#PageList::item { padding: 5px 4px; border-radius: 6px; }
QListWidget#TrayList::item:hover, QListWidget#PageList::item:hover { background: #232326; }
QListWidget#TrayList::item:selected, QListWidget#PageList::item:selected { background: rgba(10,132,255,0.22); }
"""


# ---------------------------------------------------------------------------
# Small building blocks
# ---------------------------------------------------------------------------
class FlowLayout(QLayout):
    """Lays out widgets left to right and wraps them onto new lines, like words."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 8) -> None:
        super().__init__(parent)
        self._items: list[QWidgetItem] = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:  # noqa: N802, ANN001
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):  # noqa: N802, ANN201
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):  # noqa: N802, ANN201
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _do_layout(self, rect: QRect, *, test_only: bool) -> int:
        x, y, line_h = rect.x(), rect.y(), 0
        for item in self._items:
            wid = item.widget()
            if wid is not None and wid.isHidden():
                continue
            hint = item.sizeHint()
            if x + hint.width() > rect.right() + 1 and line_h > 0:
                x = rect.x()
                y += line_h + self._spacing
                line_h = 0
            if not test_only:
                # Centre shorter items (words) on the line of taller ones (tokens).
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._spacing
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y()


def _clear_layout(layout: QLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item is None:
            break
        w = item.widget()
        if w is not None:
            w.hide()
            w.setParent(None)
            w.deleteLater()
        elif item.layout() is not None:
            _clear_layout(item.layout())


def _short_type_label(names: list[str], extra: list[str]) -> str:
    """A short pill label: whole type names first, then a count, never a wall of endings."""
    if not names and not extra:
        return "any kind of file"
    if names:
        shown = names[:2]
        rest = len(names) - len(shown) + len(extra)
    else:
        shown = extra[:3]
        rest = len(extra) - len(shown)
    label = " & ".join(shown) if names else " ".join(shown)
    return f"{label} + {rest} more" if rest else label


def _elide(text: str, limit: int = 30) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _crumb(path: str) -> str:
    parts = [p for p in fd.norm_path(path).split("\\") if p]
    return " › ".join(["Downloads", *parts])


def _quiet_button(text: str, tip: str, parent: QWidget, obj: str = "Quiet") -> QPushButton:
    btn = QPushButton(text, parent)
    btn.setObjectName(obj)
    btn.setToolTip(tip)
    btn.setAccessibleName(text or tip)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    return btn


def _card(parent: QWidget, obj: str = "Card") -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame(parent)
    frame.setObjectName(obj)
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(14, 12, 14, 12)
    lay.setSpacing(8)
    return frame, lay


_TYPE_GLYPH = {
    ".pdf": "PDF", ".doc": "DOC", ".docx": "DOC", ".xls": "XLS", ".xlsx": "XLS", ".csv": "CSV",
    ".png": "IMG", ".jpg": "IMG", ".jpeg": "IMG", ".gif": "GIF", ".webp": "IMG", ".heic": "IMG",
    ".zip": "ZIP", ".7z": "ZIP", ".rar": "ZIP", ".exe": "APP", ".msi": "APP",
    ".mp4": "VID", ".mov": "VID", ".mp3": "AUD", ".wav": "AUD",
    ".stl": "3D", ".3mf": "3D", ".step": "CAD", ".gcode": "3D",
}


class _Tile(QFrame):
    """A small file card used in "Will land here"."""

    def __init__(self, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Tile")
        self.setFixedSize(120, 74)
        self.setToolTip(name)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 6)
        lay.setSpacing(4)
        ext = Path(name).suffix.lower()
        badge = QLabel(_TYPE_GLYPH.get(ext, (ext[1:4] or "FILE").upper()), self)
        badge.setStyleSheet(
            "background: rgba(10,132,255,0.18); color: #64B5FF; border-radius: 5px; font-size: 10px;"
            "font-weight: 700; padding: 2px 6px;"
        )
        badge.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        lay.addWidget(badge, 0, Qt.AlignmentFlag.AlignLeft)
        lbl = QLabel(self)
        lbl.setObjectName("TileName")
        lbl.setText(lbl.fontMetrics().elidedText(name, Qt.TextElideMode.ElideMiddle, 102))
        lay.addWidget(lbl, 1)


# ---------------------------------------------------------------------------
# Folder list with mistake-proof drag to nest
# ---------------------------------------------------------------------------
ROLE_COUNT = Qt.ItemDataRole.UserRole + 2


class _FolderRowDelegate(QStyledItemDelegate):
    """Draws each folder row: name, how many files are inside, a grip on hover,
    and a clear outline on the folder a drag would drop into."""

    def __init__(self, tree: _FolderTree) -> None:
        super().__init__(tree)
        self._tree = tree

    def sizeHint(self, option, index) -> QSize:  # noqa: N802, ANN001
        size = super().sizeHint(option, index)
        return QSize(size.width(), max(size.height(), 30))

    def paint(self, painter, option, index) -> None:  # noqa: ANN001
        tree = self._tree
        if not shiboken6.isValid(tree):
            return
        path = str(index.data(ROLE_PATH) or "")
        is_target = bool(path) and path == tree.drop_target
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        count = int(index.data(ROLE_COUNT) or 0)
        opt = QStyleOptionViewItem(option)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        # Draw hover and selection ourselves across the whole row, then let the
        # style draw only the icon and name, so nothing paints a second bar.
        opt.state &= ~QStyle.StateFlag.State_Selected
        opt.state &= ~QStyle.StateFlag.State_MouseOver
        opt.state &= ~QStyle.StateFlag.State_HasFocus
        reserve = 0
        if count:
            reserve += opt.fontMetrics.horizontalAdvance(str(count)) + 14
        if hovered and not tree.dragging:
            reserve += 16
        if reserve:
            opt.rect = opt.rect.adjusted(0, 0, -reserve, 0)
        painter.save()
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            if selected or (hovered and not tree.dragging):
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(10, 132, 255, 56) if selected else QColor("#232326"))
                painter.drawRoundedRect(option.rect.adjusted(0, 1, -1, -1), 6, 6)
            if is_target:
                r = option.rect.adjusted(1, 1, -2, -2)
                painter.setPen(QPen(QColor(ACCENT), 2))
                painter.setBrush(QColor(10, 132, 255, 60))
                painter.drawRoundedRect(r, 6, 6)
            super().paint(painter, opt, index)
            right = option.rect.right() - 6
            if hovered and not tree.dragging:
                painter.setPen(QColor("#636366"))
                cx, cy = right - 4, option.rect.center().y()
                painter.setBrush(QColor("#636366"))
                for dx in (-2, 2):
                    for dy in (-4, 0, 4):
                        painter.drawEllipse(QPoint(cx + dx, cy + dy), 1, 1)
                right -= 16
            if count:
                text = str(count)
                w = option.fontMetrics.horizontalAdvance(text) + 10
                pill = QRect(right - w, option.rect.center().y() - 9, w, 18)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor("#2C2C2E"))
                painter.drawRoundedRect(pill, 9, 9)
                painter.setPen(QColor("#AEAEB2"))
                painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, text)
        finally:
            painter.restore()


class _FolderTree(QTreeWidget):
    move_requested = Signal(str, str)  # (folder path, new parent path or "")
    menu_requested = Signal(str, QPoint)
    drag_state_changed = Signal(bool)  # True while a folder is being dragged
    hover_target_changed = Signal(str)  # name of the folder a drop would go into, or ""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("FolderList")
        # Fusion draws a plain rounded highlight; the Windows 11 style adds an accent bar.
        fusion = QStyleFactory.create("Fusion")
        if fusion is not None:
            fusion.setParent(self)
            self.setStyle(fusion)
        self.setHeaderHidden(True)
        self.setIndentation(16)
        self.setAnimated(True)
        self.setUniformRowHeights(True)
        self.setMouseTracking(True)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(False)  # the delegate draws a clearer target outline
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context)
        self.setAccessibleName("Your folders")
        self.setItemDelegate(_FolderRowDelegate(self))
        pal = self.palette()
        for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
            pal.setColor(group, QPalette.ColorRole.Highlight, QColor(0, 0, 0, 0))
            pal.setColor(group, QPalette.ColorRole.HighlightedText, QColor("#F5F5F7"))
        self.setPalette(pal)
        self.can_drop: Callable[[str, str], bool] = lambda _src, _dst: True
        self._drag_path = ""
        self.drop_target = ""
        self.dragging = False

    def drag_path(self) -> str:
        return self._drag_path

    def drawBranches(self, painter, rect, index) -> None:  # noqa: N802, ANN001
        """Draw only a small chevron for folders that hold folders; never a highlight."""
        if not self.model().hasChildren(index):
            return
        painter.save()
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(QPen(QColor("#8E8E93"), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            cx = rect.right() - self.indentation() // 2
            cy = rect.center().y()
            if self.isExpanded(index):
                painter.drawPolyline([QPoint(cx - 4, cy - 2), QPoint(cx, cy + 2), QPoint(cx + 4, cy - 2)])
            else:
                painter.drawPolyline([QPoint(cx - 2, cy - 4), QPoint(cx + 2, cy), QPoint(cx - 2, cy + 4)])
        finally:
            painter.restore()

    def _on_context(self, pos: QPoint) -> None:
        item = self.itemAt(pos)
        if item is None:
            return
        self.setCurrentItem(item)
        self.menu_requested.emit(str(item.data(0, ROLE_PATH) or ""), self.viewport().mapToGlobal(pos))

    def startDrag(self, actions) -> None:  # noqa: N802, ANN001
        item = self.currentItem()
        self._drag_path = str(item.data(0, ROLE_PATH) or "") if item else ""
        if not self._drag_path:
            return
        self.dragging = True
        self.drag_state_changed.emit(True)
        try:
            super().startDrag(actions)
        finally:
            self.dragging = False
            self._drag_path = ""
            self._set_target("")
            self.drag_state_changed.emit(False)

    def _set_target(self, path: str) -> None:
        if path != self.drop_target:
            self.drop_target = path
            self.hover_target_changed.emit(path)
            self.viewport().update()

    def _target_for(self, event) -> str | None:  # noqa: ANN001
        pos = event.position().toPoint()
        item = self.itemAt(pos)
        if item is None:
            return ""  # empty space: top of Downloads
        rect = self.visualItemRect(item)
        # Dropping on the top or bottom edge of a row means "next to it", so the parent.
        edge = max(4, rect.height() // 5)
        if pos.y() < rect.top() + edge or pos.y() > rect.bottom() - edge:
            parent = item.parent()
            return str(parent.data(0, ROLE_PATH) or "") if parent else ""
        return str(item.data(0, ROLE_PATH) or "")

    def dragEnterEvent(self, event) -> None:  # noqa: N802, ANN001
        if event.source() is self:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802, ANN001
        self._set_target("")
        super().dragLeaveEvent(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802, ANN001
        if event.source() is not self or not self._drag_path:
            event.ignore()
            return
        target = self._target_for(event)
        if target is not None and self.can_drop(self._drag_path, target):
            self._set_target(target)
            event.acceptProposedAction()
        else:
            self._set_target("")
            event.ignore()

    def dropEvent(self, event) -> None:  # noqa: N802, ANN001
        src = self._drag_path
        target = self._target_for(event)
        # Never let the view rearrange rows itself; the model is rebuilt from settings.
        event.setDropAction(Qt.DropAction.IgnoreAction)
        event.accept()
        self._set_target("")
        if src and target is not None and self.can_drop(src, target):
            QTimer.singleShot(0, lambda s=src, t=target: self.move_requested.emit(s, t))


class _TopDropZone(QLabel):
    """Shown only while dragging: drop here to move a folder to the top of Downloads."""

    def __init__(self, tree: _FolderTree, parent: QWidget | None = None) -> None:
        super().__init__("Drop here to move it to the top of Downloads", parent)
        self.setObjectName("DropZone")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAcceptDrops(True)
        self.setProperty("active", False)
        self._tree = tree

    def _set_active(self, on: bool) -> None:
        self.setProperty("active", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, event) -> None:  # noqa: N802, ANN001
        src = self._tree.drag_path()
        if event.source() is self._tree and src and self._tree.can_drop(src, ""):
            self._set_active(True)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802, ANN001
        self._set_active(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802, ANN001
        src = self._tree.drag_path()
        event.setDropAction(Qt.DropAction.IgnoreAction)
        event.accept()
        self._set_active(False)
        if src and self._tree.can_drop(src, ""):
            QTimer.singleShot(0, lambda s=src: self._tree.move_requested.emit(s, ""))


# ---------------------------------------------------------------------------
# Dialogs
# ---------------------------------------------------------------------------
class NameDialog(QDialog):
    """Ask for a folder name and refuse bad ones as you type."""

    def __init__(
        self,
        title: str,
        check: Callable[[str], tuple[str, str]],
        *,
        initial: str = "",
        hint: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(380)
        self._check = check
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(8)
        head = QLabel(title, self)
        head.setStyleSheet("font-size: 15px; font-weight: 700;")
        lay.addWidget(head)
        if hint:
            sub = QLabel(hint, self)
            sub.setObjectName("Muted")
            sub.setWordWrap(True)
            lay.addWidget(sub)
        self.field = QLineEdit(initial, self)
        self.field.setPlaceholderText("Folder name")
        self.field.setAccessibleName("Folder name")
        self.field.selectAll()
        lay.addWidget(self.field)
        self.problem = QLabel("", self)
        self.problem.setObjectName("NameProblem")
        self.problem.setWordWrap(True)
        lay.addWidget(self.problem)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Create" if not initial else "Rename")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self.field.textChanged.connect(self._validate)
        self._validate()
        self.setStyleSheet(_STYLE)

    def _validate(self) -> None:
        name, problem = self._check(self.field.text())
        shown = problem if self.field.text().strip() else ""
        if not problem and name != self.field.text().strip():
            shown = f"Will be saved as “{name}”."
            self.problem.setStyleSheet("color: #98989D;")
        else:
            self.problem.setStyleSheet("")
        self.problem.setText(shown)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(not problem)

    def value(self) -> str:
        return self._check(self.field.text())[0]


class TidyReviewDialog(QDialog):
    """Show where every loose file will go before anything moves."""

    def __init__(self, plan: list[tuple[str, str]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Tidy Downloads")
        self.resize(520, 460)
        self.setStyleSheet(_STYLE)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(10)
        n = len(plan)
        head = QLabel(f"Tidy {n} file{'s' if n != 1 else ''}?", self)
        head.setStyleSheet("font-size: 18px; font-weight: 700;")
        lay.addWidget(head)
        sub = QLabel("Here is where each one goes. You can undo this afterwards.", self)
        sub.setObjectName("Muted")
        lay.addWidget(sub)
        groups: dict[str, list[str]] = {}
        for name, dest in plan:
            groups.setdefault(dest, []).append(name)
        self.list = QListWidget(self)
        self.list.setObjectName("PageList")
        for dest in sorted(groups, key=str.lower):
            names = groups[dest]
            head_item = QListWidgetItem(f"{_crumb(dest)}   ({len(names)})")
            f = QFont()
            f.setBold(True)
            head_item.setFont(f)
            head_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.list.addItem(head_item)
            for nm in sorted(names, key=str.lower):
                it = QListWidgetItem(f"     {nm}")
                it.setFlags(Qt.ItemFlag.ItemIsEnabled)
                self.list.addItem(it)
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = _quiet_button("Not now", "Close without moving anything", self)
        cancel.clicked.connect(self.reject)
        go = _quiet_button(f"Tidy {n} file{'s' if n != 1 else ''}", "Move these files now", self, "Primary")
        go.setEnabled(n > 0)
        go.setDefault(True)
        go.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(go)
        lay.addLayout(row)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
class _FolderPage(QWidget):
    """The selected folder as a sentence you can click to change."""

    def __init__(self, tab: FoldersTab) -> None:
        super().__init__(tab)
        self.tab = tab
        self.path = ""
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        body = QWidget(scroll)
        scroll.setWidget(body)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(28, 22, 28, 22)
        lay.setSpacing(14)

        # Header: icon, breadcrumb, editable name
        head = QHBoxLayout()
        head.setSpacing(14)
        self.icon = QLabel(body)
        self.icon.setPixmap(make_vector_icon("folder", "#64B5FF", 44).pixmap(44, 44))
        self.icon.setFixedSize(48, 48)
        head.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        self.crumb = QLabel("", body)
        self.crumb.setObjectName("Breadcrumb")
        self.crumb.setContentsMargins(8, 0, 0, 0)
        title_col.addWidget(self.crumb)
        self.title = QLineEdit("", body)
        self.title.setObjectName("FolderTitle")
        self.title.setAccessibleName("Folder name")
        self.title.setToolTip("Click to rename this folder")
        self.title.textEdited.connect(self._on_title_edited)
        self.title.editingFinished.connect(self._commit_title)
        title_col.addWidget(self.title)
        self.name_problem = QLabel("", body)
        self.name_problem.setObjectName("NameProblem")
        self.name_problem.hide()
        title_col.addWidget(self.name_problem)
        head.addLayout(title_col, 1)
        lay.addLayout(head)

        # Smart hint
        self.hint, hint_lay = _card(body, "HintNote")
        self.hint_text = QLabel("", self.hint)
        self.hint_text.setWordWrap(True)
        hint_lay.addWidget(self.hint_text)
        hint_row = QHBoxLayout()
        hint_row.addStretch(1)
        self.hint_no = _quiet_button("Not now", "Hide this suggestion", self.hint)
        self.hint_no.clicked.connect(lambda: self.tab.action_requested.emit("dismiss_suggestion", ""))
        self.hint_yes = _quiet_button("Make it automatic", "Add this as a rule", self.hint, "Primary")
        self.hint_yes.clicked.connect(lambda: self.tab.action_requested.emit("accept_suggestion", ""))
        hint_row.addWidget(self.hint_no)
        hint_row.addWidget(self.hint_yes)
        hint_lay.addLayout(hint_row)
        self.hint.hide()
        lay.addWidget(self.hint)

        # Sentence
        self.sentence_card, sc_lay = _card(body)
        eyebrow = QLabel("WHAT GOES HERE", self.sentence_card)
        eyebrow.setObjectName("SideEyebrow")
        sc_lay.addWidget(eyebrow)
        self.sentence_host = QWidget(self.sentence_card)
        self.sentence = FlowLayout(self.sentence_host, spacing=8)
        sc_lay.addWidget(self.sentence_host)
        self.sentence_note = QLabel("", self.sentence_card)
        self.sentence_note.setObjectName("Muted")
        self.sentence_note.setWordWrap(True)
        sc_lay.addWidget(self.sentence_note)
        self.also_host = QWidget(self.sentence_card)
        self.also = QVBoxLayout(self.also_host)
        self.also.setContentsMargins(0, 4, 0, 0)
        self.also.setSpacing(6)
        sc_lay.addWidget(self.also_host)
        lay.addWidget(self.sentence_card)

        # Files that belong here but sit next door, or a folder not made on disk yet
        self.stray, stray_lay = _card(body, "HintNote")
        stray_row = QHBoxLayout()
        self.stray_text = QLabel("", self.stray)
        self.stray_text.setWordWrap(True)
        stray_row.addWidget(self.stray_text, 1)
        self.stray_btn = _quiet_button("Move them in", "Create this folder in Downloads and move those files in", self.stray, "Primary")
        self.stray_btn.clicked.connect(lambda: self.tab.fill_now(self.path))
        stray_row.addWidget(self.stray_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        stray_lay.addLayout(stray_row)
        self.stray.hide()
        lay.addWidget(self.stray)

        # Clash note
        self.clash, clash_lay = _card(body, "ClashNote")
        self.clash_text = QLabel("", self.clash)
        self.clash_text.setWordWrap(True)
        clash_lay.addWidget(self.clash_text)
        self.clash.hide()
        lay.addWidget(self.clash)

        # Will land here
        self.land_card, land_lay = _card(body)
        land_head = QHBoxLayout()
        self.land_title = QLabel("Will land here", self.land_card)
        self.land_title.setObjectName("CardTitle")
        land_head.addWidget(self.land_title)
        land_head.addStretch(1)
        self.inside_label = QLabel("", self.land_card)
        self.inside_label.setObjectName("Muted")
        land_head.addWidget(self.inside_label)
        land_lay.addLayout(land_head)
        self.tiles_host = QWidget(self.land_card)
        self.tiles = FlowLayout(self.tiles_host, spacing=8)
        land_lay.addWidget(self.tiles_host)
        self.land_empty = QLabel("Nothing in Downloads matches yet.", self.land_card)
        self.land_empty.setObjectName("Muted")
        land_lay.addWidget(self.land_empty)
        lay.addWidget(self.land_card)

        # Subfolders
        self.sub_card, sub_lay = _card(body)
        sub_title = QLabel("Folders inside", self.sub_card)
        sub_title.setObjectName("CardTitle")
        sub_lay.addWidget(sub_title)
        self.sub_host = QWidget(self.sub_card)
        self.subs = FlowLayout(self.sub_host, spacing=8)
        sub_lay.addWidget(self.sub_host)
        lay.addWidget(self.sub_card)

        # More options
        self.more_btn = _quiet_button("▸  More options", "Show more options for this folder", body, "Disclosure")
        self.more_btn.clicked.connect(self._toggle_more)
        lay.addWidget(self.more_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.more, more_lay = _card(body)
        auto_row = QHBoxLayout()
        auto_lbl = QLabel("Sort files here automatically", self.more)
        auto_row.addWidget(auto_lbl, 1)
        self.auto_toggle = MacToggle("", self.more)
        self.auto_toggle.setAccessibleName("Sort files here automatically")
        self.auto_toggle.setToolTip("Off: only files you put here yourself")
        self.auto_toggle.clicked.connect(self._on_auto_toggled)
        auto_row.addWidget(self.auto_toggle)
        more_lay.addLayout(auto_row)
        self.skip_label = QLabel("", self.more)
        self.skip_label.setObjectName("Muted")
        self.skip_label.setWordWrap(True)
        more_lay.addWidget(self.skip_label)
        self.more_buttons_host = QWidget(self.more)
        self.more_buttons = FlowLayout(self.more_buttons_host, spacing=8)
        more_lay.addWidget(self.more_buttons_host)
        btn_skip = _quiet_button("Skip files named…", "Never put files with these words here", self.more)
        btn_skip.clicked.connect(self._edit_skip_words)
        btn_adv = _quiet_button("Advanced rule…", "Add a rule with PDF words, renaming, size or unzip", self.more)
        btn_adv.clicked.connect(lambda: self.tab.action_requested.emit("folders:new_advanced_rule", self.path))
        btn_show = _quiet_button("Show in File Explorer", "Open this folder", self.more)
        btn_show.clicked.connect(lambda: self.tab.action_requested.emit("tree:open_explorer", self.path))
        btn_proj = _quiet_button("Find projects inside", "Group project files that belong together", self.more)
        btn_proj.clicked.connect(lambda: self.tab.action_requested.emit("detect_projects", self.path))
        btn_rm = _quiet_button("Remove folder…", "Remove this folder. Files inside are kept", self.more, "Danger")
        btn_rm.clicked.connect(lambda: self.tab.remove_folder(self.path))
        for b in (btn_skip, btn_adv, btn_show, btn_proj, btn_rm):
            self.more_buttons.addWidget(b)
        self.more.hide()
        lay.addWidget(self.more)
        lay.addStretch(1)

    # -- header -------------------------------------------------------------
    def _parent_path(self) -> str:
        parts = fd.norm_path(self.path).split("\\")
        return "\\".join(parts[:-1])

    def _on_title_edited(self, text: str) -> None:
        arch = fd.working_architecture(self.tab.config())
        _name, problem = fd.check_folder_name(arch, text, self._parent_path(), ignore_path=self.path)
        self.name_problem.setText(problem)
        self.name_problem.setVisible(bool(problem))

    def _commit_title(self) -> None:
        if not self.path:
            return
        leaf = fd.norm_path(self.path).split("\\")[-1]
        text = self.title.text()
        if fd.clean_name(text) == leaf:
            self.title.setText(leaf)
            self.name_problem.hide()
            return
        arch = fd.working_architecture(self.tab.config())
        _name, problem = fd.check_folder_name(arch, text, self._parent_path(), ignore_path=self.path)
        if problem:
            # Put the old name back; the message says why.
            self.title.setText(leaf)
            QTimer.singleShot(2600, self.name_problem.hide)
            return
        self.tab.apply(lambda cfg: fd.rename_folder(cfg, self.path, text))

    # -- sentence -----------------------------------------------------------
    def _token(self, text: str, tip: str, menu_fn: Callable[[QMenu], None], *, empty: bool = False) -> QPushButton:
        btn = QPushButton(_elide(text, 34), self.sentence_host)
        btn.setObjectName("Token")
        btn.setProperty("empty", "true" if empty else "false")
        btn.setToolTip(tip if len(text) <= 34 else f"{text}\n\n{tip}")
        btn.setAccessibleName(text)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)

        def _open() -> None:
            menu = QMenu(btn)
            menu_fn(menu)
            menu.exec(btn.mapToGlobal(btn.rect().bottomLeft() + QPoint(0, 4)))

        btn.clicked.connect(_open)
        return btn

    def _word(self, text: str) -> QLabel:
        lbl = QLabel(text, self.sentence_host)
        lbl.setObjectName("SentenceWord")
        lbl.setMinimumHeight(32)
        return lbl

    def _build_sentence(self, cues: fd.FolderCues) -> None:
        _clear_layout(self.sentence)
        if cues.is_empty:
            has_extra = bool(fd.extra_rules_for(self.tab.config(), self.path))
            self.sentence.addWidget(
                self._word("Nothing else is sorted here." if has_extra else "Only files you put here yourself.")
            )
            start = self._token(
                "Sort files here automatically…",
                "Pick file types or words that should land here",
                self._types_menu,
                empty=True,
            )
            self.sentence.addWidget(start)
            self.sentence_note.setText("Nothing is moved here on its own. Drag files in, or pick what should land here.")
            return
        names, extra = fd.type_names_for(cues.extensions)
        type_text = _short_type_label(names, extra)
        type_full = " & ".join(names + extra) if (names or extra) else ""
        name_text = (
            "named " + " or ".join(f"“{k}”" for k in cues.keywords) if cues.keywords else "with any name"
        )
        from_text = "from " + " or ".join(cues.domains) if cues.domains else "from anywhere"
        self.sentence.addWidget(self._word("Put"))
        self.sentence.addWidget(
            self._token(
                type_text,
                f"{type_full}\n\nChoose which kinds of files go here" if type_full != type_text else "Choose which kinds of files go here",
                self._types_menu,
                empty=not cues.extensions,
            )
        )
        self.sentence.addWidget(
            self._token(name_text, "Only files with these words in the name", self._words_menu, empty=not cues.keywords)
        )
        self.sentence.addWidget(
            self._token(from_text, "Only files downloaded from these websites", self._sites_menu, empty=not cues.domains)
        )
        self.sentence.addWidget(self._word("in this folder."))
        if cues.extensions and (cues.keywords or cues.domains):
            joined = " or ".join(
                x for x in ("the name matches" if cues.keywords else "", "the website matches" if cues.domains else "") if x
            )
            self.sentence_note.setText(f"A file needs to be one of those types and {joined}.")
        elif cues.keywords and cues.domains:
            self.sentence_note.setText("A file lands here when its name or its website matches.")
        else:
            self.sentence_note.setText("Click any pill to change it.")

    def _cues(self) -> fd.FolderCues:
        return fd.folder_cues(self.tab.config(), self.path)

    def _set(self, cues: fd.FolderCues) -> None:
        self.tab.apply(lambda cfg: fd.set_cues(cfg, self.path, cues))

    def _types_menu(self, menu: QMenu) -> None:
        cues = self._cues()
        have = {e.lower() for e in cues.extensions}
        for name, exts in fd.TYPE_CHOICES:
            act = QAction(name, menu)
            act.setCheckable(True)
            act.setChecked(bool(exts) and set(exts) <= have)
            act.setToolTip(", ".join(exts))

            def _toggle(checked: bool, e=exts) -> None:
                c = self._cues()
                if checked:
                    c.extensions = [*c.extensions, *[x for x in e if x not in c.extensions]]
                else:
                    c.extensions = [x for x in c.extensions if x not in e]
                self._set(c)

            act.toggled.connect(_toggle)
            menu.addAction(act)
        _names, extra = fd.type_names_for(cues.extensions)
        if extra:
            menu.addSeparator()
            for ext in extra:
                act = QAction(ext, menu)
                act.setCheckable(True)
                act.setChecked(True)
                act.toggled.connect(
                    lambda checked, x=ext: (not checked) and self._set(
                        fd.FolderCues([e for e in self._cues().extensions if e != x], *self._rest())
                    )
                )
                menu.addAction(act)
        menu.addSeparator()
        menu.addAction("Add a file ending…").triggered.connect(self._add_extension)
        if not cues.keywords and not cues.domains:
            menu.addAction("Add a word in the name…").triggered.connect(self._add_word)
        if cues.extensions:
            menu.addAction("Any kind of file").triggered.connect(
                lambda: self._set(fd.FolderCues([], *self._rest()))
            )

    def _rest(self) -> tuple[list[str], list[str], list[str]]:
        c = self._cues()
        return c.keywords, c.domains, c.skip_words

    def _words_menu(self, menu: QMenu) -> None:
        cues = self._cues()
        for word in cues.keywords:
            act = QAction(f"“{word}”", menu)
            act.setCheckable(True)
            act.setChecked(True)
            act.toggled.connect(lambda checked, w=word: (not checked) and self._remove_word(w))
            menu.addAction(act)
        if cues.keywords:
            menu.addSeparator()
        menu.addAction("Add a word…").triggered.connect(self._add_word)
        if cues.keywords:
            menu.addAction("Any name").triggered.connect(self._clear_words)

    def _sites_menu(self, menu: QMenu) -> None:
        cues = self._cues()
        for site in cues.domains:
            act = QAction(site, menu)
            act.setCheckable(True)
            act.setChecked(True)
            act.toggled.connect(lambda checked, s=site: (not checked) and self._remove_site(s))
            menu.addAction(act)
        if cues.domains:
            menu.addSeparator()
        menu.addAction("Add a website…").triggered.connect(self._add_site)
        if cues.domains:
            menu.addAction("Any website").triggered.connect(self._clear_sites)

    def _ask(self, title: str, label: str) -> str:
        text, ok = QInputDialog.getText(self, title, label)
        return text.strip() if ok else ""

    def _add_extension(self) -> None:
        raw = self._ask("Add a file ending", "File ending, like stl or .psd:")
        if not raw:
            return
        exts = [f".{p.strip().lstrip('*').lstrip('.').lower()}" for p in raw.replace(";", ",").split(",") if p.strip(" .*")]
        c = self._cues()
        c.extensions = [*c.extensions, *[e for e in exts if e not in c.extensions]]
        self._set(c)

    def _add_word(self) -> None:
        raw = self._ask("Add a word", "Files with this word in their name land here:")
        if not raw:
            return
        c = self._cues()
        c.keywords = [*c.keywords, *[w.strip() for w in raw.split(",") if w.strip()]]
        self._set(c)

    def _remove_word(self, word: str) -> None:
        c = self._cues()
        c.keywords = [w for w in c.keywords if w != word]
        self._set(c)

    def _clear_words(self) -> None:
        c = self._cues()
        c.keywords = []
        self._set(c)

    def _add_site(self) -> None:
        raw = self._ask("Add a website", "Files downloaded from this website land here (like irs.gov):")
        if not raw:
            return
        from download_sorter.ui.flow.folder_builder import normalize_domain_tokens

        c = self._cues()
        c.domains = [*c.domains, *[d for d in normalize_domain_tokens(raw.split(",")) if d not in c.domains]]
        self._set(c)

    def _remove_site(self, site: str) -> None:
        c = self._cues()
        c.domains = [d for d in c.domains if d != site]
        self._set(c)

    def _clear_sites(self) -> None:
        c = self._cues()
        c.domains = []
        self._set(c)

    def _edit_skip_words(self) -> None:
        c = self._cues()
        text, ok = QInputDialog.getText(
            self,
            "Skip files named…",
            "Files with any of these words never land here (separate with commas):",
            text=", ".join(c.skip_words),
        )
        if ok:
            c.skip_words = [w.strip() for w in text.split(",") if w.strip()]
            self._set(c)

    def _on_auto_toggled(self, checked: bool) -> None:
        if checked:
            self.auto_toggle.setChecked(False)
            self._types_menu_at_toggle()
            return
        self._set(fd.FolderCues())

    def _types_menu_at_toggle(self) -> None:
        menu = QMenu(self.auto_toggle)
        self._types_menu(menu)
        menu.exec(self.auto_toggle.mapToGlobal(self.auto_toggle.rect().bottomLeft()))

    def _toggle_more(self) -> None:
        show = not self.more.isVisible()
        self.more_btn.setText(("▾" if show else "▸") + "  More options")
        if show:
            fade_in(self.more, 160)
        else:
            self.more.hide()

    def _build_also(self, cfg: Config, arch) -> None:  # noqa: ANN001
        _clear_layout(self.also)
        extra = fd.extra_rules_for(cfg, self.path, arch)
        for rule in extra:
            row = QHBoxLayout()
            row.setSpacing(8)
            text = "Also: " + fd.describe_cues(fd.rule_cues(rule))
            if not fd.is_plain_rule(rule):
                text += "  (advanced rule)"
            if not rule.enabled:
                text += "  (turned off)"
            lbl = QLabel(_elide(text, 90), self.also_host)
            lbl.setToolTip(f"{rule.name}: {text}")
            lbl.setWordWrap(True)
            row.addWidget(lbl, 1)
            edit = _quiet_button("Edit", f"Edit the rule “{rule.name}”", self.also_host)
            edit.clicked.connect(lambda _=False, rid=rule.id: self.tab.action_requested.emit("open_full_rule_editor", rid))
            row.addWidget(edit)
            rm = _quiet_button("Remove", f"Stop using the rule “{rule.name}”", self.also_host)
            rm.clicked.connect(lambda _=False, rid=rule.id: self.tab.apply(lambda c, r=rid: fd.remove_rule(c, r)))
            row.addWidget(rm)
            holder = QWidget(self.also_host)
            holder.setLayout(row)
            self.also.addWidget(holder)
        self.also_host.setVisible(bool(extra))

    # -- refresh ------------------------------------------------------------
    def show_folder(self, path: str) -> None:
        cfg = self.tab.config()
        arch = fd.working_architecture(cfg)
        node = fd.find_node(arch, path)
        if node is None:
            return
        changed = not fd.norm_path(path).lower() == fd.norm_path(self.path).lower()
        self.path = fd.path_of(arch, node)
        self.crumb.setText(_crumb("\\".join(self.path.split("\\")[:-1])))
        if not self.title.hasFocus() or changed:
            self.title.setText(node.name)
            self.name_problem.hide()
        cues = fd.folder_cues(cfg, self.path, arch)
        self._build_sentence(cues)
        self.auto_toggle.setChecked(not cues.is_empty)
        self.skip_label.setText(
            "Never here: " + ", ".join(f"“{w}”" for w in cues.skip_words) if cues.skip_words else ""
        )
        self.skip_label.setVisible(bool(cues.skip_words))
        self._build_also(cfg, arch)

        # Subfolders
        _clear_layout(self.subs)
        for child in arch.children_of(node.id):
            b = _quiet_button(child.name, f"Open {child.name}", self.sub_host)
            b.setIcon(make_vector_icon("folder", "#64B5FF", 14))
            b.clicked.connect(lambda _=False, p=fd.path_of(arch, child): self.tab.select_path(p))
            self.subs.addWidget(b)
        if node.tier < arch.MAX_TIERS:
            add = _quiet_button("+ New folder inside", f"Make a folder inside {node.name}", self.sub_host)
            add.clicked.connect(lambda: self.tab.new_folder(self.path))
            self.subs.addWidget(add)

        self.refresh_preview()
        if changed:
            fade_in(self.sentence_card, 180)

    def refresh_preview(self) -> None:
        if not self.path:
            return
        landing, clash = self.tab.preview_for(self.path)
        _clear_layout(self.tiles)
        for name in landing[:12]:
            self.tiles.addWidget(_Tile(name, self.tiles_host))
        more = len(landing) - 12
        if more > 0:
            lbl = QLabel(f"+{more} more", self.tiles_host)
            lbl.setObjectName("Muted")
            lbl.setMinimumHeight(74)
            self.tiles.addWidget(lbl)
        n = len(landing)
        self.land_title.setText(f"Will land here  ·  {n}" if n else "Will land here")
        self.land_empty.setVisible(n == 0)
        self.tiles_host.setVisible(n > 0)
        inside = self.tab.files_inside(self.path)
        self.inside_label.setText(f"{inside} file{'s' if inside != 1 else ''} already inside" if inside else "")
        if clash:
            winners = sorted(set(clash.values()), key=str.lower)
            n_c = len(clash)
            where = " and ".join(f"“{w.split(chr(92))[-1]}”" for w in winners[:2])
            self.clash_text.setText(
                f"{n_c} file{'s' if n_c != 1 else ''} that fit here go to {where} instead, "
                "because that folder is more specific. The more specific folder always wins."
            )
            self.clash.show()
        else:
            self.clash.hide()

    def set_strays(self, strays: int, missing: bool) -> None:
        parent = self.path.split("\\")[-2] if "\\" in self.path else "Downloads"
        if strays:
            self.stray_text.setText(
                f"{strays} file{'s' if strays != 1 else ''} that belong here {'are' if strays != 1 else 'is'} "
                f"sitting loose in {parent}."
            )
            self.stray_btn.setText("Move them in" if strays != 1 else "Move it in")
            self.stray.show()
        elif missing:
            self.stray_text.setText("This folder isn't in Downloads yet. It's made when the first file arrives.")
            self.stray_btn.setText("Create it now")
            self.stray.show()
        else:
            self.stray.hide()

    def set_hint(self, suggestion: dict | None) -> None:
        if suggestion:
            self.hint_text.setText(f"{suggestion.get('summary', '')}. Make that automatic?")
            self.hint.show()
        else:
            self.hint.hide()


class _OtherPage(QWidget):
    """Everything else: files no folder asked for."""

    def __init__(self, tab: FoldersTab) -> None:
        super().__init__(tab)
        self.tab = tab
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 22, 28, 22)
        lay.setSpacing(14)
        title = QLabel("Everything else", self)
        title.setStyleSheet("font-size: 24px; font-weight: 700;")
        lay.addWidget(title)
        sub = QLabel("Files that none of your folders asked for.", self)
        sub.setObjectName("Muted")
        lay.addWidget(sub)
        card, cl = _card(self)
        self.r_other = QRadioButton("Put them in an “Other” folder", card)
        self.r_leave = QRadioButton("Leave them in Downloads", card)
        self.group = QButtonGroup(self)
        self.group.addButton(self.r_other)
        self.group.addButton(self.r_leave)
        cl.addWidget(self.r_other)
        cl.addWidget(self.r_leave)
        self.r_other.toggled.connect(lambda on: on and self._set_mode("other"))
        self.r_leave.toggled.connect(lambda on: on and self._set_mode("leave"))
        guess_row = QHBoxLayout()
        guess_lbl = QLabel("Guess from where you moved similar files before", card)
        guess_lbl.setWordWrap(True)
        guess_row.addWidget(guess_lbl, 1)
        self.guess = MacToggle("", card)
        self.guess.setAccessibleName("Guess from where you moved similar files before")
        self.guess.setToolTip("Uses only what you did on this computer")
        self.guess.clicked.connect(self._set_guess)
        guess_row.addWidget(self.guess)
        cl.addLayout(guess_row)
        lay.addWidget(card)
        self.count = QLabel("", self)
        self.count.setObjectName("Muted")
        lay.addWidget(self.count)
        lay.addStretch(1)

    def refresh(self) -> None:
        cfg = self.tab.config()
        for b in (self.r_other, self.r_leave, self.guess):
            b.blockSignals(True)
        self.r_other.setChecked(cfg.fallback_mode == "other")
        self.r_leave.setChecked(cfg.fallback_mode == "leave")
        self.guess.setChecked(bool(cfg.ai_auto_route_fallback))
        for b in (self.r_other, self.r_leave, self.guess):
            b.blockSignals(False)
        n = self.tab.fallback_count()
        self.count.setText(f"{n} file{'s' if n != 1 else ''} in Downloads would go here right now." if n else "")

    def _set_mode(self, mode: str) -> None:
        cfg = copy.deepcopy(self.tab.config())
        if cfg.fallback_mode == mode:
            return
        cfg.fallback_mode = mode
        self.tab.commit(cfg, "Everything else: " + ("put in “Other”" if mode == "other" else "left in Downloads"))

    def _set_guess(self, on: bool) -> None:
        cfg = copy.deepcopy(self.tab.config())
        cfg.ai_auto_route_fallback = bool(on)
        self.tab.commit(cfg, "Guessing is on" if on else "Guessing is off")


class _ListPage(QWidget):
    """Shared layout for Duplicates and Waiting."""

    def __init__(self, tab: FoldersTab, title: str, subtitle: str) -> None:
        super().__init__(tab)
        self.tab = tab
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 22, 28, 22)
        lay.setSpacing(10)
        t = QLabel(title, self)
        t.setStyleSheet("font-size: 24px; font-weight: 700;")
        lay.addWidget(t)
        self.sub = QLabel(subtitle, self)
        self.sub.setObjectName("Muted")
        self.sub.setWordWrap(True)
        lay.addWidget(self.sub)
        self.list = QListWidget(self)
        self.list.setObjectName("PageList")
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        lay.addWidget(self.list, 1)
        self.empty = QLabel("Nothing here. Nice and tidy.", self)
        self.empty.setObjectName("Muted")
        lay.addWidget(self.empty)
        self.actions_host = QWidget(self)
        self.actions = FlowLayout(self.actions_host, spacing=8)
        lay.addWidget(self.actions_host)


class _DuplicatesPage(_ListPage):
    def __init__(self, tab: FoldersTab) -> None:
        super().__init__(tab, "Duplicates", "Files you downloaded twice. The second copy waits here until you choose.")
        self.items: list[dict[str, Any]] = []
        self.b_both = _quiet_button("Keep both", "Put the copy next to the original", self.actions_host)
        self.b_new = _quiet_button("Keep the new one", "Send the older copy to the Recycle Bin", self.actions_host)
        self.b_old = _quiet_button("Keep the old one", "Send the new copy to the Recycle Bin", self.actions_host)
        for b in (self.b_both, self.b_new, self.b_old):
            self.actions.addWidget(b)
        self.b_both.clicked.connect(lambda: self._resolve("both"))
        self.b_new.clicked.connect(lambda: self._resolve("new"))
        self.b_old.clicked.connect(lambda: self._resolve("old"))
        self.list.currentRowChanged.connect(lambda _r: self._sync())

    def load(self, items: list[dict[str, Any]]) -> None:
        self.items = list(items)
        self.list.clear()
        for d in self.items:
            where = str(d.get("existing_rel_path") or d.get("existing_path") or "")
            it = QListWidgetItem(f"{d.get('name', '')}\n      Same as {where}")
            self.list.addItem(it)
        self.empty.setVisible(not self.items)
        self.list.setVisible(bool(self.items))
        if self.items:
            self.list.setCurrentRow(0)
        self._sync()

    def _sync(self) -> None:
        ok = 0 <= self.list.currentRow() < len(self.items)
        self.actions_host.setVisible(bool(self.items))
        for b in (self.b_both, self.b_new, self.b_old):
            b.setEnabled(ok)

    def _resolve(self, how: str) -> None:
        row = self.list.currentRow()
        if not (0 <= row < len(self.items)):
            return
        dup = self.items[row]
        svc = self.tab.service
        if how != "both":
            gone = "older copy" if how == "new" else "new copy"
            ans = QMessageBox.question(self, "Recycle a copy", f"Send the {gone} of “{dup.get('name')}” to the Recycle Bin?")
            if ans != QMessageBox.StandardButton.Yes:
                return
        fn = {
            "both": svc.keep_both_duplicate,
            "new": svc.replace_duplicate_with_new,
            "old": svc.delete_duplicate_and_locate_old,
        }[how]
        self.tab.run_action(
            lambda _report: fn(dup["path"], dup["existing_path"]),
            lambda _res: self.tab.reload_handled(),
            "Sorting out the duplicate…",
        )


class _WaitingPage(_ListPage):
    def __init__(self, tab: FoldersTab) -> None:
        super().__init__(
            tab,
            "Waiting",
            "3D print files wait in Downloads for a few days so your slicer can still find them.",
        )
        self.paths: list[Path] = []
        row = QHBoxLayout()
        lbl = QLabel("Hold 3D print files", self)
        row.addWidget(lbl, 1)
        self.hold = MacToggle("", self)
        self.hold.setAccessibleName("Hold 3D print files")
        self.hold.setToolTip("Keep new .3mf files in Downloads for a few days before sorting")
        self.hold.clicked.connect(self._set_hold)
        row.addWidget(self.hold)
        self.layout().insertLayout(2, row)
        self.b_now = _quiet_button("File them now", "Sort the waiting files right away", self.actions_host, "Primary")
        self.b_now.clicked.connect(self._file_now)
        self.actions.addWidget(self.b_now)

    def load(self, paths: list[Path]) -> None:
        cfg = self.tab.config()
        self.hold.blockSignals(True)
        self.hold.setChecked(bool(cfg.hold_3mf_enabled))
        self.hold.blockSignals(False)
        self.sub.setText(
            f"3D print files wait in Downloads for {cfg.hold_3mf_days} days so your slicer can still find them."
        )
        self.paths = list(paths)
        self.list.clear()
        for p in self.paths:
            self.list.addItem(QListWidgetItem(p.name))
        self.empty.setVisible(not self.paths)
        self.list.setVisible(bool(self.paths))
        self.actions_host.setVisible(bool(self.paths))

    def _set_hold(self, on: bool) -> None:
        cfg = copy.deepcopy(self.tab.config())
        cfg.hold_3mf_enabled = bool(on)
        self.tab.commit(cfg, "3D print files will wait" if on else "3D print files are sorted right away")

    def _file_now(self) -> None:
        svc = self.tab.service
        self.tab.run_action(
            lambda report: svc.file_held_3mf_now(on_progress=report),
            lambda _res: self.tab.reload_handled(),
            "Filing waiting files…",
        )


class _EmptyPage(QWidget):
    def __init__(self, tab: FoldersTab) -> None:
        super().__init__(tab)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(40, 40, 40, 40)
        lay.addStretch(1)
        icon = QLabel(self)
        icon.setPixmap(make_vector_icon("folder", "#3A3A3C", 64).pixmap(64, 64))
        lay.addWidget(icon, 0, Qt.AlignmentFlag.AlignHCenter)
        self.text = QLabel("Pick a folder on the left to see what goes in it.", self)
        self.text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text.setWordWrap(True)
        self.text.setStyleSheet("font-size: 15px; color: #98989D;")
        lay.addWidget(self.text)
        row = QHBoxLayout()
        row.addStretch(1)
        self.b_start = _quiet_button(
            "Use my existing folders", "Add the folders already in your Downloads to this list", self, "Primary"
        )
        self.b_start.clicked.connect(lambda: tab.action_requested.emit("tree_tool:scan_folders", ""))
        self.b_new = _quiet_button("New folder", "Make an empty folder", self)
        self.b_new.clicked.connect(lambda: tab.new_folder(""))
        row.addWidget(self.b_new)
        row.addWidget(self.b_start)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addStretch(2)

    def set_has_folders(self, has: bool) -> None:
        self.text.setText(
            "Pick a folder on the left to see what goes in it."
            if has
            else "You have no folders yet. Make one, or use the folders already in your Downloads."
        )
        self.b_start.setVisible(not has)


# ---------------------------------------------------------------------------
# The tab
# ---------------------------------------------------------------------------
class FoldersTab(QWidget):
    """The Folders screen. See the module docstring."""

    config_committed = Signal(object)  # Config
    action_requested = Signal(str, str)
    teach_move_requested = Signal(Path, str)
    disk_change_requested = Signal(object)  # folder_designer.Change
    notice = Signal(str, object)  # (message, undo callable or None)
    tidy_requested = Signal()
    _scan_ready = Signal(object)

    def __init__(
        self,
        service,  # noqa: ANN001
        parent: QWidget | None = None,
        *,
        run_service_action: Callable[..., None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self._run_service_action = run_service_action
        self._loose: list[FileCandidate] = []
        self._inside_counts: dict[str, int] = {}
        self._strays: dict[str, int] = {}
        self._missing: set[str] = set()
        self._scanned = False
        self._decisions: list[tuple[FileCandidate, RouteDecision]] = []
        self._decisions_for: int = -1
        self._dupes: list[dict[str, Any]] = []
        self._waiting: list[Path] = []
        self._selected = ""
        self._page = PAGE_EMPTY
        self._scan_running = False
        self._scan_again = False
        self._suggestion: dict | None = None
        self.last_undo: Callable[[], None] | None = None
        self.setStyleSheet(_STYLE)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        split = QSplitter(Qt.Orientation.Horizontal, self)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(1)
        root.addWidget(split, 1)

        # Sidebar ---------------------------------------------------------
        side = QFrame(split)
        side.setObjectName("FoldersSidebar")
        side.setMinimumWidth(220)
        side.setMaximumWidth(380)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(12, 12, 12, 10)
        sl.setSpacing(6)
        eb = QLabel("WHERE WOULD A FILE GO?", side)
        eb.setObjectName("SideEyebrow")
        sl.addWidget(eb)
        self.tester = QLineEdit(side)
        self.tester.setObjectName("TesterField")
        self.tester.setPlaceholderText("Type a file name, like invoice.pdf")
        self.tester.setAccessibleName("Where would a file go?")
        self.tester.setClearButtonEnabled(True)
        self.tester.textChanged.connect(self._on_tester)
        sl.addWidget(self.tester)
        self.tester_result = QPushButton("", side)
        self.tester_result.setObjectName("SideRow")
        self.tester_result.setToolTip("Open this folder")
        self.tester_result.setAccessibleName("Tester result")
        self.tester_result.clicked.connect(self._open_tester_folder)
        self.tester_result.hide()
        self.tester_reason = QLabel("", side)
        self.tester_reason.setObjectName("TesterResult")
        self.tester_reason.setWordWrap(True)
        self.tester_reason.hide()
        sl.addWidget(self.tester_result)
        sl.addWidget(self.tester_reason)
        self._tester_dest = ""

        head = QHBoxLayout()
        head.setContentsMargins(0, 8, 0, 0)
        yf = QLabel("YOUR FOLDERS", side)
        yf.setObjectName("SideEyebrow")
        head.addWidget(yf, 1)
        self.btn_new = _quiet_button("+", "New folder", side, "SideTool")
        self.btn_new.setAccessibleName("New folder")
        self.btn_new.clicked.connect(self._open_new_menu)
        head.addWidget(self.btn_new)
        sl.addLayout(head)

        self.tree = _FolderTree(side)
        self.tree.can_drop = self._can_drop
        self.tree.move_requested.connect(self.move_folder)
        self.tree.menu_requested.connect(self._open_folder_menu)
        self.tree.currentItemChanged.connect(self._on_tree_current)
        self.tree.drag_state_changed.connect(self._on_drag_state)
        self.tree.hover_target_changed.connect(self._on_drag_target)
        sl.addWidget(self.tree, 1)
        self.drag_banner = QLabel("", side)
        self.drag_banner.setObjectName("DragBanner")
        self.drag_banner.setWordWrap(True)
        self.drag_banner.hide()
        sl.addWidget(self.drag_banner)
        self.drop_top = _TopDropZone(self.tree, side)
        self.drop_top.hide()
        sl.addWidget(self.drop_top)
        self.drag_tip = QLabel("Tip: drag a folder onto another to put it inside. Right-click for more.", side)
        self.drag_tip.setObjectName("TesterResult")
        self.drag_tip.setWordWrap(True)
        sl.addWidget(self.drag_tip)

        hf = QLabel("HANDLED AUTOMATICALLY", side)
        hf.setObjectName("SideEyebrow")
        hf.setToolTip("The app looks after these on its own. You don't need to set anything up.")
        sl.addWidget(hf)
        self.row_other = self._side_row(
            "Everything else",
            "Into a folder for its type",
            "Files no folder asks for go into a folder for their type, like Documents or Images.",
            PAGE_OTHER,
            side,
        )
        self.row_dupes = self._side_row(
            "Duplicates",
            "Second copies, never deleted",
            "Second copies of a file are set aside in the Duplicates folder. Nothing is deleted.",
            PAGE_DUPES,
            side,
        )
        self.row_wait = self._side_row(
            "Waiting",
            "3D print files, held a few days",
            "3D print files wait in Downloads a few days so your slicer can find them, then move.",
            PAGE_WAITING,
            side,
        )
        for r in (self.row_other, self.row_dupes, self.row_wait):
            sl.addWidget(r)
        split.addWidget(side)

        # Detail + tray ---------------------------------------------------
        right = QWidget(split)
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        self.stack = QStackedWidget(right)
        self.page_folder = _FolderPage(self)
        self.page_other = _OtherPage(self)
        self.page_dupes = _DuplicatesPage(self)
        self.page_wait = _WaitingPage(self)
        self.page_empty = _EmptyPage(self)
        for p in (self.page_folder, self.page_other, self.page_dupes, self.page_wait, self.page_empty):
            self.stack.addWidget(p)
        self.stack.setCurrentIndex(PAGE_EMPTY)
        rl.addWidget(self.stack, 1)

        self.tray = QFrame(right)
        self.tray.setObjectName("Tray")
        tl = QVBoxLayout(self.tray)
        tl.setContentsMargins(16, 6, 16, 8)
        tl.setSpacing(4)
        th = QHBoxLayout()
        self.tray_toggle = _quiet_button("▸  Still in Downloads", "Show the files still loose in Downloads", self.tray, "Disclosure")
        self.tray_toggle.clicked.connect(self._toggle_tray)
        th.addWidget(self.tray_toggle)
        th.addStretch(1)
        self.btn_tidy = _quiet_button("Tidy", "See where each file goes, then move them", self.tray, "Primary")
        self.btn_tidy.clicked.connect(self.review_and_tidy)
        th.addWidget(self.btn_tidy)
        tl.addLayout(th)
        self.tray_list = QListWidget(self.tray)
        self.tray_list.setObjectName("TrayList")
        self.tray_list.setMaximumHeight(150)
        self.tray_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tray_list.customContextMenuRequested.connect(self._open_tray_menu)
        self.tray_list.itemActivated.connect(lambda it: self._open_tray_menu_for(it))
        self.tray_list.setAccessibleName("Files still in Downloads")
        self.tray_list.hide()
        tl.addWidget(self.tray_list)
        self.tray_tip = QLabel("Right-click a file to put it somewhere and teach the app.", self.tray)
        self.tray_tip.setObjectName("TesterResult")
        self.tray_tip.hide()
        tl.addWidget(self.tray_tip)
        rl.addWidget(self.tray)
        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([270, 760])

        self._scan_ready.connect(self._on_scan_ready)
        self.refresh()
        self._select_first_folder()
        QTimer.singleShot(40, self.scan_disk)

    # ------------------------------------------------------------------
    # Plumbing
    # ------------------------------------------------------------------
    def config(self) -> Config:
        return self.service.config

    def _side_row(self, text: str, short: str, explain: str, page: int, parent: QWidget) -> QPushButton:
        """A two-line sidebar row: a title with a count, and one plain sentence under it."""
        btn = QPushButton("", parent)
        btn.setObjectName("SideRow")
        btn.setToolTip(explain)
        btn.setAccessibleName(text)
        btn.setAccessibleDescription(explain)
        btn.setCheckable(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        lay = QVBoxLayout(btn)
        lay.setContentsMargins(10, 4, 10, 4)
        lay.setSpacing(0)
        top = QHBoxLayout()
        top.setSpacing(6)
        title = QLabel(text, btn)
        title.setObjectName("SideRowTitle")
        badge = QLabel("", btn)
        badge.setObjectName("SideBadge")
        badge.hide()
        top.addWidget(title, 1)
        top.addWidget(badge, 0)
        lay.addLayout(top)
        sub = QLabel(short, btn)
        sub.setObjectName("SideRowSub")
        lay.addWidget(sub)
        for w in (title, badge, sub):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        btn.setMinimumHeight(lay.sizeHint().height())
        btn.setProperty("_badge", badge)
        btn._badge = badge  # type: ignore[attr-defined]
        btn.clicked.connect(lambda: self.show_page(page))
        return btn

    @staticmethod
    def _set_badge(row: QPushButton, count: int) -> None:
        badge: QLabel = row._badge  # type: ignore[attr-defined]
        badge.setText(str(count))
        badge.setVisible(count > 0)

    def _on_drag_state(self, dragging: bool) -> None:
        self.drag_tip.setVisible(not dragging and self.tree.topLevelItemCount() > 0)
        src = self.tree.drag_path()
        on_top = bool(src) and "\\" not in fd.norm_path(src)
        self.drop_top.setVisible(dragging and not on_top)
        self.drag_banner.setVisible(dragging)
        if dragging:
            self._on_drag_target("")

    def _on_drag_target(self, target: str) -> None:
        src = self.tree.drag_path()
        if not src:
            return
        name = fd.norm_path(src).split("\\")[-1]
        if target:
            dest = fd.norm_path(target).split("\\")[-1]
            self.drag_banner.setText(f"Let go to put “{name}” inside “{dest}”.")
        else:
            self.drag_banner.setText(f"Drop “{name}” on a folder to put it inside.")

    def run_action(self, work: Callable[..., Any], on_done: Callable[[Any], None], busy: str) -> None:
        if self._run_service_action is not None:
            self._run_service_action(work, on_done, busy, pass_progress=True, finish_text="Done")
            return
        run_async(work, on_done, parent=self, pass_progress=True)

    def commit(self, cfg: Config, message: str, *, change: fd.Change | None = None) -> None:
        """Save ``cfg`` and show ``message`` with Undo."""
        before = copy.deepcopy(self.service.config)
        self.config_committed.emit(cfg)
        has_disk = bool(change and (change.disk_moves or change.disk_removals or change.fill_path))
        if has_disk:
            self.disk_change_requested.emit(change)

        def _undo() -> None:
            self.config_committed.emit(before)
            if has_disk:
                self.action_requested.emit("folders:undo_disk", "")
            self.refresh()

        self.last_undo = _undo
        self.notice.emit(message, _undo)
        self.refresh()

    def apply(self, edit: Callable[[Config], fd.Change]) -> bool:
        try:
            change = edit(copy.deepcopy(self.service.config))
        except fd.DesignerError as exc:
            self.notice.emit(str(exc), None)
            self.refresh()
            return False
        if change.message == "No change":
            return False
        if change.selected_path:
            self._selected = change.selected_path
            self._page = PAGE_FOLDER
        elif change.disk_removals:
            self._selected = ""
            self._page = PAGE_EMPTY
        self.commit(change.config, change.message, change=change)
        return True

    # ------------------------------------------------------------------
    # Disk scan (off the GUI thread)
    # ------------------------------------------------------------------
    def scan_disk(self) -> None:
        if self._scan_running:
            self._scan_again = True
            return
        self._scan_running = True
        root = self.service.watch_root
        folders = [fd.path_of(a, n) for a in [fd.working_architecture(self.config())] for n in a.nodes]
        cfg = copy.deepcopy(self.config())
        svc = self.service

        def _work() -> dict[str, Any]:
            loose: list[FileCandidate] = []
            counts: dict[str, int] = {}
            strays: dict[str, int] = {}
            missing: set[str] = set()
            try:
                if root.is_dir():
                    for entry in sorted(root.iterdir(), key=lambda p: p.name.lower()):
                        if entry.is_file() and not entry.name.startswith("."):
                            loose.append(candidate_from_path(entry))
                wanted = {rel.lower() for rel in folders}
                parents_seen: set[str] = set()
                for rel in folders:
                    d = root / rel
                    try:
                        counts[rel.lower()] = sum(1 for e in os.scandir(d) if e.is_file())
                    except OSError:
                        counts[rel.lower()] = 0
                        missing.add(rel.lower())
                    # Files sitting directly in a parent folder that belong in one of its folders.
                    parent = "\\".join(rel.split("\\")[:-1])
                    if not parent or parent.lower() in parents_seen:
                        continue
                    parents_seen.add(parent.lower())
                    try:
                        entries = [e for e in os.scandir(root / parent) if e.is_file() and not e.name.startswith(".")]
                    except OSError:
                        continue
                    for e in entries:
                        dec = evaluate(candidate_from_path(Path(e.path), lazy_zone=True), cfg, include_trace=False)
                        if dec.action != "move" or dec.relative_dest is None:
                            continue
                        dest = str(dec.relative_dest.parent).replace("/", "\\").strip("\\").lower()
                        if dest in wanted and dest != parent.lower() and dest.startswith(parent.lower() + "\\"):
                            strays[dest] = strays.get(dest, 0) + 1
            except OSError:
                pass
            try:
                dupes = svc.list_duplicate_files()
            except Exception:  # noqa: BLE001 - a broken duplicate cache must not break the screen
                dupes = []
            try:
                waiting = svc.list_held_3mf()
            except Exception:  # noqa: BLE001
                waiting = []
            return {
                "loose": loose,
                "counts": counts,
                "dupes": dupes,
                "waiting": waiting,
                "strays": strays,
                "missing": missing,
            }

        def _err(_msg: str) -> None:
            self._scan_running = False

        run_async(_work, self._scan_ready.emit, _err, parent=self)

    def _on_scan_ready(self, state: dict) -> None:
        self._scan_running = False
        self._loose = list(state.get("loose", []))
        self._inside_counts = dict(state.get("counts", {}))
        self._strays = dict(state.get("strays", {}))
        self._missing = set(state.get("missing", set()))
        self._scanned = True
        self._dupes = list(state.get("dupes", []))
        self._waiting = list(state.get("waiting", []))
        self._decisions_for = -1
        self._refresh_tree_counts()
        self._refresh_handled_rows()
        self._refresh_tray()
        self._refresh_page()
        if self._scan_again:
            self._scan_again = False
            QTimer.singleShot(0, self.scan_disk)

    def _refresh_tree_counts(self) -> None:
        """Put the scanned file counts on the folder rows without rebuilding the tree."""
        it = QTreeWidgetItemIterator(self.tree)
        while it.value() is not None:
            item = it.value()
            path = str(item.data(0, ROLE_PATH) or "")
            n = self.files_inside(path)
            if int(item.data(0, ROLE_COUNT) or 0) != n:
                item.setData(0, ROLE_COUNT, n)
            it += 1
        self.tree.viewport().update()

    def reload_handled(self) -> None:
        self.scan_disk()

    def _route_all(self) -> list[tuple[FileCandidate, RouteDecision]]:
        key = id(self.service.config) ^ len(self._loose)
        if self._decisions_for != key:
            cfg = self.service.config
            waiting = {str(p) for p in self._waiting}
            out: list[tuple[FileCandidate, RouteDecision]] = []
            for c in self._loose:
                dec = evaluate(c, cfg, include_trace=False)
                if str(c.path) in waiting:
                    dec = RouteDecision("leave", dec.relative_dest, "Waiting a few days for your slicer", "3MF Hold", "hold")
                out.append((c, dec))
            self._decisions = out
            self._decisions_for = key
        return self._decisions

    @staticmethod
    def _dest_folder(dec: RouteDecision) -> str:
        if dec.action != "move" or dec.relative_dest is None:
            return ""
        return fd.norm_path(str(Path(dec.relative_dest).parent))

    def preview_for(self, path: str) -> tuple[list[str], dict[str, str]]:
        """Loose files landing in ``path`` and files that fit it but go elsewhere."""
        cues = fd.folder_cues(self.config(), path)
        landing: list[str] = []
        clash: dict[str, str] = {}
        low = fd.norm_path(path).lower()
        for cand, dec in self._route_all():
            dest = self._dest_folder(dec)
            if dest.lower() == low or dest.lower().startswith(low + "\\"):
                landing.append(cand.filename)
            elif dest and not cues.is_empty and self._fits(cand, cues):
                clash[cand.filename] = dest
        return landing, clash

    @staticmethod
    def _fits(cand: FileCandidate, cues: fd.FolderCues) -> bool:
        name = cand.filename.lower()
        ext = Path(name).suffix
        if any(s.lower() in name for s in cues.skip_words):
            return False
        if cues.extensions and ext not in cues.extensions:
            return False
        if cues.keywords or cues.domains:
            host = (cand.source_url or "").lower()
            return any(k.lower() in name for k in cues.keywords) or any(d in host for d in cues.domains)
        return True

    def files_inside(self, path: str) -> int:
        return int(self._inside_counts.get(fd.norm_path(path).lower(), 0))

    def fallback_count(self) -> int:
        return sum(1 for _c, d in self._route_all() if d.match_type == "fallback" and d.action == "move")

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        self._decisions_for = -1
        self._rebuild_tree()
        self._refresh_handled_rows()
        self._refresh_tray()
        self._refresh_page()
        self._on_tester(self.tester.text())
        if self.stack.currentIndex() != self._page:
            crossfade_stacked_widget(self.stack, self._page)

    def _rebuild_tree(self) -> None:
        arch = fd.working_architecture(self.config())
        expanded: set[str] = set()
        it = QTreeWidgetItemIterator(self.tree)
        while it.value() is not None:
            item = it.value()
            if item.isExpanded():
                expanded.add(str(item.data(0, ROLE_PATH)).lower())
            it += 1
        self.tree.blockSignals(True)
        self.tree.clear()
        by_id: dict[str, QTreeWidgetItem] = {}
        icon = make_vector_icon("folder", "#64B5FF", 16)
        manual_icon = make_vector_icon("folder", "#8E8E93", 16)
        cfg = self.config()
        selected_item: QTreeWidgetItem | None = None
        for tier in range(1, arch.MAX_TIERS + 1):
            for node in sorted(arch.nodes_in_tier(tier), key=lambda n: n.name.lower()):
                parent = by_id.get(node.parent_id or "")
                if tier > 1 and parent is None:
                    continue
                path = fd.path_of(arch, node)
                item = QTreeWidgetItem([node.name])
                item.setData(0, ROLE_PATH, path)
                manual = fd.is_manual(cfg, path, arch)
                item.setIcon(0, manual_icon if manual else icon)
                n_inside = self.files_inside(path)
                item.setData(0, ROLE_COUNT, n_inside)
                tip = fd.describe_cues(fd.folder_cues(cfg, path, arch))
                if n_inside:
                    tip += f"\n{n_inside} file{'s' if n_inside != 1 else ''} inside"
                item.setToolTip(0, tip + "\nDrag onto another folder to put it inside.")
                if parent is None:
                    self.tree.addTopLevelItem(item)
                else:
                    parent.addChild(item)
                by_id[node.id] = item
                if path.lower() == self._selected.lower():
                    selected_item = item
        it = QTreeWidgetItemIterator(self.tree)
        while it.value() is not None:
            item = it.value()
            path = str(item.data(0, ROLE_PATH)).lower()
            # Start collapsed so the list stays short; open the way to the selected folder.
            if path in expanded or self._selected.lower().startswith(path + "\\"):
                item.setExpanded(True)
            it += 1
        if selected_item is not None and self._page == PAGE_FOLDER:
            self.tree.setCurrentItem(selected_item)
        else:
            self.tree.setCurrentItem(None)
            self.tree.clearSelection()
        self.tree.blockSignals(False)
        if self._page == PAGE_FOLDER and selected_item is None:
            self._page = PAGE_EMPTY
        self.page_empty.set_has_folders(bool(arch.nodes))
        self.drag_tip.setVisible(len(arch.nodes) > 1)
        self.drop_top.hide()
        self.drag_banner.hide()

    def _refresh_handled_rows(self) -> None:
        n_d, n_w = len(self._dupes), len(self._waiting)
        self._set_badge(self.row_other, self.fallback_count())
        self._set_badge(self.row_dupes, n_d)
        self._set_badge(self.row_wait, n_w)
        # Waiting only matters when 3D print files are held, or some still are.
        self.row_wait.setVisible(bool(getattr(self.config(), "hold_3mf_enabled", False)) or n_w > 0)
        for row, page in ((self.row_other, PAGE_OTHER), (self.row_dupes, PAGE_DUPES), (self.row_wait, PAGE_WAITING)):
            row.setChecked(self._page == page)

    def _refresh_page(self) -> None:
        if self._page == PAGE_FOLDER and self._selected:
            self.page_folder.show_folder(self._selected)
            self.page_folder.set_hint(self._suggestion)
            key = fd.norm_path(self._selected).lower()
            self.page_folder.set_strays(
                int(self._strays.get(key, 0)), self._scanned and key in self._missing
            )
        elif self._page == PAGE_OTHER:
            self.page_other.refresh()
        elif self._page == PAGE_DUPES:
            self.page_dupes.load(self._dupes)
        elif self._page == PAGE_WAITING:
            self.page_wait.load(self._waiting)

    def show_page(self, page: int) -> None:
        if page != PAGE_FOLDER:
            self.tree.blockSignals(True)
            self.tree.setCurrentItem(None)
            self.tree.clearSelection()
            self.tree.blockSignals(False)
        self._page = page
        self._refresh_handled_rows()
        self._refresh_page()
        if self.stack.currentIndex() != page:
            crossfade_stacked_widget(self.stack, page)

    def select_path(self, path: str) -> None:
        self._selected = fd.norm_path(path)
        self._page = PAGE_FOLDER
        self._rebuild_tree()
        self.show_page(PAGE_FOLDER if self._page == PAGE_FOLDER else PAGE_EMPTY)

    def _select_first_folder(self) -> None:
        """Open on the first folder, so the right side is never a blank page."""
        if self._page != PAGE_EMPTY or self.tree.topLevelItemCount() == 0:
            return
        first = self.tree.topLevelItem(0)
        path = str(first.data(0, ROLE_PATH) or "") if first is not None else ""
        if path:
            self.select_path(path)

    def selected_path(self) -> str:
        return self._selected if self._page == PAGE_FOLDER else ""

    def _on_tree_current(self, current: QTreeWidgetItem | None, _prev: QTreeWidgetItem | None) -> None:
        if current is None:
            return
        self._selected = str(current.data(0, ROLE_PATH) or "")
        self.show_page(PAGE_FOLDER)

    def set_suggestion(self, suggestion: dict | None) -> None:
        self._suggestion = suggestion
        if self._page == PAGE_FOLDER:
            self.page_folder.set_hint(suggestion)

    def set_watch_label(self, _label: str) -> None:  # kept for parity with the Sorting Flow tab
        self.scan_disk()

    # ------------------------------------------------------------------
    # Tester
    # ------------------------------------------------------------------
    def _on_tester(self, text: str) -> None:
        text = text.strip()
        if not text:
            self.tester_result.hide()
            self.tester_reason.hide()
            self._tester_dest = ""
            return
        url = ""
        name = text
        if " from " in text:
            name, url = text.split(" from ", 1)
            url = url.strip()
            if url and "://" not in url:
                url = "https://" + url
        dec = evaluate(FileCandidate(filename=name.strip(), source_url=url), self.config(), include_trace=False)
        dest = self._dest_folder(dec)
        self._tester_dest = dest if fd.find_node(fd.working_architecture(self.config()), dest) else ""
        if dec.action == "move" and dest:
            self.tester_result.setText("→  " + _crumb(dest))
        elif dec.action == "ignore":
            self.tester_result.setText("Left alone")
        else:
            self.tester_result.setText("Stays in Downloads")
        self.tester_result.setEnabled(bool(self._tester_dest))
        self.tester_reason.setText(_elide(dec.reason, 120))
        self.tester_result.show()
        self.tester_reason.show()

    def _open_tester_folder(self) -> None:
        if self._tester_dest:
            self.select_path(self._tester_dest)

    def trace_from_activity(self, filename: str, source_url: str = "") -> None:
        self.tester.setText(f"{filename} from {source_url}" if source_url else filename)

    # ------------------------------------------------------------------
    # Folder edits
    # ------------------------------------------------------------------
    def fill_now(self, path: str) -> None:
        """Create ``path`` in Downloads and move in the files that belong there, with Undo."""
        change = fd.Change(copy.deepcopy(self.service.config), "", fill_path=fd.norm_path(path))
        self.disk_change_requested.emit(change)
        QTimer.singleShot(0, self.scan_disk)

    def _can_drop(self, src: str, target: str) -> bool:
        return not fd.why_cannot_move(self.config(), src, target)

    def move_folder(self, src: str, target: str) -> None:
        self.apply(lambda cfg: fd.move_folder(cfg, src, target))

    def new_folder(self, parent_path: str = "") -> None:
        arch = fd.working_architecture(self.config())
        where = parent_path.split("\\")[-1] if parent_path else "Downloads"
        dlg = NameDialog(
            "New folder",
            lambda text: fd.check_folder_name(arch, text, parent_path),
            hint=f"It goes inside {where}. You choose what lands in it next.",
            parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        name = dlg.value()
        if self.apply(lambda cfg: fd.add_folder(cfg, name, parent_path)):
            self.show_page(PAGE_FOLDER)

    def rename_folder(self, path: str) -> None:
        arch = fd.working_architecture(self.config())
        leaf = fd.norm_path(path).split("\\")[-1]
        parent = "\\".join(fd.norm_path(path).split("\\")[:-1])
        dlg = NameDialog(
            "Rename folder",
            lambda text: fd.check_folder_name(arch, text, parent, ignore_path=path),
            initial=leaf,
            hint="Files already inside move with it.",
            parent=self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            name = dlg.value()
            self.apply(lambda cfg: fd.rename_folder(cfg, path, name))

    def remove_folder(self, path: str) -> None:
        info = fd.removal_summary(self.config(), path)
        if not info.get("name"):
            return
        where = info["parent"].split("\\")[-1] if info["parent"] else "Downloads"
        lines = [f"Files inside “{info['name']}” will move to {where}. Nothing is deleted."]
        if info["subfolders"]:
            n = info["subfolders"]
            lines.append(f"The {n} folder{'s' if n != 1 else ''} inside it go too, and their files move to {where}.")
        if info["rules"]:
            n = info["rules"]
            lines.append(f"{n} rule{'s' if n != 1 else ''} that send files here will be removed.")
        box = QMessageBox(self)
        box.setWindowTitle("Remove folder")
        box.setText(f"Remove “{info['name']}”?")
        box.setInformativeText("\n\n".join(lines))
        remove = box.addButton("Remove", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is remove:
            self.apply(lambda cfg: fd.remove_folder(cfg, path))

    def _pick_folder(self, title: str, choices: list[str]) -> str | None:
        if not choices:
            self.notice.emit("There is nowhere this folder can go.", None)
            return None
        labels = ["Downloads (top level)" if c == "" else _crumb(c) for c in choices]
        label, ok = QInputDialog.getItem(self, title, "Choose a folder:", labels, 0, False)
        if not ok:
            return None
        return choices[labels.index(label)]

    def _open_folder_menu(self, path: str, global_pos: QPoint) -> None:
        if not path:
            return
        cfg = self.config()
        leaf = fd.norm_path(path).split("\\")[-1]
        menu = QMenu(self)
        arch = fd.working_architecture(cfg)
        node = fd.find_node(arch, path)
        act_new = menu.addAction(make_vector_icon("plus", "#F5F5F7", 14), "New folder inside…")
        act_new.setEnabled(bool(node and node.tier < arch.MAX_TIERS))
        act_new.triggered.connect(lambda: self.new_folder(path))
        menu.addAction(make_vector_icon("edit", "#F5F5F7", 14), "Rename…").triggered.connect(lambda: self.rename_folder(path))
        move_menu = menu.addMenu("Move to")
        targets = fd.valid_move_targets(cfg, path)
        for tgt in targets:
            act = move_menu.addAction("Downloads (top level)" if tgt == "" else _crumb(tgt))
            act.triggered.connect(lambda _=False, t=tgt: self.move_folder(path, t))
        move_menu.setEnabled(bool(targets))
        merge_menu = menu.addMenu("Combine with")
        merges = fd.merge_targets(cfg, path)
        for tgt in merges:
            act = merge_menu.addAction(_crumb(tgt))
            act.triggered.connect(lambda _=False, t=tgt: self._confirm_merge(path, t))
        merge_menu.setEnabled(bool(merges))
        menu.addSeparator()
        menu.addAction("Find projects inside").triggered.connect(lambda: self.action_requested.emit("detect_projects", path))
        menu.addAction("Show in File Explorer").triggered.connect(lambda: self.action_requested.emit("tree:open_explorer", path))
        menu.addSeparator()
        menu.addAction(make_vector_icon("trash", "#FF453A", 14), f"Remove “{_elide(leaf, 24)}”…").triggered.connect(
            lambda: self.remove_folder(path)
        )
        menu.exec(global_pos)

    def _confirm_merge(self, src: str, dst: str) -> None:
        s, d = src.split("\\")[-1], dst.split("\\")[-1]
        ans = QMessageBox.question(
            self,
            "Combine folders",
            f"Move everything in “{s}” into “{d}” and remove “{s}”?\n\nFiles are moved, never deleted. You can undo this.",
        )
        if ans == QMessageBox.StandardButton.Yes:
            self.apply(lambda cfg: fd.merge_folder(cfg, src, dst))

    def _open_new_menu(self) -> None:
        sel = self.selected_path()
        if not sel:
            self.new_folder("")
            return
        menu = QMenu(self)
        menu.addAction("New folder in Downloads…").triggered.connect(lambda: self.new_folder(""))
        menu.addAction(f"New folder inside “{_elide(sel.split(chr(92))[-1], 22)}”…").triggered.connect(
            lambda: self.new_folder(sel)
        )
        menu.exec(self.btn_new.mapToGlobal(self.btn_new.rect().bottomLeft()))

    # ------------------------------------------------------------------
    # Still in Downloads + Tidy
    # ------------------------------------------------------------------
    def _refresh_tray(self) -> None:
        decisions = self._route_all()
        self.tray_list.clear()
        movable = 0
        for cand, dec in decisions:
            dest = self._dest_folder(dec)
            if dec.action == "move" and dest:
                movable += 1
                text = f"{cand.filename}    →  {dest.replace(chr(92), ' › ')}"
            elif dec.match_type == "hold":
                text = f"{cand.filename}    ·  waiting"
            else:
                text = f"{cand.filename}    ·  stays here"
            item = QListWidgetItem(text)
            item.setData(ROLE_PATH, str(cand.path or cand.filename))
            item.setToolTip(dec.reason)
            self.tray_list.addItem(item)
        n = len(decisions)
        arrow = "▾" if self.tray_list.isVisible() else "▸"
        self.tray_toggle.setText(f"{arrow}  Still in Downloads  ·  {n}" if n else f"{arrow}  Downloads is tidy")
        self.btn_tidy.setText(f"Tidy {movable} file{'s' if movable != 1 else ''}" if movable else "Tidy")
        self.btn_tidy.setEnabled(movable > 0)

    def _toggle_tray(self) -> None:
        show = not self.tray_list.isVisible()
        self.tray_list.setVisible(show)
        self.tray_tip.setVisible(show)
        if show:
            fade_in(self.tray_list, 160)
        self._refresh_tray()

    def planned_moves(self) -> list[tuple[str, str]]:
        return [
            (c.filename, self._dest_folder(d))
            for c, d in self._route_all()
            if d.action == "move" and self._dest_folder(d)
        ]

    def review_and_tidy(self) -> None:
        plan = self.planned_moves()
        if not plan:
            return
        dlg = TidyReviewDialog(plan, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.tidy_requested.emit()

    def _open_tray_menu(self, pos: QPoint) -> None:
        item = self.tray_list.itemAt(pos)
        if item is not None:
            self._open_tray_menu_for(item, self.tray_list.viewport().mapToGlobal(pos))

    def _open_tray_menu_for(self, item: QListWidgetItem, global_pos: QPoint | None = None) -> None:
        raw = str(item.data(ROLE_PATH) or "")
        path = Path(raw)
        arch = fd.working_architecture(self.config())
        folders = sorted((fd.path_of(arch, n) for n in arch.nodes), key=str.lower)
        if not folders:
            return
        menu = QMenu(self)
        once = menu.addMenu("Put it in")
        teach = menu.addMenu("Always put files like this in")
        for f in folders:
            once.addAction(_crumb(f)).triggered.connect(lambda _=False, d=f: self._move_once(path, d))
            teach.addAction(_crumb(f)).triggered.connect(lambda _=False, d=f: self._teach(path, d))
        menu.exec(global_pos or self.tray_list.mapToGlobal(self.tray_list.visualItemRect(item).bottomLeft()))

    def _move_once(self, path: Path, dest: str) -> None:
        self.teach_move_requested.emit(path, dest)
        QTimer.singleShot(0, self.scan_disk)

    def _teach(self, path: Path, dest: str) -> None:
        cand = next((c for c in self._loose if str(c.path) == str(path)), None)
        url = cand.source_url if cand else ""
        if self.apply(lambda cfg: fd.teach(cfg, dest, path.name, url)):
            self.teach_move_requested.emit(path, dest)
            QTimer.singleShot(0, self.scan_disk)


__all__ = ["FlowLayout", "FoldersTab", "NameDialog", "TidyReviewDialog"]
