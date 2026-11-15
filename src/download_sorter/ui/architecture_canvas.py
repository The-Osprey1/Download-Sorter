"""Interactive layered Folder Architecture canvas, premade/custom dialogs & shift confirmation."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from download_sorter.architecture import (
    ARCHITECTURE_PRESETS,
    KIND_COLORS,
    PREMADE_FOLDER_CATALOG,
    ArchNode,
    FolderArchitecture,
    apply_architecture_to_config,
    architecture_from_config,
)
from download_sorter.service import SorterService
from download_sorter.ui.motion import (
    CollapsibleRibbonBtn,
    ElidedLabel,
    InteractiveRibbonBtn,
    make_vector_icon,
)
from download_sorter.ui.workers import run_async


class PremadeFolderDialog(QDialog):
    """Pick one or more premade folders from the 46-template catalog for any tier."""

    def __init__(
        self,
        arch: FolderArchitecture,
        default_tier: int = 2,
        default_parent_id: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.arch = arch
        self.setWindowTitle("Add Premade Folders")
        self.resize(620, 520)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        hdr = QLabel("Choose from 45+ premade folders & smart packs to place into your tier layout:")
        hdr.setWordWrap(True)
        hdr.setStyleSheet("color: #AEAEB2; font-size: 12.5px;")
        root.addWidget(hdr)

        top_controls = QHBoxLayout()
        top_controls.setSpacing(10)

        self.combo_tier = QComboBox()
        for t in range(1, arch.tier_count + 1):
            label = f"Tier {t}" + (" (Main Folder)" if t == 1 else " (Subfolder)")
            self.combo_tier.addItem(label, t)
        idx_t = self.combo_tier.findData(max(1, min(arch.tier_count, default_tier)))
        self.combo_tier.setCurrentIndex(max(0, idx_t))
        self.combo_tier.currentIndexChanged.connect(self._on_tier_changed)

        self.combo_parent = QComboBox()
        self.combo_parent.setMinimumWidth(180)

        self.edit_search = QLineEdit()
        self.edit_search.setPlaceholderText("Filter premade folders (e.g. PDF, School, CAD, Invoice)...")
        self.edit_search.textChanged.connect(self._populate_list)

        top_controls.addWidget(QLabel("Target Tier:"))
        top_controls.addWidget(self.combo_tier)
        top_controls.addWidget(QLabel("Inside Parent:"))
        top_controls.addWidget(self.combo_parent)
        root.addLayout(top_controls)
        root.addWidget(self.edit_search)

        self.list_widget = QListWidget()
        self.list_widget.setMinimumHeight(290)
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
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        footer.addWidget(buttons)
        root.addLayout(footer)

        self._refresh_parents(default_parent_id)
        self._populate_list()

    def _on_tier_changed(self) -> None:
        self._refresh_parents(None)
        self._populate_list()

    def _refresh_parents(self, preferred_id: str | None) -> None:
        tier = int(self.combo_tier.currentData() or 1)
        self.combo_parent.clear()
        if tier <= 1:
            self.combo_parent.addItem("Downloads/ (Root)", None)
            self.combo_parent.setEnabled(False)
            return
        self.combo_parent.setEnabled(True)
        self.combo_parent.addItem("(Auto-match default parent)", "")
        for pnode in self.arch.nodes_in_tier(tier - 1):
            self.combo_parent.addItem(self.arch.node_relative_path(pnode.id), pnode.id)
        if preferred_id:
            idx = self.combo_parent.findData(preferred_id)
            if idx >= 0:
                self.combo_parent.setCurrentIndex(idx)

    def _populate_list(self) -> None:
        checked_keys = set(self.selected_keys())
        self.list_widget.clear()
        query = self.edit_search.text().strip().lower()
        target_tier = int(self.combo_tier.currentData() or 1)
        show_all = self.chk_show_all.isChecked()

        for item in PREMADE_FOLDER_CATALOG:
            if not show_all and int(item["tier"]) != target_tier:
                continue
            hay = f"{item['name']} {item['summary']} {' '.join(item['extensions'])} {' '.join(item['keywords'])}".lower()
            if query and query not in hay:
                continue
            rule_tags: list[str] = []
            if item["extensions"]:
                rule_tags.append(", ".join(item["extensions"][:4]) + ("..." if len(item["extensions"]) > 4 else ""))
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
        return int(self.combo_tier.currentData() or 1)

    def target_parent_id(self) -> str | None:
        val = self.combo_parent.currentData()
        return str(val) if val else None


class CustomFolderNodeDialog(QDialog):
    """Create or edit a Custom Folder node with sorting rules at any tier."""

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
        self.setWindowTitle("Edit Folder & Rules" if node else "New Custom Folder & Rules")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        form = QFormLayout()
        form.setVerticalSpacing(10)
        form.setHorizontalSpacing(14)

        self.edit_name = QLineEdit(node.name if node else "")
        self.edit_name.setPlaceholderText("e.g. Lab Reports, Invoices, Guitar Tabs")

        self.combo_tier = QComboBox()
        for t in range(1, arch.tier_count + 1):
            self.combo_tier.addItem(f"Tier {t}", t)
        cur_tier = node.tier if node else default_tier
        idx_t = self.combo_tier.findData(max(1, min(arch.tier_count, cur_tier)))
        self.combo_tier.setCurrentIndex(max(0, idx_t))
        self.combo_tier.currentIndexChanged.connect(lambda: self._populate_parents(None))

        self.combo_parent = QComboBox()

        self.combo_kind = QComboBox()
        for k in (
            ("Document", "doc"),
            ("PDF", "pdf"),
            ("3D Print / .3MF / .STL", "print"),
            ("CAD Engineering", "cad"),
            ("CNC / G-Code", "cnc"),
            ("Spreadsheet", "sheet"),
            ("School & Study", "school"),
            ("Career & Resume", "career"),
            ("Finance & Taxes", "finance"),
            ("Image & Photo", "image"),
            ("Video", "video"),
            ("Audio", "audio"),
            ("Archive / ZIP", "archive"),
            ("Installer", "installer"),
            ("Code & Dev", "code"),
            ("Design & SVG", "design"),
            ("Fonts", "fonts"),
        ):
            self.combo_kind.addItem(k[0], k[1])
        if node:
            idx_k = self.combo_kind.findData(node.kind)
            if idx_k >= 0:
                self.combo_kind.setCurrentIndex(idx_k)

        self.edit_exts = QLineEdit(", ".join(node.extensions) if node else "")
        self.edit_exts.setPlaceholderText("e.g. .pdf, .docx, .zip (leave blank for any file type)")

        self.edit_keywords = QLineEdit(", ".join(node.keywords) if node else "")
        self.edit_keywords.setPlaceholderText("e.g. syllabus, lab_report, invoice (optional)")

        self.edit_domains = QLineEdit(", ".join(node.domains) if node else "")
        self.edit_domains.setPlaceholderText("e.g. canvas.edu, makerworld.com (optional)")

        form.addRow("Folder Name:", self.edit_name)
        form.addRow("Layer / Tier:", self.combo_tier)
        form.addRow("Parent Folder:", self.combo_parent)
        form.addRow("Category Color:", self.combo_kind)
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
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._populate_parents(node.parent_id if node else default_parent_id)

    def _populate_parents(self, preferred_id: str | None) -> None:
        tier = int(self.combo_tier.currentData() or 1)
        self.combo_parent.clear()
        if tier <= 1:
            self.combo_parent.addItem("Downloads/ (Top-Level)", None)
            self.combo_parent.setEnabled(False)
            return
        self.combo_parent.setEnabled(True)
        for pnode in self.arch.nodes_in_tier(tier - 1):
            if self.editing_node and pnode.id == self.editing_node.id:
                continue
            self.combo_parent.addItem(self.arch.node_relative_path(pnode.id), pnode.id)
        if preferred_id:
            idx = self.combo_parent.findData(preferred_id)
            if idx >= 0:
                self.combo_parent.setCurrentIndex(idx)

    def _on_save(self) -> None:
        name = self.edit_name.text().strip().replace("/", "").replace("\\", "")
        if not name:
            QMessageBox.warning(self, "Missing Name", "Please enter a folder name.")
            return
        tier = int(self.combo_tier.currentData() or 1)
        if tier > 1 and self.combo_parent.count() == 0:
            QMessageBox.warning(
                self,
                "Parent Required",
                f"Add at least one folder in Tier {tier - 1} before placing a folder in Tier {tier}.",
            )
            return
        self.accept()

    def values(self) -> dict[str, Any]:
        split = lambda s: [x.strip() for x in s.split(",") if x.strip()]
        return {
            "name": self.edit_name.text().strip(),
            "tier": int(self.combo_tier.currentData() or 1),
            "parent_id": self.combo_parent.currentData() if int(self.combo_tier.currentData() or 1) > 1 else None,
            "kind": str(self.combo_kind.currentData() or "default"),
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
            "All file shifts are logged in SQLite as one Undoable batch. Empty old managed folders are tidied automatically."
        )
        sub.setStyleSheet("color: #98989D; font-size: 12px;")
        layout.addWidget(sub)

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
        layout.addWidget(self.chk_create_empty)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn is not None:
            ok_btn.setText(f"OK — Apply && Shift ({len(planned)})")
            ok_btn.setObjectName("PrimaryButton")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class FolderArchitectureCanvas(QWidget):
    """Interactive visual QPainter multi-tier folder architecture graph."""

    node_selected = Signal(str)
    node_edit_requested = Signal(str)
    node_delete_requested = Signal(str)
    tier_add_requested = Signal(int)

    MARGIN_X = 16
    MARGIN_Y = 14
    TIER_GAP = 26
    HEADER_H = 32
    CARD_W = 160
    CARD_H = 52
    CARD_GAP_X = 12
    CARD_GAP_Y = 10
    BAND_PAD_X = 12
    BAND_PAD_BOTTOM = 12

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._arch = FolderArchitecture()
        self._watch_label = "Downloads/"
        self._selected_id: str | None = None
        self._hovered_id: str | None = None
        self._hovered_delete_id: str | None = None
        self._hovered_tier_add: int | None = None
        self._node_rects: dict[str, QRectF] = {}
        self._delete_rects: dict[str, QRectF] = {}
        self._tier_add_rects: dict[int, QRectF] = {}
        self._tier_band_rects: dict[int, QRectF] = {}
        self.setMouseTracking(True)
        self._recompute_layout()

    def set_architecture(self, arch: FolderArchitecture, watch_label: str = "Downloads/") -> None:
        self._arch = arch
        self._watch_label = watch_label
        if self._selected_id and arch.get_node(self._selected_id) is None:
            self._selected_id = None
        if not self._selected_id and arch.nodes:
            self._selected_id = arch.nodes[0].id
        self._recompute_layout(update_min_size=True)
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

    def _ordered_tier_nodes(self) -> dict[int, list[ArchNode]]:
        ordered: dict[int, list[ArchNode]] = {}
        t1 = self._arch.nodes_in_tier(1)
        ordered[1] = t1
        for t in range(2, self._arch.tier_count + 1):
            parent_order = {n.id: idx for idx, n in enumerate(ordered.get(t - 1, []))}
            nodes_t = list(self._arch.nodes_in_tier(t))
            nodes_t.sort(key=lambda n: (parent_order.get(n.parent_id or "", 999), n.name.lower()))
            ordered[t] = nodes_t
        return ordered

    def _recompute_layout(self, *, update_min_size: bool = True) -> None:
        self._node_rects.clear()
        self._delete_rects.clear()
        self._tier_add_rects.clear()
        self._tier_band_rects.clear()

        ordered = self._ordered_tier_nodes()
        parent_w = (self.parentWidget().width() - 4) if self.parentWidget() is not None else 740
        avail_w = max(720, self.width(), parent_w)
        band_w = float(avail_w - self.MARGIN_X * 2)
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
        self._node_rects["__root__"] = QRectF(x0, y0, root_w, self.CARD_H)

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
                self.MARGIN_X + band_w - 68, cur_band_y + 5, 56, 22
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
                self._delete_rects[node.id] = QRectF(rect.right() - 20, rect.top() + 5, 15, 15)

            cur_band_y += band_h + self.TIER_GAP

        total_h = max(360, int(cur_band_y - self.TIER_GAP + self.MARGIN_Y))
        if update_min_size and (self.minimumWidth() != 680 or self.minimumHeight() != total_h):
            self.setMinimumSize(680, total_h)

    def paintEvent(self, event) -> None:  # noqa: ANN001
        self._recompute_layout(update_min_size=False)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Recessed canvas background
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#111113"))
        p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 12, 12)

        # Subtle dot grid
        p.setPen(QColor(255, 255, 255, 14))
        for gx in range(20, self.width(), 24):
            for gy in range(20, self.height(), 24):
                p.drawPoint(gx, gy)

        # Draw Horizontal Tier Bands Top-to-Bottom
        for t in range(0, self._arch.tier_count + 1):
            band_rect = self._tier_band_rects.get(t)
            if band_rect is None:
                continue
            p.setPen(QPen(QColor("#232327"), 1.0))
            p.setBrush(QColor("#161618"))
            p.drawRoundedRect(band_rect, 11, 11)

            f_hdr = QFont("Segoe UI", 9, QFont.Weight.Bold)
            p.setFont(f_hdr)
            p.setPen(QColor("#0A84FF") if t == 0 else QColor("#98989D"))
            title = (
                "TIER 0 · ROOT FOLDER"
                if t == 0
                else f"TIER {t} · {len(self._arch.nodes_in_tier(t))} FOLDERS"
            )
            p.drawText(
                QRectF(band_rect.left() + 14, band_rect.top() + 4, band_rect.width() - 92, 24),
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

        # Draw Top-to-Bottom Cubic Bezier Connectors (dim pass first, active highlights on top)
        root_rect = self._node_rects.get("__root__")
        connector_items: list[tuple[bool, ArchNode, QRectF, QRectF]] = []
        for node in self._arch.nodes:
            if node.tier > self._arch.tier_count:
                continue
            dst_rect = self._node_rects.get(node.id)
            if dst_rect is None:
                continue
            src_rect = root_rect if node.tier == 1 else self._node_rects.get(node.parent_id or "")
            if src_rect is None:
                continue
            is_hi = bool(
                node.id == self._selected_id
                or node.id == self._hovered_id
                or (node.parent_id and node.parent_id == self._selected_id)
            )
            connector_items.append((is_hi, node, src_rect, dst_rect))

        for want_hi in (False, True):
            for is_hi, node, src_rect, dst_rect in connector_items:
                if is_hi != want_hi:
                    continue
                col = QColor(node.accent_hex)
                col.setAlpha(220 if is_hi else 48)
                p.setPen(
                    QPen(
                        col,
                        2.0 if is_hi else 1.15,
                        Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap,
                    )
                )
                p.setBrush(Qt.BrushStyle.NoBrush)
                p1 = QPointF(src_rect.center().x(), src_rect.bottom())
                p2 = QPointF(dst_rect.center().x(), dst_rect.top())
                dy = max(14.0, (p2.y() - p1.y()) * 0.48)
                path = QPainterPath(p1)
                path.cubicTo(QPointF(p1.x(), p1.y() + dy), QPointF(p2.x(), p2.y() - dy), p2)
                p.drawPath(path)

        # Draw Tier 0 Root Card
        if root_rect is not None:
            p.setPen(QPen(QColor("#0A84FF"), 1.4))
            p.setBrush(QColor("#1C1C1E"))
            p.drawRoundedRect(root_rect, 10, 10)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#0A84FF"))
            p.drawRoundedRect(QRectF(root_rect.left(), root_rect.top() + 10, 3.5, root_rect.height() - 20), 1.5, 1.5)
            p.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
            p.setPen(QColor("#F5F5F7"))
            p.drawText(root_rect.adjusted(14, 9, -10, -24), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "Downloads")
            p.setFont(QFont("Segoe UI", 8))
            p.setPen(QColor("#8E8E93"))
            fm = QFontMetrics(p.font())
            elided_watch = fm.elidedText(self._watch_label, Qt.TextElideMode.ElideMiddle, int(root_rect.width() - 24))
            p.drawText(root_rect.adjusted(14, 29, -10, -6), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elided_watch)

        # Draw Tier 1..N Node Cards
        for node in self._arch.nodes:
            rect = self._node_rects.get(node.id)
            if rect is None:
                continue
            selected = node.id == self._selected_id
            hovered = node.id == self._hovered_id
            col = QColor(node.accent_hex)

            border = col if selected else (QColor("#48484A") if hovered else QColor("#2C2C2E"))
            p.setPen(QPen(border, 1.6 if selected else 1.0))
            p.setBrush(QColor("#242428") if selected else QColor("#1C1C1E"))
            p.drawRoundedRect(rect, 10, 10)

            # Left Accent Stripe
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(QRectF(rect.left() + 0.5, rect.top() + 9, 3.5, rect.height() - 18), 1.5, 1.5)

            # Folder Name
            p.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
            p.setPen(QColor("#F5F5F7"))
            fm_name = QFontMetrics(p.font())
            title = fm_name.elidedText(node.name, Qt.TextElideMode.ElideRight, int(rect.width() - 36))
            p.drawText(rect.adjusted(13, 8, -22, -26), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)

            # Rule/Ext Summary Line
            parts: list[str] = []
            if node.keywords:
                parts.append(f"{len(node.keywords)} kw")
            if node.domains:
                parts.append(f"{len(node.domains)} site")
            if node.extensions:
                parts.append(", ".join(node.extensions[:2]) + ("+" if len(node.extensions) > 2 else ""))
            sub_txt = " • ".join(parts) if parts else "Container folder"
            p.setFont(QFont("Segoe UI", 8))
            p.setPen(QColor("#8E8E93"))
            fm_sub = QFontMetrics(p.font())
            p.drawText(
                rect.adjusted(13, 29, -10, -7),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                fm_sub.elidedText(sub_txt, Qt.TextElideMode.ElideRight, int(rect.width() - 24)),
            )

            # Hover Delete '×' button
            if hovered or selected:
                del_r = self._delete_rects[node.id]
                del_hov = self._hovered_delete_id == node.id
                p.setPen(QColor("#FF453A" if del_hov else "#8E8E93"))
                p.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
                p.drawText(del_r, Qt.AlignmentFlag.AlignCenter, "×")

    def mouseMoveEvent(self, event) -> None:  # noqa: ANN001
        pt = event.position()
        self._hovered_delete_id = None
        self._hovered_id = None
        self._hovered_tier_add = None

        for t, r in self._tier_add_rects.items():
            if r.contains(pt):
                self._hovered_tier_add = t
                self.setCursor(Qt.CursorShape.PointingHandCursor)
                self.update()
                return

        for nid, dr in self._delete_rects.items():
            nr = self._node_rects.get(nid)
            if (nid == self._selected_id or (nr and nr.contains(pt))) and dr.contains(pt):
                self._hovered_delete_id = nid
                self._hovered_id = nid
                self.setCursor(Qt.CursorShape.PointingHandCursor)
                self.update()
                return

        for nid, rect in self._node_rects.items():
            if nid != "__root__" and rect.contains(pt):
                self._hovered_id = nid
                self.setCursor(Qt.CursorShape.PointingHandCursor)
                self.update()
                return

        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: ANN001
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pt = event.position()

        for t, r in self._tier_add_rects.items():
            if r.contains(pt):
                self.tier_add_requested.emit(t)
                return

        for nid, dr in self._delete_rects.items():
            nr = self._node_rects.get(nid)
            if (nid == self._selected_id or (nr and nr.contains(pt))) and dr.contains(pt):
                self.node_delete_requested.emit(nid)
                return

        for nid, rect in self._node_rects.items():
            if nid != "__root__" and rect.contains(pt):
                self.select_node(nid)
                return

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: ANN001
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pt = event.position()
        for nid, rect in self._node_rects.items():
            if nid != "__root__" and rect.contains(pt):
                self.select_node(nid)
                self.node_edit_requested.emit(nid)
                return


class ArchitectureTab(QWidget):
    """Tab 2: Interactive Layered Folder Architecture Canvas + Premade/Custom Rules + Shift/Undo/Defaults."""

    architecture_applied = Signal(str)
    undo_requested = Signal()
    carry_over_requested = Signal()

    def __init__(self, service: SorterService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self._arch: FolderArchitecture = architecture_from_config(service.config)
        self._dirty = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 0)
        root.setSpacing(12)

        # Top Two-Row Action Bar Card (spacious Apple layout, zero horizontal cramping)
        bar = QFrame()
        bar.setObjectName("SurfaceCard")
        bar_outer = QVBoxLayout(bar)
        bar_outer.setContentsMargins(14, 10, 14, 10)
        bar_outer.setSpacing(8)

        bar_row1 = QHBoxLayout()
        bar_row1.setSpacing(8)

        self.combo_preset = QComboBox()
        for key, meta in ARCHITECTURE_PRESETS.items():
            self.combo_preset.addItem(str(meta["label"]), key)
        self.combo_preset.setMinimumWidth(150)
        self.combo_preset.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.combo_preset.setMinimumContentsLength(14)
        bar_row1.addWidget(QLabel("Blueprint:"))
        bar_row1.addWidget(self.combo_preset)

        self.btn_load_preset = InteractiveRibbonBtn("Load Preset")
        self.btn_load_preset.clicked.connect(self._on_load_preset)
        bar_row1.addWidget(self.btn_load_preset)

        self.btn_auto_create = CollapsibleRibbonBtn(
            "Auto-Create Basic",
            make_vector_icon("bolt", "#69E6AD", 14),
        )
        self.btn_auto_create.setToolTip(
            "Automatically build a clean, standard 2-tier basic folder architecture to customize"
        )
        self.btn_auto_create.clicked.connect(self._on_auto_create_basic)
        bar_row1.addWidget(self.btn_auto_create)

        bar_row1.addStretch(1)

        # Always visible Undo, Return to Defaults & Apply/Shift controls
        self.btn_arch_undo = CollapsibleRibbonBtn("Undo", make_vector_icon("undo", "#F5F5F7", 14))
        self.btn_arch_undo.setToolTip("Undo last file shift and architecture change")
        self.btn_arch_undo.clicked.connect(self._on_undo_clicked)
        bar_row1.addWidget(self.btn_arch_undo)

        self.btn_arch_defaults = InteractiveRibbonBtn("Return to Defaults")
        self.btn_arch_defaults.setObjectName("GhostButton")
        self.btn_arch_defaults.setToolTip("Reset Folder Architecture & Rules to starter defaults")
        self.btn_arch_defaults.clicked.connect(self._on_return_to_defaults)
        bar_row1.addWidget(self.btn_arch_defaults)

        self.btn_apply_shift = InteractiveRibbonBtn(
            "Apply && Shift Files",
            make_vector_icon("bolt", "#FFFFFF", 14),
        )
        self.btn_apply_shift.setObjectName("PrimaryButton")
        self.btn_apply_shift.clicked.connect(self._on_apply_and_shift)
        bar_row1.addWidget(self.btn_apply_shift)
        bar_outer.addLayout(bar_row1)

        bar_row2 = QHBoxLayout()
        bar_row2.setSpacing(8)

        self.btn_add_tier = InteractiveRibbonBtn("+ Add Tier")
        self.btn_add_tier.setToolTip("Add a new horizontal folder depth tier (up to 5 tiers)")
        self.btn_add_tier.clicked.connect(self._on_add_tier)
        bar_row2.addWidget(self.btn_add_tier)

        self.btn_remove_tier = InteractiveRibbonBtn("- Remove Tier")
        self.btn_remove_tier.setToolTip("Remove the deepest folder tier")
        self.btn_remove_tier.clicked.connect(self._on_remove_tier)
        bar_row2.addWidget(self.btn_remove_tier)

        self.btn_add_premade = InteractiveRibbonBtn("Premade Folder", make_vector_icon("folder", "#F5F5F7", 14))
        self.btn_add_premade.clicked.connect(lambda: self._open_premade_dialog())
        bar_row2.addWidget(self.btn_add_premade)

        self.btn_add_custom = InteractiveRibbonBtn("Custom Folder", make_vector_icon("plus", "#F5F5F7", 14))
        self.btn_add_custom.clicked.connect(lambda: self._open_custom_dialog())
        bar_row2.addWidget(self.btn_add_custom)

        bar_row2.addStretch(1)
        layer_tip = ElidedLabel("Top-to-Bottom Flow: Tier 0 Root -> Tier 1 Categories -> Tier 2+ Subfolders")
        layer_tip.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layer_tip.setStyleSheet("color: #6E6E73; font-size: 11px;")
        bar_row2.addWidget(layer_tip, 1)
        bar_outer.addLayout(bar_row2)

        root.addWidget(bar)

        # Canvas + Right Inspector
        body = QHBoxLayout()
        body.setSpacing(12)

        canvas_card = QFrame()
        canvas_card.setObjectName("SurfaceCard")
        cc_layout = QVBoxLayout(canvas_card)
        cc_layout.setContentsMargins(12, 12, 12, 12)
        cc_layout.setSpacing(8)

        hdr_row = QHBoxLayout()
        self.lbl_arch_summary = QLabel("")
        self.lbl_arch_summary.setStyleSheet("color: #98989D; font-size: 12px;")
        hdr_row.addWidget(self.lbl_arch_summary, 1)

        hint = ElidedLabel("Click a folder to inspect · Double-click to edit rules · Click '+ Add' inside any tier")
        hint.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        hint.setStyleSheet("color: #6E6E73; font-size: 11px;")
        hdr_row.addWidget(hint, 2)
        cc_layout.addLayout(hdr_row)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.canvas = FolderArchitectureCanvas(self)
        self.canvas.node_selected.connect(self._update_inspector)
        self.canvas.node_edit_requested.connect(self._edit_node_by_id)
        self.canvas.node_delete_requested.connect(self._delete_node_by_id)
        self.canvas.tier_add_requested.connect(self._on_tier_quick_add)
        self.scroll.setWidget(self.canvas)
        cc_layout.addWidget(self.scroll, 1)
        body.addWidget(canvas_card, 1)

        # Right Inspector Card
        insp = QFrame()
        insp.setObjectName("SurfaceCard")
        insp.setFixedWidth(250)
        # The panel scrolls in a short window instead of setting the window's height.
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
        il.setContentsMargins(14, 14, 14, 14)
        il.setSpacing(10)

        insp_title = QLabel("Selected Folder")
        insp_title.setObjectName("CardTitle")
        il.addWidget(insp_title)

        self.lbl_sel_name = QLabel("None")
        self.lbl_sel_name.setStyleSheet("font-size: 15px; font-weight: 600; color: #F5F5F7;")
        self.lbl_sel_name.setWordWrap(True)
        il.addWidget(self.lbl_sel_name)

        self.lbl_sel_path = QLabel("")
        self.lbl_sel_path.setObjectName("MonoPath")
        self.lbl_sel_path.setWordWrap(True)
        il.addWidget(self.lbl_sel_path)

        self.lbl_sel_rules = QLabel("")
        self.lbl_sel_rules.setStyleSheet("color: #98989D; font-size: 12px;")
        self.lbl_sel_rules.setWordWrap(True)
        il.addWidget(self.lbl_sel_rules)

        self.btn_edit_node = InteractiveRibbonBtn("Edit Folder && Rules", make_vector_icon("edit", "#F5F5F7", 14))
        self.btn_edit_node.clicked.connect(lambda: self._edit_node_by_id(self.canvas.selected_node_id or ""))
        il.addWidget(self.btn_edit_node)

        self.btn_add_child = InteractiveRibbonBtn("Add Child Subfolder", make_vector_icon("plus", "#F5F5F7", 14))
        self.btn_add_child.clicked.connect(self._on_add_child_for_selected)
        il.addWidget(self.btn_add_child)

        self.btn_delete_node = InteractiveRibbonBtn("Remove Folder", make_vector_icon("delete", "#FF453A", 14))
        self.btn_delete_node.clicked.connect(lambda: self._delete_node_by_id(self.canvas.selected_node_id or ""))
        il.addWidget(self.btn_delete_node)

        il.addStretch(1)

        div = QFrame()
        div.setObjectName("CardDivider")
        il.addWidget(div)

        self.btn_carry_over = InteractiveRibbonBtn("Carry Over v2.8 / Setup...")
        self.btn_carry_over.setObjectName("GhostButton")
        self.btn_carry_over.clicked.connect(self.carry_over_requested.emit)
        il.addWidget(self.btn_carry_over)

        body.addWidget(insp)
        root.addLayout(body, 1)

        self.sync_from_service(force=True)

    # Below this width Auto-Create Basic and Undo show only their icons.
    TOOLBAR_COMPACT_WIDTH = 950

    def resizeEvent(self, event) -> None:  # noqa: ANN001
        super().resizeEvent(event)
        compact = self.width() < self.TOOLBAR_COMPACT_WIDTH
        self.btn_auto_create.set_collapsed(compact)
        self.btn_arch_undo.set_collapsed(compact)

    def sync_from_service(self, *, force: bool = False) -> None:
        if force or not self._dirty:
            self._arch = architecture_from_config(self.service.config)
            self._dirty = False
        self._redraw_canvas()

    def _redraw_canvas(self) -> None:
        watch = str(self.service.watch_root)
        self.canvas.set_architecture(self._arch, watch_label=watch)
        self.btn_add_tier.setEnabled(self._arch.tier_count < FolderArchitecture.MAX_TIERS)
        self.btn_remove_tier.setEnabled(self._arch.tier_count > FolderArchitecture.MIN_TIERS)
        dirty_tag = "  •  Unsaved draft (click Apply & Shift Files)" if self._dirty else ""
        self.lbl_arch_summary.setText(
            f"{self._arch.tier_count} Tier(s)  •  {len(self._arch.nodes)} Folders{dirty_tag}"
        )
        self._update_inspector(self.canvas.selected_node_id or "")

    def _update_inspector(self, node_id: str) -> None:
        node = self._arch.get_node(node_id)
        if node is None:
            self.lbl_sel_name.setText("Click any folder node")
            self.lbl_sel_path.setText(str(self.service.watch_root))
            self.lbl_sel_rules.setText("Select a node on the canvas to inspect or customize its rules.")
            self.btn_edit_node.setEnabled(False)
            self.btn_add_child.setEnabled(False)
            self.btn_delete_node.setEnabled(False)
            return

        rel = self._arch.node_relative_path(node.id)
        self.lbl_sel_name.setText(f"{node.name} (Tier {node.tier})")
        self.lbl_sel_path.setText(f"Downloads\\{rel}")

        lines = [
            f"Extensions: {', '.join(node.extensions) or 'Any matched by rule'}",
            f"Keywords: {', '.join(node.keywords) or 'None'}",
            f"Websites: {', '.join(node.domains) or 'None'}",
        ]
        self.lbl_sel_rules.setText("\n".join(lines))
        self.btn_edit_node.setEnabled(True)
        self.btn_add_child.setEnabled(node.tier < FolderArchitecture.MAX_TIERS)
        self.btn_delete_node.setEnabled(True)

    def _on_load_preset(self) -> None:
        key = str(self.combo_preset.currentData() or "standard")
        self._arch = FolderArchitecture.from_preset(key)
        self._dirty = True
        self._redraw_canvas()

    def _on_auto_create_basic(self) -> None:
        self._arch = FolderArchitecture.auto_create_basic(self.service.watch_root)
        idx = self.combo_preset.findData("basic_starter")
        if idx >= 0:
            self.combo_preset.setCurrentIndex(idx)
        self._dirty = True
        self._redraw_canvas()
        if self._arch.nodes:
            self.canvas.select_node(self._arch.nodes[0].id)
        self.architecture_applied.emit(
            "Auto-created Basic Starter architecture — customize folders or click Apply & Shift Files."
        )

    def _on_add_tier(self) -> None:
        self._arch.add_tier()
        self._dirty = True
        self._redraw_canvas()

    def _on_remove_tier(self) -> None:
        if self._arch.tier_count <= 1:
            return
        self._arch.remove_last_tier()
        self._dirty = True
        self._redraw_canvas()

    def _on_tier_quick_add(self, tier: int) -> None:
        self._open_premade_dialog(default_tier=tier)

    def _on_add_child_for_selected(self) -> None:
        node = self._arch.get_node(self.canvas.selected_node_id)
        if node is None:
            return
        if node.tier >= self._arch.tier_count:
            self._arch.add_tier()
        self._open_custom_dialog(default_tier=node.tier + 1, default_parent_id=node.id)

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
            t_tier = dlg.target_tier()
            p_id = dlg.target_parent_id()
            last_added: ArchNode | None = None
            for k in keys:
                node = self._arch.add_premade_node(k, tier=t_tier, parent_id=p_id)
                if node is not None:
                    last_added = node
            self._dirty = True
            self._redraw_canvas()
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
            vals = dlg.values()
            node = self._arch.add_custom_node(**vals)
            self._dirty = True
            self._redraw_canvas()
            self.canvas.select_node(node.id)

    def _edit_node_by_id(self, node_id: str) -> None:
        node = self._arch.get_node(node_id)
        if node is None:
            return
        dlg = CustomFolderNodeDialog(self._arch, node=node, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            vals = dlg.values()
            self._arch.update_node(node.id, **vals)
            self._dirty = True
            self._redraw_canvas()
            self.canvas.select_node(node.id)

    def _delete_node_by_id(self, node_id: str) -> None:
        if not node_id or self._arch.get_node(node_id) is None:
            return
        self._arch.remove_node(node_id)
        self._dirty = True
        self._redraw_canvas()

    def _run_background(self, work, on_done) -> None:  # noqa: ANN001
        """Run a disk-heavy service call off the GUI thread, one at a time.

        Under pytest this is a plain synchronous call followed by ``on_done``.
        """
        if getattr(self, "_busy", False):
            return
        self._busy = True

        def _finished(result) -> None:  # noqa: ANN001
            self._busy = False
            on_done(result)

        def _failed(message: str) -> None:
            self._busy = False
            QMessageBox.warning(self, "Folder Architecture", f"That did not finish:\n{message}")

        try:
            run_async(work, _finished, _failed, parent=self)
        except Exception:
            self._busy = False
            raise

    def _on_apply_and_shift(self) -> None:
        target_cfg = apply_architecture_to_config(self._arch, self.service.config)
        # Scanning every managed folder for the confirmation preview is disk work too.
        self._run_background(
            lambda: self.service.preview_architecture_shift(target_cfg),
            lambda plan: self._confirm_and_shift(target_cfg, plan),
        )

    def _confirm_and_shift(self, target_cfg, plan: dict[str, Any]) -> None:  # noqa: ANN001
        dlg = ShiftConfirmDialog(self._arch, plan, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        create_empty = dlg.chk_create_empty.isChecked()

        def _work() -> dict[str, Any]:
            return self.service.apply_architecture_and_shift(
                target_cfg,
                shift_files=True,
                create_empty_folders=create_empty,
            )

        def _done(res: dict[str, Any]) -> None:
            self._dirty = False
            self.sync_from_service(force=True)
            shifted = res["shifted_count"]
            self.architecture_applied.emit(
                f"Applied {self._arch.tier_count}-tier Folder Architecture and shifted {shifted} file(s)."
            )

        self._run_background(_work, _done)

    def _on_undo_clicked(self) -> None:
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

        self._run_background(self.service.undo_architecture_or_last_batch, _done)

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
