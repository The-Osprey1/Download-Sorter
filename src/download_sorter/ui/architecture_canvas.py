"""Interactive direct-manipulation Folder Architecture canvas, template shelf, pills & dialogs."""

from __future__ import annotations

import json
import math
import os
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
    QPolygonF,
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
    QSpinBox,
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
    get_tier2_suggestions,
    list_architecture_snapshots,
    load_architecture_snapshot,
    merge_architectures,
    save_architecture_snapshot,
    scan_folder_architecture,
)
from download_sorter.project_detect import DetectedProject, detect_projects_in_folder
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
        self.list_parents.currentRowChanged.connect(lambda _row: self._refresh_suggestions())

        # Tier 2 Suggested Subfolders Picker (By File Type vs. By Classification, zero QComboBox)
        self._sugg_mode = "all"
        self._sugg_label = QLabel("Suggested Options:")
        self._sugg_wrap = QWidget()
        sugg_v = QVBoxLayout(self._sugg_wrap)
        sugg_v.setContentsMargins(0, 0, 0, 0)
        sugg_v.setSpacing(5)
        sugg_tabs = QHBoxLayout()
        sugg_tabs.setSpacing(5)
        self._sugg_mode_btns: dict[str, QPushButton] = {}
        for mkey, mlabel in (
            ("all", "All"),
            ("file_type", "By File Type"),
            ("classification", "By Classification"),
        ):
            mb = _make_pill_button(
                mlabel,
                checkable=True,
                extra_css="QPushButton#ArchPillBtn { padding: 2px 8px; font-size: 10.5px; }",
            )
            mb.clicked.connect(lambda _chk, m=mkey: self._set_suggestion_mode(m))
            self._sugg_mode_btns[mkey] = mb
            sugg_tabs.addWidget(mb)
        sugg_tabs.addStretch(1)
        sugg_v.addLayout(sugg_tabs)

        self.list_suggestions = QListWidget()
        self.list_suggestions.setMaximumHeight(112)
        self.list_suggestions.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_suggestions.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.list_suggestions.setToolTip(
            "Click a suggested subfolder to pre-fill name & rules, or type any custom folder name above"
        )
        self.list_suggestions.itemClicked.connect(self._apply_dialog_suggestion)
        sugg_v.addWidget(self.list_suggestions)

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
        form.addRow(self._sugg_label, self._sugg_wrap)
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
        self._set_suggestion_mode("all")
        self._select_tier(self._selected_tier)

    def _set_suggestion_mode(self, mode: str) -> None:
        self._sugg_mode = mode
        for mkey, btn in self._sugg_mode_btns.items():
            btn.setChecked(mkey == mode)
        self._refresh_suggestions()

    def _refresh_suggestions(self) -> None:
        self.list_suggestions.clear()
        if self._selected_tier < 2:
            self._sugg_label.setVisible(False)
            self._sugg_wrap.setVisible(False)
            return
        cur = self.list_parents.currentItem()
        parent_id = str(cur.data(Qt.ItemDataRole.UserRole) or "") if cur is not None else ""
        pnode = self.arch.get_node(parent_id) if parent_id else None
        suggestions = (
            get_tier2_suggestions(pnode.name, pnode.kind, mode=self._sugg_mode)
            if pnode is not None
            else []
        )
        has_any = bool(
            pnode is not None and get_tier2_suggestions(pnode.name, pnode.kind, mode="all")
        )
        self._sugg_label.setVisible(has_any)
        self._sugg_wrap.setVisible(has_any)
        if not has_any:
            return
        for s in suggestions:
            tag = "FILE TYPE" if s["group"] == "file_type" else "CLASS"
            hints: list[str] = []
            if s.get("extensions"):
                hints.append(", ".join(s["extensions"][:3]) + ("..." if len(s["extensions"]) > 3 else ""))
            if s.get("keywords"):
                hints.append("kw: " + ", ".join(s["keywords"][:3]))
            if s.get("domains"):
                hints.append("site: " + ", ".join(s["domains"][:2]))
            hint_str = "  ·  ".join(hints)
            li = QListWidgetItem(f"[{tag}]  {s['name']}   ({hint_str})" if hint_str else f"[{tag}]  {s['name']}")
            li.setToolTip(f"{s.get('summary', '')}\nClick to fill folder name & sorting rules.")
            li.setData(Qt.ItemDataRole.UserRole, s)
            self.list_suggestions.addItem(li)

    def _apply_dialog_suggestion(self, item: QListWidgetItem) -> None:
        s = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(s, dict):
            return
        self.edit_name.setText(str(s.get("name", "")))
        self.edit_exts.setText(", ".join(s.get("extensions", [])))
        self.edit_keywords.setText(", ".join(s.get("keywords", [])))
        self.edit_domains.setText(", ".join(s.get("domains", [])))
        kind_val = str(s.get("kind", "") or "")
        if kind_val in self._kind_btns:
            self._select_kind(kind_val)

    def _select_kind(self, kind_key: str) -> None:
        self._selected_kind = kind_key
        for k, btn in self._kind_btns.items():
            btn.setChecked(k == kind_key)

    def _select_tier(self, tier: int) -> None:
        self._selected_tier = max(1, min(self.arch.tier_count, tier))
        for t, btn in self._tier_btns.items():
            btn.setChecked(t == self._selected_tier)
        self._populate_parents(self._preferred_parent_id)
        self._refresh_suggestions()

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


