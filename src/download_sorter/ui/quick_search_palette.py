"""Keyboard-first Global Quick Search & History Palette (Ctrl+Shift+Space)."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from download_sorter.db import StateDB
from download_sorter.ui.motion import spring_pop_in


def build_search_index(db: StateDB, watch_root: Path, *, limit: int = 300) -> list[dict[str, Any]]:
    """Collect deduplicated searchable entries from StateDB history and ``watch_root``."""
    entries: list[dict[str, Any]] = []
    seen_paths: set[str] = set()

    for move in db.list_active_moves(limit=limit):
        dest_str = str(move.get("dest_path") or "")
        if not dest_str:
            continue
        p = Path(dest_str)
        if not p.is_file():
            continue
        norm = str(p.resolve()).lower()
        if norm in seen_paths:
            continue
        seen_paths.add(norm)
        try:
            rel_folder = str(p.parent.relative_to(watch_root.resolve()))
        except ValueError:
            rel_folder = p.parent.name
        entries.append(
            {
                "name": p.name,
                "path": str(p),
                "folder": "." if rel_folder == "." else rel_folder.replace("\\", "/"),
                "rule": str(move.get("matched_rule") or ""),
                "source_url": str(move.get("source_url") or ""),
            }
        )

    if watch_root.is_dir():
        count = 0
        for dirpath, dirnames, filenames in os.walk(watch_root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            cur = Path(dirpath)
            for fname in filenames:
                if fname.startswith("."):
                    continue
                fp = cur / fname
                norm = str(fp.resolve()).lower()
                if norm in seen_paths:
                    continue
                seen_paths.add(norm)
                try:
                    rel_folder = str(cur.relative_to(watch_root.resolve()))
                except ValueError:
                    rel_folder = cur.name
                entries.append(
                    {
                        "name": fname,
                        "path": str(fp),
                        "folder": "Downloads" if rel_folder == "." else rel_folder.replace("\\", "/"),
                        "rule": "",
                        "source_url": "",
                    }
                )
                count += 1
                if count >= limit:
                    break
            if count >= limit:
                break

    return entries


def filter_search_entries(entries: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    """Filter searchable entries by whitespace-separated terms across name, folder, rule, and URL."""
    terms = [t for t in query.strip().lower().split() if t]
    if not terms:
        return entries[:40]
    matched: list[dict[str, Any]] = []
    for item in entries:
        haystack = " ".join(
            [
                item.get("name", ""),
                item.get("folder", ""),
                item.get("rule", ""),
                item.get("source_url", ""),
            ]
        ).lower()
        if all(t in haystack for t in terms):
            matched.append(item)
            if len(matched) >= 40:
                break
    return matched


class QuickSearchPalette(QDialog):
    """Floating dark-mode search palette for sorted files and download history."""

    def __init__(
        self,
        db: StateDB,
        watch_root: Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Quick Search — Download Sorter")
        self.setMinimumSize(560, 400)
        self.resize(600, 440)
        self._entries = build_search_index(db, watch_root)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search sorted files by name, folder, rule, or site...")
        self.search_input.textChanged.connect(self._refresh_results)
        root.addWidget(self.search_input)

        self.results_list = QListWidget()
        self.results_list.itemDoubleClicked.connect(lambda _item: self._open_selected(reveal=False))
        root.addWidget(self.results_list, 1)

        footer = QHBoxLayout()
        hint = QLabel("Enter: Open  •  Ctrl+Enter: Reveal in Explorer  •  Esc: Close")
        hint.setObjectName("MutedText")
        footer.addWidget(hint)
        footer.addStretch(1)

        btn_reveal = QPushButton("Reveal")
        btn_reveal.clicked.connect(lambda: self._open_selected(reveal=True))
        btn_open = QPushButton("Open")
        btn_open.setProperty("variant", "primary")
        btn_open.clicked.connect(lambda: self._open_selected(reveal=False))
        footer.addWidget(btn_reveal)
        footer.addWidget(btn_open)
        root.addLayout(footer)

        self._refresh_results("")
        self.search_input.setFocus()

    def showEvent(self, event) -> None:  # noqa: ANN001
        super().showEvent(event)
        spring_pop_in(self, duration_ms=200, offset_y=8)

    def _refresh_results(self, query: str) -> None:
        self.results_list.clear()
        for entry in filter_search_entries(self._entries, query):
            label = f"{entry['name']}   —   {entry['folder']}"
            if entry.get("rule"):
                label += f"  [{entry['rule']}]"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, entry["path"])
            item.setToolTip(entry["path"])
            self.results_list.addItem(item)
        if self.results_list.count() > 0:
            self.results_list.setCurrentRow(0)

    def _open_selected(self, *, reveal: bool) -> None:
        item = self.results_list.currentItem()
        if item is None:
            return
        target = Path(str(item.data(Qt.ItemDataRole.UserRole) or ""))
        if not target.exists():
            return
        if sys.platform == "win32":
            if reveal:
                subprocess.Popen(["explorer", "/select,", str(target)])
            else:
                os.startfile(str(target))  # type: ignore[attr-defined]
        self.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            reveal = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
            self._open_selected(reveal=reveal)
            return
        if event.key() == Qt.Key.Key_Down and self.search_input.hasFocus():
            row = min(self.results_list.count() - 1, self.results_list.currentRow() + 1)
            self.results_list.setCurrentRow(max(0, row))
            return
        if event.key() == Qt.Key.Key_Up and self.search_input.hasFocus():
            row = max(0, self.results_list.currentRow() - 1)
            self.results_list.setCurrentRow(row)
            return
        super().keyPressEvent(event)
