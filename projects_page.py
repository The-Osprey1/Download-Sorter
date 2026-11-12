"""The Projects page in the Download Sorter window (RFC #1).

It lists project folders found under Downloads\\Extracted, Documents and Desktop (project_scan),
with their kind, whether they are active or stale, and their Git state. From here a project can be
added to the hub (a junction in "Projects Hub", see projects_hub), taken out of it, zipped as a
copy for archiving, or opened. Nothing here moves, renames or deletes a project.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

import ui_style

KIND_LABELS = {
    "software": "Software",
    "cad_cam": "CAD / CAM",
    "foundry": "Foundry",
    "electronics": "Electronics",
    "coursework": "Coursework",
}


def default_roots() -> list[Path]:
    import sorter
    profile = Path(os.environ.get("USERPROFILE") or Path.home())
    downloads = sorter.watch_root(sorter.load_config())
    return [downloads / "Extracted", profile / "Documents", profile / "Desktop"]


class _ScanWorker(QThread):
    finished_scan = Signal(object)

    def __init__(self, roots: list[Path]):
        super().__init__()
        self.roots = roots

    def run(self) -> None:
        import project_scan
        try:
            self.finished_scan.emit(project_scan.scan(self.roots))
        except Exception as exc:  # reported on the page, never raised in the thread
            self.finished_scan.emit(exc)


class ProjectsWidget(QFrame):
    # (text, kind, action text, action) for the window's message bar, like the Clean up page.
    notice = Signal(str, str, str, object)

    COLUMNS = ("Project", "Kind", "State", "Git", "In hub")

    def __init__(self, parent=None, roots: list[Path] | None = None):
        super().__init__(parent)
        self.setObjectName("projectsWidget")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._roots = roots
        self._projects: list[Any] = []
        self._worker: _ScanWorker | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        header, header_row = ui_style.page_header(
            "Projects",
            "Project folders in Extracted, Documents and Desktop. Add one to the hub to reach it from "
            "Projects Hub and to route downloads to it. Projects are never moved.",
        )
        self._scan_btn = ui_style.button("Look for projects", "primary", "search")
        self._scan_btn.clicked.connect(self.scan)
        header_row.addWidget(self._scan_btn, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(header)

        self._table = QTableWidget(0, len(self.COLUMNS))
        self._table.setHorizontalHeaderLabels(self.COLUMNS)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        head = self._table.horizontalHeader()
        head.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, len(self.COLUMNS)):
            head.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self._table.itemSelectionChanged.connect(self._update_buttons)
        layout.addWidget(self._table, 1)

        self._empty = ui_style.label("Click Look for projects to list the project folders on this PC.", "muted")
        layout.addWidget(self._empty)

        actions = QHBoxLayout()
        self._hub_btn = ui_style.button("Add to hub")
        self._hub_btn.clicked.connect(self._toggle_hub)
        self._zip_btn = ui_style.button("Zip a copy")
        self._zip_btn.clicked.connect(self._zip_copy)
        self._open_btn = ui_style.button("Open folder")
        self._open_btn.clicked.connect(self._open_folder)
        for btn in (self._hub_btn, self._zip_btn, self._open_btn):
            actions.addWidget(btn)
        actions.addStretch()
        layout.addLayout(actions)
        self._update_buttons()

    # ---- scanning ------------------------------------------------------
    def scan(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self._scan_btn.setEnabled(False)
        self._scan_btn.setText("Looking...")
        self._worker = _ScanWorker(self._roots or default_roots())
        self._worker.finished_scan.connect(self._on_scanned)
        self._worker.start()

    def _on_scanned(self, result: Any) -> None:
        self._scan_btn.setEnabled(True)
        self._scan_btn.setText("Look again")
        if isinstance(result, Exception):
            self.notice.emit(f"Couldn't look for projects: {result}", "warning", "", None)
            return
        self.show_projects(result)
        count = len(result)
        self.notice.emit(f"Found {count} project{'' if count == 1 else 's'}.", "success", "", None)

    def show_projects(self, projects: list[Any]) -> None:
        import projects_hub
        self._projects = list(projects)
        self._table.setRowCount(len(self._projects))
        for row, proj in enumerate(self._projects):
            git = "" if proj.git_clean is None else ("Clean" if proj.git_clean else "Changes")
            state = "Stale" if proj.stale else "Active"
            when = time.strftime("%Y-%m-%d", time.localtime(proj.last_active)) if proj.last_active else ""
            cells = (proj.path.name, KIND_LABELS.get(proj.kind, proj.kind), f"{state} ({when})" if when else state,
                     git, "Yes" if projects_hub.is_registered(proj.path) else "")
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col == 0:
                    item.setToolTip(str(proj.path))
                self._table.setItem(row, col, item)
        self._empty.setVisible(not self._projects)
        self._update_buttons()

    # ---- actions ---------------------------------------------------------
    def _selected(self) -> Any | None:
        rows = self._table.selectionModel().selectedRows() if self._table.selectionModel() else []
        if not rows or rows[0].row() >= len(self._projects):
            return None
        return self._projects[rows[0].row()]

    def _update_buttons(self) -> None:
        import projects_hub
        proj = self._selected()
        for btn in (self._hub_btn, self._zip_btn, self._open_btn):
            btn.setEnabled(proj is not None)
        in_hub = proj is not None and projects_hub.is_registered(proj.path)
        self._hub_btn.setText("Remove from hub" if in_hub else "Add to hub")

    def _toggle_hub(self) -> None:
        import projects_hub
        proj = self._selected()
        if proj is None:
            return
        try:
            if projects_hub.is_registered(proj.path):
                projects_hub.remove(proj.path)
                self.notice.emit(f"Removed {proj.path.name} from the hub. The project itself is untouched.",
                                 "success", "", None)
            else:
                entry = projects_hub.add(proj.path, proj.kind)
                self.notice.emit(f"Added {proj.path.name} to the hub ({entry.get('link', '')}).", "success", "", None)
        except OSError as exc:
            self.notice.emit(f"Couldn't change the hub: {exc}", "warning", "", None)
        self.show_projects(self._projects)

    def _zip_copy(self) -> None:
        import project_scan
        import sorter
        proj = self._selected()
        if proj is None:
            return
        archive_root = sorter.watch_root(sorter.load_config()) / "Projects" / "_Archive"
        try:
            out = project_scan.archive(proj.path, archive_root)
        except OSError as exc:
            self.notice.emit(f"Couldn't zip {proj.path.name}: {exc}", "warning", "", None)
            return
        self.notice.emit(f"Saved a zip copy: {out}. The project folder is unchanged.", "success", "", None)

    def _open_folder(self) -> None:
        proj = self._selected()
        if proj is None:
            return
        if sys.platform == "win32":
            os.startfile(str(proj.path))  # noqa: S606 - opens a folder the user picked