class MergeFolderDialog(QDialog):
    """Pick a target folder to merge the selected folder's rules, subfolders, and files into."""

    def __init__(
        self,
        arch: FolderArchitecture,
        source_node: ArchNode,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._arch = arch
        self._source = source_node
        self._valid_targets = [
            n for n in arch.nodes if arch.can_merge(source_node.id, n.id)
        ]
        # Prefer siblings in the same (tier, parent_id) first, then same tier, then alphabetical
        self._valid_targets.sort(
            key=lambda n: (
                0 if (n.tier == source_node.tier and n.parent_id == source_node.parent_id) else 1,
                0 if n.tier == source_node.tier else 1,
                n.tier,
                n.name.lower(),
            )
        )

        self.setWindowTitle(f"Merge '{source_node.name}' Into...")
        self.setMinimumSize(440, 400)
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(10)

        src_rel = arch.node_relative_path(source_node.id)
        lbl_hdr = QLabel(f"Merge Downloads\\{src_rel} into another folder")
        lbl_hdr.setStyleSheet("font-size: 13.5px; font-weight: 600; color: #F5F5F7;")
        lbl_hdr.setWordWrap(True)
        v.addWidget(lbl_hdr)

        lbl_info = QLabel(
            "Combines extensions, keywords, and website rules into the target folder and reparents any subfolders."
        )
        lbl_info.setStyleSheet("color: #98989D; font-size: 11.5px;")
        lbl_info.setWordWrap(True)
        v.addWidget(lbl_info)

        lbl_list = QLabel("Target Folder")
        lbl_list.setStyleSheet("color: #AEAEB2; font-size: 11px; font-weight: 600;")
        v.addWidget(lbl_list)

        self.list_targets = QListWidget()
        self.list_targets.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        for cand in self._valid_targets:
            rel = arch.node_relative_path(cand.id)
            item = QListWidgetItem(f"{cand.name}  (Tier {cand.tier} · Downloads\\{rel})")
            item.setData(Qt.ItemDataRole.UserRole, cand.id)
            item.setData(Qt.ItemDataRole.UserRole + 1, cand.name)
            self.list_targets.addItem(item)
        v.addWidget(self.list_targets, 1)

        name_row = QFormLayout()
        name_row.setSpacing(6)
        self.edit_target_name = QLineEdit()
        self.edit_target_name.setPlaceholderText("Merged folder name...")
        self.edit_target_name.setToolTip("Keep the target folder's name or enter a combined name")
        name_row.addRow("Resulting Name:", self.edit_target_name)
        v.addLayout(name_row)

        self.chk_move_disk_files = QCheckBox(
            "Also move existing files on disk from source folder into target folder"
        )
        self.chk_move_disk_files.setChecked(True)
        v.addWidget(self.chk_move_disk_files)

        self.list_targets.currentItemChanged.connect(self._on_target_changed)
        if self.list_targets.count() > 0:
            self.list_targets.setCurrentRow(0)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_btn = btns.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn is not None:
            ok_btn.setText("Merge Folders")
            ok_btn.setObjectName("PrimaryButton")
            ok_btn.setEnabled(self.list_targets.count() > 0)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        v.addWidget(btns)

    def _on_target_changed(self, cur: QListWidgetItem | None, _prev: QListWidgetItem | None) -> None:
        if cur is not None:
            self.edit_target_name.setText(str(cur.data(Qt.ItemDataRole.UserRole + 1) or ""))

    def selected_target_id(self) -> str | None:
        cur = self.list_targets.currentItem()
        if cur is None:
            return None
        val = cur.data(Qt.ItemDataRole.UserRole)
        return str(val) if val else None

    def merged_name(self) -> str:
        return self.edit_target_name.text().strip()

    def move_disk_files(self) -> bool:
        return self.chk_move_disk_files.isChecked()


class DeleteFolderDialog(QDialog):
    """Confirm removing a folder, especially when files or child subfolders exist on disk."""

    def __init__(
        self,
        arch: FolderArchitecture,
        node: ArchNode,
        disk_info: dict[str, Any],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._arch = arch
        self._node = node
        self._disk_info = dict(disk_info or {})
        self._file_count = int(self._disk_info.get("file_count", 0) or 0)
        self._exists_on_disk = bool(self._disk_info.get("exists"))
        self._children = arch.children_of(node.id)
        self.list_actions: QListWidget | None = None
        self.chk_remove_empty: QCheckBox | None = None
        self.chk_promote_children: QCheckBox | None = None
        self.lbl_disk_warning: QLabel | None = None

        self.setObjectName("DeleteFolderDialog")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        from download_sorter.ui.theme import STYLESHEET

        self.setStyleSheet(
            STYLESHEET
            + "\nQDialog#DeleteFolderDialog { background-color: #111113; color: #F5F5F7; }"
        )
        self.setWindowTitle("Remove Folder — Are you sure?")
        self.setMinimumSize(460, 300)
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(10)

        rel_path = arch.node_relative_path(node.id)
        lbl_hdr = QLabel(f"Remove '{node.name}' (Downloads\\{rel_path})?")
        lbl_hdr.setStyleSheet("font-size: 14px; font-weight: 600; color: #F5F5F7;")
        lbl_hdr.setWordWrap(True)
        v.addWidget(lbl_hdr)

        if self._file_count > 0:
            samples = [str(s) for s in (self._disk_info.get("sample_files") or []) if str(s).strip()]
            sample_txt = ", ".join(samples[:3])
            if self._file_count > min(3, len(samples)):
                sample_txt += f", +{self._file_count - min(3, len(samples))} more"
            sample_clause = f" ({sample_txt})" if sample_txt else ""
            warn = QLabel(
                f"This folder contains {self._file_count} file(s) on disk{sample_clause}. "
                "Your files will never be deleted — choose where they should go:"
            )
            warn.setWordWrap(True)
            warn.setStyleSheet(
                "background: rgba(255, 159, 10, 0.14); color: #FFB340; "
                "border: 1px solid rgba(255, 159, 10, 0.45); border-radius: 8px; "
                "padding: 8px 10px; font-size: 12px;"
            )
            self.lbl_disk_warning = warn
            v.addWidget(warn)

            self.list_actions = QListWidget()
            self.list_actions.setSelectionMode(QListWidget.SelectionMode.SingleSelection)

            parent_node = arch.get_node(node.parent_id)
            if parent_node is not None:
                parent_rel = arch.node_relative_path(parent_node.id)
                parent_label = f"Downloads\\{parent_rel}"
            else:
                parent_rel = ""
                parent_label = "Downloads\\"

            item_evac_parent = QListWidgetItem(
                f"Move {self._file_count} file(s) to {parent_label} and remove folder"
            )
            item_evac_parent.setData(Qt.ItemDataRole.UserRole, ("evacuate", parent_rel))
            self.list_actions.addItem(item_evac_parent)

            item_keep = QListWidgetItem(
                "Keep files in place on disk (remove from Architecture diagram only)"
            )
            item_keep.setData(Qt.ItemDataRole.UserRole, ("keep", None))
            self.list_actions.addItem(item_keep)

            descendant_ids = {d.id for d in arch.descendants_of(node.id)}
            for cand in arch.nodes:
                if cand.id == node.id or cand.id in descendant_ids:
                    continue
                if parent_node is not None and cand.id == parent_node.id:
                    continue
                cand_rel = arch.node_relative_path(cand.id)
                item_cand = QListWidgetItem(
                    f"Move {self._file_count} file(s) into Downloads\\{cand_rel}"
                )
                item_cand.setData(Qt.ItemDataRole.UserRole, ("evacuate", cand_rel))
                self.list_actions.addItem(item_cand)

            self.list_actions.setCurrentRow(0)
            v.addWidget(self.list_actions, 1)
        elif self._exists_on_disk:
            info = QLabel("This folder exists on disk and is currently empty (0 files).")
            info.setStyleSheet("color: #AEAEB2; font-size: 12px;")
            info.setWordWrap(True)
            v.addWidget(info)
            self.chk_remove_empty = QCheckBox("Also remove the empty folder from disk")
            self.chk_remove_empty.setChecked(True)
            v.addWidget(self.chk_remove_empty)
        else:
            info = QLabel("This folder has no files on disk yet.")
            info.setStyleSheet("color: #AEAEB2; font-size: 12px;")
            info.setWordWrap(True)
            v.addWidget(info)

        if self._children:
            self.chk_promote_children = QCheckBox(
                f"Keep {len(self._children)} child subfolder(s) by promoting them up one tier"
            )
            self.chk_promote_children.setChecked(True)
            v.addWidget(self.chk_promote_children)

        btns = QHBoxLayout()
        btns.addStretch(1)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_confirm_remove = QPushButton("Remove Folder")
        self.btn_confirm_remove.setStyleSheet(
            "QPushButton { background-color: #FF453A; color: #FFFFFF; border: 1px solid #FF453A; "
            "border-radius: 7px; font-weight: 600; padding: 6px 14px; }"
            "QPushButton:hover { background-color: #FF5B52; }"
        )
        self.btn_confirm_remove.clicked.connect(self.accept)
        btns.addWidget(self.btn_cancel)
        btns.addWidget(self.btn_confirm_remove)
        v.addLayout(btns)

    def file_action(self) -> tuple[str, str | None]:
        if self.list_actions is None:
            return ("none", None)
        cur = self.list_actions.currentItem()
        if cur is None:
            return ("keep", None)
        data = cur.data(Qt.ItemDataRole.UserRole)
        if isinstance(data, tuple) and len(data) == 2:
            return (str(data[0]), data[1] if data[1] is None else str(data[1]))
        return ("keep", None)

    def should_remove_empty_dir(self) -> bool:
        mode, _target = self.file_action()
        if mode == "evacuate":
            return True
        if self.chk_remove_empty is not None:
            return self.chk_remove_empty.isChecked()
        return False

    def should_promote_children(self) -> bool:
        if self.chk_promote_children is not None:
            return self.chk_promote_children.isChecked()
        return False


class DetectProjectsDialog(QDialog):
    """Interactive dialog to inspect, rename, filter, and add auto-detected project subfolders."""

    def __init__(
        self,
        parent_node: ArchNode,
        folder_path,  # Path
        projects: list[DetectedProject],
        *,
        exclude_subdirs: set[str],
        existing_child_names: set[str],
        min_files: int = 3,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._parent_node = parent_node
        self._folder_path = folder_path
        self._exclude_subdirs = set(exclude_subdirs)
        self._existing_child_names = {n.lower() for n in existing_child_names}
        self._projects: list[DetectedProject] = list(projects)

        self.setWindowTitle(f"Detect Projects — {parent_node.name}")
        self.resize(780, 520)

        v = QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 16)
        v.setSpacing(10)

        title = QLabel(f"Detected Projects in {parent_node.name}")
        title.setStyleSheet("font-size: 15px; font-weight: 700; color: #F5F5F7;")
        v.addWidget(title)

        self.lbl_summary = QLabel(
            "Select the projects you want to add as child subfolders on the canvas. "
            "Double-click a project name or its keywords to edit before adding. "
            "No files move until you click Apply & Shift Files."
        )
        self.lbl_summary.setStyleSheet("font-size: 12px; color: #A1A1A6;")
        self.lbl_summary.setWordWrap(True)
        v.addWidget(self.lbl_summary)

        # Controls bar: Min files spinbox + Select All / None
        ctrl = QHBoxLayout()
        ctrl.setSpacing(8)
        lbl_min = QLabel("Min files per project:")
        lbl_min.setStyleSheet("font-size: 12px; color: #D1D1D6;")
        ctrl.addWidget(lbl_min)

        self.spin_min_files = QSpinBox()
        self.spin_min_files.setRange(2, 20)
        self.spin_min_files.setValue(max(2, min(20, int(min_files))))
        self.spin_min_files.setFixedWidth(64)
        self.spin_min_files.setToolTip("Minimum matching files required to suggest a project")
        self.spin_min_files.valueChanged.connect(self._on_min_files_changed)
        ctrl.addWidget(self.spin_min_files)

        self.lbl_count_badge = QLabel("")
        self.lbl_count_badge.setStyleSheet("font-size: 12px; color: #5AC8FA; font-weight: 600;")
        ctrl.addWidget(self.lbl_count_badge)
        ctrl.addStretch(1)

        btn_all = QPushButton("Select All")
        btn_all.clicked.connect(lambda: self._set_all_checked(True))
        btn_none = QPushButton("Select None")
        btn_none.clicked.connect(lambda: self._set_all_checked(False))
        ctrl.addWidget(btn_all)
        ctrl.addWidget(btn_none)
        v.addLayout(ctrl)

        # Projects table
        self.tbl = QTableWidget(0, 4)
        self.tbl.setHorizontalHeaderLabels(["Project Subfolder", "Files & Types", "Confidence", "Auto-Route Keywords"])
        self.tbl.setAlternatingRowColors(True)
        self.tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.tbl.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.tbl.verticalHeader().setVisible(False)
        hdr = self.tbl.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.tbl.itemChanged.connect(self._on_item_changed)
        self.tbl.currentCellChanged.connect(self._on_row_selected)
        v.addWidget(self.tbl, 1)

        # Sample files preview box
        preview_box = QFrame()
        preview_box.setStyleSheet(
            "QFrame { background-color: #161618; border: 1px solid #2C2C2E; border-radius: 8px; }"
        )
        pl = QVBoxLayout(preview_box)
        pl.setContentsMargins(10, 8, 10, 8)
        pl.setSpacing(4)
        self.lbl_samples_title = QLabel("Sample matching files")
        self.lbl_samples_title.setStyleSheet("font-size: 11px; font-weight: 600; color: #8E8E93; border: none;")
        pl.addWidget(self.lbl_samples_title)
        self.lbl_samples = QLabel("Click a project row above to inspect its files.")
        self.lbl_samples.setStyleSheet("font-size: 11.5px; color: #D1D1D6; border: none;")
        self.lbl_samples.setWordWrap(True)
        pl.addWidget(self.lbl_samples)
        v.addWidget(preview_box)

        # Action buttons
        btns = QHBoxLayout()
        btns.addStretch(1)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_add = QPushButton("Add Selected Projects (0)")
        self.btn_add.setObjectName("PrimaryButton")
        self.btn_add.clicked.connect(self.accept)
        btns.addWidget(self.btn_cancel)
        btns.addWidget(self.btn_add)
        v.addLayout(btns)

        self._populate_table()

    def _populate_table(self) -> None:
        self.tbl.blockSignals(True)
        try:
            self.tbl.setRowCount(len(self._projects))
            for row, proj in enumerate(self._projects):
                already_exists = proj.name.lower() in self._existing_child_names
                name_item = QTableWidgetItem(proj.name)
                name_item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                    | Qt.ItemFlag.ItemIsUserCheckable
                    | Qt.ItemFlag.ItemIsEditable
                )
                default_checked = (not already_exists) and (proj.confidence >= 75 or len(self._projects) <= 6)
                name_item.setCheckState(Qt.CheckState.Checked if default_checked else Qt.CheckState.Unchecked)
                name_item.setData(Qt.ItemDataRole.UserRole, row)
                if already_exists:
                    name_item.setToolTip("A subfolder with this name already exists under this folder")
                self.tbl.setItem(row, 0, name_item)

                ext_str = ", ".join(proj.extensions[:4])
                if len(proj.extensions) > 4:
                    ext_str += f" +{len(proj.extensions) - 4}"
                files_text = f"{proj.file_count} files ({ext_str})" if ext_str else f"{proj.file_count} files"
                if already_exists:
                    files_text += " · exists"
                files_item = QTableWidgetItem(files_text)
                files_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.tbl.setItem(row, 1, files_item)

                conf_item = QTableWidgetItem(f"{proj.confidence}% ({proj.confidence_label})")
                conf_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                conf_color = "#30D158" if proj.confidence >= 75 else ("#FFD60A" if proj.confidence >= 55 else "#98989D")
                conf_item.setForeground(QColor(conf_color))
                self.tbl.setItem(row, 2, conf_item)

                kw_item = QTableWidgetItem(", ".join(proj.keywords))
                kw_item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEditable
                )
                self.tbl.setItem(row, 3, kw_item)
        finally:
            self.tbl.blockSignals(False)

        self.lbl_count_badge.setText(f"{len(self._projects)} project(s) found")
        self._update_add_button()
        if self._projects:
            self.tbl.selectRow(0)
            self._on_row_selected(0, 0, -1, -1)
        else:
            self.lbl_samples_title.setText("No projects detected at this threshold")
            self.lbl_samples.setText("Try lowering 'Min files per project' to 2.")

    def _on_min_files_changed(self, value: int) -> None:
        self._projects = detect_projects_in_folder(
            self._folder_path,
            min_files=int(value),
            exclude_subdirs=self._exclude_subdirs,
        )
        self._populate_table()

    def _set_all_checked(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self.tbl.blockSignals(True)
        try:
            for row in range(self.tbl.rowCount()):
                item = self.tbl.item(row, 0)
                if item is not None:
                    item.setCheckState(state)
        finally:
            self.tbl.blockSignals(False)
        self._update_add_button()

    def _on_item_changed(self, _item: QTableWidgetItem) -> None:
        self._update_add_button()

    def _on_row_selected(self, row: int, _col: int, _prev_row: int, _prev_col: int) -> None:
        if 0 <= row < len(self._projects):
            proj = self._projects[row]
            self.lbl_samples_title.setText(
                f"Sample files for '{proj.name}' ({proj.file_count} total, {proj.distinct_count} distinct)"
            )
            preview = "  ·  ".join(proj.sample_files[:6])
            if proj.file_count > 6:
                preview += f"  (+{proj.file_count - 6} more)"
            self.lbl_samples.setText(preview)

    def _update_add_button(self) -> None:
        count = len(self.selected_projects())
        self.btn_add.setText(f"Add Selected Projects ({count})")
        self.btn_add.setEnabled(count > 0)

    def selected_projects(self) -> list[dict[str, Any]]:
        chosen: list[dict[str, Any]] = []
        for row in range(self.tbl.rowCount()):
            name_item = self.tbl.item(row, 0)
            kw_item = self.tbl.item(row, 3)
            if name_item is None or name_item.checkState() != Qt.CheckState.Checked:
                continue
            proj = self._projects[row]
            clean_name = name_item.text().strip().replace("/", "").replace("\\", "") or proj.name
            raw_kws = [k.strip() for k in (kw_item.text() if kw_item else "").split(",") if k.strip()]
            chosen.append(
                {
                    "name": clean_name,
                    "keywords": raw_kws or list(proj.keywords),
                    "extensions": list(proj.extensions),
                }
            )
        return chosen


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


class _SubfolderSuggestionPopup(QFrame):
    """Floating Tier 2 subfolder dropdown popup (All | By File Type | By Classification) with zero QComboBox."""

    suggestion_chosen = Signal(dict)
    batch_group_chosen = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SubfolderSuggestionPopup")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._mode = "all"
        self._parent_name = ""
        self._all_suggestions: list[dict[str, Any]] = []
        self._query = ""

        self.setStyleSheet(
            """
            QFrame#SubfolderSuggestionPopup {
                background-color: #18181C;
                border: 1px solid #0A84FF;
                border-radius: 10px;
            }
            """
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        hdr_row = QHBoxLayout()
        hdr_row.setSpacing(4)
        self.lbl_title = QLabel("Suggested Subfolders")
        self.lbl_title.setStyleSheet("color: #AEAEB2; font-size: 10.5px; font-weight: 700;")
        self.lbl_title.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        hdr_row.addWidget(self.lbl_title, 1)

        self.lbl_custom_hint = QLabel("or type custom + Enter")
        self.lbl_custom_hint.setStyleSheet("color: #6E6E73; font-size: 10px;")
        self.lbl_custom_hint.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        hdr_row.addWidget(self.lbl_custom_hint)
        outer.addLayout(hdr_row)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(4)
        self._mode_btns: dict[str, QPushButton] = {}
        for mkey, mlabel in (
            ("all", "All"),
            ("file_type", "By File Type"),
            ("classification", "By Classification"),
        ):
            b = _make_pill_button(
                mlabel,
                checkable=True,
                extra_css="QPushButton#ArchPillBtn { padding: 2px 7px; font-size: 10.5px; }",
            )
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(lambda _chk, m=mkey: self.set_mode(m))
            self._mode_btns[mkey] = b
            mode_row.addWidget(b)
        mode_row.addStretch(1)
        outer.addLayout(mode_row)

        self.list_widget = QListWidget()
        self.list_widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list_widget.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.list_widget.setStyleSheet(
            """
            QListWidget {
                background-color: #121215;
                border: 1px solid #2C2C2E;
                border-radius: 7px;
                outline: none;
            }
            QListWidget::item {
                color: #F5F5F7;
                padding: 3px 7px;
                border-bottom: 1px solid #1E1E22;
                font-size: 11px;
            }
            QListWidget::item:hover {
                background-color: #24242A;
            }
            QListWidget::item:selected {
                background-color: rgba(10, 132, 255, 65);
                color: #FFFFFF;
            }
            """
        )
        self.list_widget.itemClicked.connect(self._on_item_clicked)
        outer.addWidget(self.list_widget, 1)

        batch_row = QHBoxLayout()
        batch_row.setSpacing(5)
        self.btn_add_types = _make_pill_button(
            "+ All File Types",
            checkable=False,
            tooltip="Create all suggested File Type subfolders under this parent",
            extra_css="QPushButton#ArchPillBtn { padding: 2px 7px; font-size: 10px; }",
        )
        self.btn_add_types.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_add_types.clicked.connect(lambda: self.batch_group_chosen.emit("file_type"))
        batch_row.addWidget(self.btn_add_types)

        self.btn_add_classes = _make_pill_button(
            "+ All Classifications",
            checkable=False,
            tooltip="Create all suggested Classification subfolders under this parent",
            extra_css="QPushButton#ArchPillBtn { padding: 2px 7px; font-size: 10px; }",
        )
        self.btn_add_classes.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_add_classes.clicked.connect(lambda: self.batch_group_chosen.emit("classification"))
        batch_row.addWidget(self.btn_add_classes)
        batch_row.addStretch(1)
        outer.addLayout(batch_row)

        self.setFixedSize(356, 254)
        self.hide()

    def mousePressEvent(self, event) -> None:  # noqa: ANN001
        event.accept()

    def set_parent_suggestions(self, parent_name: str, suggestions: list[dict[str, Any]]) -> None:
        self._parent_name = parent_name
        self._all_suggestions = list(suggestions)
        self._query = ""
        self.lbl_title.setText(f"{parent_name} Suggestions")
        self.btn_add_types.setVisible(any(s.get("group") == "file_type" for s in suggestions))
        self.btn_add_classes.setVisible(any(s.get("group") == "classification" for s in suggestions))
        self.set_mode("all")

    def set_mode(self, mode: str) -> None:
        self._mode = mode
        for mkey, btn in self._mode_btns.items():
            btn.setChecked(mkey == mode)
        self._rebuild_list()

    def set_query(self, text: str) -> None:
        raw = str(text or "").strip()
        if raw.lower() in ("new subfolder", "new folder"):
            raw = ""
        self._query = raw.lower()
        self._rebuild_list()

    def _rebuild_list(self) -> None:
        self.list_widget.clear()
        q = self._query
        for s in self._all_suggestions:
            if self._mode != "all" and s.get("group") != self._mode:
                continue
            hay = f"{s.get('name', '')} {s.get('summary', '')} {' '.join(s.get('extensions', []))} {' '.join(s.get('keywords', []))}".lower()
            if q and q not in hay:
                continue
            tag = "TYPE" if s.get("group") == "file_type" else "CLASS"
            hints: list[str] = []
            if s.get("extensions"):
                exts = s["extensions"]
                hints.append(", ".join(exts[:3]) + ("+" if len(exts) > 3 else ""))
            if s.get("keywords"):
                hints.append("kw: " + ", ".join(s["keywords"][:3]))
            if s.get("domains"):
                hints.append("site: " + ", ".join(s["domains"][:2]))
            preview = " · ".join(hints)
            label = f"[{tag}] {s['name']}   ({preview})" if preview else f"[{tag}] {s['name']}"
            li = QListWidgetItem(label)
            li.setToolTip(f"{s.get('summary', '')}\nClick to use '{s['name']}' and auto-fill its sorting rules.")
            li.setData(Qt.ItemDataRole.UserRole, s)
            self.list_widget.addItem(li)
        if self.list_widget.count() == 0:
            empty_li = QListWidgetItem(
                f"Press Enter to use custom name '{self._query}'" if self._query else "Type any custom folder name + Enter"
            )
            empty_li.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list_widget.addItem(empty_li)
        self.list_widget.setCurrentRow(-1)

    def move_selection(self, delta: int) -> bool:
        selectable = [
            i for i in range(self.list_widget.count())
            if isinstance(self.list_widget.item(i).data(Qt.ItemDataRole.UserRole), dict)
        ]
        if not selectable:
            return False
        cur = self.list_widget.currentRow()
        if cur not in selectable:
            target = selectable[0] if delta > 0 else selectable[-1]
        else:
            pos = selectable.index(cur)
            target = selectable[(pos + (1 if delta > 0 else -1)) % len(selectable)]
        self.list_widget.setCurrentRow(target)
        return True

    def current_suggestion(self) -> dict[str, Any] | None:
        item = self.list_widget.currentItem()
        if item is None:
            return None
        data = item.data(Qt.ItemDataRole.UserRole)
        return dict(data) if isinstance(data, dict) else None

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(data, dict):
            self.suggestion_chosen.emit(dict(data))


class FolderArchitectureCanvas(QWidget):
    """Interactive direct-manipulation QPainter multi-tier folder architecture graph."""

    node_selected = Signal(str)
    node_edit_requested = Signal(str)
    node_delete_requested = Signal(str)
    tier_add_requested = Signal(int)
    node_reparent_requested = Signal(str, str)  # (node_id, new_parent_id_or_empty)
    node_merge_requested = Signal(str, str)  # (source_id, target_id)
    node_spawn_child_requested = Signal(str, str)  # (parent_id_or_empty, default_name)
    node_renamed = Signal(str, str)  # (node_id, new_name)
    node_suggestion_applied = Signal(str, dict)  # (node_id, suggestion_dict)
    node_suggestion_batch_requested = Signal(str, str, str)  # (editing_node_id, parent_id, group_mode)
    template_dropped = Signal(str, int, str)  # (premade_key, target_tier, parent_id_or_empty)
    zoom_changed = Signal(float)

    MARGIN_X = 16
    MARGIN_Y = 14
    TIER_GAP = 34
    HEADER_H = 30
    CARD_W = 166
    CARD_H = 54
    CARD_GAP_X = 18
    CARD_GAP_Y = 18
    BAND_PAD_X = 12
    BAND_PAD_BOTTOM = 14

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._arch = FolderArchitecture()
        self._watch_label = "Downloads/"
        self._zoom = 1.0
        self._show_all_connectors = False
        self._expanded_tier1_id: str | None = None
        self._expand_all_tiers = False
        self._collapse_hint_rect: QRectF | None = None
        self._tier_collapse_rect: QRectF | None = None
        self._hovered_collapse: bool = False
        self._selected_id: str | None = None
        self._hovered_id: str | None = None
        self._hovered_delete_id: str | None = None
        self._hovered_plus_id: str | None = None
        self._hovered_tier_add: int | None = None

        self._node_rects: dict[str, QRectF] = {}
        self._node_row_col: dict[str, tuple[int, int]] = {}
        self._sub_pill_rects: dict[str, QRectF] = {}
        self._rendered_rects: dict[str, QRectF] = {}
        self._anim_from_rects: dict[str, QRectF] = {}
        self._appearing_ids: set[str] = set()
        self._anim_progress: float = 1.0
        self._layout_dirty: bool = True
        self._cached_visible_tiers: dict[int, list[ArchNode]] = {}
        self._cached_grid_points = QPolygonF()
        self._cached_grid_size: tuple[int, int] = (0, 0)
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

        # Layout OutQuart transition
        self._layout_anim = QVariantAnimation(self)
        self._layout_anim.setDuration(190)
        self._layout_anim.setStartValue(0.0)
        self._layout_anim.setEndValue(1.0)
        self._layout_anim.setEasingCurve(QEasingCurve.Type.OutQuart)
        self._layout_anim.valueChanged.connect(self._on_layout_anim_tick)
        self._layout_anim.finished.connect(self._on_layout_anim_done)

        # Inline double-click rename editor + Tier 2 suggestion dropdown popup
        self._editing_node_id: str | None = None
        self._inline_cancel = False
        self._user_highlighted_suggestion = False
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
        self._inline_edit.textChanged.connect(self._on_inline_text_changed)
        self._inline_edit.editingFinished.connect(self._on_inline_editing_finished)

        self._suggestion_popup = _SubfolderSuggestionPopup(self)
        self._suggestion_popup.suggestion_chosen.connect(self._on_popup_suggestion_chosen)
        self._suggestion_popup.batch_group_chosen.connect(self._on_popup_batch_chosen)

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
        self._layout_dirty = True
        self._recompute_layout(update_min_size=True)
        self.zoom_changed.emit(self._zoom)
        self.update()

    def snapshot_rects(self) -> dict[str, QRectF]:
        return {k: QRectF(v) for k, v in self._rendered_rects.items()}

    def _start_layout_transition(
        self,
        prev_rects: dict[str, QRectF] | None,
        *,
        animate: bool = True,
    ) -> None:
        should_anim = (
            animate
            and prev_rects is not None
            and animations_enabled()
            and not os.environ.get("PYTEST_CURRENT_TEST")
        )
        self._appearing_ids.clear()
        if should_anim:
            self._anim_from_rects = {}
            for nid, target in self._node_rects.items():
                if nid in prev_rects:
                    self._anim_from_rects[nid] = prev_rects[nid]
                else:
                    self._appearing_ids.add(nid)
                    self._anim_from_rects[nid] = target.translated(0.0, -14.0)
            self._anim_progress = 0.0
            self._layout_anim.stop()
            self._layout_anim.start()
        else:
            self._layout_anim.stop()
            self._anim_progress = 1.0
            self._rendered_rects = {k: QRectF(v) for k, v in self._node_rects.items()}

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
        if self._expanded_tier1_id and arch.get_node(self._expanded_tier1_id) is None:
            self._expanded_tier1_id = None
        if self._selected_id:
            sel_node = arch.get_node(self._selected_id)
            if sel_node is not None and sel_node.tier >= 2:
                self._expanded_tier1_id = self._tier1_ancestor_id(sel_node.id)
        self._layout_dirty = True
        self._recompute_layout(update_min_size=True)
        self._start_layout_transition(prev_rects, animate=animate)
        self.update()

    def _on_layout_anim_tick(self, value: Any) -> None:
        t = float(value)
        self._anim_progress = t
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
        self._anim_progress = 1.0
        self._appearing_ids.clear()
        self._rendered_rects = {k: QRectF(v) for k, v in self._node_rects.items()}
        self.update()

    def resizeEvent(self, event) -> None:  # noqa: ANN001
        super().resizeEvent(event)
        self._layout_dirty = True
        self._recompute_layout(update_min_size=True)

    @property
    def selected_node_id(self) -> str | None:
        return self._selected_id

    def select_node(self, node_id: str | None) -> None:
        self._selected_id = node_id
        if node_id:
            node = self._arch.get_node(node_id)
            if node is not None:
                target_t1 = node.id if node.tier == 1 else self._tier1_ancestor_id(node.id)
                if target_t1 != self._expanded_tier1_id:
                    self.set_expanded_tier1(target_t1, animate=True)
        self.update()
        if node_id:
            self.node_selected.emit(node_id)

    def expanded_tier1_id(self) -> str | None:
        return self._expanded_tier1_id

    def set_expanded_tier1(self, node_id: str | None, *, animate: bool = True) -> None:
        if node_id is not None:
            node = self._arch.get_node(node_id)
            if node is None:
                node_id = None
            elif node.tier != 1:
                node_id = self._tier1_ancestor_id(node.id)
        if node_id == self._expanded_tier1_id:
            return
        prev_rects = self.snapshot_rects() if animate else None
        self._expanded_tier1_id = node_id
        self._layout_dirty = True
        self._recompute_layout(update_min_size=True)
        self._start_layout_transition(prev_rects, animate=animate)
        self.update()

    def expand_all_tiers(self) -> bool:
        return bool(self._expand_all_tiers)

    def set_expand_all_tiers(self, show_all: bool, *, animate: bool = True) -> None:
        flag = bool(show_all)
        if flag == self._expand_all_tiers:
            return
        prev_rects = self.snapshot_rects() if animate else None
        self._expand_all_tiers = flag
        self._layout_dirty = True
        self._recompute_layout(update_min_size=True)
        self._start_layout_transition(prev_rects, animate=animate)
        self.update()

    def _tier1_ancestor_id(self, node_id: str | None) -> str | None:
        if not node_id or node_id == "__root__":
            return None
        cur = self._arch.get_node(node_id)
        guard = 0
        while cur is not None and guard < FolderArchitecture.MAX_TIERS + 2:
            if cur.tier == 1:
                return cur.id
            if not cur.parent_id:
                return None
            cur = self._arch.get_node(cur.parent_id)
            guard += 1
        return None

    def _position_suggestion_popup(self, rect: QRectF) -> None:
        pw = self._suggestion_popup.width()
        ph = self._suggestion_popup.height()
        px = int(max(8, min(rect.left() * self._zoom, max(8, self.width() - pw - 8))))
        below_y = int((rect.bottom() + 4) * self._zoom)
        above_y = int(rect.top() * self._zoom) - ph - 4
        if below_y + ph <= max(self.height(), self.minimumHeight()) - 6 or above_y < 8:
            py = below_y
            needed_h = py + ph + 10
            if needed_h > self.minimumHeight():
                self.setMinimumHeight(needed_h)
        else:
            py = above_y
        self._suggestion_popup.move(px, py)

    def start_inline_rename(self, node_id: str) -> None:
        node = self._arch.get_node(node_id)
        rect = self._node_rects.get(node_id) or self._rendered_rects.get(node_id)
        if node is None or rect is None:
            return
        self._editing_node_id = node_id
        self._inline_cancel = False
        self._user_highlighted_suggestion = False
        self._inline_edit.blockSignals(True)
        self._inline_edit.setText(node.name)
        self._inline_edit.blockSignals(False)
        sx = (rect.left() + 9) * self._zoom
        sy = (rect.top() + 5) * self._zoom
        sw = max(80.0, (rect.width() - 32) * self._zoom)
        sh = 22.0 * self._zoom
        self._inline_edit.setGeometry(int(sx), int(sy), int(sw), int(sh))
        self._inline_edit.show()
        self._inline_edit.raise_()
        self._inline_edit.setFocus()
        self._inline_edit.selectAll()

        # Attach Tier 2 subfolder suggestion dropdown (By File Type vs. By Classification) when parent has presets
        pnode = self._arch.get_node(node.parent_id) if node.parent_id else None
        suggestions = (
            get_tier2_suggestions(pnode.name, pnode.kind, mode="all")
            if (pnode is not None and node.tier >= 2)
            else []
        )
        if suggestions and pnode is not None:
            self._suggestion_popup.set_parent_suggestions(pnode.name, suggestions)
            self._position_suggestion_popup(rect)
            self._suggestion_popup.show()
            self._suggestion_popup.raise_()
        else:
            self._suggestion_popup.hide()
        self.update()

    def _on_inline_text_changed(self, text: str) -> None:
        self._user_highlighted_suggestion = False
        if self._suggestion_popup.isVisible():
            self._suggestion_popup.set_query(text)

    def _on_inline_editing_finished(self) -> None:
        if not self._inline_edit.isVisible():
            return
        if self._suggestion_popup.isVisible() and self._suggestion_popup.underMouse():
            self._inline_edit.setFocus()
            return
        self._commit_inline_rename()

    def _hide_inline_editors(self) -> None:
        self._inline_edit.hide()
        self._suggestion_popup.hide()
        self._user_highlighted_suggestion = False

    def _on_popup_suggestion_chosen(self, suggestion: dict[str, Any]) -> None:
        nid = self._editing_node_id
        self._editing_node_id = None
        self._hide_inline_editors()
        if nid:
            self.node_suggestion_applied.emit(nid, dict(suggestion))
        self.update()

    def _on_popup_batch_chosen(self, group_mode: str) -> None:
        nid = self._editing_node_id
        node = self._arch.get_node(nid) if nid else None
        parent_id = (node.parent_id or "") if node is not None else ""
        self._editing_node_id = None
        self._hide_inline_editors()
        if nid and parent_id:
            self.node_suggestion_batch_requested.emit(nid, parent_id, group_mode)
        self.update()

    def _commit_inline_rename(self) -> None:
        if not self._inline_edit.isVisible():
            return
        nid = self._editing_node_id
        self._editing_node_id = None
        new_name = self._inline_edit.text().strip().replace("/", "").replace("\\", "")
        self._hide_inline_editors()
        if self._inline_cancel or not nid or not new_name:
            self._inline_cancel = False
            self.update()
            return
        node = self._arch.get_node(nid)
        if node is not None:
            # If user typed an exact suggestion name for a fresh empty-rule Tier 2 subfolder, apply its rules too
            if (
                node.parent_id
                and not node.extensions
                and not node.keywords
                and not node.domains
            ):
                pnode = self._arch.get_node(node.parent_id)
                if pnode is not None:
                    exact = next(
                        (
                            s
                            for s in get_tier2_suggestions(pnode.name, pnode.kind, mode="all")
                            if s["name"].lower() == new_name.lower()
                        ),
                        None,
                    )
                    if exact is not None:
                        self.node_suggestion_applied.emit(nid, exact)
                        self.update()
                        return
            if new_name != node.name:
                self.node_renamed.emit(nid, new_name)
        self.update()

    def eventFilter(self, obj, event) -> bool:  # noqa: ANN001
        if obj is self._inline_edit and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Escape:
                self._inline_cancel = True
                self._hide_inline_editors()
                self._editing_node_id = None
                self.update()
                return True
            if self._suggestion_popup.isVisible():
                if key == Qt.Key.Key_Down:
                    if self._suggestion_popup.move_selection(1):
                        self._user_highlighted_suggestion = True
                    return True
                if key == Qt.Key.Key_Up:
                    if self._suggestion_popup.move_selection(-1):
                        self._user_highlighted_suggestion = True
                    return True
                if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self._user_highlighted_suggestion:
                    chosen = self._suggestion_popup.current_suggestion()
                    if chosen is not None:
                        self._on_popup_suggestion_chosen(chosen)
                        return True
        return super().eventFilter(obj, event)

    def show_all_connectors(self) -> bool:
        return bool(self._show_all_connectors)

    def set_show_all_connectors(self, show_all: bool) -> None:
        self._show_all_connectors = bool(show_all)
        self.update()

    def _active_lineage_ids(self) -> tuple[str | None, set[str]]:
        focus_id = self._dragging_id or self._hovered_id or self._selected_id or self._expanded_tier1_id
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

    def _connection_roles(self) -> tuple[str | None, dict[str, str], dict[str, str]]:
        """Classify visible cards relative to the active node's connected paths.

        Returns ``(focus_id, roles_by_id, path_accent_by_id)`` where roles are
        ``"focus"``, ``"connected_parent"``, ``"connected_child"``, ``"dimmed"``,
        or ``"neutral"``.
        """
        focus_id, ancestor_ids = self._active_lineage_ids()
        if not focus_id:
            return None, {}, {}
        focus_node = self._arch.get_node(focus_id)
        if focus_node is None:
            return None, {}, {}

        roles: dict[str, str] = {focus_id: "focus"}
        path_accents: dict[str, str] = {}

        # Upstream parents feeding into focus_id
        for anc_id in ancestor_ids:
            if anc_id != focus_id:
                anc = self._arch.get_node(anc_id)
                if anc is not None:
                    roles[anc_id] = "connected_parent"
                    path_accents[anc_id] = anc.accent_hex

        # Downstream visible descendants fed by focus_id (BFS)
        queue = [focus_id]
        visited = {focus_id}
        while queue:
            pid = queue.pop(0)
            pnode = self._arch.get_node(pid)
            p_hex = pnode.accent_hex if pnode is not None else focus_node.accent_hex
            for child in self._arch.children_of(pid):
                if child.id in visited:
                    continue
                visited.add(child.id)
                if child.id in self._node_rects:
                    roles[child.id] = "connected_child"
                    path_accents[child.id] = p_hex
                    queue.append(child.id)

        # Only dim unrelated cards when the active node actually has visible connections
        has_connections = any(r in ("connected_parent", "connected_child") for r in roles.values())
        if has_connections:
            for nid in self._node_rects:
                if nid != "__root__" and nid not in roles:
                    roles[nid] = "dimmed"
        return focus_id, roles, path_accents

    def _visible_tier_nodes(self) -> dict[int, list[ArchNode]]:
        ordered = self._ordered_tier_nodes()
        visible: dict[int, list[ArchNode]] = {1: ordered.get(1, [])}
        if self._expand_all_tiers:
            for t in range(2, self._arch.tier_count + 1):
                visible[t] = ordered.get(t, [])
            return visible

        exp_t1 = self._expanded_tier1_id
        for t in range(2, self._arch.tier_count + 1):
            if not exp_t1:
                visible[t] = []
            else:
                visible[t] = [
                    n for n in ordered.get(t, []) if self._tier1_ancestor_id(n.id) == exp_t1
                ]
        return visible

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
            if node.tier == 1 and not self._expand_all_tiers:
                state = "Expanded" if self._expanded_tier1_id == node.id else "Click to expand"
                lines.append(f"Subfolders: {child_count} ({state})")
            else:
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
        self._node_row_col.clear()
        self._sub_pill_rects.clear()
        self._delete_rects.clear()
        self._plus_rects.clear()
        self._tier_add_rects.clear()
        self._tier_band_rects.clear()
        self._collapse_hint_rect = None
        self._tier_collapse_rect = None

        visible = self._visible_tier_nodes()
        self._cached_visible_tiers = visible
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

        # Determine which tiers 1..N to lay out
        tiers_to_layout: list[int] = [1]
        if self._expand_all_tiers:
            tiers_to_layout.extend(range(2, self._arch.tier_count + 1))
        elif self._expanded_tier1_id is not None:
            for t in range(2, self._arch.tier_count + 1):
                if visible.get(t) or t == 2:
                    tiers_to_layout.append(t)

        # Tiers 1..N Horizontal Bands stacked Top-to-Bottom
        for t in tiers_to_layout:
            cards = visible.get(t, [])
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
            if t == 2 and not self._expand_all_tiers and self._expanded_tier1_id is not None:
                self._tier_collapse_rect = QRectF(
                    self.MARGIN_X + band_w - 144, cur_band_y + 4, 70, 22
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
                self._node_row_col[node.id] = (r_idx, c_idx)
                self._delete_rects[node.id] = QRectF(rect.right() - 20, rect.top() + 5, 16, 16)
                if node.tier < FolderArchitecture.MAX_TIERS:
                    self._plus_rects[node.id] = QRectF(rect.center().x() - 9, rect.bottom() - 9, 18, 18)

            cur_band_y += band_h + self.TIER_GAP

        # Collapsed Tier 2+ hint bar when in branch mode and no Tier 1 folder is expanded
        if (
            not self._expand_all_tiers
            and self._expanded_tier1_id is None
            and any(n.tier >= 2 for n in self._arch.nodes)
        ):
            hint_h = 34.0
            self._collapse_hint_rect = QRectF(self.MARGIN_X, cur_band_y - 12.0, band_w, hint_h)
            cur_band_y += hint_h + self.TIER_GAP - 12.0

        if self._layout_anim.state() != QVariantAnimation.State.Running:
            self._rendered_rects = {k: QRectF(v) for k, v in self._node_rects.items()}

        total_h = max(340, int((cur_band_y - self.TIER_GAP + self.MARGIN_Y) * self._zoom))
        min_w = max(380, int(380 * self._zoom))
        if update_min_size and (self.minimumWidth() != min_w or self.minimumHeight() != total_h):
            self.setMinimumSize(min_w, total_h)
        self._layout_dirty = False

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
        if self._layout_dirty and self._layout_anim.state() != QVariantAnimation.State.Running:
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

        # Subtle dot grid (batch drawn from cached points per logical viewport size)
        if (logical_w, logical_h) != self._cached_grid_size:
            self._cached_grid_size = (logical_w, logical_h)
            pts = QPolygonF()
            for gx in range(20, logical_w, 26):
                for gy in range(20, logical_h, 26):
                    pts.append(QPointF(gx, gy))
            self._cached_grid_points = pts
        if not self._cached_grid_points.isEmpty():
            p.setPen(QColor(255, 255, 255, 16))
            p.drawPoints(self._cached_grid_points)

        # Horizontal Tier Bands Top-to-Bottom
        visible_tiers = self._cached_visible_tiers or self._visible_tier_nodes()
        exp_t1_node = (
            self._arch.get_node(self._expanded_tier1_id)
            if (not self._expand_all_tiers and self._expanded_tier1_id)
            else None
        )
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
            elif t >= 2 and exp_t1_node is not None:
                count = len(visible_tiers.get(t, []))
                title = f"TIER {t} · {exp_t1_node.name.upper()} · {count} SUBFOLDER{'S' if count != 1 else ''}"
            else:
                count = len(visible_tiers.get(t, []))
                title = f"TIER {t} · {count} FOLDER{'S' if count != 1 else ''}"
                if t == 1 and self._drop_promote_tier1:
                    title += "  ·  DROP TO PROMOTE TO TIER 1"
            title_right_pad = 156 if (t == 2 and self._tier_collapse_rect is not None) else 92
            fm_hdr = QFontMetrics(f_hdr)
            elided_title = fm_hdr.elidedText(
                title,
                Qt.TextElideMode.ElideRight,
                max(60, int(band_rect.width() - 14 - title_right_pad)),
            )
            p.drawText(
                QRectF(band_rect.left() + 14, band_rect.top() + 3, band_rect.width() - title_right_pad, 24),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                elided_title,
            )

            if t == 2 and self._tier_collapse_rect is not None:
                col_rect = self._tier_collapse_rect
                col_hov = self._hovered_collapse
                p.setPen(QPen(QColor("#0A84FF" if col_hov else "#3A3A3C"), 1.0))
                p.setBrush(QColor(10, 132, 255, 45) if col_hov else QColor("#242426"))
                p.drawRoundedRect(col_rect, 6, 6)
                p.setFont(QFont("Segoe UI", 8.2, QFont.Weight.DemiBold))
                p.setPen(QColor("#D1D1D6") if not col_hov else QColor("#FFFFFF"))
                p.drawText(col_rect, Qt.AlignmentFlag.AlignCenter, "Collapse ▴")

            if t >= 1 and t in self._tier_add_rects:
                add_rect = self._tier_add_rects[t]
                hov = self._hovered_tier_add == t
                p.setPen(QPen(QColor("#0A84FF" if hov else "#3A3A3C"), 1.0))
                p.setBrush(QColor(10, 132, 255, 45) if hov else QColor("#242426"))
                p.drawRoundedRect(add_rect, 6, 6)
                p.setFont(QFont("Segoe UI", 8.5, QFont.Weight.DemiBold))
                p.setPen(QColor("#F5F5F7"))
                p.drawText(add_rect, Qt.AlignmentFlag.AlignCenter, "+ Add")

        # Collapsed Tier 2+ prompt bar (when in Branch mode with no Tier 1 folder expanded)
        if self._collapse_hint_rect is not None:
            p.setPen(QPen(QColor("#232327"), 1.0, Qt.PenStyle.DashLine))
            p.setBrush(QColor("#141417"))
            p.drawRoundedRect(self._collapse_hint_rect, 9, 9)
            p.setFont(QFont("Segoe UI", 8.5))
            p.setPen(QColor("#8E8E93"))
            p.drawText(
                self._collapse_hint_rect,
                Qt.AlignmentFlag.AlignCenter,
                "Click a Tier 1 folder to reveal its lower-tier subfolders  ·  or switch to View: All",
            )

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
        focus_id, conn_roles, path_accents = self._connection_roles()
        _fid, ancestor_ids = self._active_lineage_ids()
        connector_items: list[tuple[bool, ArchNode, ArchNode | None, QRectF, QRectF]] = []
        for node in self._arch.nodes:
            if node.tier < 2 or node.tier > self._arch.tier_count:
                continue
            dst_rect = self._display_rect(node.id)
            if dst_rect is None:
                continue
            src_rect = self._display_rect(node.parent_id or "")
            if src_rect is None:
                continue
            parent_node = self._arch.get_node(node.parent_id) if node.parent_id else None
            is_hi = bool(
                focus_id is not None
                and (
                    node.id in ancestor_ids
                    or (node.parent_id is not None and node.parent_id == focus_id)
                    or (
                        conn_roles.get(node.id) == "connected_child"
                        and conn_roles.get(node.parent_id or "") in ("focus", "connected_child")
                    )
                )
            )
            in_expanded_branch = bool(
                not self._expand_all_tiers
                and self._expanded_tier1_id is not None
                and self._tier1_ancestor_id(node.id) == self._expanded_tier1_id
            )
            if not self._show_all_connectors and not is_hi and not in_expanded_branch:
                continue
            connector_items.append((is_hi, node, parent_node, src_rect, dst_rect))

        # Compute open vertical routing channels per tier (between/outside Row 0 cards)
        tier_open_channels: dict[int, list[float]] = {}
        for tier_num, cards_in_tier in visible_tiers.items():
            if tier_num < 2:
                continue
            row0_rects = sorted(
                [
                    r
                    for n in cards_in_tier
                    if self._node_row_col.get(n.id, (0, 0))[0] == 0
                    and (r := self._display_rect(n.id)) is not None
                ],
                key=lambda r: r.left(),
            )
            if row0_rects:
                chans = [row0_rects[0].left() - self.CARD_GAP_X * 0.5]
                for i in range(len(row0_rects) - 1):
                    chans.append((row0_rects[i].right() + row0_rects[i + 1].left()) * 0.5)
                chans.append(row0_rects[-1].right() + self.CARD_GAP_X * 0.5)
                tier_open_channels[tier_num] = chans

        # Assign distinct horizontal bus channels per (tier, parent) sorted by parent X
        tier_parent_lanes: dict[tuple[int, str], float] = {}
        parents_by_tier: dict[int, list[tuple[float, str]]] = {}
        seen_pid: set[tuple[int, str]] = set()
        for _hi, node, _pnode, src_rect, _dst_rect in connector_items:
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

        active_exit_ports: dict[str, tuple[QPointF, QColor]] = {}
        for want_hi in (False, True):
            for is_hi, node, parent_node, src_rect, dst_rect in connector_items:
                if is_hi != want_hi:
                    continue
                base_hex = parent_node.accent_hex if parent_node is not None else node.accent_hex
                col = QColor(base_hex)
                p1 = QPointF(src_rect.center().x(), src_rect.bottom())
                p2 = QPointF(dst_rect.center().x(), dst_rect.top())
                bus_y = tier_parent_lanes.get((node.tier, node.parent_id or ""), (p1.y() + p2.y()) * 0.5)
                r_idx, _c_idx = self._node_row_col.get(node.id, (0, 0))
                desired_chan = dst_rect.left() - self.CARD_GAP_X * 0.5
                chans = tier_open_channels.get(node.tier)
                if r_idx >= 1 and chans:
                    channel_x = min(chans, key=lambda cx: abs(cx - desired_chan))
                else:
                    channel_x = desired_chan
                row_bus_y = dst_rect.top() - self.CARD_GAP_Y * 0.55
                path = self._build_orthogonal_bus_path(
                    p1,
                    p2,
                    bus_y,
                    row_idx=r_idx,
                    channel_x=channel_x,
                    row_bus_y=row_bus_y,
                )

                if is_hi:
                    glow = QColor(col)
                    glow.setAlpha(48)
                    p.setPen(QPen(glow, 5.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawPath(path)

                col.setAlpha(235 if is_hi else 72)
                p.setPen(
                    QPen(
                        col,
                        2.3 if is_hi else 1.25,
                        Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap,
                    )
                )
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(path)

                # Directional downward arrowhead at child's top entry port when highlighted
                if is_hi:
                    if node.parent_id:
                        active_exit_ports[node.parent_id] = (p1, QColor(base_hex))
                    arrow = QPolygonF(
                        [
                            QPointF(p2.x(), p2.y() - 0.5),
                            QPointF(p2.x() - 4.2, p2.y() - 6.2),
                            QPointF(p2.x() + 4.2, p2.y() - 6.2),
                        ]
                    )
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(col)
                    p.drawPolygon(arrow)

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
                self._paint_node_card(
                    p,
                    node,
                    rect,
                    role=conn_roles.get(node.id, "neutral"),
                    path_accent=path_accents.get(node.id),
                    has_active_out=node.id in active_exit_ports,
                )

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

    def _paint_node_card(
        self,
        p: QPainter,
        node: ArchNode,
        rect: QRectF,
        *,
        role: str = "neutral",
        path_accent: str | None = None,
        has_active_out: bool = False,
    ) -> None:
        selected = node.id == self._selected_id
        hovered = node.id == self._hovered_id
        is_dragging = node.id == self._dragging_id
        is_connected = role in ("connected_parent", "connected_child")
        is_dimmed = role == "dimmed" and not hovered and not selected and not is_dragging
        col = QColor(node.accent_hex)
        conn_col = QColor(path_accent or node.accent_hex)
        halo = self._drop_halo_for(node.id)

        p.save()
        alpha_mul = 1.0
        if node.id in self._appearing_ids and self._anim_progress < 1.0:
            alpha_mul *= max(0.05, self._anim_progress)
        if is_dimmed:
            alpha_mul *= 0.54
        if alpha_mul < 0.999:
            p.setOpacity(alpha_mul)

        if halo:
            halo_hex = "#30D158" if halo == "valid" else ("#64D2FF" if halo == "merge" else "#FF453A")
            hcol = QColor(halo_hex)
            hcol.setAlpha(75)
            p.setPen(QPen(hcol, 6))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(rect.adjusted(-2, -2, 2, 2), 12, 12)
        elif selected or is_dragging:
            hcol = QColor(col)
            hcol.setAlpha(60)
            p.setPen(QPen(hcol, 4.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(rect.adjusted(-1, -1, 1, 1), 11, 11)
        elif is_connected:
            hcol = QColor(conn_col)
            hcol.setAlpha(42)
            p.setPen(QPen(hcol, 3.2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(rect.adjusted(-0.8, -0.8, 0.8, 0.8), 11, 11)

        if halo == "valid":
            border = QColor("#30D158")
            border_w = 1.8
        elif halo == "merge":
            border = QColor("#64D2FF")
            border_w = 1.9
        elif halo == "invalid":
            border = QColor("#FF453A")
            border_w = 1.8
        elif selected or is_dragging:
            border = col
            border_w = 1.9
        elif is_connected:
            border = QColor(conn_col)
            border.setAlpha(205)
            border_w = 1.6
        elif hovered:
            border = QColor("#48484A")
            border_w = 1.1
        else:
            border = QColor("#2C2C2E")
            border_w = 1.0

        base_fill = (
            QColor("#25252A")
            if (selected or is_dragging)
            else (QColor("#212125") if (hovered or is_connected) else QColor("#1C1C1E"))
        )
        p.setPen(QPen(border, border_w))
        p.setBrush(base_fill)
        p.drawRoundedRect(rect, 10, 10)

        # Subtle accent surface wash on connected parent/child boxes
        if is_connected and not halo:
            wash = QColor(conn_col)
            wash.setAlpha(22)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(wash)
            p.drawRoundedRect(rect.adjusted(1.0, 1.0, -1.0, -1.0), 9.0, 9.0)

        # Left Accent Stripe
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawRoundedRect(QRectF(rect.left() + 0.5, rect.top() + 9, 3.5, rect.height() - 18), 1.5, 1.5)

        # Top Input Connection Port on Tier 2+ cards
        if node.tier >= 2 and not is_dragging:
            port_pt = QPointF(rect.center().x(), rect.top())
            port_lit = role in ("focus", "connected_child")
            if port_lit:
                p.setPen(QPen(QColor("#111113"), 1.3))
                p.setBrush(conn_col)
                p.drawEllipse(port_pt, 3.6, 3.6)
            else:
                p.setPen(QPen(QColor("#3A3A3C"), 1.0))
                p.setBrush(QColor("#161618"))
                p.drawEllipse(port_pt, 3.0, 3.0)

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

        # Parent badge pill (Tier 2+) OR expandable subfolder badge (Tier 1 parents) + Tier micro-footer
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
            fill_col.setAlpha(60 if (role in ("focus", "connected_child")) else 36)
            border_col = QColor(p_col)
            border_col.setAlpha(200 if (role in ("focus", "connected_child")) else 135)
            p.setPen(QPen(border_col, 1.0 if (role in ("focus", "connected_child")) else 0.9))
            p.setBrush(fill_col)
            p.drawRoundedRect(pill_rect, 4.5, 4.5)
            p.setPen(QColor("#F5F5F7") if (role in ("focus", "connected_child")) else QColor("#E5E5EA"))
            p.drawText(pill_rect.adjusted(5, 0, -5, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, pill_label)

            p.setFont(QFont("Segoe UI", 7.2))
            p.setPen(QColor("#8E8E93") if is_connected else QColor("#6E6E73"))
            p.drawText(
                rect.adjusted(12, 30, -10, -5),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                footer,
            )
        elif node.tier == 1 and child_count > 0:
            is_expanded = self._expand_all_tiers or (self._expanded_tier1_id == node.id)
            chev = "▾" if is_expanded else "▸"
            pill_label = f"{child_count} sub {chev}"
            p.setFont(QFont("Segoe UI", 7.2, QFont.Weight.DemiBold))
            fm_pill = QFontMetrics(p.font())
            pill_w = float(fm_pill.horizontalAdvance(pill_label)) + 12.0
            pill_rect = QRectF(rect.left() + 12.0, rect.bottom() - 21.0, pill_w, 16.0)
            self._sub_pill_rects[node.id] = pill_rect
            fill_col = QColor(col)
            fill_col.setAlpha(58 if is_expanded else 26)
            border_col = QColor(col)
            border_col.setAlpha(195 if is_expanded else 115)
            p.setPen(QPen(border_col, 1.0 if is_expanded else 0.9))
            p.setBrush(fill_col)
            p.drawRoundedRect(pill_rect, 4.5, 4.5)
            p.setPen(QColor("#F5F5F7") if is_expanded else QColor("#D1D1D6"))
            p.drawText(pill_rect, Qt.AlignmentFlag.AlignCenter, pill_label)

            p.setFont(QFont("Segoe UI", 7.2))
            p.setPen(QColor("#8E8E93") if (is_expanded or is_connected) else QColor("#6E6E73"))
            p.drawText(
                rect.adjusted(12, 30, -10, -5),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                "T1",
            )
        else:
            self._sub_pill_rects.pop(node.id, None)
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

        # '+' Spawn handle / illuminated output port on bottom center
        if not is_dragging:
            self._paint_plus_handle(
                p,
                node.id,
                rect,
                active_accent=node.accent_hex if has_active_out else None,
                has_children=child_count > 0,
            )

        p.restore()

    @staticmethod
    def _build_orthogonal_bus_path(
        p1: QPointF,
        p2: QPointF,
        bus_y: float,
        *,
        row_idx: int = 0,
        channel_x: float | None = None,
        row_bus_y: float | None = None,
    ) -> QPainterPath:
        """Build a clean rounded orthogonal bus connector between two cards.

        For cards on Row 2+ (``row_idx >= 1``), routes vertically down an open
        inter-column channel ``channel_x`` to ``row_bus_y`` before entering ``p2``
        so the connector never cuts across Row 0 cards.
        """
        path = QPainterPath(p1)
        if p1.y() >= bus_y - 2.0 or p2.y() <= bus_y + 2.0:
            dy = max(14.0, abs(p2.y() - p1.y()) * 0.45)
            path.cubicTo(QPointF(p1.x(), p1.y() + dy), QPointF(p2.x(), p2.y() - dy), p2)
            return path

        if (
            row_idx >= 1
            and channel_x is not None
            and row_bus_y is not None
            and row_bus_y > bus_y + 8.0
            and p2.y() > row_bus_y + 2.0
        ):
            r = 5.0
            dx1 = channel_x - p1.x()
            if abs(dx1) >= 2.0:
                sx1 = 1.0 if dx1 > 0.0 else -1.0
                r1 = min(
                    r,
                    abs(dx1) * 0.45,
                    max(1.0, (bus_y - p1.y()) * 0.45),
                    max(1.0, (row_bus_y - bus_y) * 0.45),
                )
                path.lineTo(p1.x(), bus_y - r1)
                path.quadTo(QPointF(p1.x(), bus_y), QPointF(p1.x() + sx1 * r1, bus_y))
                path.lineTo(channel_x - sx1 * r1, bus_y)
                path.quadTo(QPointF(channel_x, bus_y), QPointF(channel_x, bus_y + r1))
            else:
                path.lineTo(channel_x, bus_y)

            dx2 = p2.x() - channel_x
            if abs(dx2) >= 2.0:
                sx2 = 1.0 if dx2 > 0.0 else -1.0
                r2 = min(
                    r,
                    abs(dx2) * 0.45,
                    max(1.0, (row_bus_y - bus_y) * 0.45),
                    max(1.0, (p2.y() - row_bus_y) * 0.45),
                )
                path.lineTo(channel_x, row_bus_y - r2)
                path.quadTo(QPointF(channel_x, row_bus_y), QPointF(channel_x + sx2 * r2, row_bus_y))
                path.lineTo(p2.x() - sx2 * r2, row_bus_y)
                path.quadTo(QPointF(p2.x(), row_bus_y), QPointF(p2.x(), row_bus_y + r2))
            else:
                path.lineTo(p2.x(), row_bus_y)
            path.lineTo(p2)
            return path

        dx = p2.x() - p1.x()
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

    def _paint_plus_handle(
        self,
        p: QPainter,
        nid: str,
        rect: QRectF,
        *,
        active_accent: str | None = None,
        has_children: bool = False,
    ) -> None:
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
            port_pt = QPointF(rect.center().x(), rect.bottom())
            if active_accent:
                p.setPen(QPen(QColor("#111113"), 1.3))
                p.setBrush(QColor(active_accent))
                p.drawEllipse(port_pt, 4.0, 4.0)
            elif has_children:
                p.setPen(QPen(QColor("#5A5A60"), 1.2))
                p.setBrush(QColor("#2C2C30"))
                p.drawEllipse(port_pt, 3.5, 3.5)
            else:
                # Subtle output port dot
                p.setPen(QPen(QColor("#2C2C2E"), 1.0))
                p.setBrush(QColor("#161618"))
                p.drawEllipse(port_pt, 3.0, 3.0)
            return

        hov = nid == self._hovered_plus_id or nid == self._spawning_from_id
        p.setPen(QPen(QColor("#111113"), 1.4))
        p.setBrush(
            QColor("#0A84FF")
            if hov
            else (QColor(active_accent) if active_accent else QColor("#3A3A3C"))
        )
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

        # 2. Active card drag-and-drop reparenting (or Shift+Drag merge)
        if self._press_node_id is not None:
            delta = pt - self._press_pt
            if self._dragging_id is None and delta.manhattanLength() > 6:
                self._dragging_id = self._press_node_id
            if self._dragging_id is not None:
                self._drag_offset = delta
                shift_held = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                self._evaluate_reparent_hover(pt, shift_held=shift_held)
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                self.update()
                return

        # 3. Hover hit-testing
        self._hovered_delete_id = None
        self._hovered_plus_id = None
        self._hovered_id = None
        self._hovered_tier_add = None
        self._hovered_collapse = False

        if self._tier_collapse_rect is not None and self._tier_collapse_rect.contains(pt):
            self._hovered_collapse = True
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setToolTip("Collapse lower tiers back to Tier 1 overview")
            self.update()
            return

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

    def _evaluate_reparent_hover(self, pt: QPointF, *, shift_held: bool = False) -> None:
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
            if shift_held:
                self._drop_target_id = best_target
                self._drop_state = (
                    "merge" if self._arch.can_merge(dragged_id, best_target) else "invalid"
                )
                return
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

        # 2. Check tier 'Collapse ▴' and '+ Add' pills
        if self._tier_collapse_rect is not None and self._tier_collapse_rect.contains(pt):
            self.set_expanded_tier1(None, animate=True)
            return

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

        # 3b. Check clickable 'N sub ▾' collapse badge on expanded Tier 1 card
        if not self._expand_all_tiers and self._expanded_tier1_id:
            sub_r = self._sub_pill_rects.get(self._expanded_tier1_id)
            if sub_r is not None and sub_r.contains(pt):
                self.set_expanded_tier1(None, animate=True)
                return

        # 4. Select & prepare drag on a non-root card
        for nid, rect in self._rendered_rects.items():
            if nid != "__root__" and rect.contains(pt):
                self.select_node(nid)
                self._press_node_id = nid
                self._press_pt = pt
                self._drag_offset = QPointF()
                return

        # 5. Clicking Root card or empty canvas collapses the expanded Tier 1 branch
        if not self._expand_all_tiers and self._expanded_tier1_id is not None:
            self.set_expanded_tier1(None, animate=True)

    def mouseReleaseEvent(self, event) -> None:  # noqa: ANN001
        if self._panning:
            self._panning = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return

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
            elif drop_target and drop_state == "merge":
                self.node_merge_requested.emit(dragged_id, drop_target)
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

        self.btn_toggle_view = _make_pill_button(
            "View: Branch",
            checkable=True,
            tooltip="Show only the clicked Tier 1 folder's subfolders (Branch) or lay out every tier at once (All)",
        )
        self.btn_toggle_view.toggled.connect(self._on_toggle_view)
        bar_row2.addWidget(self.btn_toggle_view)

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
        self.canvas.node_merge_requested.connect(self._on_nodes_merged)
        self.canvas.node_spawn_child_requested.connect(self._on_spawn_child_inline)
        self.canvas.node_renamed.connect(self._on_node_renamed)
        self.canvas.node_suggestion_applied.connect(self._on_node_suggestion_applied)
        self.canvas.node_suggestion_batch_requested.connect(self._on_node_suggestion_batch_requested)
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

        self.btn_detect_projects = InteractiveRibbonBtn("Detect Projects...", make_vector_icon("search", "#5AC8FA", 14))
        self.btn_detect_projects.setToolTip(
            "Scan this folder on disk to detect multi-file projects and add them as subfolders"
        )
        self.btn_detect_projects.clicked.connect(self._on_detect_projects_for_selected)
        il.addWidget(self.btn_detect_projects)

        self.btn_merge_node = InteractiveRibbonBtn("Merge Into...", make_vector_icon("layers", "#64D2FF", 14))
        self.btn_merge_node.setToolTip(
            "Merge this folder's rules, subfolders, and files into another folder (or hold Shift while dragging onto a folder)"
        )
        self.btn_merge_node.clicked.connect(self._on_merge_selected_clicked)
        il.addWidget(self.btn_merge_node)

        self.btn_delete_node = InteractiveRibbonBtn("Remove Folder", make_vector_icon("delete", "#FF453A", 14))
        self.btn_delete_node.setToolTip(
            "Remove the selected folder (confirms first if files or subfolders exist on disk)"
        )
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
                self.btn_detect_projects.setEnabled(False)
                self.btn_merge_node.setEnabled(False)
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
            self.btn_detect_projects.setEnabled(node.tier < FolderArchitecture.MAX_TIERS)
            self.btn_merge_node.setEnabled(
                any(self._arch.can_merge(node.id, cand.id) for cand in self._arch.nodes)
            )
            self.btn_delete_node.setEnabled(True)
        finally:
            self._syncing_inspector = False

    def _on_toggle_view(self, checked: bool) -> None:
        self.canvas.set_expand_all_tiers(checked, animate=True)
        self.btn_toggle_view.setText("View: All" if checked else "View: Branch")

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

    def _on_node_suggestion_applied(self, node_id: str, suggestion: dict[str, Any]) -> None:
        node = self._arch.get_node(node_id)
        if node is None:
            return
        self._push_undo_snapshot()
        self._arch.update_node(
            node_id,
            name=str(suggestion.get("name") or node.name),
            kind=str(suggestion.get("kind") or node.kind),
            extensions=list(suggestion.get("extensions") or ()),
            keywords=list(suggestion.get("keywords") or ()),
            domains=list(suggestion.get("domains") or ()),
        )
        self._dirty = True
        self._redraw_canvas()
        self.canvas.select_node(node_id)

    def _on_node_suggestion_batch_requested(
        self, editing_node_id: str, parent_id: str, group_mode: str
    ) -> None:
        pnode = self._arch.get_node(parent_id)
        if pnode is None or pnode.tier >= FolderArchitecture.MAX_TIERS:
            return
        suggestions = get_tier2_suggestions(pnode.name, pnode.kind, mode=group_mode)
        if not suggestions:
            return
        prev_rects = self.canvas.snapshot_rects()
        self._push_undo_snapshot()

        existing_names = {
            c.name.strip().lower()
            for c in self._arch.children_of(parent_id)
            if c.id != editing_node_id
        }
        edit_node = self._arch.get_node(editing_node_id)
        reusable_placeholder = bool(
            edit_node is not None
            and edit_node.parent_id == parent_id
            and edit_node.name in ("New Subfolder", "New Folder")
            and not edit_node.extensions
            and not edit_node.keywords
            and not edit_node.domains
        )

        last_node: ArchNode | None = None
        for sug in suggestions:
            sname = str(sug.get("name") or "").strip()
            if not sname or sname.lower() in existing_names:
                continue
            existing_names.add(sname.lower())
            if reusable_placeholder and edit_node is not None:
                self._arch.update_node(
                    edit_node.id,
                    name=sname,
                    kind=str(sug.get("kind") or pnode.kind),
                    extensions=list(sug.get("extensions") or ()),
                    keywords=list(sug.get("keywords") or ()),
                    exclude_keywords=list(sug.get("exclude_keywords") or ()),
                    domains=list(sug.get("domains") or ()),
                )
                last_node = edit_node
                reusable_placeholder = False
            else:
                last_node = self._arch.add_custom_node(
                    name=sname,
                    tier=pnode.tier + 1,
                    parent_id=parent_id,
                    kind=str(sug.get("kind") or pnode.kind),
                    extensions=list(sug.get("extensions") or ()),
                    keywords=list(sug.get("keywords") or ()),
                    exclude_keywords=list(sug.get("exclude_keywords") or ()),
                    domains=list(sug.get("domains") or ()),
                )

        if reusable_placeholder and edit_node is not None:
            # All suggested names already existed; drop the temporary empty placeholder cleanly
            self._arch.remove_node(edit_node.id)

        self._dirty = True
        self._redraw_canvas(animate=True, prev_rects=prev_rects)
        if last_node is not None and self._arch.get_node(last_node.id) is not None:
            self.canvas.select_node(last_node.id)
        mode_label = "File Type" if group_mode == "file_type" else "Classification"
        self.architecture_applied.emit(
            f"Added {mode_label} subfolders under '{pnode.name}' — click Apply & Shift Files to sync."
        )

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

        use_tfidf = bool(getattr(self.service.config, "ai_smart_scan_keywords", True))

        def _work(progress) -> dict[str, Any]:  # noqa: ANN001
            return scan_folder_architecture(watch_dir, use_tfidf=use_tfidf, on_progress=progress)

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
                item = QListWidgetItem(f"{indent}[T{node.tier}] {node.name}{detail}")
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked)
                item.setData(Qt.ItemDataRole.UserRole, node.id)
                preview_list.addItem(item)
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

            checked_ids = {
                str(preview_list.item(i).data(Qt.ItemDataRole.UserRole))
                for i in range(preview_list.count())
                if preview_list.item(i).checkState() == Qt.CheckState.Checked
            }
            kept_nodes: list[ArchNode] = []
            kept_ids: set[str] = set()
            for node in sorted(scanned.nodes, key=lambda n: n.tier):
                if node.id not in checked_ids:
                    continue
                if node.parent_id and node.parent_id not in kept_ids:
                    continue
                kept_nodes.append(node)
                kept_ids.add(node.id)
            filtered_scan = FolderArchitecture(tier_count=scanned.tier_count, nodes=kept_nodes)

            prev_rects = self.canvas.snapshot_rects()
            self._push_undo_snapshot()
            self._active_preset = "scanned"
            self._update_blueprint_pills()
            if choice["mode"] == "merge":
                self._arch = merge_architectures(self._arch, filtered_scan)
            else:
                self._arch = filtered_scan
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
            elif tier == 2 and self.canvas.expanded_tier1_id():
                parent_id = self.canvas.expanded_tier1_id() or ""
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

    def apply_detected_projects(
        self,
        parent_id: str,
        projects: list[dict[str, Any]],
    ) -> list[ArchNode]:
        """Add confirmed detected projects as child nodes under ``parent_id`` with Undo support."""
        parent_node = self._arch.get_node(parent_id)
        if parent_node is None or parent_node.tier >= FolderArchitecture.MAX_TIERS or not projects:
            return []

        existing_names = {c.name.lower() for c in self._arch.children_of(parent_id)}
        to_add = [
            p
            for p in projects
            if str(p.get("name") or "").strip()
            and str(p.get("name") or "").strip().lower() not in existing_names
        ]
        if not to_add:
            return []

        prev_rects = self.canvas.snapshot_rects()
        self._push_undo_snapshot()
        added: list[ArchNode] = []
        for proj in to_add:
            pname = str(proj["name"]).strip()
            if pname.lower() in existing_names:
                continue
            existing_names.add(pname.lower())
            child = self._arch.add_custom_node(
                name=pname,
                tier=parent_node.tier + 1,
                parent_id=parent_id,
                kind=parent_node.kind,
                extensions=list(proj.get("extensions") or ()),
                keywords=list(proj.get("keywords") or ()),
            )
            added.append(child)

        if added:
            self._dirty = True
            self._redraw_canvas(animate=True, prev_rects=prev_rects)
            self.canvas.select_node(added[0].id)
            self.architecture_applied.emit(
                f"Added {len(added)} project folder(s) under '{parent_node.name}' — click Apply & Shift Files to organize."
            )
        return added

    def _on_detect_projects_for_selected(self) -> None:
        node = self._arch.get_node(self.canvas.selected_node_id)
        if node is None or node.tier >= FolderArchitecture.MAX_TIERS:
            return
        rel = self._arch.node_relative_path(node.id)
        if not rel:
            return
        target_dir = (self.service.watch_root / rel).resolve()
        if not target_dir.is_dir():
            QMessageBox.information(
                self,
                "Detect Projects",
                f"Folder does not exist on disk yet:\n{target_dir}\n\n"
                "Apply your layout or sort files into this folder first.",
            )
            return

        # Skip existing classification/project child folders (those with keywords/domains),
        # while scanning file-type subfolders (like STEP / Fusion 360 / 3MF / STL) together.
        children = self._arch.children_of(node.id)
        exclude_subdirs = {c.name.lower() for c in children if c.keywords or c.domains}
        existing_child_names = {c.name.lower() for c in children}

        self.btn_detect_projects.setEnabled(False)
        self.btn_detect_projects.setText("Detecting...")

        def _restore_btn(_msg: str = "") -> None:
            self.btn_detect_projects.setText("Detect Projects...")
            cur = self._arch.get_node(self.canvas.selected_node_id)
            self.btn_detect_projects.setEnabled(cur is not None and cur.tier < FolderArchitecture.MAX_TIERS)

        def _work(progress) -> dict[str, Any]:  # noqa: ANN001
            projs = detect_projects_in_folder(
                target_dir,
                min_files=3,
                exclude_subdirs=exclude_subdirs,
                on_progress=progress,
            )
            used_min = 3
            if not projs:
                projs = detect_projects_in_folder(
                    target_dir,
                    min_files=2,
                    exclude_subdirs=exclude_subdirs,
                    on_progress=progress,
                )
                used_min = 2
            return {"projects": projs, "min_files": used_min}

        def _done(res: dict[str, Any]) -> None:
            _restore_btn()
            projs: list[DetectedProject] = res.get("projects") or []
            used_min = int(res.get("min_files") or 3)
            if not projs:
                QMessageBox.information(
                    self,
                    f"Detect Projects — {node.name}",
                    f"No multi-file project groups were detected inside:\n{target_dir}",
                )
                return

            dlg = DetectProjectsDialog(
                node,
                target_dir,
                projs,
                exclude_subdirs=exclude_subdirs,
                existing_child_names=existing_child_names,
                min_files=used_min,
                parent=self,
            )
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            chosen = dlg.selected_projects()
            if chosen:
                self.apply_detected_projects(node.id, chosen)

        self._run_background(
            _work,
            _done,
            title=f"Detecting projects in {node.name}...",
            pass_progress=True,
            on_error=_restore_btn,
        )

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

    def _delete_node_by_id(self, node_id: str, *, confirm: bool = True) -> None:
        node = self._arch.get_node(node_id)
        if not node_id or node is None:
            return
        node_name = node.name
        rel_path = self._arch.node_relative_path(node_id)
        disk_info = self.service.inspect_folder_on_disk(rel_path)
        children = self._arch.children_of(node_id)

        file_action_mode = "keep"
        evacuate_target: str | None = None
        remove_empty_dir = False
        promote_children = False

        needs_prompt = (
            int(disk_info.get("file_count", 0) or 0) > 0
            or bool(disk_info.get("exists"))
            or len(children) > 0
            or not os.environ.get("PYTEST_CURRENT_TEST")
        )
        if confirm and needs_prompt:
            dlg = DeleteFolderDialog(self._arch, node, disk_info, parent=self)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            file_action_mode, evacuate_target = dlg.file_action()
            remove_empty_dir = dlg.should_remove_empty_dir()
            promote_children = dlg.should_promote_children()

        prev_rects = self.canvas.snapshot_rects()
        self._push_undo_snapshot()
        self._arch.remove_node(node_id, promote_children=promote_children)
        self._dirty = True
        self._redraw_canvas(animate=True, prev_rects=prev_rects)

        should_touch_disk = file_action_mode == "evacuate" or (
            remove_empty_dir
            and bool(disk_info.get("exists"))
            and int(disk_info.get("file_count", 0) or 0) == 0
        )
        if should_touch_disk and rel_path:
            target_arg = evacuate_target if file_action_mode == "evacuate" else None

            def _work(report):  # noqa: ANN001
                return self.service.evacuate_or_remove_folder_on_disk(
                    rel_path,
                    target_rel=target_arg,
                    remove_empty_dir=remove_empty_dir,
                    on_progress=report,
                )

            def _done(res: dict[str, Any]) -> None:
                moved_n = int(res.get("moved_count", 0) or 0)
                if moved_n > 0:
                    self.architecture_applied.emit(
                        f"Removed '{node_name}' and moved {moved_n} file(s) safely (Undo available)."
                    )
                elif res.get("removed_dir"):
                    self.architecture_applied.emit(
                        f"Removed '{node_name}' and cleaned up the empty folder on disk."
                    )
                else:
                    self.architecture_applied.emit(
                        f"Removed '{node_name}' from Folder Architecture."
                    )

            self._run_background(
                _work,
                _done,
                title=f"Updating files for removed '{node_name}'...",
                pass_progress=True,
            )
        elif disk_info.get("exists") and file_action_mode == "keep":
            self.architecture_applied.emit(
                f"Removed '{node_name}' from Folder Architecture (kept files in place on disk)."
            )

    def _on_merge_selected_clicked(self) -> None:
        src = self._arch.get_node(self.canvas.selected_node_id)
        if src is None:
            return
        dlg = MergeFolderDialog(self._arch, src, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            target_id = dlg.selected_target_id()
            if not target_id:
                return
            self._on_nodes_merged(
                src.id,
                target_id,
                new_name=dlg.merged_name() or None,
                move_disk=dlg.move_disk_files(),
            )

    def _on_nodes_merged(
        self,
        source_id: str,
        target_id: str,
        *,
        new_name: str | None = None,
        move_disk: bool = True,
    ) -> None:
        if not self._arch.can_merge(source_id, target_id):
            return
        src_node = self._arch.get_node(source_id)
        dst_node = self._arch.get_node(target_id)
        if src_node is None or dst_node is None:
            return
        src_name = src_node.name
        src_rel = self._arch.node_relative_path(source_id)

        prev_rects = self.canvas.snapshot_rects()
        self._push_undo_snapshot()
        merged = self._arch.merge_nodes(source_id, target_id, new_name=new_name)
        if merged is None:
            self._undo_stack.pop()
            self._update_undo_redo_buttons()
            return
        dst_rel = self._arch.node_relative_path(merged.id)
        self._dirty = True
        self._redraw_canvas(animate=True, prev_rects=prev_rects)
        self.canvas.select_node(merged.id)

        src_dir = self.service.watch_root / src_rel
        if move_disk and src_rel != dst_rel and src_dir.is_dir():
            def _work(report):  # noqa: ANN001
                return self.service.merge_folders_on_disk(src_rel, dst_rel, on_progress=report)

            def _done(res: dict[str, Any]) -> None:
                moved_n = int(res.get("moved_count", 0) or 0)
                self.architecture_applied.emit(
                    f"Merged '{src_name}' into '{merged.name}' ({moved_n} file(s) moved; click Apply & Shift Files to save rules)."
                )

            self._run_background(
                _work,
                _done,
                title=f"Merging '{src_name}' into '{merged.name}'...",
                pass_progress=True,
            )
        else:
            self.architecture_applied.emit(
                f"Merged '{src_name}' into '{merged.name}' — click Apply & Shift Files to sync."
            )

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
            lambda report: self.service.preview_architecture_shift(target_cfg, on_progress=report),
            lambda plan: self._confirm_and_shift(target_cfg, plan),
            title="Scanning managed folders...",
            pass_progress=True,
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
                precomputed_plan=plan,
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
