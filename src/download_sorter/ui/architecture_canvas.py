"""Interactive direct-manipulation Folder Architecture canvas, template shelf, pills & dialogs."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QMimeData,
    QPoint,
    QPointF,
    QRectF,
    Qt,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QDrag,
    QFont,
    QFontMetrics,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from download_sorter.architecture import (
    KIND_COLORS,
    PREMADE_FOLDER_CATALOG,
    ArchNode,
    FolderArchitecture,
    _clean_exts,
    _clean_strs,
    apply_architecture_to_config,
    architecture_from_config,
    delete_architecture_snapshot,
    list_architecture_snapshots,
    load_architecture_snapshot,
    merge_architectures,
    save_architecture_snapshot,
    scan_folder_architecture,
)
from download_sorter.service import SorterService
from download_sorter.ui.motion import (
    CollapsibleRibbonBtn,
    ElidedLabel,
    InteractiveRibbonBtn,
    animations_enabled,
    make_vector_icon,
)
from download_sorter.ui.workers import run_async

MIME_TEMPLATE = "application/x-downloadsorter-template"

KIND_CHOICES: tuple[tuple[str, str], ...] = (
    ("Document", "doc"),
    ("PDF", "pdf"),
    ("3D Print", "print"),
    ("CAD", "cad"),
    ("CNC", "cnc"),
    ("Sheet", "sheet"),
    ("School", "school"),
    ("Career", "career"),
    ("Finance", "finance"),
    ("Image", "image"),
    ("Video", "video"),
    ("Audio", "audio"),
    ("Archive", "archive"),
    ("Installer", "installer"),
    ("Code", "code"),
    ("Design", "design"),
    ("Fonts", "fonts"),
)

BLUEPRINT_PILLS: tuple[tuple[str, str, str], ...] = (
    ("standard", "Standard", "Balanced 2-Tier Default"),
    ("basic_starter", "Starter", "Clean 2-Tier Foundation"),
    ("flat", "1-Tier", "Minimalist 1-Tier Flat Categories"),
    ("maker_pro", "Maker", "Maker & CAD Pro (3-Tier Workshop)"),
    ("student_pro", "Student", "Student & Career Pro (3-Tier Academic)"),
    ("creator_pro", "Creator", "Creator, Design & Finance (3-Tier Studio)"),
)


_PILL_CSS = """
        QPushButton#ArchPillBtn {
            background-color: #1C1C1E;
            color: #AEAEB2;
            border: 1px solid #2C2C2E;
            border-radius: 7px;
            padding: 3px 9px;
            font-size: 11.5px;
            font-weight: 600;
        }
        QPushButton#ArchPillBtn:hover {
            background-color: #262629;
            border-color: #3A3A3C;
            color: #F5F5F7;
        }
        QPushButton#ArchPillBtn:checked {
            background-color: rgba(10, 132, 255, 42);
            border: 1px solid #0A84FF;
            color: #FFFFFF;
        }
        QPushButton#ArchPillBtn:disabled {
            background-color: #161618;
            color: #48484A;
            border-color: #222225;
        }
        """


def _make_pill_button(
    text: str, *, checkable: bool = True, tooltip: str = "", extra_css: str = ""
) -> QPushButton:
    btn = QPushButton(text)
    btn.setObjectName("ArchPillBtn")
    btn.setCheckable(checkable)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    btn.setStyleSheet(_PILL_CSS + extra_css)
    if tooltip:
        btn.setToolTip(tooltip)
    return btn


class PremadeFolderDialog(QDialog):
    """Pick one or more premade folders from the 46-template catalog using visual pills."""

    def __init__(
        self,
        arch: FolderArchitecture,
        default_tier: int = 2,
        default_parent_id: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.arch = arch
        self._selected_tier = max(1, min(arch.tier_count, default_tier))
        self._preferred_parent_id = default_parent_id
        self.setWindowTitle("Add Premade Folders")
        self.resize(620, 520)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        hdr = QLabel("Choose from 45+ premade folders & smart packs to place into your tier layout:", self)
        hdr.setVisible(False)
        self.setToolTip("Choose premade folders & smart packs to add into your Download Sorter tier layout.")

        tier_row = QHBoxLayout()
        tier_row.setSpacing(6)
        tier_row.addWidget(QLabel("Target Tier:"))
        self._tier_btns: dict[int, QPushButton] = {}
        for t in range(1, arch.tier_count + 1):
            label = f"Tier {t}" + (" (Main)" if t == 1 else "")
            btn = _make_pill_button(label, checkable=True)
            btn.clicked.connect(lambda _chk, tier=t: self._select_tier(tier))
            self._tier_btns[t] = btn
            tier_row.addWidget(btn)
        tier_row.addStretch(1)
        root.addLayout(tier_row)

        parent_hdr = QLabel("Inside Parent Folder:")
        parent_hdr.setStyleSheet("color: #98989D; font-size: 11.5px;")
        root.addWidget(parent_hdr)

        self.list_parents = QListWidget()
        self.list_parents.setMaximumHeight(92)
        root.addWidget(self.list_parents)

        self.edit_search = QLineEdit()
        self.edit_search.setPlaceholderText("Filter premade folders (e.g. PDF, School, CAD, Invoice)...")
        self.edit_search.textChanged.connect(self._populate_list)
        root.addWidget(self.edit_search)

        self.list_widget = QListWidget()
        self.list_widget.setMinimumHeight(210)
        root.addWidget(self.list_widget, 1)

        footer = QHBoxLayout()
        self.chk_show_all = QCheckBox("Show premades from all tiers")
        self.chk_show_all.setChecked(True)
        self.chk_show_all.toggled.connect(self._populate_list)
        footer.addWidget(self.chk_show_all)
        footer.addStretch(1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn is not None:
            ok_btn.setText("Add Checked Folders")
            ok_btn.setObjectName("PrimaryButton")
            ok_btn.setToolTip("Add all checked premade folders into the selected tier and parent")
        cancel_btn = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_btn is not None:
            cancel_btn.setToolTip("Close without adding folders")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        footer.addWidget(buttons)
        root.addLayout(footer)

        self._select_tier(self._selected_tier)

    def _select_tier(self, tier: int) -> None:
        self._selected_tier = max(1, min(self.arch.tier_count, tier))
        for t, btn in self._tier_btns.items():
            btn.setChecked(t == self._selected_tier)
        self._refresh_parents(self._preferred_parent_id)
        self._populate_list()

    def _refresh_parents(self, preferred_id: str | None) -> None:
        tier = self._selected_tier
        self.list_parents.clear()
        if tier <= 1:
            it = QListWidgetItem("Downloads/ (Root)")
            it.setData(Qt.ItemDataRole.UserRole, None)
            self.list_parents.addItem(it)
            self.list_parents.setCurrentRow(0)
            self.list_parents.setEnabled(False)
            return
        self.list_parents.setEnabled(True)
        auto_it = QListWidgetItem("(Auto-match default parent)")
        auto_it.setData(Qt.ItemDataRole.UserRole, "")
        self.list_parents.addItem(auto_it)
        target_row = 0
        for pnode in self.arch.nodes_in_tier(tier - 1):
            it = QListWidgetItem(self.arch.node_relative_path(pnode.id))
            it.setData(Qt.ItemDataRole.UserRole, pnode.id)
            self.list_parents.addItem(it)
            if preferred_id and pnode.id == preferred_id:
                target_row = self.list_parents.count() - 1
        self.list_parents.setCurrentRow(target_row)

    def _populate_list(self) -> None:
        checked_keys = set(self.selected_keys())
        self.list_widget.clear()
        query = self.edit_search.text().strip().lower()
        target_tier = self._selected_tier
        show_all = self.chk_show_all.isChecked()

        for item in PREMADE_FOLDER_CATALOG:
            if not show_all and int(item["tier"]) != target_tier:
                continue
            hay = (
                f"{item['name']} {item['summary']} "
                f"{' '.join(item['extensions'])} {' '.join(item['keywords'])}"
            ).lower()
            if query and query not in hay:
                continue
            rule_tags: list[str] = []
            if item["extensions"]:
                rule_tags.append(
                    ", ".join(item["extensions"][:4])
                    + ("..." if len(item["extensions"]) > 4 else "")
                )
            if item["keywords"]:
                rule_tags.append(f"{len(item['keywords'])} keywords")
            if item["domains"]:
                rule_tags.append(f"{len(item['domains'])} sites")
            tag_str = " • ".join(rule_tags)
            li = QListWidgetItem(
                f"[Tier {item['tier']}]  {item['name']}  —  {item['summary']}  ({tag_str})"
            )
            li.setFlags(li.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            li.setCheckState(
                Qt.CheckState.Checked if item["key"] in checked_keys else Qt.CheckState.Unchecked
            )
            li.setData(Qt.ItemDataRole.UserRole, item["key"])
            self.list_widget.addItem(li)

    def selected_keys(self) -> list[str]:
        keys: list[str] = []
        for idx in range(self.list_widget.count()):
            li = self.list_widget.item(idx)
            if li.checkState() == Qt.CheckState.Checked:
                keys.append(str(li.data(Qt.ItemDataRole.UserRole)))
        return keys

    def target_tier(self) -> int:
        return self._selected_tier

    def target_parent_id(self) -> str | None:
        cur = self.list_parents.currentItem()
        if cur is None:
            return None
        val = cur.data(Qt.ItemDataRole.UserRole)
        return str(val) if val else None


class CustomFolderNodeDialog(QDialog):
    """Create or edit a Custom Folder node with clickable tier & category pills (zero dropdowns)."""

    def __init__(
        self,
        arch: FolderArchitecture,
        *,
        node: ArchNode | None = None,
        default_tier: int = 1,
        default_parent_id: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.arch = arch
        self.editing_node = node
        self._selected_tier = max(1, min(arch.tier_count, node.tier if node else default_tier))
        self._selected_kind = node.kind if node else "doc"
        self._preferred_parent_id = node.parent_id if node else default_parent_id
        self.setWindowTitle("Edit Folder & Rules" if node else "New Custom Folder & Rules")
        self.setMinimumWidth(540)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        form = QFormLayout()
        form.setVerticalSpacing(10)
        form.setHorizontalSpacing(14)

        self.edit_name = QLineEdit(node.name if node else "")
        self.edit_name.setPlaceholderText("e.g. Lab Reports, Invoices, Guitar Tabs")

        # Clickable Tier Pill Selector
        tier_wrap = QWidget()
        tier_row = QHBoxLayout(tier_wrap)
        tier_row.setContentsMargins(0, 0, 0, 0)
        tier_row.setSpacing(6)
        self._tier_btns: dict[int, QPushButton] = {}
        for t in range(1, arch.tier_count + 1):
            btn = _make_pill_button(f"Tier {t}", checkable=True)
            btn.clicked.connect(lambda _chk, tier=t: self._select_tier(tier))
            self._tier_btns[t] = btn
            tier_row.addWidget(btn)
        tier_row.addStretch(1)

        # Clickable Parent Folder List
        self.list_parents = QListWidget()
        self.list_parents.setMaximumHeight(92)

        # Clickable Category Color Pills
        kind_wrap = QWidget()
        kind_grid = QGridLayout(kind_wrap)
        kind_grid.setContentsMargins(0, 0, 0, 0)
        kind_grid.setSpacing(5)
        self._kind_btns: dict[str, QPushButton] = {}
        for idx, (label, k_key) in enumerate(KIND_CHOICES):
            col_hex = KIND_COLORS.get(k_key, KIND_COLORS["default"])
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                f"""
                QPushButton {{
                    background-color: #1C1C1E;
                    color: #AEAEB2;
                    border: 1px solid #2C2C2E;
                    border-left: 3px solid {col_hex};
                    border-radius: 6px;
                    padding: 3px 8px;
                    font-size: 11px;
                    font-weight: 600;
                    text-align: left;
                }}
                QPushButton:hover {{ background-color: #262629; color: #F5F5F7; }}
                QPushButton:checked {{
                    background-color: rgba(10, 132, 255, 40);
                    border: 1px solid {col_hex};
                    border-left: 4px solid {col_hex};
                    color: #FFFFFF;
                }}
                """
            )
            btn.clicked.connect(lambda _chk, k=k_key: self._select_kind(k))
            self._kind_btns[k_key] = btn
            kind_grid.addWidget(btn, idx // 4, idx % 4)

        self.edit_exts = QLineEdit(", ".join(node.extensions) if node else "")
        self.edit_exts.setPlaceholderText("e.g. .pdf, .docx, .zip (leave blank for any file type)")
        self.edit_exts.setToolTip("Comma-separated file extensions that belong in this folder (e.g. .pdf, .docx)")

        self.edit_keywords = QLineEdit(", ".join(node.keywords) if node else "")
        self.edit_keywords.setPlaceholderText("e.g. syllabus, lab_report, invoice (optional)")
        self.edit_keywords.setToolTip("Comma-separated filename keywords to match (e.g. invoice, syllabus)")

        self.edit_domains = QLineEdit(", ".join(node.domains) if node else "")
        self.edit_domains.setPlaceholderText("e.g. canvas.edu, makerworld.com (optional)")
        self.edit_domains.setToolTip("Comma-separated source website domains extracted from Windows Zone.Identifier")

        form.addRow("Folder Name:", self.edit_name)
        form.addRow("Layer / Tier:", tier_wrap)
        form.addRow("Parent Folder:", self.list_parents)
        form.addRow("Category Color:", kind_wrap)
        form.addRow("File Extensions:", self.edit_exts)
        form.addRow("Filename Keywords:", self.edit_keywords)
        form.addRow("Website Domains:", self.edit_domains)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        save_btn = buttons.button(QDialogButtonBox.StandardButton.Save)
        if save_btn is not None:
            save_btn.setObjectName("PrimaryButton")
            save_btn.setToolTip("Save folder name, placement, and sorting rules")
        cancel_btn = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_btn is not None:
            cancel_btn.setToolTip("Discard changes to this folder")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._select_kind(self._selected_kind)
        self._select_tier(self._selected_tier)

    def _select_kind(self, kind_key: str) -> None:
        self._selected_kind = kind_key
        for k, btn in self._kind_btns.items():
            btn.setChecked(k == kind_key)

    def _select_tier(self, tier: int) -> None:
        self._selected_tier = max(1, min(self.arch.tier_count, tier))
        for t, btn in self._tier_btns.items():
            btn.setChecked(t == self._selected_tier)
        self._populate_parents(self._preferred_parent_id)

    def _populate_parents(self, preferred_id: str | None) -> None:
        tier = self._selected_tier
        self.list_parents.clear()
        if tier <= 1:
            it = QListWidgetItem("Downloads/ (Top-Level)")
            it.setData(Qt.ItemDataRole.UserRole, None)
            self.list_parents.addItem(it)
            self.list_parents.setCurrentRow(0)
            self.list_parents.setEnabled(False)
            return
        self.list_parents.setEnabled(True)
        selected_row = 0
        for pnode in self.arch.nodes_in_tier(tier - 1):
            if self.editing_node and (
                pnode.id == self.editing_node.id
                or self.arch.is_descendant_of(pnode.id, self.editing_node.id)
            ):
                continue
            it = QListWidgetItem(self.arch.node_relative_path(pnode.id))
            it.setData(Qt.ItemDataRole.UserRole, pnode.id)
            self.list_parents.addItem(it)
            if preferred_id and pnode.id == preferred_id:
                selected_row = self.list_parents.count() - 1
        if self.list_parents.count() > 0:
            self.list_parents.setCurrentRow(selected_row)

    def _on_save(self) -> None:
        name = self.edit_name.text().strip().replace("/", "").replace("\\", "")
        if not name:
            QMessageBox.warning(self, "Missing Name", "Please enter a folder name.")
            return
        if self._selected_tier > 1 and self.list_parents.count() == 0:
            QMessageBox.warning(
                self,
                "Parent Required",
                f"Add at least one folder in Tier {self._selected_tier - 1} before placing a folder in Tier {self._selected_tier}.",
            )
            return
        self.accept()

    def values(self) -> dict[str, Any]:
        split = lambda s: [x.strip() for x in s.split(",") if x.strip()]
        cur_parent = self.list_parents.currentItem()
        parent_id = (
            str(cur_parent.data(Qt.ItemDataRole.UserRole))
            if (self._selected_tier > 1 and cur_parent and cur_parent.data(Qt.ItemDataRole.UserRole))
            else None
        )
        return {
            "name": self.edit_name.text().strip(),
            "tier": self._selected_tier,
            "parent_id": parent_id,
            "kind": self._selected_kind or "default",
            "extensions": split(self.edit_exts.text()),
            "keywords": split(self.edit_keywords.text()),
            "domains": split(self.edit_domains.text()),
        }


class ShiftConfirmDialog(QDialog):
    """Preview every file & folder shift before applying the new Folder Architecture."""

    def __init__(
        self,
        arch: FolderArchitecture,
        plan: dict[str, Any],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Confirm Folder Architecture & Shift Files")
        self.resize(740, 480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        planned = plan["moves_planned"]
        arch_folders = plan["architecture_folders"]

        title = QLabel(
            f"Ready to apply {arch.tier_count}-tier architecture ({len(arch_folders)} configured folders) "
            f"and shift {len(planned)} file(s)."
        )
        title.setStyleSheet("font-size: 14px; font-weight: 600; color: #F5F5F7;")
        title.setWordWrap(True)
        layout.addWidget(title)

        sub = QLabel(
            "All file shifts are logged in SQLite as one Undoable batch. Empty old managed folders are tidied automatically.",
            self,
        )
        sub.setVisible(False)
        self.setToolTip(
            "All file shifts are logged in SQLite as one Undoable batch, and empty managed folders are tidied automatically."
        )

        tbl = QTableWidget(len(planned), 3)
        tbl.setHorizontalHeaderLabels(["Current File Location", "New Expected Folder", "Matched By"])
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tbl.setAlternatingRowColors(True)
        tbl.verticalHeader().setVisible(False)
        hdr = tbl.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)

        for idx, item in enumerate(planned):
            tbl.setItem(idx, 0, QTableWidgetItem(item["src_rel"]))
            tbl.setItem(idx, 1, QTableWidgetItem(item["dest_rel"]))
            tbl.setItem(idx, 2, QTableWidgetItem(item["decision"].matched_rule))
        layout.addWidget(tbl, 1)

        if not planned:
            empty_note = QLabel(
                "0 existing files need moving right now — clicking OK will apply this layout for incoming downloads."
            )
            empty_note.setStyleSheet("color: #30D158; font-size: 12px;")
            layout.addWidget(empty_note)

        self.chk_create_empty = QCheckBox(
            "Also pre-create empty tier folders on disk right now"
        )
        self.chk_create_empty.setChecked(False)
        self.chk_create_empty.setToolTip("Create all configured tier folders immediately even if they are currently empty")
        layout.addWidget(self.chk_create_empty)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn is not None:
            ok_btn.setText(f"OK — Apply && Shift ({len(planned)})")
            ok_btn.setObjectName("PrimaryButton")
            ok_btn.setToolTip("Apply this layout, update sorting rules, and shift the listed files as one undoable batch")
        cancel_btn = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_btn is not None:
            cancel_btn.setToolTip("Return to canvas without applying or shifting files")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class _TemplateDragList(QListWidget):
    """Draggable list of premade folder templates with translucent Sequoia card ghost."""

    template_double_clicked = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSpacing(4)
        self.setStyleSheet(
            """
            QListWidget {
                background: transparent;
                border: none;
                outline: none;
            }
            QListWidget::item {
                background-color: #161618;
                color: #F5F5F7;
                border: 1px solid #2C2C2E;
                border-radius: 8px;
                padding: 6px 8px;
                font-size: 11.5px;
            }
            QListWidget::item:hover {
                background-color: #242428;
                border: 1px solid #0A84FF;
            }
            QListWidget::item:selected {
                background-color: #242834;
                border: 1px solid #0A84FF;
            }
            """
        )
        self.itemDoubleClicked.connect(self._on_double_click)

    def _on_double_click(self, item: QListWidgetItem) -> None:
        tpl = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(tpl, dict) and tpl.get("key"):
            self.template_double_clicked.emit(str(tpl["key"]))

    def startDrag(self, supportedActions) -> None:  # noqa: ANN001
        item = self.currentItem()
        if item is None:
            return
        tpl = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(tpl, dict):
            return

        payload = {
            "key": tpl["key"],
            "name": tpl["name"],
            "tier": tpl["tier"],
            "kind": tpl["kind"],
            "extensions": tpl["extensions"],
        }
        mime = QMimeData()
        mime.setData(MIME_TEMPLATE, json.dumps(payload).encode("utf-8"))

        color_hex = KIND_COLORS.get(str(tpl.get("kind", "default")), "#0A84FF")
        ghost = QPixmap(186, 58)
        ghost.fill(Qt.GlobalColor.transparent)
        p = QPainter(ghost)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setBrush(QColor(28, 28, 30, 232))
        p.setPen(QPen(QColor(color_hex), 1.8))
        p.drawRoundedRect(2, 2, 182, 54, 10, 10)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color_hex))
        p.drawRoundedRect(4, 11, 3.5, 36, 1.5, 1.5)

        p.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        p.setPen(QColor("#F5F5F7"))
        fm = QFontMetrics(p.font())
        p.drawText(
            QRectF(14, 9, 160, 20),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            fm.elidedText(str(tpl["name"]), Qt.TextElideMode.ElideRight, 158),
        )
        exts = ", ".join(tpl.get("extensions", [])[:3]) or f"Tier {tpl['tier']} pack"
        p.setFont(QFont("Segoe UI", 8))
        p.setPen(QColor("#8E8E93"))
        p.drawText(
            QRectF(14, 30, 160, 18),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            QFontMetrics(p.font()).elidedText(exts, Qt.TextElideMode.ElideRight, 158),
        )
        p.end()

        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(ghost)
        drag.setHotSpot(QPoint(93, 29))
        drag.exec(Qt.DropAction.CopyAction)


class TemplateShelfWidget(QFrame):
    """Collapsible left-hand Sequoia shelf of draggable premade folder chips."""

    template_activated = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SurfaceCard")
        self._collapsed = False
        self._tier_filter = "all"
        self.setFixedWidth(168)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        hdr = QHBoxLayout()
        hdr.setSpacing(4)
        self._lbl_title = QLabel("TEMPLATES")
        self._lbl_title.setStyleSheet(
            "color: #8E8E93; font-size: 10px; font-weight: 700; letter-spacing: 0.7px;"
        )
        hdr.addWidget(self._lbl_title, 1)

        self.btn_toggle = QPushButton("")
        self.btn_toggle.setIcon(make_vector_icon("arrow_left", "#98989D", 12))
        self.btn_toggle.setFixedSize(24, 24)
        self.btn_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_toggle.setToolTip("Collapse or expand Template Shelf")
        self.btn_toggle.setStyleSheet(
            "QPushButton { background: transparent; border: 1px solid #2C2C2E; border-radius: 6px; padding: 0; }"
            "QPushButton:hover { background: #262629; border-color: #48484A; }"
        )
        self.btn_toggle.clicked.connect(self.toggle_collapsed)
        hdr.addWidget(self.btn_toggle)
        outer.addLayout(hdr)

        self._body = QWidget()
        bl = QVBoxLayout(self._body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(6)

        self.edit_search = QLineEdit()
        self.edit_search.setPlaceholderText("Filter...")
        self.edit_search.setFixedHeight(26)
        self.edit_search.setStyleSheet(
            "QLineEdit { background: #141416; border: 1px solid #2C2C2E; border-radius: 6px; padding: 2px 7px; font-size: 11.5px; }"
            "QLineEdit:focus { border-color: #0A84FF; }"
        )
        self.edit_search.textChanged.connect(self._populate)
        bl.addWidget(self.edit_search)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(4)
        self._filter_btns: dict[str, QPushButton] = {}
        for fkey, label in (("all", "All"), ("t1", "T1"), ("t2", "T2+")):
            # Styled in one go: reading a style back and setting it again would scale
            # its font sizes twice when Windows text is bigger (see ui/text_scale.py).
            b = _make_pill_button(
                label,
                checkable=True,
                extra_css="QPushButton#ArchPillBtn { padding: 2px 8px; font-size: 10.5px; }",
            )
            b.clicked.connect(lambda _c, k=fkey: self._set_tier_filter(k))
            self._filter_btns[fkey] = b
            filter_row.addWidget(b)
        filter_row.addStretch(1)
        bl.addLayout(filter_row)

        self.list_templates = _TemplateDragList()
        self.list_templates.template_double_clicked.connect(self.template_activated.emit)
        bl.addWidget(self.list_templates, 1)

        tip = QLabel("Drag onto canvas or card", self)
        tip.setVisible(False)
        shelf_tip = "Drag any template pack onto the canvas or a folder card (or double-click) to insert it"
        self.setToolTip(shelf_tip)
        self.list_templates.setToolTip(shelf_tip)

        outer.addWidget(self._body, 1)
        self._set_tier_filter("all")

    def toggle_collapsed(self) -> None:
        self._collapsed = not self._collapsed
        self._body.setVisible(not self._collapsed)
        self._lbl_title.setVisible(not self._collapsed)
        if self._collapsed:
            self.setFixedWidth(40)
            self.btn_toggle.setIcon(make_vector_icon("arrow_right", "#98989D", 12))
        else:
            self.setFixedWidth(168)
            self.btn_toggle.setIcon(make_vector_icon("arrow_left", "#98989D", 12))

    def _set_tier_filter(self, fkey: str) -> None:
        self._tier_filter = fkey
        for k, b in self._filter_btns.items():
            b.setChecked(k == fkey)
        self._populate()

    def _populate(self) -> None:
        self.list_templates.clear()
        query = self.edit_search.text().strip().lower()
        for item in PREMADE_FOLDER_CATALOG:
            tier = int(item["tier"])
            if self._tier_filter == "t1" and tier != 1:
                continue
            if self._tier_filter == "t2" and tier < 2:
                continue
            hay = (
                f"{item['name']} {item['summary']} "
                f"{' '.join(item['extensions'])} {' '.join(item['keywords'])}"
            ).lower()
            if query and query not in hay:
                continue
            exts = ", ".join(item["extensions"][:2])
            if len(item["extensions"]) > 2:
                exts += "+"
            sub = exts or f"Tier {tier}"
            li = QListWidgetItem(f"{item['name']}\n{sub}")
            li.setToolTip(f"Drag to add '{item['name']}' (Tier {tier})\n{item['summary']}")
            li.setData(Qt.ItemDataRole.UserRole, item)
            self.list_templates.addItem(li)


class FolderArchitectureCanvas(QWidget):
    """Interactive direct-manipulation QPainter multi-tier folder architecture graph."""

    node_selected = Signal(str)
    node_edit_requested = Signal(str)
    node_delete_requested = Signal(str)
    tier_add_requested = Signal(int)
    node_reparent_requested = Signal(str, str)  # (node_id, new_parent_id_or_empty)
    node_spawn_child_requested = Signal(str, str)  # (parent_id_or_empty, default_name)
    node_renamed = Signal(str, str)  # (node_id, new_name)
    template_dropped = Signal(str, int, str)  # (premade_key, target_tier, parent_id_or_empty)
    zoom_changed = Signal(float)

    MARGIN_X = 16
    MARGIN_Y = 14
    TIER_GAP = 34
    HEADER_H = 30
    CARD_W = 166
    CARD_H = 54
    CARD_GAP_X = 12
    CARD_GAP_Y = 12
    BAND_PAD_X = 12
    BAND_PAD_BOTTOM = 14

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._arch = FolderArchitecture()
        self._watch_label = "Downloads/"
        self._zoom = 1.0
        self._show_all_connectors = False
        self._selected_id: str | None = None
        self._hovered_id: str | None = None
        self._hovered_delete_id: str | None = None
        self._hovered_plus_id: str | None = None
        self._hovered_tier_add: int | None = None

        self._node_rects: dict[str, QRectF] = {}
        self._rendered_rects: dict[str, QRectF] = {}
        self._anim_from_rects: dict[str, QRectF] = {}
        self._delete_rects: dict[str, QRectF] = {}
        self._plus_rects: dict[str, QRectF] = {}
        self._tier_add_rects: dict[int, QRectF] = {}
        self._tier_band_rects: dict[int, QRectF] = {}

        # Drag-and-drop card reparenting state
        self._press_node_id: str | None = None
        self._press_pt = QPointF()
        self._dragging_id: str | None = None
        self._drag_offset = QPointF()
        self._drop_target_id: str | None = None
        self._drop_state: str | None = None  # None | "valid" | "invalid"
        self._drop_promote_tier1 = False

        # Middle-mouse / Alt+drag panning inside parent QScrollArea
        self._panning = False
        self._pan_last_global = QPoint()

        # Drag-to-spawn '+' handle state
        self._spawning_from_id: str | None = None
        self._spawn_press_pt = QPointF()
        self._spawn_cur_pt = QPointF()
        self._spawn_dragging = False
        self._spawn_hover_node_id: str | None = None
        self._spawn_hover_valid = False

        # External template drag-over state
        self._ext_drop_node_id: str | None = None
        self._ext_drop_valid = False
        self._ext_drop_band: int | None = None

        # Layout OutCubic transition
        self._layout_anim = QVariantAnimation(self)
        self._layout_anim.setDuration(220)
        self._layout_anim.setStartValue(0.0)
        self._layout_anim.setEndValue(1.0)
        self._layout_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._layout_anim.valueChanged.connect(self._on_layout_anim_tick)
        self._layout_anim.finished.connect(self._on_layout_anim_done)

        # Inline double-click rename editor
        self._editing_node_id: str | None = None
        self._inline_cancel = False
        self._inline_edit = QLineEdit(self)
        self._inline_edit.hide()
        self._inline_edit.setStyleSheet(
            """
            QLineEdit {
                background-color: #111113;
                color: #F5F5F7;
                border: 1px solid #0A84FF;
                border-radius: 5px;
                padding: 1px 5px;
                font-size: 11.5px;
                font-weight: 600;
            }
            """
        )
        self._inline_edit.installEventFilter(self)
        self._inline_edit.editingFinished.connect(self._commit_inline_rename)

        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self._recompute_layout()

    @property
    def zoom(self) -> float:
        return self._zoom

    def set_zoom(self, factor: float) -> None:
        clamped = max(0.65, min(1.45, float(factor)))
        if abs(clamped - self._zoom) < 0.01:
            return
        self._zoom = clamped
        self._recompute_layout(update_min_size=True)
        self.zoom_changed.emit(self._zoom)
        self.update()

    def snapshot_rects(self) -> dict[str, QRectF]:
        return {k: QRectF(v) for k, v in self._rendered_rects.items()}

    def set_architecture(
        self,
        arch: FolderArchitecture,
        watch_label: str = "Downloads/",
        *,
        animate: bool = False,
        prev_rects: dict[str, QRectF] | None = None,
    ) -> None:
        self._arch = arch
        self._watch_label = watch_label
        if self._selected_id and arch.get_node(self._selected_id) is None:
            self._selected_id = None
        if not self._selected_id and arch.nodes:
            self._selected_id = arch.nodes[0].id
        self._recompute_layout(update_min_size=True)

        should_anim = (
            animate
            and prev_rects
            and animations_enabled()
            and not os.environ.get("PYTEST_CURRENT_TEST")
        )
        if should_anim:
            self._anim_from_rects = dict(prev_rects or {})
            self._layout_anim.stop()
            self._layout_anim.start()
        else:
            self._layout_anim.stop()
            self._rendered_rects = {k: QRectF(v) for k, v in self._node_rects.items()}
        self.update()

    def _on_layout_anim_tick(self, value: Any) -> None:
        t = float(value)
        blended: dict[str, QRectF] = {}
        for nid, target in self._node_rects.items():
            start = self._anim_from_rects.get(nid, target)
            blended[nid] = QRectF(
                start.x() + (target.x() - start.x()) * t,
                start.y() + (target.y() - start.y()) * t,
                start.width() + (target.width() - start.width()) * t,
                start.height() + (target.height() - start.height()) * t,
            )
        self._rendered_rects = blended
        self.update()

    def _on_layout_anim_done(self) -> None:
        self._rendered_rects = {k: QRectF(v) for k, v in self._node_rects.items()}
        self.update()

    def resizeEvent(self, event) -> None:  # noqa: ANN001
        super().resizeEvent(event)
        self._recompute_layout(update_min_size=True)

    @property
    def selected_node_id(self) -> str | None:
        return self._selected_id

    def select_node(self, node_id: str | None) -> None:
        self._selected_id = node_id
        self.update()
        if node_id:
            self.node_selected.emit(node_id)

    def start_inline_rename(self, node_id: str) -> None:
        node = self._arch.get_node(node_id)
        rect = self._rendered_rects.get(node_id) or self._node_rects.get(node_id)
        if node is None or rect is None:
            return
        self._editing_node_id = node_id
        self._inline_cancel = False
        self._inline_edit.setText(node.name)
        sx = (rect.left() + 9) * self._zoom
        sy = (rect.top() + 5) * self._zoom
        sw = max(80.0, (rect.width() - 32) * self._zoom)
        sh = 22.0 * self._zoom
        self._inline_edit.setGeometry(int(sx), int(sy), int(sw), int(sh))
        self._inline_edit.show()
        self._inline_edit.setFocus()
        self._inline_edit.selectAll()
        self.update()

    def _commit_inline_rename(self) -> None:
        if not self._inline_edit.isVisible():
            return
        nid = self._editing_node_id
        self._editing_node_id = None
        new_name = self._inline_edit.text().strip().replace("/", "").replace("\\", "")
        self._inline_edit.hide()
        if self._inline_cancel or not nid or not new_name:
            self._inline_cancel = False
            self.update()
            return
        node = self._arch.get_node(nid)
        if node is not None and new_name != node.name:
            self.node_renamed.emit(nid, new_name)
        self.update()

    def eventFilter(self, obj, event) -> bool:  # noqa: ANN001
        if obj is self._inline_edit and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                self._inline_cancel = True
                self._inline_edit.hide()
                self._editing_node_id = None
                self.update()
                return True
        return super().eventFilter(obj, event)

    def show_all_connectors(self) -> bool:
        return bool(self._show_all_connectors)

    def set_show_all_connectors(self, show_all: bool) -> None:
        self._show_all_connectors = bool(show_all)
        self.update()

    def _active_lineage_ids(self) -> tuple[str | None, set[str]]:
        focus_id = self._dragging_id or self._hovered_id or self._selected_id
        if not focus_id or focus_id == "__root__":
            return None, set()
        ancestors: set[str] = set()
        cur = self._arch.get_node(focus_id)
        guard = 0
        while cur is not None and guard < FolderArchitecture.MAX_TIERS + 2:
            ancestors.add(cur.id)
            if not cur.parent_id:
                break
            cur = self._arch.get_node(cur.parent_id)
            guard += 1
        return focus_id, ancestors

    def _node_tooltip(self, node: ArchNode) -> str:
        rel = self._arch.node_relative_path(node.id)
        lines = [f"{node.name} (Tier {node.tier})", f"Path: Downloads\\{rel}"]
        if node.extensions:
            lines.append("Extensions: " + ", ".join(node.extensions))
        if node.keywords:
            lines.append("Keywords: " + ", ".join(node.keywords))
        if node.domains:
            lines.append("Websites: " + ", ".join(node.domains))
        child_count = len(self._arch.children_of(node.id))
        if child_count:
            lines.append(f"Subfolders: {child_count}")
        lines.append("Drag card to reparent · Double-click title to rename · Double-click card for rules")
        return "\n".join(lines)

    def _ordered_tier_nodes(self) -> dict[int, list[ArchNode]]:
        ordered: dict[int, list[ArchNode]] = {}
        t1 = list(self._arch.nodes_in_tier(1))
        # Place leaf folders first and parent folders on the bottom row directly above the Tier 1->2 gutter
        t1.sort(key=lambda n: 1 if self._arch.children_of(n.id) else 0)
        ordered[1] = t1
        for t in range(2, self._arch.tier_count + 1):
            parent_order = {n.id: idx for idx, n in enumerate(ordered.get(t - 1, []))}
            nodes_t = list(self._arch.nodes_in_tier(t))
            if t < self._arch.tier_count:
                nodes_t.sort(
                    key=lambda n: (
                        1 if self._arch.children_of(n.id) else 0,
                        parent_order.get(n.parent_id or "", 999),
                        n.name.lower(),
                    )
                )
            else:
                nodes_t.sort(key=lambda n: (parent_order.get(n.parent_id or "", 999), n.name.lower()))
            ordered[t] = nodes_t
        return ordered

    def _recompute_layout(self, *, update_min_size: bool = True) -> None:
        self._node_rects.clear()
        self._delete_rects.clear()
        self._plus_rects.clear()
        self._tier_add_rects.clear()
        self._tier_band_rects.clear()

        ordered = self._ordered_tier_nodes()
        parent_w = (self.parentWidget().width() - 4) if self.parentWidget() is not None else 740
        logical_w = max(380.0, max(self.width(), parent_w) / self._zoom)
        band_w = float(logical_w - self.MARGIN_X * 2)
        cols = max(
            1,
            int((band_w - self.BAND_PAD_X * 2 + self.CARD_GAP_X) // (self.CARD_W + self.CARD_GAP_X)),
        )

        # Tier 0 Root Horizontal Band (Top)
        band0_y = float(self.MARGIN_Y)
        band0_h = float(self.HEADER_H + self.CARD_H + self.BAND_PAD_BOTTOM)
        self._tier_band_rects[0] = QRectF(self.MARGIN_X, band0_y, band_w, band0_h)
        root_w = max(self.CARD_W, 210)
        x0 = self.MARGIN_X + (band_w - root_w) / 2.0
        y0 = band0_y + self.HEADER_H
        root_rect = QRectF(x0, y0, root_w, self.CARD_H)
        self._node_rects["__root__"] = root_rect
        self._plus_rects["__root__"] = QRectF(root_rect.center().x() - 9, root_rect.bottom() - 9, 18, 18)

        cur_band_y = band0_y + band0_h + self.TIER_GAP

        # Tiers 1..N Horizontal Bands stacked Top-to-Bottom
        for t in range(1, self._arch.tier_count + 1):
            cards = ordered.get(t, [])
            rows = max(1, math.ceil(len(cards) / cols))
            band_h = float(
                self.HEADER_H
                + rows * self.CARD_H
                + max(0, rows - 1) * self.CARD_GAP_Y
                + self.BAND_PAD_BOTTOM
            )
            self._tier_band_rects[t] = QRectF(self.MARGIN_X, cur_band_y, band_w, band_h)
            self._tier_add_rects[t] = QRectF(
                self.MARGIN_X + band_w - 68, cur_band_y + 4, 56, 22
            )

            for idx, node in enumerate(cards):
                r_idx = idx // cols
                c_idx = idx % cols
                row_count = min(cols, len(cards) - r_idx * cols)
                row_total_w = row_count * self.CARD_W + max(0, row_count - 1) * self.CARD_GAP_X
                row_start_x = self.MARGIN_X + (band_w - row_total_w) / 2.0
                cx = row_start_x + c_idx * (self.CARD_W + self.CARD_GAP_X)
                cy = cur_band_y + self.HEADER_H + r_idx * (self.CARD_H + self.CARD_GAP_Y)
                rect = QRectF(cx, cy, self.CARD_W, self.CARD_H)
                self._node_rects[node.id] = rect
                self._delete_rects[node.id] = QRectF(rect.right() - 20, rect.top() + 5, 16, 16)
                if node.tier < FolderArchitecture.MAX_TIERS:
                    self._plus_rects[node.id] = QRectF(rect.center().x() - 9, rect.bottom() - 9, 18, 18)

            cur_band_y += band_h + self.TIER_GAP

        if self._layout_anim.state() != QVariantAnimation.State.Running:
            self._rendered_rects = {k: QRectF(v) for k, v in self._node_rects.items()}

        total_h = max(340, int((cur_band_y - self.TIER_GAP + self.MARGIN_Y) * self._zoom))
        min_w = max(380, int(380 * self._zoom))
        if update_min_size and (self.minimumWidth() != min_w or self.minimumHeight() != total_h):
            self.setMinimumSize(min_w, total_h)

    def _display_rect(self, nid: str) -> QRectF | None:
        base = self._rendered_rects.get(nid) or self._node_rects.get(nid)
        if base is None:
            return None
        if nid == self._dragging_id:
            return base.translated(self._drag_offset)
        return base

    def _to_canvas(self, pt: QPointF) -> QPointF:
        return QPointF(pt.x() / self._zoom, pt.y() / self._zoom)

    def paintEvent(self, event) -> None:  # noqa: ANN001
        if self._layout_anim.state() != QVariantAnimation.State.Running:
            self._recompute_layout(update_min_size=False)

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Recessed canvas background
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#111113"))
        p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 12, 12)

        p.save()
        p.scale(self._zoom, self._zoom)
        logical_w = int(self.width() / self._zoom)
        logical_h = int(self.height() / self._zoom)

        # Subtle dot grid
        p.setPen(QColor(255, 255, 255, 16))
        for gx in range(20, logical_w, 26):
            for gy in range(20, logical_h, 26):
                p.drawPoint(gx, gy)

        # Horizontal Tier Bands Top-to-Bottom
        for t in range(0, self._arch.tier_count + 1):
            band_rect = self._tier_band_rects.get(t)
            if band_rect is None:
                continue
            promote_hi = (t == 1 and self._drop_promote_tier1) or (self._ext_drop_band == t)
            band_border = QColor("#30D158") if promote_hi else QColor("#232327")
            band_bg = QColor(48, 209, 88, 16) if promote_hi else QColor("#161618")
            p.setPen(QPen(band_border, 1.4 if promote_hi else 1.0))
            p.setBrush(band_bg)
            p.drawRoundedRect(band_rect, 11, 11)

            f_hdr = QFont("Segoe UI", 9, QFont.Weight.Bold)
            p.setFont(f_hdr)
            p.setPen(QColor("#0A84FF") if t == 0 else (QColor("#30D158") if promote_hi else QColor("#98989D")))
            if t == 0:
                title = "TIER 0 · ROOT FOLDER"
            else:
                count = len(self._arch.nodes_in_tier(t))
                title = f"TIER {t} · {count} FOLDER{'S' if count != 1 else ''}"
                if t == 1 and self._drop_promote_tier1:
                    title += "  ·  DROP TO PROMOTE TO TIER 1"
            p.drawText(
                QRectF(band_rect.left() + 14, band_rect.top() + 3, band_rect.width() - 92, 24),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                title,
            )

            if t >= 1 and t in self._tier_add_rects:
                add_rect = self._tier_add_rects[t]
                hov = self._hovered_tier_add == t
                p.setPen(QPen(QColor("#0A84FF" if hov else "#3A3A3C"), 1.0))
                p.setBrush(QColor(10, 132, 255, 45) if hov else QColor("#242426"))
                p.drawRoundedRect(add_rect, 6, 6)
                p.setPen(QColor("#F5F5F7"))
                p.drawText(add_rect, Qt.AlignmentFlag.AlignCenter, "+ Add")

        # Root Trunk (single vertical stem from Tier 0 Downloads to Tier 1 band)
        root_rect = self._display_rect("__root__")
        t1_band = self._tier_band_rects.get(1)
        if root_rect is not None and t1_band is not None and self._arch.nodes_in_tier(1):
            trunk_x = root_rect.center().x()
            p.setPen(QPen(QColor("#0A84FF"), 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(trunk_x, root_rect.bottom()), QPointF(trunk_x, t1_band.top()))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#0A84FF"))
            p.drawEllipse(QPointF(trunk_x, t1_band.top()), 3.2, 3.2)

        # Tier 2+ Orthogonal Bus Connectors (focused active lineage by default, or all when toggled)
        focus_id, ancestor_ids = self._active_lineage_ids()
        connector_items: list[tuple[bool, ArchNode, QRectF, QRectF]] = []
        for node in self._arch.nodes:
            if node.tier < 2 or node.tier > self._arch.tier_count:
                continue
            dst_rect = self._display_rect(node.id)
            if dst_rect is None:
                continue
            src_rect = self._display_rect(node.parent_id or "")
            if src_rect is None:
                continue
            is_hi = bool(
                focus_id is not None
                and (
                    node.id in ancestor_ids
                    or (node.parent_id is not None and node.parent_id == focus_id)
                )
            )
            if not self._show_all_connectors and not is_hi:
                continue
            connector_items.append((is_hi, node, src_rect, dst_rect))

        # Assign distinct horizontal bus channels per (tier, parent) sorted by parent X
        tier_parent_lanes: dict[tuple[int, str], float] = {}
        parents_by_tier: dict[int, list[tuple[float, str]]] = {}
        seen_pid: set[tuple[int, str]] = set()
        for _hi, node, src_rect, _dst_rect in connector_items:
            pid = node.parent_id or ""
            key = (node.tier, pid)
            if key not in seen_pid:
                seen_pid.add(key)
                parents_by_tier.setdefault(node.tier, []).append((src_rect.center().x(), pid))
        for tier_num, plist in parents_by_tier.items():
            plist.sort(key=lambda pair: (pair[0], pair[1]))
            band_above = self._tier_band_rects.get(tier_num - 1)
            band_below = self._tier_band_rects.get(tier_num)
            if band_above is not None and band_below is not None:
                gutter_top = band_above.bottom() + 5.0
                gutter_bot = max(gutter_top + 4.0, band_below.top() - 5.0)
            else:
                gutter_top = 0.0
                gutter_bot = 0.0
            total_p = len(plist)
            for idx, (_px, pid) in enumerate(plist):
                frac = (idx + 1) / float(total_p + 1)
                tier_parent_lanes[(tier_num, pid)] = gutter_top + frac * (gutter_bot - gutter_top)

        for want_hi in (False, True):
            for is_hi, node, src_rect, dst_rect in connector_items:
                if is_hi != want_hi:
                    continue
                col = QColor(node.accent_hex)
                col.setAlpha(230 if is_hi else 72)
                p.setPen(
                    QPen(
                        col,
                        2.2 if is_hi else 1.25,
                        Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap,
                    )
                )
                p.setBrush(Qt.BrushStyle.NoBrush)
                p1 = QPointF(src_rect.center().x(), src_rect.bottom())
                p2 = QPointF(dst_rect.center().x(), dst_rect.top())
                bus_y = tier_parent_lanes.get((node.tier, node.parent_id or ""), (p1.y() + p2.y()) * 0.5)
                path = self._build_orthogonal_bus_path(p1, p2, bus_y)
                p.drawPath(path)

        # Live '+' Handle Drag-to-Spawn Dashed Wire
        if self._spawning_from_id and self._spawn_dragging:
            src_rect = self._display_rect(self._spawning_from_id)
            if src_rect is not None:
                p1 = QPointF(src_rect.center().x(), src_rect.bottom())
                p2 = self._spawn_cur_pt
                dy = max(24.0, abs(p2.y() - p1.y()) * 0.48)
                path = QPainterPath(p1)
                path.cubicTo(QPointF(p1.x(), p1.y() + dy), QPointF(p2.x(), p2.y() - dy), p2)
                pen = QPen(QColor("#0A84FF"), 2.2, Qt.PenStyle.DashLine, Qt.PenCapStyle.RoundCap)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(path)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor("#0A84FF"))
                p.drawEllipse(p2, 4.5, 4.5)

        # Tier 0 Root Card
        if root_rect is not None:
            self._paint_root_card(p, root_rect)

        # Tier 1..N Node Cards (draw non-dragged first, dragged card on top)
        ordered_nodes = [n for n in self._arch.nodes if n.id != self._dragging_id]
        if self._dragging_id:
            dragged_node = self._arch.get_node(self._dragging_id)
            if dragged_node is not None:
                ordered_nodes.append(dragged_node)

        for node in ordered_nodes:
            rect = self._display_rect(node.id)
            if rect is not None:
                self._paint_node_card(p, node, rect)

        p.restore()

    def _drop_halo_for(self, nid: str) -> str | None:
        if self._drop_target_id == nid and self._drop_state:
            return self._drop_state
        if self._ext_drop_node_id == nid:
            return "valid" if self._ext_drop_valid else "invalid"
        if self._spawn_hover_node_id == nid:
            return "valid" if self._spawn_hover_valid else "invalid"
        return None

    def _paint_root_card(self, p: QPainter, root_rect: QRectF) -> None:
        halo = self._drop_halo_for("__root__")
        if halo:
            hcol = QColor("#30D158" if halo == "valid" else "#FF453A")
            hcol.setAlpha(75)
            p.setPen(QPen(hcol, 6))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(root_rect.adjusted(-2, -2, 2, 2), 12, 12)

        border_col = QColor("#30D158" if halo == "valid" else ("#FF453A" if halo == "invalid" else "#0A84FF"))
        p.setPen(QPen(border_col, 1.8 if halo else 1.4))
        p.setBrush(QColor("#1C1C1E"))
        p.drawRoundedRect(root_rect, 10, 10)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#0A84FF"))
        p.drawRoundedRect(QRectF(root_rect.left() + 0.5, root_rect.top() + 10, 3.5, root_rect.height() - 20), 1.5, 1.5)

        p.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
        p.setPen(QColor("#F5F5F7"))
        p.drawText(
            root_rect.adjusted(14, 9, -56, -28),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "Downloads",
        )

        # ROOT badge
        badge_r = QRectF(root_rect.right() - 48, root_rect.top() + 10, 38, 16)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(10, 132, 255, 40))
        p.drawRoundedRect(badge_r, 5, 5)
        p.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        p.setPen(QColor("#64D2FF"))
        p.drawText(badge_r, Qt.AlignmentFlag.AlignCenter, "ROOT")

        p.setFont(QFont("Segoe UI", 8))
        p.setPen(QColor("#8E8E93"))
        fm = QFontMetrics(p.font())
        elided_watch = fm.elidedText(self._watch_label, Qt.TextElideMode.ElideMiddle, int(root_rect.width() - 26))
        p.drawText(
            root_rect.adjusted(14, 31, -10, -8),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            elided_watch,
        )

        # '+' Spawn handle on bottom center
        self._paint_plus_handle(p, "__root__", root_rect)

    def _paint_node_card(self, p: QPainter, node: ArchNode, rect: QRectF) -> None:
        selected = node.id == self._selected_id
        hovered = node.id == self._hovered_id
        is_dragging = node.id == self._dragging_id
        col = QColor(node.accent_hex)
        halo = self._drop_halo_for(node.id)

        if halo:
            hcol = QColor("#30D158" if halo == "valid" else "#FF453A")
            hcol.setAlpha(75)
            p.setPen(QPen(hcol, 6))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(rect.adjusted(-2, -2, 2, 2), 12, 12)
        elif selected or is_dragging:
            hcol = QColor(col)
            hcol.setAlpha(55)
            p.setPen(QPen(hcol, 4.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(rect.adjusted(-1, -1, 1, 1), 11, 11)

        if halo == "valid":
            border = QColor("#30D158")
        elif halo == "invalid":
            border = QColor("#FF453A")
        elif selected or is_dragging:
            border = col
        elif hovered:
            border = QColor("#48484A")
        else:
            border = QColor("#2C2C2E")

        p.setPen(QPen(border, 1.8 if (selected or halo or is_dragging) else 1.0))
        p.setBrush(QColor("#25252A") if (selected or is_dragging) else (QColor("#212125") if hovered else QColor("#1C1C1E")))
        p.drawRoundedRect(rect, 10, 10)

        # Left Accent Stripe
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawRoundedRect(QRectF(rect.left() + 0.5, rect.top() + 9, 3.5, rect.height() - 18), 1.5, 1.5)

        # Folder Name (hidden while inline editing)
        if self._editing_node_id != node.id:
            p.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
            p.setPen(QColor("#F5F5F7"))
            fm_name = QFontMetrics(p.font())
            title = fm_name.elidedText(node.name, Qt.TextElideMode.ElideRight, int(rect.width() - 36))
            p.drawText(
                rect.adjusted(12, 6, -24, -22),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                title,
            )

        # Parent badge pill (Tier 2+) + Tier micro-footer
        child_count = len(self._arch.children_of(node.id))
        parent_node = self._arch.get_node(node.parent_id) if (node.tier >= 2 and node.parent_id) else None
        footer = f"T{node.tier}" + (f" · {child_count} sub" if child_count else "")
        if parent_node is not None:
            p_col = QColor(parent_node.accent_hex)
            p.setFont(QFont("Segoe UI", 7.2, QFont.Weight.DemiBold))
            fm_pill = QFontMetrics(p.font())
            pill_label = fm_pill.elidedText(f"↳ {parent_node.name}", Qt.TextElideMode.ElideRight, int(rect.width() - 56))
            pill_w = min(rect.width() - 48.0, float(fm_pill.horizontalAdvance(pill_label)) + 12.0)
            pill_rect = QRectF(rect.left() + 12.0, rect.bottom() - 21.0, pill_w, 16.0)
            fill_col = QColor(p_col)
            fill_col.setAlpha(36)
            border_col = QColor(p_col)
            border_col.setAlpha(135)
            p.setPen(QPen(border_col, 0.9))
            p.setBrush(fill_col)
            p.drawRoundedRect(pill_rect, 4.5, 4.5)
            p.setPen(QColor("#E5E5EA"))
            p.drawText(pill_rect.adjusted(5, 0, -5, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, pill_label)

            p.setFont(QFont("Segoe UI", 7.2))
            p.setPen(QColor("#6E6E73"))
            p.drawText(
                rect.adjusted(12, 30, -10, -5),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                footer,
            )
        else:
            p.setFont(QFont("Segoe UI", 7.5))
            p.setPen(QColor("#6E6E73"))
            p.drawText(
                rect.adjusted(12, 30, -10, -5),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                footer,
            )

        # Hover / Selected Delete '×' button
        if (hovered or selected) and not is_dragging:
            del_r = self._delete_rects[node.id]
            del_hov = self._hovered_delete_id == node.id
            if del_hov:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor("#FF453A"))
                p.drawEllipse(del_r)
                p.setPen(QColor("#FFFFFF"))
            else:
                p.setPen(QColor("#8E8E93"))
            p.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            p.drawText(del_r, Qt.AlignmentFlag.AlignCenter, "×")

        # '+' Spawn handle on bottom center
        if not is_dragging:
            self._paint_plus_handle(p, node.id, rect)

    @staticmethod
    def _build_orthogonal_bus_path(p1: QPointF, p2: QPointF, bus_y: float) -> QPainterPath:
        """Build a clean rounded orthogonal bus connector between two cards."""
        path = QPainterPath(p1)
        dx = p2.x() - p1.x()
        if p1.y() >= bus_y - 2.0 or p2.y() <= bus_y + 2.0:
            dy = max(14.0, abs(p2.y() - p1.y()) * 0.45)
            path.cubicTo(QPointF(p1.x(), p1.y() + dy), QPointF(p2.x(), p2.y() - dy), p2)
            return path
        if abs(dx) < 2.0:
            path.lineTo(p2)
            return path
        sign_x = 1.0 if dx > 0.0 else -1.0
        r = min(
            7.0,
            abs(dx) * 0.45,
            max(1.0, (bus_y - p1.y()) * 0.45),
            max(1.0, (p2.y() - bus_y) * 0.45),
        )
        path.lineTo(p1.x(), bus_y - r)
        path.quadTo(QPointF(p1.x(), bus_y), QPointF(p1.x() + sign_x * r, bus_y))
        path.lineTo(p2.x() - sign_x * r, bus_y)
        path.quadTo(QPointF(p2.x(), bus_y), QPointF(p2.x(), bus_y + r))
        path.lineTo(p2)
        return path

    def _paint_plus_handle(self, p: QPainter, nid: str, rect: QRectF) -> None:
        plus_r = self._plus_rects.get(nid)
        if plus_r is None:
            return
        is_active = (
            nid == self._hovered_id
            or nid == self._selected_id
            or nid == self._hovered_plus_id
            or nid == self._spawning_from_id
        )
        if not is_active:
            # Subtle output port dot
            p.setPen(QPen(QColor("#2C2C2E"), 1.0))
            p.setBrush(QColor("#161618"))
            p.drawEllipse(QPointF(rect.center().x(), rect.bottom()), 3.2, 3.2)
            return

        hov = nid == self._hovered_plus_id or nid == self._spawning_from_id
        p.setPen(QPen(QColor("#111113"), 1.4))
        p.setBrush(QColor("#0A84FF") if hov else QColor("#3A3A3C"))
        p.drawEllipse(plus_r)
        p.setPen(QPen(QColor("#FFFFFF"), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        c = plus_r.center()
        p.drawLine(QPointF(c.x() - 3.2, c.y()), QPointF(c.x() + 3.2, c.y()))
        p.drawLine(QPointF(c.x(), c.y() - 3.2), QPointF(c.x(), c.y() + 3.2))

    # --- Mouse & Direct Manipulation Events ---

    def _hit_node_at(self, pt: QPointF, *, exclude: str | None = None) -> str | None:
        for nid, rect in self._rendered_rects.items():
            if nid == exclude:
                continue
            if rect.contains(pt):
                return nid
        return None

    def mouseMoveEvent(self, event) -> None:  # noqa: ANN001
        if self._panning:
            cur_g = event.globalPosition().toPoint()
            delta_g = cur_g - self._pan_last_global
            self._pan_last_global = cur_g
            scroll_area = self._find_scroll_area()
            if scroll_area is not None:
                hb = scroll_area.horizontalScrollBar()
                vb = scroll_area.verticalScrollBar()
                hb.setValue(hb.value() - delta_g.x())
                vb.setValue(vb.value() - delta_g.y())
            return

        pt = self._to_canvas(event.position())

        # 1. Active '+' handle drag-to-spawn
        if self._spawning_from_id is not None:
            if not self._spawn_dragging and (pt - self._spawn_press_pt).manhattanLength() > 8:
                self._spawn_dragging = True
            self._spawn_cur_pt = pt
            self._spawn_hover_node_id = None
            self._spawn_hover_valid = False
            if self._spawn_dragging:
                hit = self._hit_node_at(pt, exclude=self._spawning_from_id)
                if hit and hit != "__root__":
                    src_pid = None if self._spawning_from_id == "__root__" else self._spawning_from_id
                    hit_node = self._arch.get_node(hit)
                    if hit_node is not None and hit_node.parent_id != src_pid:
                        self._spawn_hover_node_id = hit
                        self._spawn_hover_valid = self._arch.can_reparent(hit, src_pid)
            self.setCursor(Qt.CursorShape.CrossCursor)
            self.update()
            return

        # 2. Active card drag-and-drop reparenting
        if self._press_node_id is not None:
            delta = pt - self._press_pt
            if self._dragging_id is None and delta.manhattanLength() > 6:
                self._dragging_id = self._press_node_id
            if self._dragging_id is not None:
                self._drag_offset = delta
                self._evaluate_reparent_hover(pt)
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                self.update()
                return

        # 3. Hover hit-testing
        self._hovered_delete_id = None
        self._hovered_plus_id = None
        self._hovered_id = None
        self._hovered_tier_add = None

        for nid, pr in self._plus_rects.items():
            nr = self._rendered_rects.get(nid)
            is_vis = nid == self._selected_id or (nr is not None and nr.adjusted(-6, -6, 6, 12).contains(pt))
            if is_vis and pr.adjusted(-3, -3, 3, 3).contains(pt):
                self._hovered_plus_id = nid
                self._hovered_id = nid
                self.setCursor(Qt.CursorShape.PointingHandCursor)
                self.setToolTip("Drag onto a child folder or click to spawn a subfolder")
                self.update()
                return

        for t, r in self._tier_add_rects.items():
            if r.contains(pt):
                self._hovered_tier_add = t
                self.setCursor(Qt.CursorShape.PointingHandCursor)
                self.setToolTip(f"Add a new folder inside Tier {t}")
                self.update()
                return

        for nid, dr in self._delete_rects.items():
            nr = self._rendered_rects.get(nid)
            if (nid == self._selected_id or (nr and nr.contains(pt))) and dr.contains(pt):
                self._hovered_delete_id = nid
                self._hovered_id = nid
                self.setCursor(Qt.CursorShape.PointingHandCursor)
                self.setToolTip("Remove this folder from the architecture")
                self.update()
                return

        for nid, rect in self._rendered_rects.items():
            if rect.contains(pt):
                self._hovered_id = nid
                if nid == "__root__":
                    self.setCursor(Qt.CursorShape.ArrowCursor)
                    self.setToolTip(
                        f"{self._watch_label} (Tier 0 Root)\nAll top-level downloads start here before routing into subfolders."
                    )
                else:
                    self.setCursor(Qt.CursorShape.OpenHandCursor)
                    node = self._arch.get_node(nid)
                    self.setToolTip(self._node_tooltip(node) if node is not None else "")
                self.update()
                return

        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setToolTip(
            "Folder Architecture Canvas\nHover a card to highlight its connections · Drag cards to reparent · Ctrl+Wheel to zoom"
        )
        self.update()

    def _evaluate_reparent_hover(self, pt: QPointF) -> None:
        self._drop_target_id = None
        self._drop_state = None
        self._drop_promote_tier1 = False

        dragged_id = self._dragging_id
        if not dragged_id:
            return
        dragged = self._arch.get_node(dragged_id)
        if dragged is None:
            return
        dragged_rect = self._display_rect(dragged_id)
        if dragged_rect is None:
            return

        # Find best overlap with another card (or cursor inside another card)
        best_target: str | None = None
        best_area = 0.0
        for nid, rect in self._rendered_rects.items():
            if nid == dragged_id:
                continue
            if rect.contains(pt):
                best_target = nid
                break
            inter = dragged_rect.intersected(rect)
            area = inter.width() * inter.height()
            if area > 650.0 and area > best_area:
                best_area = area
                best_target = nid

        if best_target == "__root__":
            if dragged.tier != 1:
                self._drop_target_id = "__root__"
                self._drop_state = "valid" if self._arch.can_reparent(dragged_id, None) else "invalid"
            return

        if best_target is not None:
            if best_target == dragged.parent_id:
                return
            self._drop_target_id = best_target
            self._drop_state = (
                "valid" if self._arch.can_reparent(dragged_id, best_target) else "invalid"
            )
            return

        # Empty space inside Tier 1 band promotes nested nodes back to Tier 1
        band1 = self._tier_band_rects.get(1)
        if band1 is not None and band1.contains(pt) and dragged.tier != 1:
            if self._arch.can_reparent(dragged_id, None):
                self._drop_promote_tier1 = True

    def _find_scroll_area(self) -> QScrollArea | None:
        w = self.parentWidget()
        while w is not None:
            if isinstance(w, QScrollArea):
                return w
            w = w.parentWidget()
        return None

    def mousePressEvent(self, event) -> None:  # noqa: ANN001
        if event.button() == Qt.MouseButton.MiddleButton or (
            event.button() == Qt.MouseButton.LeftButton
            and (event.modifiers() & Qt.KeyboardModifier.AltModifier)
        ):
            self._panning = True
            self._pan_last_global = event.globalPosition().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._inline_edit.isVisible():
            self._commit_inline_rename()

        pt = self._to_canvas(event.position())

        # 1. Check '+' handle press first (works on __root__ and tier < MAX_TIERS nodes)
        for nid, pr in self._plus_rects.items():
            nr = self._rendered_rects.get(nid)
            is_vis = nid == self._selected_id or (nr is not None and nr.adjusted(-6, -6, 6, 12).contains(pt))
            if is_vis and pr.adjusted(-3, -3, 3, 3).contains(pt):
                self._spawning_from_id = nid
                self._spawn_press_pt = pt
                self._spawn_cur_pt = pt
                self._spawn_dragging = False
                self.update()
                return

        # 2. Check tier '+ Add' pill
        for t, r in self._tier_add_rects.items():
            if r.contains(pt):
                self.tier_add_requested.emit(t)
                return

        # 3. Check delete '×'
        for nid, dr in self._delete_rects.items():
            nr = self._rendered_rects.get(nid)
            if (nid == self._selected_id or (nr and nr.contains(pt))) and dr.contains(pt):
                self.node_delete_requested.emit(nid)
                return

        # 4. Select & prepare drag on a non-root card
        for nid, rect in self._rendered_rects.items():
            if nid != "__root__" and rect.contains(pt):
                self.select_node(nid)
                self._press_node_id = nid
                self._press_pt = pt
                self._drag_offset = QPointF()
                return

    def mouseReleaseEvent(self, event) -> None:  # noqa: ANN001
        if self._panning:
            self._panning = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pt = self._to_canvas(event.position())

        # Complete '+' handle spawn or wire-reparent
        if self._spawning_from_id is not None:
            src_id = self._spawning_from_id
            was_dragging = self._spawn_dragging
            hover_nid = self._spawn_hover_node_id
            hover_valid = self._spawn_hover_valid

            self._spawning_from_id = None
            self._spawn_dragging = False
            self._spawn_hover_node_id = None
            self._spawn_hover_valid = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.update()

            parent_arg = "" if src_id == "__root__" else src_id
            if was_dragging and hover_nid and hover_valid:
                self.node_reparent_requested.emit(hover_nid, parent_arg)
            elif not was_dragging or hover_nid is None:
                self.node_spawn_child_requested.emit(parent_arg, "New Subfolder" if parent_arg else "New Folder")
            return

        # Complete card drag reparenting
        dragged_id = self._dragging_id
        dragged_rect = self._display_rect(dragged_id) if dragged_id else None
        drop_target = self._drop_target_id
        drop_state = self._drop_state
        promote_t1 = self._drop_promote_tier1

        self._press_node_id = None
        self._dragging_id = None
        self._drag_offset = QPointF()
        self._drop_target_id = None
        self._drop_state = None
        self._drop_promote_tier1 = False
        self.setCursor(Qt.CursorShape.ArrowCursor)

        if dragged_id is not None:
            if drop_target == "__root__" and drop_state == "valid":
                self.node_reparent_requested.emit(dragged_id, "")
            elif promote_t1:
                self.node_reparent_requested.emit(dragged_id, "")
            elif drop_target and drop_state == "valid":
                self.node_reparent_requested.emit(dragged_id, drop_target)
            elif (
                dragged_rect is not None
                and animations_enabled()
                and not os.environ.get("PYTEST_CURRENT_TEST")
            ):
                # Spring back smoothly to original layout slot
                self._anim_from_rects = {**self._rendered_rects, dragged_id: dragged_rect}
                self._layout_anim.stop()
                self._layout_anim.start()
            else:
                self.update()
            return

        self.update()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: ANN001
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pt = self._to_canvas(event.position())
        for nid, rect in self._rendered_rects.items():
            if nid != "__root__" and rect.contains(pt):
                self.select_node(nid)
                # Double-clicking the title area opens inline rename; lower body opens full rules editor
                title_zone = QRectF(rect.left(), rect.top(), rect.width() - 22, 28)
                if title_zone.contains(pt):
                    self.start_inline_rename(nid)
                else:
                    self.node_edit_requested.emit(nid)
                return

    def wheelEvent(self, event) -> None:  # noqa: ANN001
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta != 0:
                factor = 1.1 if delta > 0 else 1 / 1.1
                self.set_zoom(self._zoom * factor)
                event.accept()
                return
        super().wheelEvent(event)

    # --- External Template Shelf Drag-and-Drop ---

    def dragEnterEvent(self, event) -> None:  # noqa: ANN001
        if event.mimeData().hasFormat(MIME_TEMPLATE):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # noqa: ANN001
        if not event.mimeData().hasFormat(MIME_TEMPLATE):
            super().dragMoveEvent(event)
            return
        pt = self._to_canvas(event.position())
        self._ext_drop_node_id = None
        self._ext_drop_valid = False
        self._ext_drop_band = None

        hit = self._hit_node_at(pt)
        if hit == "__root__":
            self._ext_drop_node_id = "__root__"
            self._ext_drop_valid = True
        elif hit is not None:
            node = self._arch.get_node(hit)
            self._ext_drop_node_id = hit
            self._ext_drop_valid = bool(node is not None and node.tier < FolderArchitecture.MAX_TIERS)
        else:
            for t, band in self._tier_band_rects.items():
                if t >= 1 and band.contains(pt):
                    self._ext_drop_band = t
                    break
        self.update()
        event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:  # noqa: ANN001
        self._ext_drop_node_id = None
        self._ext_drop_valid = False
        self._ext_drop_band = None
        self.update()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: ANN001
        if not event.mimeData().hasFormat(MIME_TEMPLATE):
            super().dropEvent(event)
            return
        try:
            raw = bytes(event.mimeData().data(MIME_TEMPLATE)).decode("utf-8")
            payload = json.loads(raw)
            key = str(payload.get("key", ""))
        except Exception:
            key = ""

        target_nid = self._ext_drop_node_id
        target_valid = self._ext_drop_valid
        target_band = self._ext_drop_band

        self._ext_drop_node_id = None
        self._ext_drop_valid = False
        self._ext_drop_band = None
        self.update()

        if not key:
            return
        if target_nid == "__root__":
            self.template_dropped.emit(key, 1, "")
        elif target_nid is not None and target_valid:
            parent_node = self._arch.get_node(target_nid)
            if parent_node is not None:
                self.template_dropped.emit(key, parent_node.tier + 1, parent_node.id)
        elif target_nid is not None and not target_valid:
            # Dropped on max-depth card -> ignore cleanly
            return
        elif target_band is not None:
            self.template_dropped.emit(key, target_band, "")
        else:
            self.template_dropped.emit(key, 1, "")
        event.acceptProposedAction()


class ArchitectureTab(QWidget):
    """Tab 2: Direct-Manipulation Folder Architecture Canvas + Template Shelf + Inline Inspector."""

    architecture_applied = Signal(str)
    undo_requested = Signal()
    carry_over_requested = Signal()
    action_started = Signal(str)
    action_progress = Signal(int, int, str)
    action_finished = Signal(str, str)  # (summary_msg, kind)

    TOOLBAR_COMPACT_WIDTH = 1180

    def __init__(self, service: SorterService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self._arch: FolderArchitecture = architecture_from_config(service.config)
        self._dirty = False
        self._active_preset = "standard"
        self._undo_stack: list[tuple[FolderArchitecture, str | None]] = []
        self._redo_stack: list[tuple[FolderArchitecture, str | None]] = []
        self._syncing_inspector = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 0)
        root.setSpacing(10)

        # Top Compact Command & Blueprint Pill Bar
        bar = QFrame()
        bar.setObjectName("SurfaceCard")
        bar_outer = QVBoxLayout(bar)
        bar_outer.setContentsMargins(12, 8, 12, 8)
        bar_outer.setSpacing(7)

        # Row 1: Tiers, Diagram Undo/Redo, Arrange, Auto-Create, Shift Undo, Defaults, Apply & Shift
        bar_row1 = QHBoxLayout()
        bar_row1.setSpacing(6)

        self.btn_add_tier = InteractiveRibbonBtn("+ Tier")
        self.btn_add_tier.setToolTip("Add a new horizontal folder depth tier (up to 5 tiers)")
        self.btn_add_tier.clicked.connect(self._on_add_tier)
        bar_row1.addWidget(self.btn_add_tier)

        self.btn_remove_tier = InteractiveRibbonBtn("- Tier")
        self.btn_remove_tier.setToolTip("Remove the deepest folder tier")
        self.btn_remove_tier.clicked.connect(self._on_remove_tier)
        bar_row1.addWidget(self.btn_remove_tier)

        self.btn_diag_undo = QPushButton("")
        self.btn_diag_undo.setIcon(make_vector_icon("undo", "#F5F5F7", 14))
        self.btn_diag_undo.setFixedSize(32, 30)
        self.btn_diag_undo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_diag_undo.setToolTip("Undo diagram edit (Ctrl+Z)")
        self.btn_diag_undo.clicked.connect(self.undo_diagram_edit)
        bar_row1.addWidget(self.btn_diag_undo)

        self.btn_diag_redo = QPushButton("")
        self.btn_diag_redo.setIcon(make_vector_icon("redo", "#F5F5F7", 14))
        self.btn_diag_redo.setFixedSize(32, 30)
        self.btn_diag_redo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_diag_redo.setToolTip("Redo diagram edit (Ctrl+Y)")
        self.btn_diag_redo.clicked.connect(self.redo_diagram_edit)
        bar_row1.addWidget(self.btn_diag_redo)

        self.btn_auto_layout = CollapsibleRibbonBtn("Arrange", make_vector_icon("fit", "#F5F5F7", 14))
        self.btn_auto_layout.setToolTip("Auto-arrange cards and reset zoom to 100%")
        self.btn_auto_layout.clicked.connect(self._on_auto_arrange)
        bar_row1.addWidget(self.btn_auto_layout)

        bar_row1.addStretch(1)

        self.btn_scan_folders = CollapsibleRibbonBtn(
            "Scan Folders",
            make_vector_icon("search", "#5AC8FA", 14),
        )
        self.btn_scan_folders.setToolTip(
            "Scan your existing Downloads subfolders to automatically build or merge the root map"
        )
        self.btn_scan_folders.clicked.connect(self._on_scan_folders)
        bar_row1.addWidget(self.btn_scan_folders)

        self.btn_snapshots = CollapsibleRibbonBtn(
            "Snapshots",
            make_vector_icon("folder", "#FBBF24", 14),
        )
        self.btn_snapshots.setToolTip(
            "Save, restore, or export snapshots of your current Folder Architecture"
        )
        self.btn_snapshots.clicked.connect(self._open_snapshots_dialog)
        bar_row1.addWidget(self.btn_snapshots)

        self.btn_auto_create = CollapsibleRibbonBtn(
            "Auto-Create Basic",
            make_vector_icon("bolt", "#69E6AD", 14),
        )
        self.btn_auto_create.setToolTip(
            "Automatically build a clean, standard 2-tier basic folder architecture to customize"
        )
        self.btn_auto_create.clicked.connect(self._on_auto_create_basic)
        bar_row1.addWidget(self.btn_auto_create)

        self.btn_arch_undo = CollapsibleRibbonBtn("Undo", make_vector_icon("undo", "#F5F5F7", 14))
        self.btn_arch_undo.setToolTip("Undo last file shift and applied architecture change")
        self.btn_arch_undo.clicked.connect(self._on_undo_clicked)
        bar_row1.addWidget(self.btn_arch_undo)

        self.btn_arch_defaults = CollapsibleRibbonBtn(
            "Defaults",
            make_vector_icon("refresh", "#98989D", 14),
        )
        self.btn_arch_defaults.setObjectName("GhostButton")
        self.btn_arch_defaults.setToolTip("Return Folder Architecture & Rules to starter defaults")
        self.btn_arch_defaults.clicked.connect(self._on_return_to_defaults)
        bar_row1.addWidget(self.btn_arch_defaults)

        self.btn_apply_shift = InteractiveRibbonBtn(
            "Apply && Shift Files",
            make_vector_icon("bolt", "#FFFFFF", 14),
        )
        self.btn_apply_shift.setObjectName("PrimaryButton")
        self.btn_apply_shift.setToolTip(
            "Preview file shifts, save updated rules, and organize Downloads according to this folder tree"
        )
        self.btn_apply_shift.clicked.connect(self._on_apply_and_shift)
        bar_row1.addWidget(self.btn_apply_shift)
        bar_outer.addLayout(bar_row1)

        # Row 2: Segmented Blueprint Pill Selector (replaces Load Preset dropdown)
        bar_row2 = QHBoxLayout()
        bar_row2.setSpacing(6)
        lbl_bp = QLabel("Blueprint:")
        lbl_bp.setStyleSheet("color: #98989D; font-size: 11.5px; font-weight: 600;")
        lbl_bp.setToolTip("Switch to a starter folder layout blueprint")
        bar_row2.addWidget(lbl_bp)

        self._blueprint_btns: dict[str, QPushButton] = {}
        for key, short_label, tip in BLUEPRINT_PILLS:
            pill = _make_pill_button(short_label, checkable=True, tooltip=f"Load {tip}")
            pill.clicked.connect(lambda _chk, pkey=key: self._on_load_preset(pkey))
            self._blueprint_btns[key] = pill
            bar_row2.addWidget(pill)

        layer_tip = ElidedLabel(
            "Drag templates onto canvas/cards · Drag '+' handle to spawn · Drag cards to reparent · Double-click title to rename",
            self,
        )
        layer_tip.setVisible(False)
        bar_row2.addStretch(1)

        self.btn_toggle_lines = _make_pill_button(
            "Lines: Focused",
            checkable=True,
            tooltip="Show only connections for the hovered or selected folder (Focused) or show all orthogonal bus lines (All)",
        )
        self.btn_toggle_lines.toggled.connect(self._on_toggle_lines)
        bar_row2.addWidget(self.btn_toggle_lines)
        bar_outer.addLayout(bar_row2)
        bar.setToolTip(
            "Drag templates onto canvas or cards · Drag '+' handle to spawn · Drag cards to reparent · Double-click title to rename"
        )

        root.addWidget(bar)

        # 3-Pane Body: Template Shelf | Interactive Canvas | Inline Inspector
        body = QHBoxLayout()
        body.setSpacing(10)

        self.shelf = TemplateShelfWidget(self)
        self.shelf.template_activated.connect(self._on_template_activated)
        body.addWidget(self.shelf)

        canvas_card = QFrame()
        canvas_card.setObjectName("SurfaceCard")
        cc_layout = QVBoxLayout(canvas_card)
        cc_layout.setContentsMargins(10, 10, 10, 10)
        cc_layout.setSpacing(6)

        hdr_row = QHBoxLayout()
        self.lbl_arch_summary = QLabel("")
        self.lbl_arch_summary.setStyleSheet("color: #98989D; font-size: 12px;")
        hdr_row.addWidget(self.lbl_arch_summary, 1)

        self.lbl_zoom = QLabel("100%")
        self.lbl_zoom.setStyleSheet("color: #6E6E73; font-size: 11px;")
        self.lbl_zoom.setToolTip("Hold Ctrl + Mouse Wheel on canvas to zoom")
        hdr_row.addWidget(self.lbl_zoom)

        hint = ElidedLabel("Click to inspect · Double-click title to rename · Double-click rules to edit", self)
        hint.setVisible(False)
        cc_layout.addLayout(hdr_row)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.canvas = FolderArchitectureCanvas(self)
        self.canvas.node_selected.connect(self._update_inspector)
        self.canvas.node_edit_requested.connect(self._edit_node_by_id)
        self.canvas.node_delete_requested.connect(self._delete_node_by_id)
        self.canvas.tier_add_requested.connect(self._on_tier_quick_add)
        self.canvas.node_reparent_requested.connect(self._on_node_reparented)
        self.canvas.node_spawn_child_requested.connect(self._on_spawn_child_inline)
        self.canvas.node_renamed.connect(self._on_node_renamed)
        self.canvas.template_dropped.connect(self._on_template_dropped)
        self.canvas.zoom_changed.connect(lambda z: self.lbl_zoom.setText(f"{int(round(z * 100))}%"))
        self.scroll.setWidget(self.canvas)
        cc_layout.addWidget(self.scroll, 1)
        body.addWidget(canvas_card, 1)

        # Right Inspector Card (scrollable, inline editing without dropdowns)
        insp = QFrame()
        insp.setObjectName("SurfaceCard")
        insp.setFixedWidth(232)
        insp_outer = QVBoxLayout(insp)
        insp_outer.setContentsMargins(0, 0, 0, 0)
        insp_scroll = QScrollArea()
        insp_scroll.setObjectName("InspectorScroll")
        insp_scroll.setWidgetResizable(True)
        insp_scroll.setFrameShape(QFrame.Shape.NoFrame)
        insp_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        insp_scroll.setStyleSheet(
            "QScrollArea#InspectorScroll, QScrollArea#InspectorScroll > QWidget > QWidget { background: transparent; }"
        )
        insp_body = QWidget()
        insp_scroll.setWidget(insp_body)
        insp_outer.addWidget(insp_scroll)
        il = QVBoxLayout(insp_body)
        il.setContentsMargins(12, 12, 12, 12)
        il.setSpacing(8)

        insp_title = QLabel("Selected Folder")
        insp_title.setObjectName("CardTitle")
        il.addWidget(insp_title)

        self.lbl_sel_name = QLabel("None")
        self.lbl_sel_name.setStyleSheet("font-size: 14px; font-weight: 600; color: #F5F5F7;")
        self.lbl_sel_name.setWordWrap(True)
        il.addWidget(self.lbl_sel_name)

        self.lbl_sel_path = QLabel("")
        self.lbl_sel_path.setObjectName("MonoPath")
        self.lbl_sel_path.setWordWrap(True)
        il.addWidget(self.lbl_sel_path)

        # Inline Name & Color Swatches
        self.edit_insp_name = QLineEdit()
        self.edit_insp_name.setPlaceholderText("Folder name...")
        self.edit_insp_name.setToolTip("Rename the selected folder and press Enter")
        self.edit_insp_name.editingFinished.connect(self._on_inspector_fields_committed)
        il.addWidget(self.edit_insp_name)

        swatch_row = QHBoxLayout()
        swatch_row.setSpacing(4)
        self._swatch_btns: dict[str, QPushButton] = {}
        for label, k_key in (
            ("Doc", "doc"),
            ("PDF", "pdf"),
            ("3D", "print"),
            ("CAD", "cad"),
            ("Image", "image"),
            ("Video", "video"),
            ("Code", "code"),
            ("Finance", "finance"),
            ("School", "school"),
        ):
            hex_c = KIND_COLORS[k_key]
            sb = QPushButton("")
            sb.setFixedSize(16, 16)
            sb.setCursor(Qt.CursorShape.PointingHandCursor)
            sb.setToolTip(f"Color: {label}")
            sb.setCheckable(True)
            sb.setStyleSheet(
                f"QPushButton {{ background-color: {hex_c}; border: 1px solid #2C2C2E; border-radius: 8px; padding: 0; }}"
                f"QPushButton:checked {{ border: 2px solid #FFFFFF; }}"
            )
            sb.clicked.connect(lambda _c, k=k_key: self._on_inspector_kind_picked(k))
            self._swatch_btns[k_key] = sb
            swatch_row.addWidget(sb)
        swatch_row.addStretch(1)
        il.addLayout(swatch_row)

        # Inline Rule Inputs
        lbl_ext = QLabel("Extensions")
        lbl_ext.setStyleSheet("color: #8E8E93; font-size: 10.5px; font-weight: 600;")
        il.addWidget(lbl_ext)
        self.edit_insp_exts = QLineEdit()
        self.edit_insp_exts.setPlaceholderText(".pdf, .docx, .3mf")
        self.edit_insp_exts.editingFinished.connect(self._on_inspector_fields_committed)
        il.addWidget(self.edit_insp_exts)

        lbl_kw = QLabel("Keywords")
        lbl_kw.setStyleSheet("color: #8E8E93; font-size: 10.5px; font-weight: 600;")
        il.addWidget(lbl_kw)
        self.edit_insp_keywords = QLineEdit()
        self.edit_insp_keywords.setPlaceholderText("invoice, syllabus")
        self.edit_insp_keywords.editingFinished.connect(self._on_inspector_fields_committed)
        il.addWidget(self.edit_insp_keywords)

        lbl_dom = QLabel("Websites")
        lbl_dom.setStyleSheet("color: #8E8E93; font-size: 10.5px; font-weight: 600;")
        il.addWidget(lbl_dom)
        self.edit_insp_domains = QLineEdit()
        self.edit_insp_domains.setPlaceholderText("makerworld.com")
        self.edit_insp_domains.setToolTip("Source website domains that route directly into this folder")
        self.edit_insp_domains.editingFinished.connect(self._on_inspector_fields_committed)
        il.addWidget(self.edit_insp_domains)

        self.lbl_sel_rules = QLabel("", self)
        self.lbl_sel_rules.setVisible(False)

        self.btn_edit_node = InteractiveRibbonBtn("Edit Folder && Rules", make_vector_icon("edit", "#F5F5F7", 14))
        self.btn_edit_node.setToolTip("Open the full folder & rule editor for the selected node")
        self.btn_edit_node.clicked.connect(lambda: self._edit_node_by_id(self.canvas.selected_node_id or ""))
        il.addWidget(self.btn_edit_node)

        self.btn_add_child = InteractiveRibbonBtn("Add Child Subfolder", make_vector_icon("plus", "#F5F5F7", 14))
        self.btn_add_child.setToolTip("Add a nested subfolder under the selected folder")
        self.btn_add_child.clicked.connect(self._on_add_child_for_selected)
        il.addWidget(self.btn_add_child)

        self.btn_delete_node = InteractiveRibbonBtn("Remove Folder", make_vector_icon("delete", "#FF453A", 14))
        self.btn_delete_node.setToolTip("Remove the selected folder node from your layout")
        self.btn_delete_node.clicked.connect(lambda: self._delete_node_by_id(self.canvas.selected_node_id or ""))
        il.addWidget(self.btn_delete_node)

        il.addStretch(1)

        div = QFrame()
        div.setObjectName("CardDivider")
        il.addWidget(div)

        self.btn_carry_over = InteractiveRibbonBtn("Carry Over Setup...")
        self.btn_carry_over.setObjectName("GhostButton")
        self.btn_carry_over.setToolTip("Carry over a Download Sorter v2.8 config or exported v3 setup")
        self.btn_carry_over.clicked.connect(self.carry_over_requested.emit)
        il.addWidget(self.btn_carry_over)

        body.addWidget(insp)
        root.addLayout(body, 1)

        self._update_blueprint_pills()
        self._update_undo_redo_buttons()
        self.sync_from_service(force=True)

    def _apply_compact_mode(self) -> None:
        compact = self.width() < self.TOOLBAR_COMPACT_WIDTH
        changed = (
            self.btn_auto_layout.is_collapsed() != compact
            or self.btn_scan_folders.is_collapsed() != compact
            or self.btn_snapshots.is_collapsed() != compact
            or self.btn_auto_create.is_collapsed() != compact
            or self.btn_arch_undo.is_collapsed() != compact
            or self.btn_arch_defaults.is_collapsed() != compact
        )
        self.btn_auto_layout.set_collapsed(compact)
        self.btn_scan_folders.set_collapsed(compact)
        self.btn_snapshots.set_collapsed(compact)
        self.btn_auto_create.set_collapsed(compact)
        self.btn_arch_undo.set_collapsed(compact)
        self.btn_arch_defaults.set_collapsed(compact)
        if changed:
            w: QWidget | None = self
            while w is not None:
                if w.layout() is not None:
                    w.layout().invalidate()
                w.updateGeometry()
                if w.isWindow():
                    break
                w = w.parentWidget()

    def showEvent(self, event) -> None:  # noqa: ANN001
        super().showEvent(event)
        self._apply_compact_mode()

    def resizeEvent(self, event) -> None:  # noqa: ANN001
        super().resizeEvent(event)
        self._apply_compact_mode()

    def keyPressEvent(self, event) -> None:  # noqa: ANN001
        from PySide6.QtWidgets import QApplication

        if isinstance(QApplication.focusWidget(), QLineEdit):
            super().keyPressEvent(event)
            return
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if event.key() == Qt.Key.Key_Z:
                if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    self.redo_diagram_edit()
                else:
                    self.undo_diagram_edit()
                event.accept()
                return
            if event.key() == Qt.Key.Key_Y:
                self.redo_diagram_edit()
                event.accept()
                return
        super().keyPressEvent(event)

    # --- Local Diagram Undo / Redo ---

    def _push_undo_snapshot(self) -> None:
        self._undo_stack.append((self._arch.clone(), self.canvas.selected_node_id))
        if len(self._undo_stack) > 40:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self._update_undo_redo_buttons()

    def undo_diagram_edit(self) -> None:
        if not self._undo_stack:
            return
        prev_rects = self.canvas.snapshot_rects()
        self._redo_stack.append((self._arch.clone(), self.canvas.selected_node_id))
        arch_snap, sel_id = self._undo_stack.pop()
        self._arch = arch_snap
        self._dirty = True
        self._redraw_canvas(animate=True, prev_rects=prev_rects)
        if sel_id and self._arch.get_node(sel_id):
            self.canvas.select_node(sel_id)
        self._update_undo_redo_buttons()

    def redo_diagram_edit(self) -> None:
        if not self._redo_stack:
            return
        prev_rects = self.canvas.snapshot_rects()
        self._undo_stack.append((self._arch.clone(), self.canvas.selected_node_id))
        arch_snap, sel_id = self._redo_stack.pop()
        self._arch = arch_snap
        self._dirty = True
        self._redraw_canvas(animate=True, prev_rects=prev_rects)
        if sel_id and self._arch.get_node(sel_id):
            self.canvas.select_node(sel_id)
        self._update_undo_redo_buttons()

    def _update_undo_redo_buttons(self) -> None:
        self.btn_diag_undo.setEnabled(bool(self._undo_stack))
        self.btn_diag_redo.setEnabled(bool(self._redo_stack))

    def _update_blueprint_pills(self) -> None:
        for key, pill in self._blueprint_btns.items():
            pill.setChecked(key == self._active_preset)

    def sync_from_service(self, *, force: bool = False) -> None:
        if force or not self._dirty:
            self._arch = architecture_from_config(self.service.config)
            self._dirty = False
            self._undo_stack.clear()
            self._redo_stack.clear()
            self._update_undo_redo_buttons()
        self._redraw_canvas()

    def _redraw_canvas(
        self,
        *,
        animate: bool = False,
        prev_rects: dict[str, QRectF] | None = None,
    ) -> None:
        watch = str(self.service.watch_root)
        self.canvas.set_architecture(
            self._arch,
            watch_label=watch,
            animate=animate,
            prev_rects=prev_rects,
        )
        self.btn_add_tier.setEnabled(self._arch.tier_count < FolderArchitecture.MAX_TIERS)
        self.btn_remove_tier.setEnabled(self._arch.tier_count > FolderArchitecture.MIN_TIERS)
        dirty_tag = "  •  Unsaved draft (click Apply & Shift Files)" if self._dirty else ""
        self.lbl_arch_summary.setText(
            f"{self._arch.tier_count} Tier(s)  •  {len(self._arch.nodes)} Folders{dirty_tag}"
        )
        self._update_inspector(self.canvas.selected_node_id or "")

    def _update_inspector(self, node_id: str) -> None:
        self._syncing_inspector = True
        try:
            node = self._arch.get_node(node_id)
            if node is None:
                self.lbl_sel_name.setText("Click any folder node")
                self.lbl_sel_path.setText(str(self.service.watch_root))
                self.lbl_sel_rules.setText("Select a folder card or drag a template onto the canvas.")
                self.edit_insp_name.setText("")
                self.edit_insp_exts.setText("")
                self.edit_insp_keywords.setText("")
                self.edit_insp_domains.setText("")
                for w in (
                    self.edit_insp_name,
                    self.edit_insp_exts,
                    self.edit_insp_keywords,
                    self.edit_insp_domains,
                ):
                    w.setEnabled(False)
                for sb in self._swatch_btns.values():
                    sb.setChecked(False)
                    sb.setEnabled(False)
                self.btn_edit_node.setEnabled(False)
                self.btn_add_child.setEnabled(False)
                self.btn_delete_node.setEnabled(False)
                return

            rel = self._arch.node_relative_path(node.id)
            self.lbl_sel_name.setText(f"{node.name} (Tier {node.tier})")
            self.lbl_sel_path.setText(f"Downloads\\{rel}")
            self.edit_insp_name.setText(node.name)
            self.edit_insp_exts.setText(", ".join(node.extensions))
            self.edit_insp_keywords.setText(", ".join(node.keywords))
            self.edit_insp_domains.setText(", ".join(node.domains))
            for w in (
                self.edit_insp_name,
                self.edit_insp_exts,
                self.edit_insp_keywords,
                self.edit_insp_domains,
            ):
                w.setEnabled(True)
            for k, sb in self._swatch_btns.items():
                sb.setEnabled(True)
                sb.setChecked(k == node.kind)

            child_count = len(self._arch.children_of(node.id))
            summary_tip = f"{child_count} subfolder{'s' if child_count != 1 else ''} · Press Enter to apply inline edits"
            self.lbl_sel_rules.setText(summary_tip)
            self.lbl_sel_name.setToolTip(summary_tip)
            self.lbl_sel_path.setToolTip(f"Downloads\\{rel}\n{summary_tip}")
            self.btn_edit_node.setEnabled(True)
            self.btn_add_child.setEnabled(node.tier < FolderArchitecture.MAX_TIERS)
            self.btn_delete_node.setEnabled(True)
        finally:
            self._syncing_inspector = False

    def _on_toggle_lines(self, checked: bool) -> None:
        self.canvas.set_show_all_connectors(checked)
        self.btn_toggle_lines.setText("Lines: All" if checked else "Lines: Focused")

    def _on_inspector_fields_committed(self) -> None:
        if self._syncing_inspector:
            return
        node = self._arch.get_node(self.canvas.selected_node_id)
        if node is None:
            return
        split = lambda s: [x.strip() for x in s.split(",") if x.strip()]
        new_name = self.edit_insp_name.text().strip().replace("/", "").replace("\\", "") or node.name
        new_exts = _clean_exts(split(self.edit_insp_exts.text()))
        new_kws = _clean_strs(split(self.edit_insp_keywords.text()))
        new_doms = [d.lower() for d in _clean_strs(split(self.edit_insp_domains.text()))]

        if (
            new_name == node.name
            and new_exts == node.extensions
            and new_kws == node.keywords
            and new_doms == node.domains
        ):
            return

        self._push_undo_snapshot()
        self._arch.update_node(
            node.id,
            name=new_name,
            extensions=new_exts,
            keywords=new_kws,
            domains=new_doms,
        )
        self._dirty = True
        self._redraw_canvas()

    def _on_inspector_kind_picked(self, kind_key: str) -> None:
        node = self._arch.get_node(self.canvas.selected_node_id)
        if node is None or node.kind == kind_key:
            self._update_inspector(self.canvas.selected_node_id or "")
            return
        self._push_undo_snapshot()
        self._arch.update_node(node.id, kind=kind_key)
        self._dirty = True
        self._redraw_canvas()

    # --- Direct-Manipulation Canvas Signal Handlers ---

    def _on_node_reparented(self, node_id: str, new_parent_id: str) -> None:
        pid: str | None = new_parent_id or None
        node = self._arch.get_node(node_id)
        if node is None or node.parent_id == pid:
            return
        if not self._arch.can_reparent(node_id, pid):
            return
        prev_rects = self.canvas.snapshot_rects()
        self._push_undo_snapshot()
        if self._arch.reparent_node(node_id, pid):
            self._dirty = True
            self._redraw_canvas(animate=True, prev_rects=prev_rects)
            self.canvas.select_node(node_id)

    def _on_spawn_child_inline(self, parent_id: str, default_name: str) -> None:
        pid = parent_id or None
        if pid is None:
            tier = 1
            kind = "doc"
        else:
            pnode = self._arch.get_node(pid)
            if pnode is None or pnode.tier >= FolderArchitecture.MAX_TIERS:
                return
            tier = pnode.tier + 1
            kind = pnode.kind

        prev_rects = self.canvas.snapshot_rects()
        self._push_undo_snapshot()
        node = self._arch.add_custom_node(
            name=default_name,
            tier=tier,
            parent_id=pid,
            kind=kind,
        )
        self._dirty = True
        self._redraw_canvas(animate=True, prev_rects=prev_rects)
        self.canvas.select_node(node.id)
        if rect := self.canvas._node_rects.get(node.id):
            z = self.canvas.zoom
            self.scroll.ensureVisible(int(rect.center().x() * z), int(rect.center().y() * z), 40, 40)
        self.canvas.start_inline_rename(node.id)

    def _on_node_renamed(self, node_id: str, new_name: str) -> None:
        node = self._arch.get_node(node_id)
        if node is None or node.name == new_name:
            return
        self._push_undo_snapshot()
        self._arch.update_node(node_id, name=new_name)
        self._dirty = True
        self._redraw_canvas()
        self.canvas.select_node(node_id)

    def _on_template_dropped(self, premade_key: str, target_tier: int, parent_id: str) -> None:
        prev_rects = self.canvas.snapshot_rects()
        self._push_undo_snapshot()
        pid = parent_id or None
        node = self._arch.add_premade_node(premade_key, tier=target_tier, parent_id=pid)
        if node is None:
            self._undo_stack.pop()
            self._update_undo_redo_buttons()
            return
        self._dirty = True
        self._redraw_canvas(animate=True, prev_rects=prev_rects)
        self.canvas.select_node(node.id)

    def _on_template_activated(self, premade_key: str) -> None:
        sel = self._arch.get_node(self.canvas.selected_node_id)
        if sel is not None and sel.tier < FolderArchitecture.MAX_TIERS:
            target_tier = sel.tier + 1
            parent_id = sel.id
        else:
            target_tier = 1
            parent_id = ""
        self._on_template_dropped(premade_key, target_tier, parent_id)

    # --- Toolbar & Blueprint Actions ---

    def _on_load_preset(self, key: str = "standard") -> None:
        prev_rects = self.canvas.snapshot_rects()
        self._push_undo_snapshot()
        self._active_preset = key
        self._update_blueprint_pills()
        self._arch = FolderArchitecture.from_preset(key)
        self._dirty = True
        self._redraw_canvas(animate=True, prev_rects=prev_rects)

    def _on_auto_arrange(self) -> None:
        prev_rects = self.canvas.snapshot_rects()
        self.canvas.set_zoom(1.0)
        self._redraw_canvas(animate=True, prev_rects=prev_rects)

    def _on_auto_create_basic(self) -> None:
        prev_rects = self.canvas.snapshot_rects()
        self._push_undo_snapshot()
        self._active_preset = "basic_starter"
        self._update_blueprint_pills()
        self._arch = FolderArchitecture.auto_create_basic(self.service.watch_root)
        self._dirty = True
        self._redraw_canvas(animate=True, prev_rects=prev_rects)
        if self._arch.nodes:
            self.canvas.select_node(self._arch.nodes[0].id)
        self.architecture_applied.emit(
            "Auto-created Basic Starter architecture — customize folders or click Apply & Shift Files."
        )

    def _on_scan_folders(self) -> None:
        watch_dir = self.service.watch_root
        self.btn_scan_folders.setEnabled(False)
        self.btn_scan_folders.setText("Scanning...")

        def _restore_scan_btn(_msg: str = "") -> None:
            self.btn_scan_folders.setEnabled(True)
            self.btn_scan_folders.setText("Scan Folders")

        def _work(progress) -> dict[str, Any]:  # noqa: ANN001
            return scan_folder_architecture(watch_dir, on_progress=progress)

        def _done(result: dict[str, Any]) -> None:
            _restore_scan_btn()
            scanned: FolderArchitecture = result["architecture"]
            folder_count = int(result.get("folder_count") or 0)
            file_count = int(result.get("file_count") or 0)
            if folder_count == 0:
                self.architecture_applied.emit(f"Scanned {watch_dir.name} — 0 subfolders found.")
                QMessageBox.information(
                    self,
                    "Scan Existing Folders",
                    f"No subfolders were found inside:\n{watch_dir}",
                )
                return

            merged_preview = merge_architectures(self._arch, scanned)
            new_count = max(0, len(merged_preview.nodes) - len(self._arch.nodes))
            existing_count = max(0, folder_count - new_count)

            from download_sorter.ui.emblems import FolderScanRadarWidget

            dlg = QDialog(self)
            dlg.setWindowTitle("Scan Existing Folders — Root Map Preview")
            dlg.setMinimumSize(540, 410)
            v = QVBoxLayout(dlg)
            v.setContentsMargins(16, 16, 16, 16)
            v.setSpacing(10)

            hdr = QHBoxLayout()
            hdr.setSpacing(12)
            radar = FolderScanRadarWidget(size=52, scanning=True)
            lbl = QLabel(
                f"Found {folder_count} folder(s) ({new_count} new, {existing_count} already mapped) "
                f"across {result.get('max_tier', 1)} tier(s) and {file_count} file(s)."
            )
            lbl.setWordWrap(True)
            hdr.addWidget(radar)
            hdr.addWidget(lbl, 1)
            v.addLayout(hdr)

            preview_list = QListWidget()
            for node in sorted(scanned.nodes, key=lambda n: (n.tier, n.name.lower())):
                indent = "  " * (node.tier - 1)
                tags: list[str] = []
                if node.extensions:
                    tags.append(", ".join(node.extensions[:4]))
                if node.keywords:
                    tags.append("kw: " + ", ".join(node.keywords[:3]))
                if node.domains:
                    tags.append("site: " + ", ".join(node.domains[:2]))
                detail = f"  ({'; '.join(tags)})" if tags else ""
                preview_list.addItem(f"{indent}[T{node.tier}] {node.name}{detail}")
            v.addWidget(preview_list, 1)

            btns = QHBoxLayout()
            choice = {"mode": ""}
            btn_merge = QPushButton("Merge Missing")
            btn_merge.setToolTip("Keep your current root map and add any missing folders from disk")
            btn_replace = QPushButton("Replace Map")
            btn_replace.setObjectName("PrimaryButton")
            btn_replace.setToolTip("Replace the current canvas with the scanned folder map")
            btn_cancel = QPushButton("Cancel")
            btn_cancel.setToolTip("Close scan preview without modifying the canvas")

            def _pick(mode: str) -> None:
                choice["mode"] = mode
                dlg.accept()

            btn_merge.clicked.connect(lambda: _pick("merge"))
            btn_replace.clicked.connect(lambda: _pick("replace"))
            btn_cancel.clicked.connect(dlg.reject)
            btns.addStretch(1)
            btns.addWidget(btn_cancel)
            btns.addWidget(btn_merge)
            btns.addWidget(btn_replace)
            v.addLayout(btns)

            if dlg.exec() != QDialog.DialogCode.Accepted or not choice["mode"]:
                self.architecture_applied.emit(
                    f"Scanned {folder_count} folder(s) ({new_count} new, {existing_count} already mapped) — canvas unchanged."
                )
                return

            prev_rects = self.canvas.snapshot_rects()
            self._push_undo_snapshot()
            self._active_preset = "scanned"
            self._update_blueprint_pills()
            if choice["mode"] == "merge":
                self._arch = merged_preview
            else:
                self._arch = scanned
            self._dirty = True
            self._redraw_canvas(animate=True, prev_rects=prev_rects)
            if self._arch.nodes:
                self.canvas.select_node(self._arch.nodes[0].id)
            self.architecture_applied.emit(
                f"Scanned {folder_count} folder(s) ({new_count} new, {existing_count} existing) via {choice['mode']} — click Apply & Shift Files to sync rules."
            )

        self._run_background(
            _work,
            _done,
            title="Scanning existing folder architecture...",
            pass_progress=True,
            on_error=_restore_scan_btn,
        )

    def _open_snapshots_dialog(self) -> None:
        import time
        from download_sorter.ui.emblems import SnapshotEmblemWidget

        dlg = QDialog(self)
        dlg.setWindowTitle("Folder Architecture Snapshots")
        dlg.setMinimumSize(540, 420)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(10)

        hdr = QHBoxLayout()
        hdr.setSpacing(12)
        emblem = SnapshotEmblemWidget(size=48)
        hdr_text = QLabel(
            "Save a named snapshot of your current Folder Architecture so you can experiment freely and restore any layout in 1 click.",
            dlg,
        )
        hdr_text.setVisible(False)
        dlg.setToolTip(
            "Save named snapshots of your Folder Architecture so you can experiment freely and restore any layout in 1 click."
        )
        hdr_title = QLabel("Architecture Snapshots")
        hdr_title.setStyleSheet("font-size: 14px; font-weight: 600; color: #F5F5F7;")
        hdr.addWidget(emblem)
        hdr.addWidget(hdr_title, 1)
        v.addLayout(hdr)

        save_row = QHBoxLayout()
        edit_name = QLineEdit()
        edit_name.setPlaceholderText("Snapshot name (e.g. Before School Cleanup)")
        btn_save_snap = QPushButton("Save Snapshot")
        btn_save_snap.setObjectName("PrimaryButton")
        btn_save_snap.setToolTip("Save the current Folder Architecture layout under this name")
        save_row.addWidget(edit_name, 1)
        save_row.addWidget(btn_save_snap)
        v.addLayout(save_row)

        snap_list = QListWidget()
        v.addWidget(snap_list, 1)

        def _reload_list() -> None:
            snap_list.clear()
            for item in list_architecture_snapshots(self.service.paths.data_dir):
                ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(item.get("created_at") or 0)))
                li = QListWidgetItem(
                    f"{item.get('name')}  —  {item.get('node_count', 0)} folders, "
                    f"{item.get('tier_count', 2)} tiers  ({ts})"
                )
                li.setData(Qt.ItemDataRole.UserRole, item.get("id"))
                snap_list.addItem(li)

        def _on_save_snap() -> None:
            save_architecture_snapshot(self._arch, edit_name.text().strip(), self.service.paths.data_dir)
            edit_name.clear()
            _reload_list()

        def _on_restore() -> None:
            cur = snap_list.currentItem()
            if cur is None:
                return
            sid = str(cur.data(Qt.ItemDataRole.UserRole) or "")
            restored = load_architecture_snapshot(sid, self.service.paths.data_dir)
            if restored is None:
                return
            prev_rects = self.canvas.snapshot_rects()
            self._push_undo_snapshot()
            self._arch = restored
            self._dirty = True
            self._redraw_canvas(animate=True, prev_rects=prev_rects)
            self.architecture_applied.emit("Restored Folder Architecture snapshot — click Apply & Shift Files to sync.")
            dlg.accept()

        def _on_delete() -> None:
            cur = snap_list.currentItem()
            if cur is None:
                return
            sid = str(cur.data(Qt.ItemDataRole.UserRole) or "")
            if delete_architecture_snapshot(sid, self.service.paths.data_dir):
                _reload_list()

        btn_save_snap.clicked.connect(_on_save_snap)
        _reload_list()

        btn_row = QHBoxLayout()
        btn_restore = QPushButton("Restore Selected")
        btn_restore.setObjectName("PrimaryButton")
        btn_restore.setToolTip("Load the highlighted snapshot onto the Folder Architecture canvas")
        btn_restore.clicked.connect(_on_restore)
        btn_del = QPushButton("Delete")
        btn_del.setToolTip("Permanently remove the selected snapshot from disk")
        btn_del.clicked.connect(_on_delete)
        btn_close = QPushButton("Close")
        btn_close.setToolTip("Close the snapshots manager")
        btn_close.clicked.connect(dlg.reject)
        btn_row.addWidget(btn_del)
        btn_row.addStretch(1)
        btn_row.addWidget(btn_close)
        btn_row.addWidget(btn_restore)
        v.addLayout(btn_row)
        dlg.exec()

    def _on_add_tier(self) -> None:
        if self._arch.tier_count >= FolderArchitecture.MAX_TIERS:
            return
        prev_rects = self.canvas.snapshot_rects()
        self._push_undo_snapshot()
        self._arch.add_tier()
        self._dirty = True
        self._redraw_canvas(animate=True, prev_rects=prev_rects)

    def _on_remove_tier(self) -> None:
        if self._arch.tier_count <= 1:
            return
        prev_rects = self.canvas.snapshot_rects()
        self._push_undo_snapshot()
        self._arch.remove_last_tier()
        self._dirty = True
        self._redraw_canvas(animate=True, prev_rects=prev_rects)

    def _on_tier_quick_add(self, tier: int) -> None:
        parent_id = ""
        if tier > 1:
            sel = self._arch.get_node(self.canvas.selected_node_id)
            if sel is not None and sel.tier == tier - 1:
                parent_id = sel.id
            else:
                prev_nodes = self._arch.nodes_in_tier(tier - 1)
                if not prev_nodes:
                    self._open_premade_dialog(default_tier=tier)
                    return
                parent_id = prev_nodes[0].id
        self._on_spawn_child_inline(parent_id, "New Folder")

    def _on_add_child_for_selected(self) -> None:
        node = self._arch.get_node(self.canvas.selected_node_id)
        if node is None or node.tier >= FolderArchitecture.MAX_TIERS:
            return
        self._on_spawn_child_inline(node.id, "New Subfolder")

    def _open_premade_dialog(self, default_tier: int = 2, default_parent_id: str | None = None) -> None:
        dlg = PremadeFolderDialog(
            self._arch,
            default_tier=default_tier,
            default_parent_id=default_parent_id,
            parent=self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            keys = dlg.selected_keys()
            if not keys:
                return
            prev_rects = self.canvas.snapshot_rects()
            self._push_undo_snapshot()
            t_tier = dlg.target_tier()
            p_id = dlg.target_parent_id()
            last_added: ArchNode | None = None
            for k in keys:
                node = self._arch.add_premade_node(k, tier=t_tier, parent_id=p_id)
                if node is not None:
                    last_added = node
            self._dirty = True
            self._redraw_canvas(animate=True, prev_rects=prev_rects)
            if last_added is not None:
                self.canvas.select_node(last_added.id)

    def _open_custom_dialog(
        self,
        *,
        default_tier: int = 1,
        default_parent_id: str | None = None,
    ) -> None:
        dlg = CustomFolderNodeDialog(
            self._arch,
            default_tier=default_tier,
            default_parent_id=default_parent_id,
            parent=self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            prev_rects = self.canvas.snapshot_rects()
            self._push_undo_snapshot()
            vals = dlg.values()
            node = self._arch.add_custom_node(**vals)
            self._dirty = True
            self._redraw_canvas(animate=True, prev_rects=prev_rects)
            self.canvas.select_node(node.id)

    def _edit_node_by_id(self, node_id: str) -> None:
        node = self._arch.get_node(node_id)
        if node is None:
            return
        dlg = CustomFolderNodeDialog(self._arch, node=node, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            prev_rects = self.canvas.snapshot_rects()
            self._push_undo_snapshot()
            vals = dlg.values()
            self._arch.update_node(node.id, **vals)
            self._dirty = True
            self._redraw_canvas(animate=True, prev_rects=prev_rects)
            self.canvas.select_node(node.id)

    def _delete_node_by_id(self, node_id: str) -> None:
        if not node_id or self._arch.get_node(node_id) is None:
            return
        prev_rects = self.canvas.snapshot_rects()
        self._push_undo_snapshot()
        self._arch.remove_node(node_id)
        self._dirty = True
        self._redraw_canvas(animate=True, prev_rects=prev_rects)

    def _run_background(
        self,
        work,  # noqa: ANN001
        on_done,  # noqa: ANN001
        *,
        title: str = "Updating folders...",
        pass_progress: bool = False,
        on_error=None,  # noqa: ANN001
    ) -> None:
        """Run a disk-heavy service call off the GUI thread, one at a time.

        Under pytest this is a plain synchronous call followed by ``on_done``.
        """
        if getattr(self, "_busy", False):
            if on_error is not None:
                on_error("Busy")
            return
        self._busy = True
        self.action_started.emit(title)

        def _finished(result) -> None:  # noqa: ANN001
            self._busy = False
            self.action_finished.emit(title, "success")
            on_done(result)

        def _failed(message: str) -> None:
            self._busy = False
            self.action_finished.emit(f"Failed: {message}", "warning")
            if on_error is not None:
                on_error(message)
            QMessageBox.warning(self, "Folder Architecture", f"That did not finish:\n{message}")

        def _progress(done: int, total: int, label: str) -> None:
            self.action_progress.emit(done, total, label)

        try:
            run_async(
                work,
                _finished,
                _failed,
                parent=self,
                on_progress=_progress,
                pass_progress=pass_progress,
            )
        except Exception:
            self._busy = False
            self.action_finished.emit("Failed", "warning")
            if on_error is not None:
                on_error("Error")
            raise

    def _on_apply_and_shift(self) -> None:
        self._on_inspector_fields_committed()
        target_cfg = apply_architecture_to_config(self._arch, self.service.config)
        self._run_background(
            lambda: self.service.preview_architecture_shift(target_cfg),
            lambda plan: self._confirm_and_shift(target_cfg, plan),
            title="Scanning managed folders...",
        )

    def _confirm_and_shift(self, target_cfg, plan: dict[str, Any]) -> None:  # noqa: ANN001
        dlg = ShiftConfirmDialog(self._arch, plan, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        create_empty = dlg.chk_create_empty.isChecked()

        def _work(report_progress) -> dict[str, Any]:  # noqa: ANN001
            return self.service.apply_architecture_and_shift(
                target_cfg,
                shift_files=True,
                create_empty_folders=create_empty,
                on_progress=report_progress,
            )

        def _done(res: dict[str, Any]) -> None:
            self._dirty = False
            self.sync_from_service(force=True)
            shifted = res["shifted_count"]
            self.architecture_applied.emit(
                f"Applied {self._arch.tier_count}-tier Folder Architecture and shifted {shifted} file(s)."
            )

        self._run_background(
            _work,
            _done,
            title="Applying Folder Architecture...",
            pass_progress=True,
        )

    def _on_undo_clicked(self) -> None:
        def _work(report_progress) -> dict[str, Any]:  # noqa: ANN001
            return self.service.undo_architecture_or_last_batch(on_progress=report_progress)

        def _done(res: dict[str, Any]) -> None:
            self._dirty = False
            self.sync_from_service(force=True)
            self.undo_requested.emit()
            restored = res.get("restored", 0)
            cfg_restored = res.get("config_restored", False)
            locked = int(res.get("skipped_locked", 0) or 0)
            if restored or cfg_restored:
                tail = f" {locked} file(s) are open in another app and stayed put." if locked else ""
                self.architecture_applied.emit(
                    f"Undid architecture change: restored {restored} shifted file(s).{tail}"
                )
            elif locked:
                self.architecture_applied.emit(
                    f"Nothing to undo right now: {locked} file(s) are open in another app."
                )
            else:
                self.architecture_applied.emit("Nothing to undo right now.")

        self._run_background(
            _work,
            _done,
            title="Undoing architecture change...",
            pass_progress=True,
        )

    def _on_return_to_defaults(self) -> None:
        ans = QMessageBox.question(
            self,
            "Return Folder Architecture to Defaults",
            "Reset your Folder Architecture and sorting rules back to the starter defaults, and shift managed files back into their default folders?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return

        def _done(res: dict[str, Any]) -> None:
            self._dirty = False
            self.sync_from_service(force=True)
            self.architecture_applied.emit(
                f"Returned to starter defaults and shifted {res['shifted_count']} file(s)."
            )

        self._run_background(
            lambda: self.service.reset_to_defaults_and_shift(shift_files=True),
            _done,
        )
