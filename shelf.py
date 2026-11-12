"""Recent files shelf (#25): the last sorted files, ready to pick from any app.

Ctrl+Shift+Space (or Settings / the tray menu) opens a small list of the files Download Sorter
filed most recently. 3D files (.3mf, .stl, .step) are listed first while a slicer or CAD app is
open. Double-click (or Enter) opens the file's folder with it selected; "Copy path" puts the full
path on the clipboard, so it can be pasted into a file dialog in an app that does not use the
standard Windows Open/Save window.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

THREE_D_SUFFIXES = (".3mf", ".stl", ".step", ".stp", ".f3d", ".f3z", ".obj")
THREE_D_APPS = (
    "bambu-studio.exe", "bambu-studio-worker.exe", "orca-slicer.exe", "prusa-slicer.exe",
    "cura.exe", "fusion360.exe", "fusion.exe", "inventor.exe", "solidworks.exe",
)
SHELF_LIMIT = 15


def three_d_app_open() -> bool:
    import hold_manager
    return hold_manager.is_slicer_running(list(THREE_D_APPS))


def recent_files(limit: int = SHELF_LIMIT, three_d_first: bool | None = None) -> list[dict]:
    """The most recently filed files that still exist, newest first; 3D files lead when a
    slicer or CAD app is open (the order inside each group stays newest first)."""
    import config_manager

    records = [r for r in config_manager.get_recent_activity(limit=max(60, limit * 4)) if r.get("exists")]
    if three_d_first is None:
        three_d_first = three_d_app_open()
    if three_d_first:
        records.sort(key=lambda r: 0 if str(r.get("dest", "")).lower().endswith(THREE_D_SUFFIXES) else 1)
    return records[:limit]


def open_in_explorer(path: str | Path) -> None:
    """Show the file selected in its folder (a path with a comma is passed safely)."""
    target = os.path.abspath(str(path))
    if sys.platform == "win32":
        # Same form ribbon_popup uses: the path is one quoted argument, so spaces work.
        subprocess.Popen(f'explorer.exe /select,"{target}"')
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", target])


def copy_file(path: str | Path) -> bool:
    """Put the file itself on the clipboard, so Ctrl+V pastes it into Explorer or most apps."""
    import batch_handoff
    return bool(batch_handoff.copy_files_to_clipboard([str(path)]))


def drag_file(path: str | Path, hwnd: int | None = None) -> int:
    """Start a native file drag (the drop target gets the real file). Returns 0 when cancelled."""
    import batch_handoff
    return int(batch_handoff.start_file_drag([str(path)], hwnd=hwnd) or 0)


def _build_shelf_window(files: list[dict] | None = None):
    """Build the Recent Files Shelf Qt window using the shared Download Sorter suite theme."""
    from PySide6.QtCore import QEvent, QObject, Qt
    from PySide6.QtGui import QKeySequence, QShortcut
    from PySide6.QtWidgets import (
        QAbstractItemView,
        QApplication,
        QHBoxLayout,
        QListWidget,
        QListWidgetItem,
        QVBoxLayout,
        QWidget,
    )
    import ui_style

    records = list(files) if files is not None else recent_files()

    class RecentFilesShelfWindow(QWidget):
        """Topmost recent-files shelf supporting explorer reveal, copy, and Win32 drag-out."""

        def __init__(self, items: list[dict], parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self._files = list(items)
            self._drag_index: int | None = None
            self._drag_origin = None
            self._drag_started = False

            self.setWindowTitle("Recent files")
            self.resize(560, 420)
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
            self.setStyleSheet(ui_style.stylesheet())

            root_layout = QVBoxLayout(self)
            root_layout.setContentsMargins(16, 14, 16, 14)
            root_layout.setSpacing(10)

            self.heading = ui_style.label(
                "Recently filed: drag a file out, double-click to show it, or Ctrl+C to copy",
                role="muted",
                wrap=True,
                parent=self,
            )
            root_layout.addWidget(self.heading)

            self.listbox = QListWidget(self)
            self.listbox.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            for rec in self._files:
                item = QListWidgetItem(f"{rec.get('filename', '')}    ({rec.get('folder', '')})")
                item.setToolTip(str(rec.get("dest", "")))
                self.listbox.addItem(item)
            if not self._files:
                empty = QListWidgetItem("Nothing has been filed yet.")
                empty.setFlags(Qt.ItemFlag.NoItemFlags)
                self.listbox.addItem(empty)
            root_layout.addWidget(self.listbox, 1)

            btn_row = QHBoxLayout()
            btn_row.setContentsMargins(0, 4, 0, 0)
            btn_row.setSpacing(8)
            self.show_btn = ui_style.button("Show in Explorer", role="primary", parent=self)
            self.copy_btn = ui_style.button("Copy file", parent=self)
            self.close_btn = ui_style.button("Close", role="ghost", parent=self)
            btn_row.addWidget(self.show_btn)
            btn_row.addWidget(self.copy_btn)
            btn_row.addStretch(1)
            btn_row.addWidget(self.close_btn)
            root_layout.addLayout(btn_row)

            self.show_btn.clicked.connect(self.show_selected)
            self.copy_btn.clicked.connect(self.copy_selected)
            self.close_btn.clicked.connect(self.close)
            self.listbox.itemDoubleClicked.connect(lambda _item: self.show_selected())

            self._shortcut_return = QShortcut(QKeySequence(Qt.Key.Key_Return), self.listbox)
            self._shortcut_return.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            self._shortcut_return.activated.connect(self.show_selected)

            self._shortcut_enter = QShortcut(QKeySequence(Qt.Key.Key_Enter), self.listbox)
            self._shortcut_enter.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            self._shortcut_enter.activated.connect(self.show_selected)

            self._shortcut_copy = QShortcut(QKeySequence.StandardKey.Copy, self.listbox)
            self._shortcut_copy.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            self._shortcut_copy.activated.connect(self.copy_selected)

            self._shortcut_escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
            self._shortcut_escape.activated.connect(self.close)

            class _DragFilter(QObject):
                def __init__(self, owner: RecentFilesShelfWindow) -> None:
                    super().__init__(owner)
                    self._owner = owner

                def eventFilter(self, _obj: QObject, event: QEvent) -> bool:
                    etype = event.type()
                    if etype == QEvent.Type.MouseButtonPress and getattr(event, "button", lambda: None)() == Qt.MouseButton.LeftButton:
                        pos = event.position().toPoint()
                        item = self._owner.listbox.itemAt(pos)
                        row = self._owner.listbox.row(item) if item is not None else -1
                        self._owner._drag_index = row if 0 <= row < len(self._owner._files) else None
                        self._owner._drag_origin = pos
                        self._owner._drag_started = False
                    elif etype == QEvent.Type.MouseMove and getattr(event, "buttons", lambda: Qt.MouseButton.NoButton)() & Qt.MouseButton.LeftButton:
                        idx = self._owner._drag_index
                        if not self._owner._drag_started and idx is not None and 0 <= idx < len(self._owner._files):
                            if self._owner._drag_origin is None or (event.position().toPoint() - self._owner._drag_origin).manhattanLength() >= QApplication.startDragDistance():
                                self._owner._drag_started = True
                                try:
                                    drag_file(self._owner._files[idx]["dest"], hwnd=int(self._owner.winId()))
                                except Exception:
                                    logging.exception("Could not start the file drag")
                    elif etype == QEvent.Type.MouseButtonRelease:
                        self._owner._drag_index = None
                        self._owner._drag_origin = None
                        self._owner._drag_started = False
                    return False

            self._drag_filter = _DragFilter(self)
            self.listbox.viewport().installEventFilter(self._drag_filter)

            if self._files:
                self.listbox.setCurrentRow(0)
                self.listbox.setFocus()
            else:
                self.show_btn.setEnabled(False)
                self.copy_btn.setEnabled(False)

        def chosen(self) -> dict | None:
            row = self.listbox.currentRow()
            if row < 0 or row >= len(self._files):
                return None
            return self._files[row]

        def show_selected(self) -> None:
            rec = self.chosen()
            if rec:
                try:
                    open_in_explorer(rec["dest"])
                except OSError as exc:
                    logging.warning("Could not show %s: %s", rec.get("dest"), exc)

        def copy_selected(self) -> None:
            rec = self.chosen()
            if rec:
                copy_file(rec["dest"])

    return RecentFilesShelfWindow(records)


def main(argv: list[str] | None = None) -> int:
    import sys
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication

    try:
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except Exception:
        pass

    app = QApplication.instance()
    owns_app = False
    if app is None:
        app = QApplication(sys.argv if argv is None else ["shelf", *argv])
        owns_app = True

    window = _build_shelf_window()
    window.show()
    if owns_app:
        return int(app.exec())
    return 0
