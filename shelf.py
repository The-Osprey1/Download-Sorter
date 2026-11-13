"""The shelf: find any file in the sorted folder, ready to pick from any app.

Ctrl+Shift+Space (or Settings / the tray menu) opens a small window with a search box. With
nothing typed it lists the files Download Sorter filed most recently; 3D files (.3mf, .stl,
.step) are listed first while a slicer or CAD app is open. Typing searches every file under
the sorted folder by name, folder, website and print details (see file_index.py):
"statement from:yourbank", "pla type:3mf", "in:banking".

Enter shows the file in its folder, Ctrl+Enter opens it, Ctrl+C copies it, and a row can be
dragged out into any app. The "..." menu (or a right-click) moves a file to another folder,
puts it back where it was downloaded, or sends it to a project.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

THREE_D_SUFFIXES = (".3mf", ".stl", ".step", ".stp", ".f3d", ".f3z", ".obj")
THREE_D_APPS = (
    "bambu-studio.exe", "bambu-studio-worker.exe", "orca-slicer.exe", "prusa-slicer.exe",
    "cura.exe", "fusion360.exe", "fusion.exe", "inventor.exe", "solidworks.exe",
)
SHELF_LIMIT = 15
SEARCH_LIMIT = 40
SEARCH_DELAY_MS = 90        # wait for a pause in typing before asking the file list
MAX_PROJECTS_IN_MENU = 6


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


def find_files(text: str, limit: int = SEARCH_LIMIT, three_d_first: bool | None = None) -> list[dict]:
    """Files matching ``text`` anywhere under the sorted folder, in the shape recent_files() gives."""
    import file_index

    if three_d_first is None:
        try:
            three_d_first = three_d_app_open()
        except Exception:
            three_d_first = False
    found = []
    for item in file_index.search(text, limit=limit, three_d_first=bool(three_d_first)):
        found.append({
            "filename": item["name"],
            "folder": item.get("folder") or "",
            "dest": item["path"],
            "when": item.get("when"),
            "site": item.get("site") or "",
            "why": item.get("why") or "",
            "rule": item.get("rule") or "",
            "extra": item.get("extra") or "",
            "is_dir": bool(item.get("is_dir")),
            "exists": True,
        })
    return found


def how_long_ago(when: float | str | None, now: float | None = None) -> str:
    """"just now", "5 min ago", "3 h ago", "yesterday", "12 days ago", then the date."""
    if when in (None, ""):
        return ""
    try:
        stamp = float(when) if isinstance(when, (int, float)) else datetime.fromisoformat(str(when)).timestamp()
    except (TypeError, ValueError):
        return ""
    seconds = max((time.time() if now is None else now) - stamp, 0.0)
    if seconds < 90:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)} h ago"
    days = int(seconds // 86400)
    if days == 1:
        return "yesterday"
    if days < 31:
        return f"{days} days ago"
    return datetime.fromtimestamp(stamp).strftime("%b %d, %Y").replace(" 0", " ")


def detail_line(record: dict) -> str:
    """The second line of a row: "Banking · 3 h ago · yourbank.com"."""
    parts = [str(record.get("folder") or "") or "Downloads",
             how_long_ago(record.get("when") or record.get("timestamp"))]
    if record.get("is_dir"):
        parts.append("project folder")
    if record.get("site"):
        parts.append(str(record["site"]))
    try:
        import prints
        row = prints.latest_for(str(record.get("filename", "")))
        if row:
            parts.append(prints.describe(row))
    except Exception:
        pass
    return " · ".join(p for p in parts if p)


def open_in_explorer(path: str | Path) -> None:
    """Show the file selected in its folder (a path with a comma is passed safely)."""
    target = os.path.abspath(str(path))
    if sys.platform == "win32":
        # Same form ribbon_popup uses: the path is one quoted argument, so spaces work.
        subprocess.Popen(f'explorer.exe /select,"{target}"')
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", target])


def open_file(path: str | Path) -> None:
    """Open the file with the app Windows uses for it."""
    if sys.platform == "win32":
        os.startfile(str(path))  # noqa: S606 - the user's own file, chosen by them
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])


def copy_file(path: str | Path) -> bool:
    """Put the file itself on the clipboard, so Ctrl+V pastes it into Explorer or most apps."""
    import batch_handoff
    return bool(batch_handoff.copy_files_to_clipboard([str(path)]))


def drag_file(path: str | Path, hwnd: int | None = None) -> int:
    """Start a native file drag (the drop target gets the real file). Returns 0 when cancelled."""
    import batch_handoff
    return int(batch_handoff.start_file_drag([str(path)], hwnd=hwnd) or 0)


def _history_record(record: dict) -> dict:
    """The history record of the move that put this file where it is, or just its path."""
    import sort_undo

    found = sort_undo.find_active_move(record["dest"])
    return found or {"dest": record["dest"]}


def move_to(record: dict, folder: str | Path) -> tuple[bool, str]:
    """Move a file to a folder the user picked (journaled; the learner hears about it)."""
    import config_manager
    import file_index
    import refile
    import sorter

    config = config_manager.load_sorter_config()
    root = sorter.watch_root(config)
    outcome = refile.move_to_folder(_history_record(record), folder, config, root)
    if outcome.ok and outcome.path is not None:
        file_index.note_move(record["dest"], outcome.path, root)
        if outcome.suggestion:
            refile.ask_about_suggestion(outcome.suggestion, config)
    return outcome.ok, outcome.message


def put_back(record: dict) -> tuple[bool, str]:
    """Put a filed file back where it was downloaded."""
    import config_manager
    import file_index
    import refile
    import sorter

    history = _history_record(record)
    if not history.get("src"):
        return False, f"{record.get('filename', 'That file')} wasn't filed by Download Sorter, so there is nowhere to put it back."
    root = sorter.watch_root(config_manager.load_sorter_config())
    outcome = refile.put_back(history, root)
    if outcome.ok:
        file_index.forget(record["dest"])
        file_index.note_file(history["src"], root)
    return outcome.ok, outcome.message


def send_to_project(record: dict, project: dict) -> tuple[bool, str]:
    """Move a file into a project from the Projects hub (journaled, so Undo brings it back)."""
    import file_index
    import projects_hub

    try:
        target = projects_hub.route_file(record["dest"], project)
    except Exception as exc:
        return False, f"Couldn't send {record.get('filename', 'that file')} to {project.get('name', 'the project')}: {exc}"
    file_index.forget(record["dest"])
    return True, f"Sent {target.name} to {project.get('name') or Path(project['path']).name}."


def _build_shelf_window(files: list[dict] | None = None):
    """Build the shelf window. ``files`` fixes what the empty search box lists (for tests and
    screenshots); left out, it lists the recently filed files and re-reads them when shown."""
    from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer
    from PySide6.QtGui import QKeySequence, QShortcut
    from PySide6.QtWidgets import (
        QAbstractItemView,
        QApplication,
        QFileDialog,
        QHBoxLayout,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMenu,
        QVBoxLayout,
        QWidget,
    )
    import ui_categories
    import ui_style
    import ui_widgets

    HINT = "Drag a file out, press Enter to show it, Ctrl+Enter to open it, or Ctrl+C to copy it"

    class RecentFilesShelfWindow(QWidget):
        """Topmost search-and-pick window: explorer reveal, open, copy, and Win32 drag-out."""

        def __init__(self, items: list[dict] | None, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self._fixed = list(items) if items is not None else None
            self._files: list[dict] = []
            self._drag_index: int | None = None
            self._drag_origin = None
            self._drag_started = False

            self.setWindowTitle("Find a file")
            self.resize(600, 480)
            self.setMinimumSize(420, 320)
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
            # A plain top-level window gets no background from the shared stylesheet (only dialogs
            # do), which left light text on the system's light grey. Give it the app's own.
            self.setObjectName("shelfWindow")
            self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            colors = ui_style.COLORS
            self.setStyleSheet(
                ui_style.stylesheet()
                + f"QWidget#shelfWindow {{ background: {colors['canvas']}; }}"
                + f"QListWidget::item:selected {{ background: {colors['surfaceHover']}; color: {colors['text']}; }}"
            )

            root_layout = QVBoxLayout(self)
            root_layout.setContentsMargins(16, 14, 16, 14)
            root_layout.setSpacing(10)

            self.search_box = QLineEdit(self)
            self.search_box.setPlaceholderText("Find a file: a name, a folder, from:website, type:pdf")
            self.search_box.setClearButtonEnabled(True)
            self.search_box.setAccessibleName("Find a file")
            root_layout.addWidget(self.search_box)

            self.heading = ui_style.label(HINT, role="muted", wrap=True, parent=self)
            root_layout.addWidget(self.heading)

            self.listbox = QListWidget(self)
            self.listbox.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            self.listbox.setIconSize(QSize(28, 28))
            self.listbox.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self.listbox.customContextMenuRequested.connect(self._show_context_menu)
            root_layout.addWidget(self.listbox, 1)

            btn_row = QHBoxLayout()
            btn_row.setContentsMargins(0, 4, 0, 0)
            btn_row.setSpacing(8)
            self.show_btn = ui_style.button("Show in Explorer", role="primary", parent=self)
            self.copy_btn = ui_style.button("Copy file", parent=self)
            self.more_btn = ui_widgets.RowMenuButton("More for this file", parent=self)
            self.more_btn.menu().aboutToShow.connect(lambda: self.more_btn.set_actions(self.menu_entries()))
            self.close_btn = ui_style.button("Close", role="ghost", parent=self)
            btn_row.addWidget(self.show_btn)
            btn_row.addWidget(self.copy_btn)
            btn_row.addWidget(self.more_btn)
            btn_row.addStretch(1)
            btn_row.addWidget(self.close_btn)
            root_layout.addLayout(btn_row)

            self.show_btn.clicked.connect(self.show_selected)
            self.copy_btn.clicked.connect(self.copy_selected)
            self.close_btn.clicked.connect(self.close)
            self.listbox.itemDoubleClicked.connect(lambda _item: self.show_selected())
            self.listbox.currentRowChanged.connect(lambda _row: self._sync_buttons())

            self._search_timer = QTimer(self)
            self._search_timer.setSingleShot(True)
            self._search_timer.setInterval(SEARCH_DELAY_MS)
            self._search_timer.timeout.connect(self.search_now)
            self.search_box.textChanged.connect(lambda _text: self._search_timer.start())
            self.search_box.returnPressed.connect(self.show_selected)

            def shortcut(keys, handler, widget=None) -> QShortcut:
                made = QShortcut(QKeySequence(keys), widget or self)
                made.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
                made.activated.connect(handler)
                return made

            self._shortcut_return = shortcut(Qt.Key.Key_Return, self.show_selected, self.listbox)
            self._shortcut_enter = shortcut(Qt.Key.Key_Enter, self.show_selected, self.listbox)
            self._shortcut_open = shortcut("Ctrl+Return", self.open_selected)
            self._shortcut_copy = shortcut(QKeySequence.StandardKey.Copy, self.copy_selected, self.listbox)
            self._shortcut_escape = shortcut(Qt.Key.Key_Escape, self.close)
            self._shortcut_find = shortcut("Ctrl+F", self.focus_search)

            class _Keys(QObject):
                """Up and Down in the search box move through the list, so typing never has to stop."""

                def __init__(self, owner: RecentFilesShelfWindow) -> None:
                    super().__init__(owner)
                    self._owner = owner

                def eventFilter(self, _obj: QObject, event: QEvent) -> bool:
                    if event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                        step = 1 if event.key() == Qt.Key.Key_Down else -1
                        self._owner.step(step)
                        return True
                    return False

            self._keys = _Keys(self)
            self.search_box.installEventFilter(self._keys)

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

            self.search_now()
            if self._files:
                self.listbox.setFocus()

        # ---- what is listed -------------------------------------------------
        def query(self) -> str:
            return self.search_box.text().strip()

        def search_now(self) -> None:
            """Fill the list for what is in the search box (the recent files when it is empty)."""
            self._search_timer.stop()
            text = self.query()
            try:
                if text:
                    files = find_files(text)
                elif self._fixed is not None:
                    files = list(self._fixed)
                else:
                    files = recent_files()
            except Exception:
                logging.exception("Could not list files for the shelf")
                files = []
            self._fill(files, text)

        def _fill(self, files: list[dict], text: str) -> None:
            self._files = files
            self.listbox.clear()
            for rec in files:
                name, folder = str(rec.get("filename", "")), str(rec.get("folder", ""))
                detail = detail_line(rec)
                item = QListWidgetItem(f"{name}\n{detail}")
                item.setToolTip(str(rec.get("dest", "")))
                item.setIcon(ui_widgets.category_tile_icon(ui_categories.kind_of(rec.get("dest") or name, folder)))
                self.listbox.addItem(item)
            if not files:
                empty = QListWidgetItem(f"No file matches “{text}”." if text else "Nothing has been filed yet.")
                empty.setFlags(Qt.ItemFlag.NoItemFlags)
                self.listbox.addItem(empty)
            else:
                self.listbox.setCurrentRow(0)
            self._sync_buttons()

        def _sync_buttons(self) -> None:
            has = self.chosen() is not None
            self.show_btn.setEnabled(has)
            self.copy_btn.setEnabled(has)
            self.more_btn.setEnabled(has)

        def step(self, by: int) -> None:
            if self._files:
                self.listbox.setCurrentRow(max(0, min(len(self._files) - 1, self.listbox.currentRow() + by)))

        def focus_search(self) -> None:
            self.search_box.setFocus()
            self.search_box.selectAll()

        def chosen(self) -> dict | None:
            row = self.listbox.currentRow()
            if row < 0 or row >= len(self._files):
                return None
            return self._files[row]

        # ---- what can be done with the chosen file ------------------------------
        def show_selected(self) -> None:
            rec = self.chosen()
            if rec:
                try:
                    open_in_explorer(rec["dest"])
                except OSError as exc:
                    logging.warning("Could not show %s: %s", rec.get("dest"), exc)

        def open_selected(self) -> None:
            rec = self.chosen()
            if rec:
                try:
                    open_file(rec["dest"])
                except OSError as exc:
                    self.say(f"Couldn't open {rec.get('filename')}: {exc.strerror or exc}")

        def copy_selected(self) -> None:
            rec = self.chosen()
            if rec:
                copy_file(rec["dest"])

        def copy_path(self) -> None:
            rec = self.chosen()
            if rec:
                QApplication.clipboard().setText(str(rec["dest"]))
                self.say("Copied the path.")

        def say(self, message: str) -> None:
            self.heading.setText(message or HINT)

        def _after(self, result: tuple[bool, str]) -> None:
            self.search_now()
            self.say(result[1])

        def move_selected(self) -> None:
            rec = self.chosen()
            if not rec:
                return
            folder = QFileDialog.getExistingDirectory(self, "Move to", str(Path(rec["dest"]).parent))
            if folder:
                self._after(move_to(rec, folder))

        def put_back_selected(self) -> None:
            rec = self.chosen()
            if rec:
                self._after(put_back(rec))

        def send_selected(self, project: dict) -> None:
            rec = self.chosen()
            if rec:
                self._after(send_to_project(rec, project))

        def menu_entries(self) -> list:
            """The "..." and right-click menu for the chosen file: (text, icon, callback, enabled) or None."""
            rec = self.chosen()
            if not rec:
                return []
            is_file = not rec.get("is_dir")
            entries: list = [
                ("Open", None, self.open_selected, True),
                ("Show in Explorer", "folder", self.show_selected, True),
                ("Copy file", None, self.copy_selected, is_file),
                ("Copy path", None, self.copy_path, True),
                None,
                ("Move to...", "move", self.move_selected, is_file),
                ("Put back where it was", None, self.put_back_selected, is_file and bool(rec.get("why") or rec.get("src"))),
            ]
            try:
                import projects_hub
                projects = projects_hub.route_targets()[:MAX_PROJECTS_IN_MENU]
            except Exception:
                projects = []
            if projects and is_file:
                entries.append(None)
                for project in projects:
                    name = project.get("name") or Path(project["path"]).name
                    entries.append((f"Send to project: {name}", None,
                                    lambda p=project: self.send_selected(p), True))
            return entries

        def _show_context_menu(self, pos) -> None:
            item = self.listbox.itemAt(pos)
            if item is None or self.listbox.row(item) >= len(self._files):
                return
            self.listbox.setCurrentItem(item)
            menu = QMenu(self)
            ui_widgets.fill_menu(menu, self.menu_entries())
            menu.exec(self.listbox.viewport().mapToGlobal(pos))

        # ---- shown again and again when it lives in the tray's process --------------
        def present(self) -> None:
            """Show the window fresh: an empty search box, the newest files, the keyboard in the box."""
            self.search_box.blockSignals(True)
            self.search_box.clear()
            self.search_box.blockSignals(False)
            self.say(HINT)
            self.search_now()
            self.show()
            self.raise_()
            self.activateWindow()
            self.search_box.setFocus()

    return RecentFilesShelfWindow(files)


_WINDOW = None


def show_in_process() -> None:
    """Show the shelf inside a process that already runs Qt (the tray), reusing one window, so
    the shortcut opens it at once instead of starting a new program each time."""
    global _WINDOW
    if _WINDOW is None:
        _WINDOW = _build_shelf_window()
    _WINDOW.present()


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
    window.search_box.setFocus()
    if owns_app:
        return int(app.exec())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
