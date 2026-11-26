"""Right-hand live Downloads folder tree for Sorting Flow."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QMimeData, QPoint, QPointF, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from download_sorter.architecture import FolderArchitecture, architecture_from_config
from download_sorter.config import Config
from download_sorter.pipeline import PipelineTrace
from download_sorter.ui.flow.spine_panel import (
    MIME_BUCKET_ID,
    MIME_RULE_ID,
    make_flow_drag_pixmap,
)
from download_sorter.ui.motion import InteractiveRibbonBtn, make_vector_icon
from download_sorter.ui.text_scale import scaled_px

MIME_FOLDER_PATH = "application/x-ds-folder-path"


class _SyncDotWidget(QWidget):
    """Crisp antialiased 7px status dot for the Downloads live-sync pill."""

    def __init__(self, color: str = "#30D158", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = color
        self.setFixedSize(9, 9)

    def set_color(self, color: str) -> None:
        if self._color != color:
            self._color = color
            self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self._color))
        painter.drawEllipse(QPointF(4.5, 4.5), 3.3, 3.3)


class FolderRowWidget(QFrame):
    """Single clickable, draggable, and droppable folder row inside the Downloads tree."""

    clicked = Signal(str)
    hover_entered = Signal(str)
    hover_left = Signal()
    add_subfolder_requested = Signal(str)
    file_dropped = Signal(Path, str)
    rule_dropped = Signal(str, str)  # (rule_id, folder_path)
    bucket_dropped = Signal(str, str)  # (bucket_id, folder_path)
    folder_dropped = Signal(str, str)  # (src_folder_path, target_folder_path)
    drag_hover_entered = Signal(object, object)  # (FolderRowWidget, global_pos)
    drag_hover_left = Signal()
    context_action = Signal(str, str)  # (action_name, folder_path)

    def __init__(
        self,
        folder_path: str,
        display_name: str,
        *,
        depth: int = 0,
        has_children: bool = False,
        expanded: bool = True,
        rule_count: int = 0,
        is_hit: bool = False,
        is_focused: bool = False,
        is_draft_target: bool = False,
        land_label: str = "",
        exists_on_disk: bool = True,
        disk_file_count: int = 0,
        is_custom_disk: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.folder_path = folder_path
        self.display_name = display_name
        self._drag_start: QPoint | None = None
        self._double_clicked = False
        self.setMinimumHeight(scaled_px(50) if (is_hit and land_label) else scaled_px(32))
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        if not exists_on_disk:
            self.setToolTip("Created automatically when a matching file arrives · Drag rules, file types, or folders here")
        elif is_custom_disk:
            self.setToolTip("Your own folder in Downloads — drag rules or file types here to route into it")
        else:
            self.setToolTip("Click to inspect · Double-click to open · Drag rules, file types, files, or folders here")

        if is_hit or is_focused or is_draft_target:
            self._base_qss = (
                "FolderRowWidget { background-color: rgba(10, 132, 255, 0.11); "
                "border: 1px solid rgba(10, 132, 255, 0.45); border-radius: 10px; }"
            )
        else:
            self._base_qss = (
                "FolderRowWidget { background-color: transparent; border: 1px solid transparent; border-radius: 10px; }"
                "FolderRowWidget:hover { background-color: #161618; }"
            )
        self.setStyleSheet(self._base_qss)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8 + depth * scaled_px(16), 6, 8, 6)
        outer.setSpacing(4)

        row = QHBoxLayout()
        row.setSpacing(6)

        if has_children:
            self.btn_expand = QPushButton("▾" if expanded else "▸")
            self.btn_expand.setFixedSize(scaled_px(18), scaled_px(18))
            self.btn_expand.setAccessibleName(f"Toggle {display_name} subfolders")
            self.btn_expand.setStyleSheet(
                "QPushButton { border: none; background: transparent; color: #8E8E93; "
                "padding: 0px; min-width: 0px; min-height: 0px; font-size: 11px; }"
                "QPushButton:hover { color: #F5F5F7; }"
            )
            self.btn_expand.clicked.connect(lambda: self.context_action.emit("toggle_expand", self.folder_path))
            row.addWidget(self.btn_expand)
        else:
            spacer = QLabel("")
            spacer.setFixedWidth(scaled_px(12))
            row.addWidget(spacer)

        if is_hit or is_focused or is_draft_target:
            icon_col = "#3B82F6"
        elif not exists_on_disk:
            icon_col = "#52525A"
        elif is_custom_disk:
            icon_col = "#8E8E93"
        else:
            icon_col = "#9CA3AF"
        lbl_icon = QLabel()
        lbl_icon.setPixmap(make_vector_icon("folder", icon_col, 14).pixmap(14, 14))
        row.addWidget(lbl_icon)

        lbl_name = QLabel(display_name)
        weight = "600" if (is_hit or is_focused or is_draft_target or (depth == 0 and exists_on_disk)) else "400"
        if is_hit or is_focused or is_draft_target:
            col = "#F5F5F7"
        elif not exists_on_disk:
            col = "#71717A"
        elif depth == 0:
            col = "#EAEAEF"
        else:
            col = "#B4B4BC"
        lbl_name.setStyleSheet(f"color: {col}; font-weight: {weight}; font-size: 12.5px;")
        row.addWidget(lbl_name, 1)

        if is_hit:
            tag = QLabel("Will create" if not exists_on_disk else "Lands here")
            tag.setStyleSheet("color: #3B82F6; font-size: 11px; font-weight: 600;")
            row.addWidget(tag)
        elif is_draft_target:
            tag = QLabel("Draft target")
            tag.setStyleSheet("color: #3B82F6; font-size: 11px; font-weight: 600;")
            row.addWidget(tag)
        elif is_focused:
            tag = QLabel("Inspecting")
            tag.setStyleSheet("color: #3B82F6; font-size: 11px; font-weight: 600;")
            row.addWidget(tag)
        elif rule_count > 0:
            tag = QLabel(f"{rule_count} rule{'s' if rule_count != 1 else ''}")
            tag.setStyleSheet("color: #71717A; font-size: 11px;")
            row.addWidget(tag)
        elif not exists_on_disk:
            tag = QLabel("Auto")
            tag.setStyleSheet("color: #52525A; font-size: 10.5px; font-style: italic;")
            row.addWidget(tag)
        elif disk_file_count > 0:
            tag = QLabel(str(disk_file_count))
            tag.setStyleSheet(
                "color: #8E8E93; background-color: rgba(255, 255, 255, 0.05); "
                "border-radius: 5px; padding: 1px 5px; font-size: 10.5px;"
            )
            row.addWidget(tag)

        self.btn_add = QPushButton("+")
        self.btn_add.setFixedSize(scaled_px(22), scaled_px(22))
        self.btn_add.setAccessibleName(f"Add subfolder inside {display_name}")
        self.btn_add.setToolTip(f"Create subfolder inside {display_name}")
        self.btn_add.setStyleSheet(
            "QPushButton { border: none; background: transparent; color: #8E8E93; "
            "border-radius: 5px; font-weight: 600; padding: 0px; min-width: 0px; min-height: 0px; }"
            "QPushButton:hover { background-color: #2C2C2E; color: #F5F5F7; }"
        )
        self.btn_add.clicked.connect(lambda: self.add_subfolder_requested.emit(self.folder_path))
        row.addWidget(self.btn_add)

        outer.addLayout(row)

        if is_hit and land_label:
            preview_row = QHBoxLayout()
            preview_row.setContentsMargins(scaled_px(22), 0, 0, 0)
            preview_row.setSpacing(5)
            lbl_arrow = QLabel("↳")
            lbl_arrow.setStyleSheet("color: #0A84FF; font-size: 11.5px;")
            lbl_file = QLabel(land_label)
            lbl_file.setStyleSheet("color: #F5F5F7; font-size: 11.5px; font-family: 'Cascadia Mono', Consolas, monospace;")
            preview_row.addWidget(lbl_arrow)
            preview_row.addWidget(lbl_file, 1)
            outer.addLayout(preview_row)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.position().toPoint()
            self._double_clicked = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if (
            self._drag_start is not None
            and (event.buttons() & Qt.MouseButton.LeftButton)
            and (event.position().toPoint() - self._drag_start).manhattanLength() >= 10
        ):
            self._drag_start = None
            drag = QDrag(self.window() or self)
            mime = QMimeData()
            mime.setData(MIME_FOLDER_PATH, self.folder_path.encode("utf-8"))
            drag.setMimeData(mime)
            sub_hint = (
                "Drop on folder to nest · Drop on Downloads header for top-level"
                if "\\" in self.folder_path
                else "Drop onto another folder to nest inside it"
            )
            ghost = make_flow_drag_pixmap(f"Folder: {self.display_name}", sub_hint, "#0A84FF")
            drag.setPixmap(ghost)
            drag.setHotSpot(QPoint(18, ghost.height() // 2))
            drag.exec(Qt.DropAction.MoveAction)
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._drag_start is not None:
            dist = (event.position().toPoint() - self._drag_start).manhattanLength()
            self._drag_start = None
            if dist < 10 and not self._double_clicked:
                folder = self.folder_path
                QTimer.singleShot(0, lambda f=folder: self.clicked.emit(f))
            event.accept()
            return
        self._drag_start = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._double_clicked = True
            self._drag_start = None
            self.context_action.emit("open_explorer", self.folder_path)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def enterEvent(self, event) -> None:  # noqa: N802
        self.hover_entered.emit(self.folder_path)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.hover_left.emit()
        super().leaveEvent(event)

    def _can_accept_mime(self, mime) -> bool:
        if mime.hasFormat(MIME_RULE_ID) or mime.hasFormat(MIME_BUCKET_ID):
            return True
        if mime.hasFormat(MIME_FOLDER_PATH):
            src = bytes(mime.data(MIME_FOLDER_PATH)).decode("utf-8", "ignore").replace("/", "\\").strip("\\")
            cur = self.folder_path.replace("/", "\\").strip("\\")
            if not src or src.lower() == cur.lower() or cur.lower().startswith(src.lower() + "\\"):
                return False
            # Already direct parent -> no-op
            src_parent = src.rsplit("\\", 1)[0] if "\\" in src else ""
            if src_parent.lower() == cur.lower():
                return False
            return True
        if mime.hasUrls():
            return any(u.isLocalFile() for u in mime.urls())
        return False

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        mime = event.mimeData()
        if self._can_accept_mime(mime):
            event.acceptProposedAction()
            self.setStyleSheet(
                "FolderRowWidget { background-color: rgba(10, 132, 255, 0.16); "
                "border: 1.5px solid #0A84FF; border-radius: 10px; }"
            )
            if mime.hasFormat(MIME_RULE_ID) or mime.hasFormat(MIME_BUCKET_ID):
                self.drag_hover_entered.emit(self, self.mapToGlobal(event.position().toPoint()))
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        mime = event.mimeData()
        if self._can_accept_mime(mime):
            event.acceptProposedAction()
            if mime.hasFormat(MIME_RULE_ID) or mime.hasFormat(MIME_BUCKET_ID):
                self.drag_hover_entered.emit(self, self.mapToGlobal(event.position().toPoint()))
            return
        super().dragMoveEvent(event)

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self.setStyleSheet(self._base_qss)
        self.drag_hover_left.emit()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        self.setStyleSheet(self._base_qss)
        self.drag_hover_left.emit()
        mime = event.mimeData()
        dest = self.folder_path
        if mime.hasFormat(MIME_RULE_ID):
            rid = bytes(mime.data(MIME_RULE_ID)).decode("utf-8", "ignore").strip()
            event.acceptProposedAction()
            if rid:
                QTimer.singleShot(0, lambda r=rid, d=dest: self.rule_dropped.emit(r, d))
            return
        if mime.hasFormat(MIME_BUCKET_ID):
            bid = bytes(mime.data(MIME_BUCKET_ID)).decode("utf-8", "ignore").strip()
            event.acceptProposedAction()
            if bid:
                QTimer.singleShot(0, lambda b=bid, d=dest: self.bucket_dropped.emit(b, d))
            return
        if mime.hasFormat(MIME_FOLDER_PATH) and self._can_accept_mime(mime):
            src = bytes(mime.data(MIME_FOLDER_PATH)).decode("utf-8", "ignore").strip()
            event.acceptProposedAction()
            if src:
                QTimer.singleShot(0, lambda s=src, d=dest: self.folder_dropped.emit(s, d))
            return
        if mime.hasUrls():
            for u in mime.urls():
                if u.isLocalFile():
                    p = Path(u.toLocalFile())
                    if p.is_file():
                        event.acceptProposedAction()
                        QTimer.singleShot(0, lambda fp=p, d=dest: self.file_dropped.emit(fp, d))
                        return
        super().dropEvent(event)

    def _show_context_menu(self, pos) -> None:
        menu = QMenu(self)
        act_new = menu.addAction("New Subfolder...")
        act_rename = menu.addAction("Rename...")
        act_merge = menu.addAction("Merge Into...")
        act_open = menu.addAction("Open in Explorer")
        menu.addSeparator()
        act_del = menu.addAction("Delete Folder...")
        chosen = menu.exec(self.mapToGlobal(pos))
        if chosen == act_new:
            self.add_subfolder_requested.emit(self.folder_path)
        elif chosen == act_rename:
            self.context_action.emit("rename", self.folder_path)
        elif chosen == act_merge:
            self.context_action.emit("merge", self.folder_path)
        elif chosen == act_open:
            self.context_action.emit("open_explorer", self.folder_path)
        elif chosen == act_del:
            self.context_action.emit("delete", self.folder_path)


class _RootDropHeader(QFrame):
    """Downloads header strip that accepts nested folder drops to promote them to top-level."""

    folder_promoted = Signal(str)  # src_folder_path

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._base_qss = "_RootDropHeader { background: transparent; border: 1px solid transparent; border-radius: 8px; }"
        self.setStyleSheet(self._base_qss)

    @staticmethod
    def _promotable_path(mime) -> str:
        if not mime.hasFormat(MIME_FOLDER_PATH):
            return ""
        src = bytes(mime.data(MIME_FOLDER_PATH)).decode("utf-8", "ignore").replace("/", "\\").strip("\\")
        return src if "\\" in src else ""

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if self._promotable_path(event.mimeData()):
            event.acceptProposedAction()
            self.setStyleSheet(
                "_RootDropHeader { background-color: rgba(10, 132, 255, 0.14); "
                "border: 1px dashed #0A84FF; border-radius: 8px; }"
            )
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if self._promotable_path(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self.setStyleSheet(self._base_qss)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        self.setStyleSheet(self._base_qss)
        src = self._promotable_path(event.mimeData())
        if src:
            event.acceptProposedAction()
            QTimer.singleShot(0, lambda s=src: self.folder_promoted.emit(s))
            return
        super().dropEvent(event)


class FolderTreePanel(QFrame):
    """Right-hand live folder tree backed by FolderArchitecture & Config."""

    folder_clicked = Signal(str)
    folder_hovered = Signal(str)
    folder_hover_left = Signal()
    subfolder_created = Signal(str, str)  # (parent_path, new_name)
    file_dropped_on_folder = Signal(Path, str)
    rule_dropped_on_folder = Signal(str, str)  # (rule_id, folder_path)
    bucket_dropped_on_folder = Signal(str, str)  # (bucket_id, folder_path)
    folder_dropped_on_folder = Signal(str, str)  # (src_folder, target_parent_folder; "" = root)
    drag_hover_folder = Signal(object, object)  # (FolderRowWidget, global_pos)
    drag_hover_left = Signal()
    context_action = Signal(str, str)  # (action_name, folder_path)
    tool_action = Signal(str)  # scan_folders | sort_lower_tiers | snapshots | open_canvas | defaults | apply_shift | discard_shift
    clear_focus_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SurfaceCard")
        self._row_widgets: dict[str, QWidget] = {}
        self._expanded_parents: set[str] = {"Documents"}
        self._inline_parent: str | None = None
        self._pending_changes = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        # Header (also accepts subfolder drops to promote to top-level Downloads)
        self.hdr_drop = _RootDropHeader(self)
        self.hdr_drop.folder_promoted.connect(lambda src: self.folder_dropped_on_folder.emit(src, ""))
        hdr = QHBoxLayout(self.hdr_drop)
        hdr.setContentsMargins(2, 2, 2, 2)
        hdr.setSpacing(6)
        lbl_ic = QLabel()
        lbl_ic.setPixmap(make_vector_icon("folder", "#0A84FF", 15).pixmap(15, 15))
        self.lbl_root_title = QLabel("Downloads")
        self.lbl_root_title.setObjectName("CardTitle")
        self.lbl_retarget_hint = QLabel("· Click folder to set")
        self.lbl_retarget_hint.setStyleSheet("color: #0A84FF; font-size: 11.5px; font-weight: 600;")
        self.lbl_retarget_hint.setVisible(False)

        self.btn_add_sub = InteractiveRibbonBtn("+ Subfolder")
        self.btn_add_sub.setObjectName("GhostButton")
        self.btn_add_sub.clicked.connect(lambda: self.start_inline_subfolder("Documents"))

        self.btn_more = InteractiveRibbonBtn("⋯")
        self.btn_more.setObjectName("GhostButton")
        self.btn_more.setToolTip("Folder tools: Scan Folders, Snapshots, Sort Lower Tiers...")
        self.menu_tools = QMenu(self.btn_more)
        self.menu_tools.addAction("Scan Existing Folders...", lambda: self.tool_action.emit("scan_folders"))
        self.menu_tools.addAction("Sort Lower Tiers", lambda: self.tool_action.emit("sort_lower_tiers"))
        self.menu_tools.addAction("Snapshots...", lambda: self.tool_action.emit("snapshots"))
        self.menu_tools.addSeparator()
        self.menu_tools.addAction("Open Node Canvas...", lambda: self.tool_action.emit("open_canvas"))
        self.menu_tools.addAction("Return to Defaults...", lambda: self.tool_action.emit("defaults"))
        self.btn_more.clicked.connect(
            lambda: self.menu_tools.exec(
                self.btn_more.mapToGlobal(self.btn_more.rect().bottomRight())
            )
        )

        hdr.addWidget(lbl_ic)
        hdr.addWidget(self.lbl_root_title)
        hdr.addWidget(self.lbl_retarget_hint)
        hdr.addStretch(1)
        hdr.addWidget(self.btn_add_sub)
        hdr.addWidget(self.btn_more)
        root.addWidget(self.hdr_drop)

        # Live Disk Alignment Bar (shows loose files vs. synced state with 1-click Align Now)
        self.sync_bar = QFrame()
        self.sync_bar.setStyleSheet(
            "QFrame { background-color: rgba(255, 255, 255, 0.03); "
            "border: 1px solid rgba(255, 255, 255, 0.07); border-radius: 8px; }"
        )
        sb_l = QHBoxLayout(self.sync_bar)
        sb_l.setContentsMargins(9, 5, 6, 5)
        sb_l.setSpacing(7)
        self.sync_dot = _SyncDotWidget("#30D158")
        self.lbl_sync_status = QLabel("Checking Downloads...")
        self.lbl_sync_status.setStyleSheet("color: #AEAEB2; font-size: 11.5px; border: none; background: transparent;")
        self.btn_align_now = QPushButton("Align Now")
        self.btn_align_now.setObjectName("PrimaryButton")
        self.btn_align_now.setVisible(False)
        self.btn_align_now.setToolTip("Organize existing loose files in Downloads so the folder matches Sorting Flow (with Undo)")
        self.btn_align_now.setStyleSheet(
            "QPushButton { padding: 3px 9px; font-size: 11.5px; border-radius: 6px; min-width: 0px; }"
        )
        self.btn_align_now.clicked.connect(lambda: self.tool_action.emit("align_downloads"))
        sb_l.addWidget(self.sync_dot)
        sb_l.addWidget(self.lbl_sync_status, 1)
        sb_l.addWidget(self.btn_align_now)
        root.addWidget(self.sync_bar)

        # Scrollable Tree
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet(
            "QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }"
            "QScrollBar:vertical { background: transparent; width: 5px; margin: 4px 0; }"
            "QScrollBar::handle:vertical { background: rgba(255, 255, 255, 0.12); border-radius: 2px; min-height: 24px; }"
            "QScrollBar::handle:vertical:hover { background: rgba(255, 255, 255, 0.28); }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical, QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; height: 0px; }"
        )
        self.tree_host = QWidget()
        self.tree_layout = QVBoxLayout(self.tree_host)
        self.tree_layout.setContentsMargins(0, 2, 2, 18)
        self.tree_layout.setSpacing(3)
        self.tree_layout.addStretch(1)
        self.scroll.setWidget(self.tree_host)
        root.addWidget(self.scroll, 1)

        # Footer: Calm hint OR contextual Apply & Shift Files bar
        self.footer_hint_row = QWidget()
        fh = QHBoxLayout(self.footer_hint_row)
        fh.setContentsMargins(0, 4, 0, 0)
        self.lbl_footer_hint = QLabel("Click folder to inspect · Drop file to teach")
        self.lbl_footer_hint.setObjectName("MutedText")
        self.btn_reset_focus = QPushButton("Reset view")
        self.btn_reset_focus.setVisible(False)
        self.btn_reset_focus.setStyleSheet(
            "border: none; background: transparent; color: #0A84FF; font-size: 12px; font-weight: 600;"
        )
        self.btn_reset_focus.clicked.connect(self.clear_focus_requested.emit)
        fh.addWidget(self.lbl_footer_hint, 1)
        fh.addWidget(self.btn_reset_focus)
        root.addWidget(self.footer_hint_row)

        self.apply_bar = QFrame()
        self.apply_bar.setVisible(False)
        self.apply_bar.setStyleSheet(
            "QFrame { background-color: rgba(10, 132, 255, 0.10); border: 1px solid rgba(10, 132, 255, 0.35); border-radius: 10px; }"
        )
        ab = QHBoxLayout(self.apply_bar)
        ab.setContentsMargins(10, 7, 10, 7)
        ab.setSpacing(6)
        self.lbl_pending = QLabel("Folder changes pending")
        self.lbl_pending.setStyleSheet("color: #F5F5F7; font-size: 12px; border: none; background: transparent;")
        btn_discard = QPushButton("Discard")
        btn_discard.setStyleSheet("padding: 3px 8px; font-size: 11.5px;")
        btn_discard.clicked.connect(lambda: self.tool_action.emit("discard_shift"))
        btn_apply = QPushButton("Apply & Shift Files")
        btn_apply.setObjectName("PrimaryButton")
        btn_apply.setStyleSheet("padding: 4px 10px; font-size: 11.5px;")
        btn_apply.clicked.connect(lambda: self.tool_action.emit("apply_shift"))
        ab.addWidget(self.lbl_pending, 1)
        ab.addWidget(btn_discard)
        ab.addWidget(btn_apply)
        root.addWidget(self.apply_bar)

    def set_root_label(self, label: str) -> None:
        self.lbl_root_title.setText(label)

    def set_retarget_mode(self, active: bool) -> None:
        self.lbl_retarget_hint.setVisible(active)

    def set_pending_changes(self, count: int, label: str = "") -> None:
        self._pending_changes = count
        self.apply_bar.setVisible(count > 0)
        self.footer_hint_row.setVisible(count == 0)
        if count > 0:
            self.lbl_pending.setText(label or f"{count} folder change{'s' if count != 1 else ''} pending")

    def start_inline_subfolder(self, parent_path: str) -> None:
        self._expanded_parents.add(parent_path)
        self._inline_parent = parent_path
        self.context_action.emit("refresh_tree", parent_path)

    def folder_widget(self, folder_path: str) -> QWidget | None:
        return self._row_widgets.get(folder_path)

    def _wire_row(self, row: FolderRowWidget) -> None:
        row.clicked.connect(self.folder_clicked.emit)
        row.hover_entered.connect(self.folder_hovered.emit)
        row.hover_left.connect(self.folder_hover_left.emit)
        row.add_subfolder_requested.connect(self.start_inline_subfolder)
        row.file_dropped.connect(self.file_dropped_on_folder.emit)
        row.rule_dropped.connect(self.rule_dropped_on_folder.emit)
        row.bucket_dropped.connect(self.bucket_dropped_on_folder.emit)
        row.folder_dropped.connect(self.folder_dropped_on_folder.emit)
        row.drag_hover_entered.connect(self.drag_hover_folder.emit)
        row.drag_hover_left.connect(self.drag_hover_left.emit)
        row.context_action.connect(self._on_row_context_action)

    def rebuild(
        self,
        config: Config,
        trace: PipelineTrace | None,
        *,
        focused_folder: str | None = None,
        draft_target: str | None = None,
        disk_state: dict | None = None,
        override_arch: FolderArchitecture | None = None,
    ) -> None:
        self._row_widgets.clear()
        while self.tree_layout.count() > 1:
            item = self.tree_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        arch = override_arch.clone() if override_arch is not None else architecture_from_config(config)
        # Build ordered folder tree from architecture + any custom rule/map destinations
        tree: dict[str, list[str]] = {}
        for node in arch.nodes_in_tier(1):
            tree.setdefault(node.name, [])
            for child in arch.children_of(node.id):
                rel = arch.node_relative_path(child.id).replace("/", "\\")
                if rel not in tree[node.name]:
                    tree[node.name].append(rel)
                for grand in arch.children_of(child.id):
                    grel = arch.node_relative_path(grand.id).replace("/", "\\")
                    if grel not in tree[node.name]:
                        tree[node.name].append(grel)

        for r in config.rules:
            dest = r.destination.replace("/", "\\").strip("\\")
            if not dest or "{" in dest:
                continue
            top = dest.split("\\")[0]
            tree.setdefault(top, [])
            if "\\" in dest and dest not in tree[top]:
                tree[top].append(dest)

        # Count rules per folder
        rule_counts: dict[str, int] = {}
        for r in config.rules:
            if r.enabled:
                d = r.destination.replace("/", "\\").strip("\\")
                rule_counts[d] = rule_counts.get(d, 0) + 1

        active_target = ""
        if draft_target:
            active_target = draft_target.replace("/", "\\").strip("\\")
        elif focused_folder:
            active_target = focused_folder.replace("/", "\\").strip("\\")
        elif trace is not None and trace.dest_folder:
            active_target = trace.dest_folder.replace("/", "\\").strip("\\")
        if "\\" in active_target:
            self._expanded_parents.add(active_target.split("\\", 1)[0])

        # Root Hold card when Step 1 holds a .3mf in Downloads root
        if not focused_folder and not draft_target and trace is not None and trace.outcome == "hold":
            hold_card = QFrame()
            hold_card.setStyleSheet(
                "QFrame { background-color: rgba(129, 140, 248, 0.12); "
                "border: 1px solid rgba(129, 140, 248, 0.45); border-radius: 10px; }"
            )
            hl = QHBoxLayout(hold_card)
            hl.setContentsMargins(10, 7, 10, 7)
            hl.setSpacing(6)
            lbl_hf = QLabel(trace.filename)
            lbl_hf.setStyleSheet("color: #F5F5F7; font-size: 12px; font-weight: 600; border: none; background: transparent;")
            lbl_ht = QLabel(f"Held {config.hold_3mf_days}d in root")
            lbl_ht.setStyleSheet("color: #818CF8; font-size: 11px; font-weight: 600; border: none; background: transparent;")
            hl.addWidget(lbl_hf, 1)
            hl.addWidget(lbl_ht)
            self._row_widgets["__ROOT_HOLD__"] = hold_card
            self.tree_layout.insertWidget(self.tree_layout.count() - 1, hold_card)

        land_label = ""
        if trace is not None and trace.filename and not focused_folder and not draft_target:
            if trace.final_filename and trace.final_filename != trace.filename:
                land_label = f"{trace.filename} → {trace.final_filename}"
            else:
                land_label = trace.filename

        existing_folders: dict[str, int] = dict((disk_state or {}).get("existing_folders") or {})
        has_disk_scan = bool(disk_state and disk_state.get("scanned"))
        loose_count = int((disk_state or {}).get("loose_count", 0))
        if has_disk_scan:
            if loose_count == 0:
                self.sync_dot.set_color("#30D158")
                self.lbl_sync_status.setText("In sync with Downloads")
                self.btn_align_now.setVisible(False)
            else:
                self.sync_dot.set_color("#3B82F6")
                noun = "file" if loose_count == 1 else "files"
                self.lbl_sync_status.setText(f"{loose_count} loose {noun} in Downloads")
                self.btn_align_now.setVisible(True)

        has_3d_activity = (
            config.show_advanced_tools
            or bool(config.architecture)
            or active_target.startswith("3D Models")
            or ("3D Models" in existing_folders)
            or ("CAD" in existing_folders)
            or any("3d" in r.destination.lower() or ".3mf" in r.extensions or ".stl" in r.extensions for r in config.rules)
        )

        managed_tops_lower: set[str] = set()
        for parent_name, children in tree.items():
            managed_tops_lower.add(parent_name.lower())
            if parent_name in ("3D Models", "CAD") and not has_3d_activity:
                continue
            if active_target == parent_name or active_target.startswith(parent_name + "\\"):
                self._expanded_parents.add(parent_name)

            expanded = parent_name in self._expanded_parents
            is_parent_hit = (not focused_folder and not draft_target and active_target == parent_name)
            p_exists = (parent_name in existing_folders) if has_disk_scan else True
            p_count = existing_folders.get(parent_name, 0)
            p_row = FolderRowWidget(
                parent_name,
                parent_name,
                depth=0,
                has_children=bool(children),
                expanded=expanded,
                rule_count=rule_counts.get(parent_name, 0),
                is_hit=is_parent_hit,
                is_focused=(focused_folder == parent_name),
                is_draft_target=(draft_target == parent_name),
                land_label=land_label if is_parent_hit else "",
                exists_on_disk=p_exists,
                disk_file_count=p_count,
            )
            self._wire_row(p_row)
            self._row_widgets[parent_name] = p_row
            self.tree_layout.insertWidget(self.tree_layout.count() - 1, p_row)

            if expanded:
                for child_path in children:
                    depth = child_path.count("\\")
                    leaf = child_path.split("\\")[-1]
                    is_child_hit = (not focused_folder and not draft_target and active_target == child_path)
                    c_exists = (child_path in existing_folders) if has_disk_scan else True
                    c_count = existing_folders.get(child_path, 0)
                    c_row = FolderRowWidget(
                        child_path,
                        leaf,
                        depth=depth,
                        has_children=False,
                        rule_count=rule_counts.get(child_path, 0),
                        is_hit=is_child_hit,
                        is_focused=(focused_folder == child_path),
                        is_draft_target=(draft_target == child_path),
                        land_label=land_label if is_child_hit else "",
                        exists_on_disk=c_exists,
                        disk_file_count=c_count,
                    )
                    self._wire_row(c_row)
                    self._row_widgets[child_path] = c_row
                    self.tree_layout.insertWidget(self.tree_layout.count() - 1, c_row)

                if self._inline_parent == parent_name:
                    inp_frame = QFrame()
                    il = QHBoxLayout(inp_frame)
                    il.setContentsMargins(scaled_px(28), 2, 8, 2)
                    edit = QLineEdit()
                    edit.setPlaceholderText(f"New subfolder in {parent_name} (Enter)...")
                    edit.setStyleSheet(
                        "background-color: #161618; border: 1px solid #0A84FF; border-radius: 7px; padding: 4px 8px;"
                    )

                    def _commit(e=edit, p=parent_name) -> None:
                        name = e.text().strip().replace("/", "").replace("\\", "")
                        self._inline_parent = None
                        if name:
                            self.subfolder_created.emit(p, name)
                        else:
                            self.context_action.emit("refresh_tree", p)

                    edit.returnPressed.connect(_commit)
                    il.addWidget(edit)
                    self.tree_layout.insertWidget(self.tree_layout.count() - 1, inp_frame)
                    edit.setFocus()

        # Calmly list user-created folders currently on disk that aren't managed by Sorting Flow
        custom_disk_folders = [
            (name, cnt)
            for name, cnt in ((disk_state or {}).get("custom_top_folders") or [])
            if name.lower() not in managed_tops_lower
        ]
        if custom_disk_folders:
            div_lbl = QLabel("Other folders in Downloads")
            div_lbl.setStyleSheet(
                "color: #636366; font-size: 10.5px; font-weight: 600; "
                "padding: 10px 8px 2px 8px; border-top: 1px solid rgba(255, 255, 255, 0.06);"
            )
            self.tree_layout.insertWidget(self.tree_layout.count() - 1, div_lbl)
            for cname, ccnt in custom_disk_folders[:12]:
                u_row = FolderRowWidget(
                    cname,
                    cname,
                    depth=0,
                    has_children=False,
                    exists_on_disk=True,
                    disk_file_count=ccnt,
                    is_custom_disk=True,
                )
                self._wire_row(u_row)
                self._row_widgets[cname] = u_row
                self.tree_layout.insertWidget(self.tree_layout.count() - 1, u_row)

        self.btn_reset_focus.setVisible(bool(focused_folder))
        if focused_folder:
            leaf_name = focused_folder.split("\\")[-1]
            self.lbl_footer_hint.setText(f"Showing rules & types feeding {leaf_name}")
        else:
            self.lbl_footer_hint.setText("Click to inspect · Drag rules, types, or folders to adjust")

    def _on_row_context_action(self, action: str, folder_path: str) -> None:
        if action == "toggle_expand":
            if folder_path in self._expanded_parents:
                self._expanded_parents.discard(folder_path)
            else:
                self._expanded_parents.add(folder_path)
            self.context_action.emit("refresh_tree", folder_path)
            return
        self.context_action.emit(action, folder_path)
