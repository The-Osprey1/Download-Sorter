"""Visual Rules Manager & Live Sorter Sandbox for Suite Central.

Provides:
- RulesManagerDialog: Main dark-mode dialog with tabs, active project mode, and live sandbox.
- RuleTableWidget: Precedence table with add, edit, delete, toggle enabled, and move up/down.
- RuleEditDialog: Clean modal to edit rule properties.
- ActiveProjectCard: Toggle switch, project selector, target folder, and auto-expiry.
- LiveSandboxWidget: Instant real-time routing simulator with chips, badges, and trace.
"""

from __future__ import annotations

import copy
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QPoint, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QIcon, QKeySequence, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

if "suite_data" in sys.modules:
    import suite_data
else:
    try:
        import config_manager as suite_data
    except ImportError:
        import suite_data

try:
    from suite_theme import ACCENTS, COLORS, RADII, SPACING, TOKENS, TYPE, qt_stylesheet
except ImportError:
    from tokens import ACCENTS, COLORS, RADII, SPACING, TOKENS, TYPE, qt_stylesheet

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import about

try:
    import sorter
except ImportError:
    sorter = None

# Reference Haas samples for realistic sandbox evaluation if available
HAAS_STUDIO_DIR = Path("C:/Users/devyn/Projects/haas-gcode-studio")
LOCAL_SAMPLES_DIR = ROOT / "tests" / "samples"
if HAAS_STUDIO_DIR.exists():
    HAAS_CONTOUR_SAMPLE = HAAS_STUDIO_DIR / "samples" / "sample1_contour.nc"
    HAAS_BOLT_SAMPLE = HAAS_STUDIO_DIR / "samples" / "sample2_bolt_circle.nc"
elif LOCAL_SAMPLES_DIR.exists():
    HAAS_CONTOUR_SAMPLE = LOCAL_SAMPLES_DIR / "sample1_contour.nc"
    HAAS_BOLT_SAMPLE = LOCAL_SAMPLES_DIR / "sample2_bolt_circle.nc"
else:
    HAAS_CONTOUR_SAMPLE = None
    HAAS_BOLT_SAMPLE = None
MAKERWORLD_SAMPLE = ROOT / "tests" / "fixtures" / "3mf" / "makerworld_model.3mf"


# =====================================================================
# 1. PRESET LIBRARY & RULE PACK DIALOG
# =====================================================================

class PresetLibraryDialog(QDialog):
    """Modal dialog for browsing and selecting curated rule packs."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("📚 Rule Preset Library")
        self.setMinimumSize(580, 500)
        self.resize(620, 540)
        self._selected_preset_ids: list[str] = []
        self._checkboxes: dict[str, QCheckBox] = {}
        self._init_ui()

    def _init_ui(self) -> None:
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {COLORS['surface']};
                color: {COLORS['text']};
            }}
            QLabel {{
                color: {COLORS['text']};
                font-family: '{TYPE['ui']}';
            }}
            QScrollArea {{
                border: 1px solid {COLORS['border']};
                background: {COLORS['canvas']};
                border-radius: {RADII['small']}px;
            }}
            QPushButton {{
                background: {COLORS['surfaceRaised']};
                border: 1px solid {COLORS['border']};
                border-radius: {RADII['small']}px;
                color: {COLORS['text']};
                padding: 6px 14px;
                font-family: '{TYPE['ui']}';
                font-weight: 600;
                font-size: 11px;
            }}
            QPushButton:hover {{
                background: {COLORS['surfaceHover']};
                border-color: {ACCENTS['downloadSorter']};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        # Header
        title_lbl = QLabel("📚 Curated Rule Presets Library")
        title_lbl.setFont(QFont(TYPE["ui"], 13, QFont.Weight.Bold))
        layout.addWidget(title_lbl)

        sub_lbl = QLabel("Select rule packs to add to your configuration. Rules with matching names will not be duplicated.")
        sub_lbl.setStyleSheet(f"color: {COLORS['textMuted']}; font-size: 11px;")
        sub_lbl.setWordWrap(True)
        layout.addWidget(sub_lbl)

        # Scroll Area with Presets
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(12, 12, 12, 12)
        container_layout.setSpacing(10)

        presets = suite_data.get_available_presets() if hasattr(suite_data, "get_available_presets") else []
        for p in presets:
            card = QFrame()
            card.setStyleSheet(f"""
                QFrame {{
                    background: {COLORS['surfaceRaised']};
                    border: 1px solid {COLORS['border']};
                    border-radius: {RADII['small']}px;
                    padding: 8px;
                }}
            """)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(10, 8, 10, 8)
            card_layout.setSpacing(4)

            top_row = QHBoxLayout()
            cb = QCheckBox(f"{p.get('name', p.get('id'))}")
            cb.setFont(QFont(TYPE["ui"], 11, QFont.Weight.Bold))
            cb.setStyleSheet(f"color: {ACCENTS['downloadSorter']};")
            self._checkboxes[p["id"]] = cb
            top_row.addWidget(cb)

            badge = QLabel(f"  {p.get('category', '')}  ")
            badge.setStyleSheet(f"""
                background: {COLORS['canvas']};
                color: {COLORS['textMuted']};
                border: 1px solid {COLORS['border']};
                border-radius: 4px;
                font-size: 9px;
            """)
            top_row.addWidget(badge)
            top_row.addStretch()
            card_layout.addLayout(top_row)

            desc = QLabel(p.get("description", ""))
            desc.setStyleSheet(f"color: {COLORS['textMuted']}; font-size: 10px;")
            desc.setWordWrap(True)
            card_layout.addWidget(desc)

            rule_names = [r.get("name", "") for r in p.get("rules", [])]
            rules_lbl = QLabel(f"Includes: {', '.join(rule_names)}")
            rules_lbl.setStyleSheet(f"color: {COLORS['textFaint']}; font-size: 9px; font-style: italic;")
            rules_lbl.setWordWrap(True)
            card_layout.addWidget(rules_lbl)

            container_layout.addWidget(card)

        container_layout.addStretch()
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)

        # Buttons
        btn_layout = QHBoxLayout()
        select_all_btn = QPushButton("Select All")
        select_all_btn.clicked.connect(self._select_all)
        btn_layout.addWidget(select_all_btn)

        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self._clear_all)
        btn_layout.addWidget(clear_btn)

        btn_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        add_btn = QPushButton("✓ Add Selected Rules")
        add_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ACCENTS['downloadSorter']};
                color: #0B0D12;
                border: 1px solid {ACCENTS['downloadSorter']};
                font-weight: bold;
                padding: 6px 16px;
            }}
            QPushButton:hover {{
                background: #82ecc0;
            }}
        """)
        add_btn.clicked.connect(self._on_accept)
        btn_layout.addWidget(add_btn)

        layout.addLayout(btn_layout)

    def _select_all(self) -> None:
        for cb in self._checkboxes.values():
            cb.setChecked(True)

    def _clear_all(self) -> None:
        for cb in self._checkboxes.values():
            cb.setChecked(False)

    def _on_accept(self) -> None:
        self._selected_preset_ids = [pid for pid, cb in self._checkboxes.items() if cb.isChecked()]
        if not self._selected_preset_ids:
            QMessageBox.information(self, "No Selection", "Please check at least one preset to add.")
            return
        self.accept()

    def get_selected_preset_ids(self) -> list[str]:
        return list(self._selected_preset_ids)


# =====================================================================
# 2. RULE EDIT MODAL DIALOG
# =====================================================================

class RuleEditDialog(QDialog):
    """Modal dialog for creating and editing a keyword or extension rule."""

    def __init__(self, rule: dict[str, Any] | None = None, rule_type: str = "keyword", parent: QWidget | None = None):
        super().__init__(parent)
        self.rule_type = rule_type
        self.original_rule = rule or {}

        title = "Edit Keyword Rule" if rule_type == "keyword" else "Edit Extension Rule"
        if not rule:
            title = "Add Keyword Rule" if rule_type == "keyword" else "Add Extension Rule"
        self.setWindowTitle(title)
        self.setMinimumWidth(500)
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {COLORS['surface']};
                color: {COLORS['text']};
            }}
            QLabel {{
                color: {COLORS['text']};
                font-family: '{TYPE['ui']}';
                font-size: 11px;
            }}
            QLineEdit {{
                background: {COLORS['canvas']};
                border: 1px solid {COLORS['border']};
                border-radius: {RADII['small']}px;
                color: {COLORS['text']};
                padding: 6px 10px;
                font-family: '{TYPE['ui']}';
                font-size: 12px;
            }}
            QLineEdit:focus {{
                border-color: {ACCENTS['downloadSorter']};
            }}
            QPushButton {{
                background: {COLORS['surfaceRaised']};
                border: 1px solid {COLORS['border']};
                border-radius: {RADII['small']}px;
                color: {COLORS['text']};
                padding: 6px 14px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background: {COLORS['surfaceHover']};
                border-color: {ACCENTS['downloadSorter']};
            }}
            QCheckBox {{
                color: {COLORS['text']};
                font-size: 11px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        # Header Title
        header_lbl = QLabel(title.upper())
        header_lbl.setFont(QFont(TYPE["ui"], 12, QFont.Weight.Bold))
        header_lbl.setStyleSheet(f"color: {ACCENTS['downloadSorter']}; letter-spacing: 0.5px;")
        layout.addWidget(header_lbl)

        # Rule Name
        layout.addWidget(QLabel("RULE NAME"))
        self._name_input = QLineEdit()
        self._name_input.setPlaceholderText("e.g. ME 3210 Coursework, 3D Models STL...")
        self._name_input.setText(self.original_rule.get("name", ""))
        layout.addWidget(self._name_input)

        # Destination Folder
        layout.addWidget(QLabel("DESTINATION FOLDER (Relative to Downloads root or Absolute)"))
        dest_box = QHBoxLayout()
        dest_box.setSpacing(8)
        self._folder_input = QLineEdit()
        self._folder_input.setPlaceholderText("e.g. Documents\\School\\ME3210 or 3D Models\\STL")
        self._folder_input.setText(self.original_rule.get("folder", ""))
        dest_box.addWidget(self._folder_input)

        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse_folder)
        dest_box.addWidget(browse_btn)
        layout.addLayout(dest_box)

        # Keywords (for keyword rules)
        if self.rule_type == "keyword":
            layout.addWidget(QLabel("KEYWORDS / MATCH TOKENS (Comma-separated)"))
            self._keywords_input = QLineEdit()
            self._keywords_input.setPlaceholderText("e.g. me3210, me 3210, solidcast, exam")
            matches = self.original_rule.get("match", [])
            self._keywords_input.setText(", ".join(matches) if isinstance(matches, list) else str(matches))
            layout.addWidget(self._keywords_input)

        # Allowed Extensions
        layout.addWidget(QLabel("EXTENSIONS (Comma-separated, e.g. .pdf, .docx, .stl)"))
        self._exts_input = QLineEdit()
        self._exts_input.setPlaceholderText("e.g. .pdf, .docx, .xlsx or .stl, .3mf")
        exts = self.original_rule.get("extensions", [])
        self._exts_input.setText(", ".join(exts) if isinstance(exts, list) else str(exts))
        layout.addWidget(self._exts_input)

        # Active Toggle Checkbox
        self._active_chk = QCheckBox("Rule is active and evaluated during sorting")
        self._active_chk.setChecked(self.original_rule.get("enabled", True))
        layout.addWidget(self._active_chk)

        layout.addSpacing(8)

        # Action Buttons
        btn_box = QHBoxLayout()
        btn_box.setSpacing(10)
        btn_box.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_box.addWidget(cancel_btn)

        save_btn = QPushButton("Save Rule")
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ACCENTS['downloadSorter']};
                color: #0B0D12;
                border: 1px solid {ACCENTS['downloadSorter']};
                font-weight: bold;
                padding: 6px 16px;
            }}
            QPushButton:hover {{
                background: #82ecc0;
            }}
        """)
        save_btn.clicked.connect(self._validate_and_accept)
        btn_box.addWidget(save_btn)

        layout.addLayout(btn_box)

    def _browse_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Destination Directory")
        if folder:
            self._folder_input.setText(folder)

    def _validate_and_accept(self) -> None:
        name = self._name_input.text().strip()
        folder = self._folder_input.text().strip()
        if not name:
            QMessageBox.warning(self, "Missing Name", "Please enter a rule name.")
            self._name_input.setFocus()
            return
        if not folder:
            QMessageBox.warning(self, "Missing Folder", "Please enter a destination folder.")
            self._folder_input.setFocus()
            return
        self.accept()

    def get_rule_data(self) -> dict[str, Any]:
        """Parse inputs into standard rule dictionary."""
        raw_exts = [e.strip().lower() for e in self._exts_input.text().split(",") if e.strip()]
        clean_exts = [e if e.startswith(".") else f".{e}" for e in raw_exts]

        rule: dict[str, Any] = {
            "name": self._name_input.text().strip(),
            "folder": self._folder_input.text().strip(),
            "extensions": clean_exts,
            "enabled": self._active_chk.isChecked(),
        }
        if self.rule_type == "keyword":
            raw_kws = [k.strip().lower() for k in self._keywords_input.text().split(",") if k.strip()]
            rule["match"] = raw_kws
        return rule


# =====================================================================
# 2. RULE TABLE WIDGET (Precedence, Reorder, Toggle)
# =====================================================================

class RuleTableWidget(QWidget):
    """Precedence-ordered table widget for keyword or extension rules."""

    rules_changed = Signal()

    def __init__(self, rule_type: str = "keyword", parent: QWidget | None = None):
        super().__init__(parent)
        self.rule_type = rule_type
        self._rules: list[dict[str, Any]] = []
        self._block_signals = False

        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # Toolbar
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)

        self._add_btn = QPushButton("➕ Add Rule")
        self._add_btn.setToolTip("Create a new sorting rule")
        self._add_btn.clicked.connect(self.add_rule_dialog)
        toolbar.addWidget(self._add_btn)

        if self.rule_type == "keyword":
            self._preset_btn = QPushButton("📚 Add from Presets...")
            self._preset_btn.setToolTip("Add pre-configured keyword rules from the Preset Library")
            self._preset_btn.clicked.connect(self.add_from_presets_dialog)
            toolbar.addWidget(self._preset_btn)

        self._move_up_btn = QPushButton("▲ Move Up")
        self._move_up_btn.setToolTip("Increase rule precedence (evaluated earlier)")
        self._move_up_btn.clicked.connect(self.move_rule_up)
        toolbar.addWidget(self._move_up_btn)

        self._move_down_btn = QPushButton("▼ Move Down")
        self._move_down_btn.setToolTip("Decrease rule precedence (evaluated later)")
        self._move_down_btn.clicked.connect(self.move_rule_down)
        toolbar.addWidget(self._move_down_btn)

        self._edit_btn = QPushButton("✏️ Edit")
        self._edit_btn.setToolTip("Edit selected rule properties")
        self._edit_btn.clicked.connect(self.edit_selected_rule)
        toolbar.addWidget(self._edit_btn)

        self._delete_btn = QPushButton("🗑️ Delete")
        self._delete_btn.setToolTip("Delete selected rule")
        self._delete_btn.clicked.connect(self.delete_selected_rule)
        toolbar.addWidget(self._delete_btn)

        toolbar.addStretch()

        hint_lbl = QLabel("Rules are evaluated top-to-bottom in order of precedence")
        hint_lbl.setStyleSheet(f"color: {COLORS['textMuted']}; font-size: 10px; font-style: italic;")
        toolbar.addWidget(hint_lbl)

        layout.addLayout(toolbar)

        # Table
        self._table = QTableWidget()
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.verticalHeader().setVisible(False)
        self._table.setShowGrid(False)
        self._table.setAlternatingRowColors(True)

        if self.rule_type == "keyword":
            headers = ["#", "Rule Name", "Destination Folder", "Keywords", "Extensions", "Active"]
        else:
            headers = ["#", "Rule Name", "Destination Folder", "Extensions", "Active"]

        self._table.setColumnCount(len(headers))
        self._table.setHorizontalHeaderLabels(headers)

        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(0, 40)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self._table.setColumnWidth(1, 170)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self._table.setColumnWidth(2, 180)
        if self.rule_type == "keyword":
            header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
            self._table.setColumnWidth(4, 130)
            header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
            self._table.setColumnWidth(5, 60)
        else:
            header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
            self._table.setColumnWidth(4, 60)

        self._table.setStyleSheet(f"""
            QTableWidget {{
                background-color: {COLORS['canvas']};
                alternate-background-color: {COLORS['surface']};
                border: 1px solid {COLORS['border']};
                border-radius: {RADII['small']}px;
                color: {COLORS['text']};
                gridline-color: transparent;
                selection-background-color: {COLORS['surfaceHover']};
                selection-color: {COLORS['text']};
            }}
            QHeaderView::section {{
                background-color: {COLORS['surfaceRaised']};
                color: {COLORS['textMuted']};
                padding: 6px;
                border: none;
                border-bottom: 1px solid {COLORS['border']};
                font-family: '{TYPE['ui']}';
                font-size: 11px;
                font-weight: 600;
            }}
        """)

        self._table.itemChanged.connect(self._on_table_item_changed)
        self._table.cellDoubleClicked.connect(lambda row, col: self.edit_selected_rule())
        layout.addWidget(self._table)

    def load_rules(self, rules: list[dict[str, Any]]) -> None:
        """Populate table from list of rule dictionaries."""
        self._rules = copy.deepcopy(rules)
        self._refresh_table()

    def get_rules(self) -> list[dict[str, Any]]:
        """Return current in-memory rules list reflecting all edits and ordering."""
        return copy.deepcopy(self._rules)

    def _refresh_table(self) -> None:
        self._block_signals = True
        self._table.setRowCount(len(self._rules))

        for row_idx, rule in enumerate(self._rules):
            is_enabled = rule.get("enabled", True)
            fg = COLORS["text"] if is_enabled else COLORS["textFaint"]

            # Col 0: Precedence index
            prec_item = QTableWidgetItem(f"#{row_idx + 1}")
            prec_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            prec_item.setForeground(QColor(COLORS["textMuted"]))
            prec_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self._table.setItem(row_idx, 0, prec_item)

            # Col 1: Rule Name
            name_item = QTableWidgetItem(rule.get("name", "Unnamed Rule"))
            name_item.setForeground(QColor(fg))
            name_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self._table.setItem(row_idx, 1, name_item)

            # Col 2: Destination Folder
            folder_item = QTableWidgetItem(rule.get("folder", ""))
            folder_item.setForeground(QColor(fg))
            folder_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self._table.setItem(row_idx, 2, folder_item)

            if self.rule_type == "keyword":
                # Col 3: Keywords
                matches = rule.get("match", [])
                match_str = ", ".join(matches) if isinstance(matches, list) else str(matches)
                kw_item = QTableWidgetItem(match_str)
                kw_item.setForeground(QColor(fg))
                kw_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self._table.setItem(row_idx, 3, kw_item)

                # Col 4: Extensions
                exts = rule.get("extensions", [])
                ext_str = ", ".join(exts) if isinstance(exts, list) else str(exts)
                ext_item = QTableWidgetItem(ext_str)
                ext_item.setForeground(QColor(fg))
                ext_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self._table.setItem(row_idx, 4, ext_item)

                # Col 5: Active Checkbox
                active_item = QTableWidgetItem()
                active_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable)
                active_item.setCheckState(Qt.CheckState.Checked if is_enabled else Qt.CheckState.Unchecked)
                self._table.setItem(row_idx, 5, active_item)
            else:
                # Col 3: Extensions
                exts = rule.get("extensions", [])
                ext_str = ", ".join(exts) if isinstance(exts, list) else str(exts)
                ext_item = QTableWidgetItem(ext_str)
                ext_item.setForeground(QColor(fg))
                ext_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self._table.setItem(row_idx, 3, ext_item)

                # Col 4: Active Checkbox
                active_item = QTableWidgetItem()
                active_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable)
                active_item.setCheckState(Qt.CheckState.Checked if is_enabled else Qt.CheckState.Unchecked)
                self._table.setItem(row_idx, 4, active_item)

        self._block_signals = False

    def _on_table_item_changed(self, item: QTableWidgetItem) -> None:
        if self._block_signals:
            return
        active_col = 5 if self.rule_type == "keyword" else 4
        if item.column() == active_col:
            row = item.row()
            if 0 <= row < len(self._rules):
                checked = (item.checkState() == Qt.CheckState.Checked)
                self._rules[row]["enabled"] = checked
                self._refresh_table()
                self.rules_changed.emit()

    def add_rule_dialog(self) -> None:
        """Open Add Rule dialog and append result."""
        dlg = RuleEditDialog(rule_type=self.rule_type, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_rule = dlg.get_rule_data()
            self._rules.append(new_rule)
            self._refresh_table()
            self._table.setCurrentCell(len(self._rules) - 1, 1)
            self.rules_changed.emit()

    def add_from_presets_dialog(self) -> None:
        """Open Preset Library dialog and append selected preset rules."""
        dlg = PresetLibraryDialog(parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            selected_ids = dlg.get_selected_preset_ids()
            presets = suite_data.get_available_presets() if hasattr(suite_data, "get_available_presets") else []
            existing_names = {r.get("name", "").lower() for r in self._rules}
            added_count = 0
            for pid in selected_ids:
                p = next((x for x in presets if x["id"] == pid), None)
                if p:
                    for r in p.get("rules", []):
                        if r.get("name", "").lower() not in existing_names:
                            self._rules.append(copy.deepcopy(r))
                            existing_names.add(r.get("name", "").lower())
                            added_count += 1
            if added_count > 0:
                self._refresh_table()
                self._table.setCurrentCell(len(self._rules) - 1, 1)
                self.rules_changed.emit()

    def edit_selected_rule(self) -> None:
        """Open Edit Rule dialog for the currently selected row."""
        row = self._table.currentRow()
        if row < 0 or row >= len(self._rules):
            return
        rule = self._rules[row]
        dlg = RuleEditDialog(rule=rule, rule_type=self.rule_type, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._rules[row] = dlg.get_rule_data()
            self._refresh_table()
            self._table.setCurrentCell(row, 1)
            self.rules_changed.emit()

    def delete_selected_rule(self) -> None:
        """Delete currently selected rule."""
        row = self._table.currentRow()
        if row < 0 or row >= len(self._rules):
            return
        del self._rules[row]
        self._refresh_table()
        if self._rules:
            new_row = min(row, len(self._rules) - 1)
            self._table.setCurrentCell(new_row, 1)
        self.rules_changed.emit()

    def move_rule_up(self) -> None:
        """Move selected rule up by 1 position (swap precedence)."""
        row = self._table.currentRow()
        if row <= 0 or row >= len(self._rules):
            return
        self._rules[row], self._rules[row - 1] = self._rules[row - 1], self._rules[row]
        self._refresh_table()
        self._table.setCurrentCell(row - 1, 1)
        self.rules_changed.emit()

    def move_rule_down(self) -> None:
        """Move selected rule down by 1 position (swap precedence)."""
        row = self._table.currentRow()
        if row < 0 or row >= len(self._rules) - 1:
            return
        self._rules[row], self._rules[row + 1] = self._rules[row + 1], self._rules[row]
        self._refresh_table()
        self._table.setCurrentCell(row + 1, 1)
        self.rules_changed.emit()

# =====================================================================
# 3. ACTIVE PROJECT CARD
# =====================================================================

class ActiveProjectCard(QFrame):
    """Card managing temporary Active Project catch-all mode and expiry."""

    state_changed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._maker_projects: list[dict[str, Any]] = []
        self._expires_at: float | None = None
        self._duration_hours: float | None = None

        self._init_ui()

        # Timer for countdown refresh (only active when catch-all has expiry)
        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(15000)  # 15 seconds
        self._countdown_timer.timeout.connect(self._update_status_display)

    def _init_ui(self) -> None:
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(f"""
            ActiveProjectCard {{
                background-color: {COLORS['surface']};
                border: 1px solid {COLORS['border']};
                border-radius: {RADII['card']}px;
            }}
            QLabel {{
                color: {COLORS['text']};
                font-family: '{TYPE['ui']}';
                font-size: 11px;
            }}
            QLineEdit, QComboBox {{
                background: {COLORS['canvas']};
                border: 1px solid {COLORS['border']};
                border-radius: {RADII['small']}px;
                color: {COLORS['text']};
                padding: 5px 9px;
                font-family: '{TYPE['ui']}';
                font-size: 11px;
            }}
            QLineEdit:focus, QComboBox:focus {{
                border-color: {ACCENTS['broncoGold']};
            }}
            QPushButton {{
                background: {COLORS['surfaceRaised']};
                border: 1px solid {COLORS['border']};
                border-radius: {RADII['small']}px;
                color: {COLORS['text']};
                padding: 5px 12px;
                font-size: 11px;
            }}
            QPushButton:hover {{
                background: {COLORS['surfaceHover']};
                border-color: {ACCENTS['broncoGold']};
            }}
            QCheckBox {{
                color: {COLORS['text']};
                font-family: '{TYPE['ui']}';
                font-size: 12px;
                font-weight: bold;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        # Header: Mode Toggle + Status Pill
        header = QHBoxLayout()
        header.setSpacing(8)

        self._enable_chk = QCheckBox("ACTIVE PROJECT CATCH-ALL MODE")
        self._enable_chk.setStyleSheet(f"color: {ACCENTS['broncoGold']}; font-weight: bold;")
        self._enable_chk.toggled.connect(self._on_enable_toggled)
        header.addWidget(self._enable_chk)

        header.addStretch()

        self._status_badge = QLabel("○ INACTIVE")
        self._status_badge.setFont(QFont(TYPE["ui"], 8, QFont.Weight.Bold))
        self._status_badge.setStyleSheet(f"""
            background: {COLORS['surfaceRaised']};
            color: {COLORS['textMuted']};
            border: 1px solid {COLORS['border']};
            border-radius: 9px;
            padding: 2px 8px;
        """)
        header.addWidget(self._status_badge)
        layout.addLayout(header)

        # Project and Folder Selector Row
        row1 = QHBoxLayout()
        row1.setSpacing(10)

        proj_box = QVBoxLayout()
        proj_box.setSpacing(3)
        proj_box.addWidget(QLabel("SELECT MAKER PROJECT"))
        self._proj_combo = QComboBox()
        self._proj_combo.currentIndexChanged.connect(self._on_project_selected)
        proj_box.addWidget(self._proj_combo)
        row1.addLayout(proj_box, 3)

        dir_box = QVBoxLayout()
        dir_box.setSpacing(3)
        dir_box.addWidget(QLabel("TARGET DIRECTORY (Incoming CAD / 3D / CNC files)"))
        dir_inner = QHBoxLayout()
        dir_inner.setSpacing(6)
        self._dir_edit = QLineEdit()
        self._dir_edit.setPlaceholderText("Select project or choose directory...")
        self._dir_edit.textChanged.connect(lambda: self.state_changed.emit())
        dir_inner.addWidget(self._dir_edit)

        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse_custom_dir)
        dir_inner.addWidget(browse_btn)
        dir_box.addLayout(dir_inner)
        row1.addLayout(dir_box, 4)

        # Expiry Duration Selector
        exp_box = QVBoxLayout()
        exp_box.setSpacing(3)
        exp_box.addWidget(QLabel("AUTO-EXPIRY DURATION"))
        self._expiry_combo = QComboBox()
        self._expiry_combo.addItem("Manual Toggle (No Expiry)", None)
        self._expiry_combo.addItem("1 Hour", 1.0)
        self._expiry_combo.addItem("4 Hours", 4.0)
        self._expiry_combo.addItem("8 Hours", 8.0)
        self._expiry_combo.addItem("24 Hours", 24.0)
        self._expiry_combo.currentIndexChanged.connect(self._on_expiry_selected)
        exp_box.addWidget(self._expiry_combo)
        row1.addLayout(exp_box, 2)

        layout.addLayout(row1)

        # Live Status Summary line
        self._status_summary = QLabel("Catch-all disabled. Normal keyword & extension rules apply.")
        self._status_summary.setStyleSheet(f"color: {COLORS['textMuted']}; font-size: 10px;")
        layout.addWidget(self._status_summary)

        # Populate projects
        self.reload_projects()

    def reload_projects(self) -> None:
        """Load projects from Print Library SQLite database."""
        self._maker_projects = suite_data.get_maker_projects()
        self._proj_combo.blockSignals(True)
        self._proj_combo.clear()
        self._proj_combo.addItem("-- Select Active Project --", None)
        for proj in self._maker_projects:
            self._proj_combo.addItem(f"📁 {proj['name']}", proj)
        self._proj_combo.addItem("📂 [Custom Directory...]", "custom")
        self._proj_combo.blockSignals(False)

    def _on_enable_toggled(self, enabled: bool) -> None:
        self._dir_edit.setEnabled(enabled)
        self._proj_combo.setEnabled(enabled)
        self._expiry_combo.setEnabled(enabled)
        if enabled and self._expires_at is None:
            dur = self._expiry_combo.currentData()
            if dur:
                self._expires_at = time.time() + (dur * 3600.0)
                self._duration_hours = dur
        elif not enabled:
            self._expires_at = None
        self._update_status_display()
        self.state_changed.emit()

    def _on_project_selected(self, index: int) -> None:
        data = self._proj_combo.currentData()
        if isinstance(data, dict):
            target = data.get("target_directory") or data.get("folder") or ""
            self._dir_edit.setText(target)
            self._update_status_display()
            self.state_changed.emit()
        elif data == "custom":
            self._browse_custom_dir()

    def _browse_custom_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Project Target Directory")
        if folder:
            self._dir_edit.setText(folder)
            self._update_status_display()
            self.state_changed.emit()

    def _on_expiry_selected(self, index: int) -> None:
        dur = self._expiry_combo.currentData()
        if dur:
            self._duration_hours = float(dur)
            self._expires_at = time.time() + (self._duration_hours * 3600.0)
        else:
            self._duration_hours = None
            self._expires_at = None
        self._update_status_display()
        self.state_changed.emit()

    def _update_status_display(self) -> None:
        enabled = self._enable_chk.isChecked()
        now = time.time()

        if not enabled:
            if hasattr(self, "_countdown_timer") and self._countdown_timer.isActive():
                self._countdown_timer.stop()
            self._status_badge.setText("○ INACTIVE")
            self._status_badge.setStyleSheet(f"""
                background: {COLORS['surfaceRaised']};
                color: {COLORS['textMuted']};
                border: 1px solid {COLORS['border']};
                border-radius: 9px;
                padding: 2px 8px;
            """)
            self._status_summary.setText("Catch-all disabled. Normal keyword & extension rules apply.")
            return

        # Check expiry
        if self._expires_at is not None and self._expires_at > 0 and self.isVisible():
            if hasattr(self, "_countdown_timer") and not self._countdown_timer.isActive():
                self._countdown_timer.start()
            rem = self._expires_at - now
            if rem <= 0:
                if hasattr(self, "_countdown_timer") and self._countdown_timer.isActive():
                    self._countdown_timer.stop()
                self._enable_chk.blockSignals(True)
                self._enable_chk.setChecked(False)
                self._enable_chk.blockSignals(False)
                self._status_badge.setText("⏱ EXPIRED")
                self._status_badge.setStyleSheet(f"""
                    background: {COLORS['surfaceRaised']};
                    color: {COLORS['warning']};
                    border: 1px solid {COLORS['warning']};
                    border-radius: 9px;
                    padding: 2px 8px;
                """)
                self._status_summary.setText("Active project period has expired. Standard rules restored.")
                return
            else:
                hrs = int(rem // 3600)
                mins = int((rem % 3600) // 60)
                time_str = f"expires in {hrs}h {mins}m" if hrs > 0 else f"expires in {mins}m"
        else:
            if hasattr(self, "_countdown_timer") and self._countdown_timer.isActive():
                self._countdown_timer.stop()
            time_str = "no expiry (manual toggle)"

        self._status_badge.setText("● ACTIVE")
        self._status_badge.setStyleSheet(f"""
            background: {ACCENTS['broncoGold']};
            color: #0B0D12;
            border: 1px solid {ACCENTS['broncoGold']};
            border-radius: 9px;
            padding: 2px 8px;
            font-weight: bold;
        """)
        target = self._dir_edit.text().strip() or "No target selected"
        self._status_summary.setText(f"Routing 27 CAD/3D/CNC extensions to: {target} ({time_str})")

    def get_state(self) -> dict[str, Any]:
        """Return active project dictionary matching PROJECT.md § 2 contract."""
        data = self._proj_combo.currentData()
        proj_id = data.get("id", "custom") if isinstance(data, dict) else "custom"
        proj_name = data.get("name", Path(self._dir_edit.text()).name or "Active Project") if isinstance(data, dict) else (Path(self._dir_edit.text()).name or "Active Project")

        return {
            "enabled": self._enable_chk.isChecked(),
            "project_id": str(proj_id),
            "project_name": proj_name,
            "target_directory": self._dir_edit.text().strip(),
            "expires_at": self._expires_at,
            "duration_hours": self._duration_hours,
            "cad_extensions": list(suite_data.CAD_EXTENSIONS),
        }

    def set_state(self, state: dict[str, Any] | None) -> None:
        """Apply active project state dictionary."""
        if not state:
            self._enable_chk.setChecked(False)
            return

        is_enabled = state.get("enabled", state.get("active", False))
        target_dir = state.get("target_directory", "")
        self._dir_edit.setText(target_dir)
        self._expires_at = state.get("expires_at")
        self._duration_hours = state.get("duration_hours")

        # Match combo if known project
        proj_id = str(state.get("project_id", ""))
        matched = False
        for idx in range(self._proj_combo.count()):
            d = self._proj_combo.itemData(idx)
            if isinstance(d, dict) and str(d.get("id")) == proj_id:
                self._proj_combo.setCurrentIndex(idx)
                matched = True
                break
        if not matched and target_dir:
            for idx in range(self._proj_combo.count()):
                d = self._proj_combo.itemData(idx)
                if isinstance(d, dict) and (d.get("target_directory") == target_dir or d.get("folder") == target_dir):
                    self._proj_combo.setCurrentIndex(idx)
                    matched = True
                    break

        self._enable_chk.setChecked(bool(is_enabled))
        self._update_status_display()

    def hideEvent(self, event) -> None:
        if hasattr(self, "_countdown_timer") and self._countdown_timer.isActive():
            self._countdown_timer.stop()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:
        if hasattr(self, "_countdown_timer") and self._countdown_timer.isActive():
            self._countdown_timer.stop()
        super().closeEvent(event)


# =====================================================================
# 4. LIVE SANDBOX SIMULATOR WIDGET
# =====================================================================

class LiveSandboxWidget(QFrame):
    """Interactive real-time routing simulator evaluating filename inputs."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._config: dict[str, Any] = {}
        self._active_project: dict[str, Any] | None = None
        self._trace_expanded = False

        self._init_ui()

    def _init_ui(self) -> None:
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(f"""
            LiveSandboxWidget {{
                background-color: {COLORS['surface']};
                border: 1px solid {COLORS['border']};
                border-radius: {RADII['card']}px;
            }}
            QLabel {{
                color: {COLORS['text']};
                font-family: '{TYPE['ui']}';
            }}
            QLineEdit {{
                background: {COLORS['canvas']};
                border: 1px solid {COLORS['border']};
                border-radius: {RADII['small']}px;
                color: {COLORS['text']};
                padding: 7px 11px;
                font-family: '{TYPE['mono']}';
                font-size: 12px;
            }}
            QLineEdit:focus {{
                border-color: {ACCENTS['downloadSorter']};
            }}
            QPushButton.chip {{
                background: {COLORS['surfaceRaised']};
                border: 1px solid {COLORS['border']};
                border-radius: 12px;
                color: {COLORS['textMuted']};
                padding: 3px 10px;
                font-family: '{TYPE['mono']}';
                font-size: 10px;
            }}
            QPushButton.chip:hover {{
                background: {COLORS['surfaceHover']};
                color: {ACCENTS['downloadSorter']};
                border-color: {ACCENTS['downloadSorter']};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        # Title Row
        title_box = QHBoxLayout()
        title_box.setSpacing(6)
        title_lbl = QLabel("🧪 LIVE SORTER SANDBOX SIMULATOR")
        title_lbl.setFont(QFont(TYPE["ui"], 11, QFont.Weight.Bold))
        title_lbl.setStyleSheet(f"color: {ACCENTS['downloadSorter']};")
        title_box.addWidget(title_lbl)

        title_box.addStretch()
        parity_badge = QLabel("EXACT ENGINE PARITY")
        parity_badge.setFont(QFont(TYPE["ui"], 8, QFont.Weight.Medium))
        parity_badge.setStyleSheet(f"color: {COLORS['textMuted']}; background: {COLORS['surfaceRaised']}; border-radius: 4px; padding: 2px 6px;")
        title_box.addWidget(parity_badge)
        layout.addLayout(title_box)

        # Input Row
        self._input = QLineEdit()
        self._input.setClearButtonEnabled(True)
        self._input.setPlaceholderText("Enter hypothetical filename (e.g. ME3210_Exam_Review.pdf, bracket_op1.nc, vase.3mf)...")
        self._input.textChanged.connect(lambda: self.evaluate())
        layout.addWidget(self._input)

        # Quick Preset Chips
        self._current_persona = "maker_3d"
        self._chips_box = QHBoxLayout()
        self._chips_box.setSpacing(6)
        layout.addLayout(self._chips_box)
        self._rebuild_chips()

        # Result Display Card
        self._result_frame = QFrame()
        self._result_frame.setStyleSheet(f"""
            QFrame {{
                background: {COLORS['canvas']};
                border: 1px solid {COLORS['borderStrong']};
                border-radius: {RADII['small']}px;
                padding: 6px;
            }}
        """)
        res_layout = QVBoxLayout(self._result_frame)
        res_layout.setContentsMargins(10, 8, 10, 8)
        res_layout.setSpacing(6)

        # Row 1: Match Badge + Winning Rule
        r1 = QHBoxLayout()
        r1.setSpacing(8)

        self._badge_lbl = QLabel("[WAITING FOR INPUT]")
        self._badge_lbl.setFont(QFont(TYPE["ui"], 9, QFont.Weight.Bold))
        self._badge_lbl.setStyleSheet(f"background: {COLORS['surfaceRaised']}; color: {COLORS['textMuted']}; padding: 3px 8px; border-radius: 4px;")
        r1.addWidget(self._badge_lbl)

        self._rule_lbl = QLabel("Type a filename or click a preset above to simulate routing.")
        self._rule_lbl.setFont(QFont(TYPE["ui"], 11, QFont.Weight.DemiBold))
        r1.addWidget(self._rule_lbl, 1)

        res_layout.addLayout(r1)

        # Row 2: Destination path
        r2 = QHBoxLayout()
        r2.setSpacing(6)
        r2.addWidget(QLabel("DESTINATION:"))
        self._dest_lbl = QLabel("—")
        self._dest_lbl.setFont(QFont(TYPE["mono"], 11, QFont.Weight.Medium))
        self._dest_lbl.setStyleSheet(f"color: {ACCENTS['downloadSorter']};")
        r2.addWidget(self._dest_lbl, 1)
        res_layout.addLayout(r2)

        # Row 3: Matched Trigger token
        self._trigger_lbl = QLabel("")
        self._trigger_lbl.setFont(QFont(TYPE["ui"], 10))
        self._trigger_lbl.setStyleSheet(f"color: {COLORS['textMuted']}; font-style: italic;")
        res_layout.addWidget(self._trigger_lbl)

        layout.addWidget(self._result_frame)

        # Collapsible Rule Chain Trace
        trace_header = QHBoxLayout()
        self._trace_btn = QPushButton("▶ Rule Chain Trace")
        self._trace_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                border: none;
                color: {COLORS['textMuted']};
                font-size: 11px;
                text-align: left;
                padding: 2px 0;
            }}
            QPushButton:hover {{
                color: {COLORS['text']};
            }}
        """)
        self._trace_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._trace_btn.clicked.connect(self._toggle_trace)
        trace_header.addWidget(self._trace_btn)
        trace_header.addStretch()
        layout.addLayout(trace_header)

        self._trace_list = QListWidget()
        self._trace_list.setVisible(False)
        self._trace_list.setMaximumHeight(130)
        self._trace_list.setStyleSheet(f"""
            QListWidget {{
                background: {COLORS['canvas']};
                border: 1px solid {COLORS['border']};
                border-radius: {RADII['small']}px;
                color: {COLORS['textMuted']};
                font-family: '{TYPE['mono']}';
                font-size: 10px;
                padding: 4px;
            }}
        """)
        layout.addWidget(self._trace_list)

    def set_persona(self, persona: str) -> None:
        """Update preset chips to reflect the active user profile."""
        self._current_persona = persona
        self._rebuild_chips()

    def _rebuild_chips(self) -> None:
        if not hasattr(self, "_chips_box"):
            return
        while self._chips_box.count() > 0:
            item = self._chips_box.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        lbl = QLabel("TEST PRESETS:")
        lbl.setFont(QFont(TYPE["ui"], 9, QFont.Weight.Bold))
        lbl.setStyleSheet(f"color: {COLORS['textMuted']};")
        self._chips_box.addWidget(lbl)

        persona = getattr(self, "_current_persona", "maker_3d")
        if persona == "maker_3d":
            presets = [
                ("Haas CNC .nc", "bracket_op1.nc"),
                ("Bambu Slicer .gcode", "vase_multicolor.gcode"),
                ("3MF Model", "turbine_bracket.3mf"),
                ("ME3210 Exam", "ME3210_Exam_Review.pdf"),
                ("ZIP Archive", "hardware_pack.zip"),
            ]
            self._input.setPlaceholderText("Enter hypothetical filename (e.g. ME3210_Exam_Review.pdf, bracket_op1.nc, vase.3mf)...")
        else:
            presets = [
                ("Invoice_2026.pdf", "Invoice_2026.pdf"),
                ("Family_Photo.jpg", "Family_Photo.jpg"),
                ("Syllabus_Fall.pdf", "Syllabus_Fall.pdf"),
                ("Zoom_Installer.exe", "Zoom_Installer.exe"),
                ("Vacation_Photos.zip", "Vacation_Photos.zip"),
            ]
            self._input.setPlaceholderText("Enter hypothetical filename (e.g. Invoice_2026.pdf, Family_Photo.jpg, Syllabus_Fall.pdf)...")

        for label, fname in presets:
            btn = QPushButton(label)
            btn.setProperty("class", "chip")
            btn.setStyleSheet(f"""
                background: {COLORS['surfaceRaised']};
                border: 1px solid {COLORS['border']};
                border-radius: 11px;
                color: {COLORS['textMuted']};
                padding: 3px 9px;
                font-family: '{TYPE['mono']}';
                font-size: 10px;
            """)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _c=False, f=fname: self._set_input(f))
            self._chips_box.addWidget(btn)

        self._chips_box.addStretch()

    def _set_input(self, fname: str) -> None:
        self._input.setText(fname)

    def _toggle_trace(self) -> None:
        self._trace_expanded = not self._trace_expanded
        self._trace_list.setVisible(self._trace_expanded)
        self._trace_btn.setText("▼ Rule Chain Trace" if self._trace_expanded else "▶ Rule Chain Trace")

    def set_config_and_project(self, config: dict[str, Any], active_project: dict[str, Any] | None) -> None:
        """Update draft rules and active project model and re-evaluate immediately."""
        self._config = copy.deepcopy(config)
        self._active_project = copy.deepcopy(active_project) if active_project else None
        self.evaluate()

    def evaluate(self, filename: str | None = None) -> dict[str, Any] | None:
        """Simulate routing chain for hypothetical filename."""
        name = filename if filename is not None else self._input.text().strip()
        if not name:
            self._badge_lbl.setText("[WAITING]")
            self._badge_lbl.setStyleSheet(f"background: {COLORS['surfaceRaised']}; color: {COLORS['textMuted']}; padding: 3px 8px; border-radius: 4px;")
            self._rule_lbl.setText("Type a filename or click a preset above to simulate routing.")
            self._dest_lbl.setText("—")
            self._trigger_lbl.setText("")
            self._trace_list.clear()
            return None

        # Content sample heuristics for realistic sniffer detection
        file_path = None
        content_sample = None
        name_lower = name.lower()

        if "bracket_op1" in name_lower or "haas" in name_lower or "contour" in name_lower or "bolt" in name_lower:
            if HAAS_CONTOUR_SAMPLE and HAAS_CONTOUR_SAMPLE.exists():
                file_path = HAAS_CONTOUR_SAMPLE
            else:
                content_sample = "%\nO01001 (HAAS CONTOUR)\nG00 G90 G54 X0 Y0\nT1 M06\nM30\n%"
        elif "bambu" in name_lower or "slicer" in name_lower or (name_lower.endswith(".gcode") and "haas" not in name_lower):
            content_sample = (
                "; generated by BambuStudio 01.09.00.70\n"
                "; filament_type = PLA\n"
                "M140 S60\n"
                "M104 S220\n"
                "G1 X10 Y10 E1.0\n"
            )
        elif "makerworld" in name_lower and MAKERWORLD_SAMPLE.exists():
            file_path = MAKERWORLD_SAMPLE

        if sorter and hasattr(sorter, "evaluate_routing"):
            res = sorter.evaluate_routing(
                filename=name,
                config=self._config,
                active_project=self._active_project,
                file_path=file_path,
                content_sample=content_sample,
            )
        else:
            res = {
                "destination": "Other",
                "match_type": "fallback",
                "winning_rule": "Default Fallback",
                "matched_trigger": "",
                "is_active_project": False,
                "sniffer_details": None,
                "rule_trace": [],
            }

        self._update_display(res)
        return res

    def _update_display(self, res: dict[str, Any]) -> None:
        match_type = res.get("match_type", "fallback")
        winning_rule = res.get("winning_rule", "")
        dest = res.get("destination", "")
        trigger = res.get("matched_trigger", "")

        badges = {
            "active_project": ("[ACTIVE PROJECT]", f"background: {ACCENTS['broncoGold']}; color: #0B0D12; font-weight: bold; padding: 3px 8px; border-radius: 4px;"),
            "makerworld": ("[MAKERWORLD]", f"background: {ACCENTS['printLibrary']}; color: #0B0D12; font-weight: bold; padding: 3px 8px; border-radius: 4px;"),
            "keyword": ("[KEYWORD RULE]", f"background: {COLORS['success']}; color: #0B0D12; font-weight: bold; padding: 3px 8px; border-radius: 4px;"),
            "gcode_sniffer": ("[GCODE SNIFFER]", f"background: {ACCENTS['suiteCentral']}; color: #0B0D12; font-weight: bold; padding: 3px 8px; border-radius: 4px;"),
            "extension": ("[EXTENSION RULE]", f"background: {COLORS['borderStrong']}; color: {COLORS['text']}; font-weight: bold; padding: 3px 8px; border-radius: 4px;"),
            "fallback": ("[DEFAULT FALLBACK]", f"background: {COLORS['warning']}; color: #0B0D12; font-weight: bold; padding: 3px 8px; border-radius: 4px;"),
            "ignored": ("[IGNORED]", f"background: {COLORS['critical']}; color: #FFFFFF; font-weight: bold; padding: 3px 8px; border-radius: 4px;"),
        }

        b_text, b_style = badges.get(match_type, ("[MATCH]", f"background: {COLORS['surfaceRaised']}; color: {COLORS['text']};"))
        self._badge_lbl.setText(b_text)
        self._badge_lbl.setStyleSheet(b_style)

        self._rule_lbl.setText(winning_rule or match_type.upper())
        self._dest_lbl.setText(dest or "None")

        if trigger:
            self._trigger_lbl.setText(f"Matched trigger token: '{trigger}'")
        elif match_type == "active_project":
            self._trigger_lbl.setText("Matched active project CAD/3D/CNC extension whitelist")
        elif match_type == "makerworld":
            self._trigger_lbl.setText(f"MakerWorld 3MF metadata parsed: {winning_rule}")
        elif match_type == "gcode_sniffer":
            self._trigger_lbl.setText(f"G-code machine sniffer evaluated header: {res.get('sniffer_details')}")
        else:
            self._trigger_lbl.setText("")

        # Update trace items
        self._trace_list.clear()
        for t in res.get("rule_trace", []):
            tier = t.get("tier", "")
            status = t.get("status", "")
            rule_name = t.get("rule", "")
            if status == "matched":
                txt = f"★ [{tier.upper()}] MATCHED '{rule_name or tier}' -> {t.get('destination', '')}"
            else:
                txt = f"  · [{tier}] Skipped '{rule_name or tier}' ({status})"
            self._trace_list.addItem(txt)


# =====================================================================
# 5. MAIN RULES MANAGER DIALOG
# =====================================================================

def show_about_dialog(parent: QWidget | None = None) -> None:
    """Help -> About: version, author credit, repository, and license."""
    box = QMessageBox(parent)
    box.setWindowTitle(f"About {about.APP_NAME}")
    box.setIcon(QMessageBox.Icon.Information)
    box.setText(f"<b>{about.APP_NAME}</b> {about.__version__}")
    box.setInformativeText(
        f"{about.CREDIT}\n\nLicense: {about.LICENSE_NAME}\n{about.REPO_URL}"
    )
    box.exec()


class RemodelWorker(QThread):
    scan_finished = Signal(object)
    apply_finished = Signal(object)
    error_occurred = Signal(str)

    def __init__(self, mode: str, engine: Any, plan_or_target: Any, card_overrides: Any = None):
        super().__init__()
        self.mode = mode
        self.engine = engine
        self.plan_or_target = plan_or_target
        self.card_overrides = card_overrides

    def run(self):
        try:
            if self.mode == "scan":
                scope, target = self.plan_or_target
                plan = self.engine.scan(target, scope=scope)
                self.scan_finished.emit(plan)
            elif self.mode == "apply":
                plan = self.plan_or_target
                receipt = self.engine.apply(plan, card_overrides=self.card_overrides)
                self.apply_finished.emit(receipt)
        except Exception as exc:
            self.error_occurred.emit(str(exc))


class MachineRemodelWidget(QFrame):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("machineRemodelWidget")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._current_plan: Any = None
        self._card_buttons: dict[str, QButtonGroup] = {}
        import remodel
        self._engine = remodel.RemodelEngine()
        self._worker: RemodelWorker | None = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        header_lbl = QLabel("Automated Machine Remodel & Folder Reorganization")
        header_lbl.setStyleSheet(f"font-size: 14px; font-weight: bold; color: {COLORS.get('text', '#EDF1F8')};")
        layout.addWidget(header_lbl)

        sub_lbl = QLabel(
            "Scan and restructure messy machine directories. Automatically protects CAD assemblies, SOLIDCast, "
            "MAGMASOFT, KiCad, active projects (<14d), and deduplicates identical files into quarantine."
        )
        sub_lbl.setWordWrap(True)
        sub_lbl.setStyleSheet(f"font-size: 11px; color: {COLORS.get('textMuted', '#8B94A7')};")
        layout.addWidget(sub_lbl)

        scope_layout = QHBoxLayout()
        scope_layout.addWidget(QLabel("Scope:"))
        self._scope_combo = QComboBox()
        self._scope_combo.addItems([
            "Downloads (Full deep scan)",
            "Desktop (Files >30 days, preserves shortcuts)",
            "Documents (Top-level only)",
            "Custom Directory...",
        ])
        self._scope_combo.currentIndexChanged.connect(self._on_scope_changed)
        scope_layout.addWidget(self._scope_combo, 1)

        self._custom_path_edit = QLineEdit()
        self._custom_path_edit.setPlaceholderText("Select custom folder to remodel...")
        self._custom_path_edit.setEnabled(False)
        scope_layout.addWidget(self._custom_path_edit, 2)

        self._browse_btn = QPushButton("Browse...")
        self._browse_btn.setEnabled(False)
        self._browse_btn.clicked.connect(self._on_browse)
        scope_layout.addWidget(self._browse_btn)

        self._scan_btn = QPushButton("🔍 Scan & Preview")
        self._scan_btn.setStyleSheet(f"background-color: {COLORS.get('info', '#61A5FF')}; color: #FFFFFF; font-weight: bold;")
        self._scan_btn.clicked.connect(self._start_scan)
        scope_layout.addWidget(self._scan_btn)

        layout.addLayout(scope_layout)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        self._summary_lbl = QLabel("Select a scope and click 'Scan & Preview' to inspect proposed actions.")
        self._summary_lbl.setStyleSheet(f"color: {COLORS.get('textMuted', '#8B94A7')}; font-style: italic;")
        layout.addWidget(self._summary_lbl)

        self._sub_tabs = QTabWidget()

        self._cards_scroll = QScrollArea()
        self._cards_scroll.setWidgetResizable(True)
        self._cards_container = QWidget()
        self._cards_layout = QVBoxLayout(self._cards_container)
        self._cards_layout.setContentsMargins(4, 4, 4, 4)
        self._cards_layout.setSpacing(8)
        self._cards_layout.addStretch()
        self._cards_scroll.setWidget(self._cards_container)
        self._sub_tabs.addTab(self._cards_scroll, "Folder Review Cards")

        table_qss = f"""
            QTableWidget {{
                background-color: {COLORS['canvas']};
                alternate-background-color: {COLORS['surface']};
                border: 1px solid {COLORS['border']};
                border-radius: {RADII['small']}px;
                color: {COLORS['text']};
                gridline-color: transparent;
                selection-background-color: {COLORS['surfaceHover']};
                selection-color: {COLORS['text']};
            }}
            QHeaderView::section {{
                background-color: {COLORS['surfaceRaised']};
                color: {COLORS['textMuted']};
                padding: 6px;
                border: none;
                border-bottom: 1px solid {COLORS['border']};
                font-family: '{TYPE['ui']}';
                font-size: 11px;
                font-weight: 600;
            }}
        """

        self._prot_table = QTableWidget(0, 2)
        self._prot_table.setStyleSheet(table_qss)
        self._prot_table.setHorizontalHeaderLabels(["Protected Folder / Item", "Protection Reason"])
        self._prot_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self._prot_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._sub_tabs.addTab(self._prot_table, "Protected & Skipped (0)")

        self._dupe_table = QTableWidget(0, 3)
        self._dupe_table.setStyleSheet(table_qss)
        self._dupe_table.setHorizontalHeaderLabels(["Duplicate File", "Size", "Kept Original"])
        self._dupe_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self._dupe_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._sub_tabs.addTab(self._dupe_table, "Duplicates to Quarantine (0)")

        layout.addWidget(self._sub_tabs, 1)

        action_layout = QHBoxLayout()
        self._apply_btn = QPushButton("🚀 Apply Remodel")
        self._apply_btn.setEnabled(False)
        self._apply_btn.setStyleSheet(f"background-color: {COLORS.get('success', '#69E6AD')}; color: #000000; font-weight: bold; padding: 6px 16px;")
        self._apply_btn.clicked.connect(self._start_apply)
        action_layout.addWidget(self._apply_btn)

        self._undo_btn = QPushButton("↺ Undo Remodel")
        self._undo_btn.clicked.connect(self._on_undo)
        action_layout.addWidget(self._undo_btn)

        action_layout.addStretch()
        self._receipt_btn = QPushButton("📄 Open HTML Receipt")
        self._receipt_btn.setEnabled(False)
        self._receipt_btn.clicked.connect(self._open_receipt)
        action_layout.addWidget(self._receipt_btn)

        layout.addLayout(action_layout)

    def _on_scope_changed(self, idx: int):
        is_custom = (idx == 3)
        self._custom_path_edit.setEnabled(is_custom)
        self._browse_btn.setEnabled(is_custom)

    def _on_browse(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Folder to Remodel")
        if folder:
            self._custom_path_edit.setText(folder)

    def _get_target_path_and_scope(self) -> tuple[str, Path]:
        idx = self._scope_combo.currentIndex()
        if idx == 0:
            return "downloads", sorter.watch_root(self._engine.config)
        elif idx == 1:
            desktop = Path(os.environ.get("USERPROFILE", "")) / "Desktop"
            return "desktop", desktop
        elif idx == 2:
            documents = Path(os.environ.get("USERPROFILE", "")) / "Documents"
            return "documents", documents
        else:
            p = self._custom_path_edit.text().strip()
            return "custom", Path(p) if p else sorter.watch_root(self._engine.config)

    def _start_scan(self):
        scope, target = self._get_target_path_and_scope()
        if not target.exists():
            QMessageBox.warning(self, "Invalid Directory", f"The directory {target} does not exist.")
            return

        self._scan_btn.setEnabled(False)
        self._apply_btn.setEnabled(False)
        self._progress.setVisible(True)
        self._summary_lbl.setText(f"Scanning {target} ...")

        self._worker = RemodelWorker("scan", self._engine, (scope, target))
        self._worker.scan_finished.connect(self._on_scan_finished)
        self._worker.error_occurred.connect(self._on_worker_error)
        self._worker.start()

    def _on_scan_finished(self, plan: Any):
        self._progress.setVisible(False)
        self._scan_btn.setEnabled(True)
        self._current_plan = plan

        dupe_mb = round(plan.reclaimable_duplicate_bytes / (1024 * 1024), 2)
        self._summary_lbl.setText(
            f"Scan Complete: {plan.total_files_scanned} files inspected across {len(plan.cards)} subfolders. "
            f"{len(plan.duplicates_to_quarantine)} duplicates found ({dupe_mb} MB reclaimable)."
        )
        self._apply_btn.setEnabled(len(plan.cards) > 0 or len(plan.loose_files_to_organize) > 0 or len(plan.duplicates_to_quarantine) > 0)

        while self._cards_layout.count() > 1:
            child = self._cards_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        self._card_buttons.clear()

        for card in plan.cards:
            card_frame = QFrame()
            card_frame.setFrameShape(QFrame.Shape.StyledPanel)
            card_frame.setStyleSheet(f"""
                QFrame {{
                    background-color: {COLORS['surfaceRaised']};
                    border: 1px solid {COLORS['border']};
                    border-radius: {RADII['card']}px; /* 11px */
                    padding: 8px;
                }}
                QRadioButton {{
                    color: {COLORS['text']};
                    font-family: '{TYPE['ui']}';
                    font-size: 11px;
                    spacing: 6px;
                }}
                QRadioButton::indicator {{
                    width: 14px;
                    height: 14px;
                    border: 1px solid {COLORS['borderStrong']};
                    border-radius: 7px;
                    background: {COLORS['canvas']};
                }}
                QRadioButton::indicator:hover {{
                    border-color: {ACCENTS['downloadSorter']};
                }}
                QRadioButton::indicator:checked {{
                    background: {ACCENTS['downloadSorter']};
                    border-color: {ACCENTS['downloadSorter']};
                }}
            """)
            c_layout = QVBoxLayout(card_frame)

            top_h = QHBoxLayout()
            name_lbl = QLabel(f"📁 {card.folder_path.name}")
            name_lbl.setStyleSheet("font-weight: bold; font-size: 13px;")
            top_h.addWidget(name_lbl)

            if card.protection.is_protected:
                badge = QLabel(f"PROTECTED: {card.protection.reason}")
                badge.setStyleSheet(f"background: {COLORS['successSubtle']}; color: {COLORS['success']}; border: 1px solid {COLORS['successBorder']}; font-size: 10px; font-weight: bold; padding: 2px 6px; border-radius: {RADII['small']}px;")
                top_h.addWidget(badge)
            else:
                badge = QLabel("ORGANIZE CANDIDATE")
                badge.setStyleSheet(f"background: {COLORS['infoSubtle']}; color: {COLORS['info']}; border: 1px solid {COLORS['infoBorder']}; font-size: 10px; font-weight: bold; padding: 2px 6px; border-radius: {RADII['small']}px;")
                top_h.addWidget(badge)
            top_h.addStretch()

            c_layout.addLayout(top_h)

            info_lbl = QLabel(f"Files: {card.file_count} · Size: {card.total_size_mb} MB · Path: {card.folder_path}")
            info_lbl.setStyleSheet(f"font-size: 11px; color: {COLORS.get('textMuted', '#8B94A7')};")
            c_layout.addWidget(info_lbl)

            btn_group = QButtonGroup(card_frame)
            rad_h = QHBoxLayout()
            rb_org = QRadioButton("Organize into Categories")
            rb_keep = QRadioButton("Keep Intact (Untouched)")
            rb_skip = QRadioButton("Skip")

            btn_group.addButton(rb_org, 1)
            btn_group.addButton(rb_keep, 2)
            btn_group.addButton(rb_skip, 3)

            if card.action == "organize":
                rb_org.setChecked(True)
            elif card.action == "keep_intact":
                rb_keep.setChecked(True)
            else:
                rb_skip.setChecked(True)

            self._card_buttons[str(card.folder_path)] = btn_group

            rad_h.addWidget(rb_org)
            rad_h.addWidget(rb_keep)
            rad_h.addWidget(rb_skip)
            rad_h.addStretch()
            c_layout.addLayout(rad_h)

            self._cards_layout.insertWidget(self._cards_layout.count() - 1, card_frame)

        self._prot_table.setRowCount(len(plan.protected_items))
        for row, (p, reason) in enumerate(plan.protected_items):
            self._prot_table.setItem(row, 0, QTableWidgetItem(str(p)))
            self._prot_table.setItem(row, 1, QTableWidgetItem(reason))
        self._sub_tabs.setTabText(1, f"Protected & Skipped ({len(plan.protected_items)})")

        self._dupe_table.setRowCount(len(plan.duplicates_to_quarantine))
        for row, (src, _q, keeper) in enumerate(plan.duplicates_to_quarantine):
            try:
                sz_str = f"{round(src.stat().st_size / 1024, 1)} KB"
            except OSError:
                sz_str = "0 KB"
            self._dupe_table.setItem(row, 0, QTableWidgetItem(src.name))
            self._dupe_table.setItem(row, 1, QTableWidgetItem(sz_str))
            self._dupe_table.setItem(row, 2, QTableWidgetItem(keeper))
        self._sub_tabs.setTabText(2, f"Duplicates to Quarantine ({len(plan.duplicates_to_quarantine)})")

    def _start_apply(self):
        if not self._current_plan:
            return

        confirm = QMessageBox.question(
            self,
            "Confirm Machine Remodel",
            f"Are you ready to remodel {self._current_plan.target_root}?\n\n"
            f"All moves are journaled and 100% reversible via the Undo button.\n"
            f"A 'Where did my files go.html' receipt will be placed in the folder root.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        overrides: dict[str, str] = {}
        for folder_str, grp in self._card_buttons.items():
            checked_id = grp.checkedId()
            if checked_id == 1:
                overrides[folder_str] = "organize"
            elif checked_id == 2:
                overrides[folder_str] = "keep_intact"
            elif checked_id == 3:
                overrides[folder_str] = "skip"

        self._apply_btn.setEnabled(False)
        self._progress.setVisible(True)
        self._summary_lbl.setText("Applying remodel...")

        self._worker = RemodelWorker("apply", self._engine, self._current_plan, overrides)
        self._worker.apply_finished.connect(self._on_apply_finished)
        self._worker.error_occurred.connect(self._on_worker_error)
        self._worker.start()

    def _on_apply_finished(self, receipt: Any):
        self._progress.setVisible(False)
        executed = len(receipt.get("executed_moves", []))
        skipped = len(receipt.get("skipped_items", []))
        self._summary_lbl.setText(
            f"✓ Remodel Completed: {executed} files organized, {skipped} items protected. "
            f"Receipt created at {self._current_plan.target_root / 'Where did my files go.html'}"
        )
        self._receipt_btn.setEnabled(True)
        QMessageBox.information(
            self,
            "Remodel Complete",
            f"Remodel successfully completed!\n\n"
            f"• Organized: {executed} files\n"
            f"• Protected: {skipped} items\n\n"
            f"A searchable 'Where did my files go.html' receipt has been created in your folder.",
        )

    def _on_undo(self):
        confirm = QMessageBox.question(
            self,
            "Undo Remodel",
            "Do you want to restore the most recent machine remodel and return all files to their original locations?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        res = self._engine.undo()
        if res.get("ok"):
            QMessageBox.information(self, "Undo Complete", res.get("message", "Restored files successfully."))
            self._summary_lbl.setText(f"✓ {res.get('message')}")
        elif res.get("partial"):
            QMessageBox.warning(self, "Undo Partially Complete", res.get("message", "Some files could not be restored."))
            self._summary_lbl.setText(f"⚠️ {res.get('message')}")
        else:
            QMessageBox.critical(self, "Undo Failed", res.get("message", "Failed to undo remodel."))

    def _open_receipt(self):
        if self._current_plan:
            receipt_f = self._current_plan.target_root / "Where did my files go.html"
            if receipt_f.exists() and sys.platform == "win32":
                os.startfile(str(receipt_f))

    def _on_worker_error(self, err_msg: str):
        self._progress.setVisible(False)
        self._scan_btn.setEnabled(True)
        self._apply_btn.setEnabled(True)
        QMessageBox.critical(self, "Remodel Error", f"An error occurred during remodel operation:\n\n{err_msg}")


class RulesManagerDialog(QDialog):
    """Visual Rules Manager & Live Sorter Sandbox Dialog for Suite Central."""

    def __init__(self, parent: QWidget | None = None, initial_view: str = "customize"):
        if isinstance(parent, str):
            initial_view = parent
            parent = None
        super().__init__(parent)
        self.setWindowTitle("Visual Rules Manager & Live Sorter Sandbox")
        self.setMinimumSize(880, 800)
        self.resize(920, 850)

        # Clean up any lingering top-level headless dialogs to prevent Qt window registry leaks
        app = QApplication.instance()
        if app is not None:
            for top_win in list(app.topLevelWidgets()):
                if isinstance(top_win, RulesManagerDialog) and top_win is not self:
                    try:
                        top_win.close()
                        top_win.deleteLater()
                    except Exception:
                        pass

        self._config: dict[str, Any] = {}
        self._init_ui()
        self.load_data()
        self.switch_view(initial_view)

    def _init_ui(self) -> None:
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {COLORS['canvas']};
                color: {COLORS['text']};
            }}
            QTabWidget::pane {{
                border: 1px solid {COLORS['border']};
                background: {COLORS['surface']};
                border-radius: {RADII['small']}px;
            }}
            QTabBar::tab {{
                background: {COLORS['surfaceRaised']};
                border: 1px solid {COLORS['border']};
                border-bottom: none;
                border-top-left-radius: {RADII['small']}px;
                border-top-right-radius: {RADII['small']}px;
                padding: 8px 18px;
                color: {COLORS['textMuted']};
                font-family: '{TYPE['ui']}';
                font-weight: 600;
                font-size: 11px;
            }}
            QTabBar::tab:selected {{
                background: {COLORS['surface']};
                color: {ACCENTS['downloadSorter']};
                border-bottom: 2px solid {ACCENTS['downloadSorter']};
            }}
            QPushButton {{
                background: {COLORS['surfaceRaised']};
                border: 1px solid {COLORS['border']};
                border-radius: {RADII['small']}px;
                color: {COLORS['text']};
                padding: 6px 14px;
                font-family: '{TYPE['ui']}';
                font-size: 11px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background: {COLORS['surfaceHover']};
                border-color: {ACCENTS['downloadSorter']};
            }}
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(18, 16, 18, 16)
        main_layout.setSpacing(12)

        # -------------------------------------------------------------
        # Header Bar
        # -------------------------------------------------------------
        header = QHBoxLayout()
        header.setSpacing(10)

        title_lbl = QLabel("⚡ Visual Rules Manager & Live Sorter Sandbox")
        title_lbl.setFont(QFont(TYPE["ui"], 13, QFont.Weight.Bold))
        title_lbl.setStyleSheet(f"color: {COLORS['text']}; letter-spacing: 0.5px;")
        header.addWidget(title_lbl)

        # Sorter Status Pill
        self._sorter_status_pill = QLabel("● ACTIVE" if suite_data.is_sorter_running() else "○ IDLE")
        self._sorter_status_pill.setFont(QFont(TYPE["ui"], 8, QFont.Weight.DemiBold))
        is_running = suite_data.is_sorter_running()
        self._sorter_status_pill.setStyleSheet(f"""
            background: {COLORS['surfaceRaised']};
            color: {COLORS['success'] if is_running else COLORS['textMuted']};
            border: 1px solid {COLORS['border']};
            border-radius: 9px;
            padding: 3px 8px;
        """)
        header.addWidget(self._sorter_status_pill)

        header.addStretch()

        # Preset Library Button
        self._preset_lib_btn = QPushButton("📚 Preset Library...")
        self._preset_lib_btn.setToolTip("Browse and add curated rule packs for Office, School, 3D, and Code")
        self._preset_lib_btn.clicked.connect(self.open_preset_library)
        header.addWidget(self._preset_lib_btn)

        # Import & Export Buttons
        self._export_btn = QPushButton("📤 Export...")
        self._export_btn.setToolTip("Export rules configuration to a JSON file")
        self._export_btn.clicked.connect(self.export_rules)
        header.addWidget(self._export_btn)

        self._import_btn = QPushButton("📥 Import...")
        self._import_btn.setToolTip("Import rules from an external JSON file")
        self._import_btn.clicked.connect(self.import_rules)
        header.addWidget(self._import_btn)

        self._reset_btn = QPushButton("↺ Defaults")
        self._reset_btn.setToolTip("Reset rules to factory defaults")
        self._reset_btn.clicked.connect(self.reset_to_defaults)
        header.addWidget(self._reset_btn)

        # Help -> About
        self._about_btn = QPushButton("ⓘ About")
        self._about_btn.setToolTip(f"About {about.APP_NAME} {about.__version__}")
        self._about_btn.clicked.connect(lambda: show_about_dialog(self))
        header.addWidget(self._about_btn)

        # Save Button
        self._save_btn = QPushButton("💾 Save Changes")
        self._save_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ACCENTS['downloadSorter']};
                color: #0B0D12;
                border: 1px solid {ACCENTS['downloadSorter']};
                font-weight: bold;
                padding: 6px 16px;
            }}
            QPushButton:hover {{
                background: #82ecc0;
            }}
        """)
        self._save_btn.clicked.connect(self.save_changes)
        header.addWidget(self._save_btn)

        # Close Button
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(28, 28)
        close_btn.setStyleSheet("padding: 0px;")
        close_btn.clicked.connect(self.close)
        header.addWidget(close_btn)

        main_layout.addLayout(header)

        # -------------------------------------------------------------
        # Segmented View Switcher Bar (Home vs Customize)
        # -------------------------------------------------------------
        self._view_switcher = QWidget()
        switcher_row = QHBoxLayout(self._view_switcher)
        switcher_row.setContentsMargins(0, 0, 0, 0)
        switcher_row.setSpacing(6)

        self._seg_btn_home = QPushButton("🏠 Home & Activity")
        self._seg_btn_home.setCheckable(True)

        self._seg_btn_rules = QPushButton("⚙️ Customize Rules & Remodel")
        self._seg_btn_rules.setCheckable(True)

        self._view_btn_home = self._seg_btn_home
        self._view_btn_rules = self._seg_btn_rules

        self._view_btn_group = QButtonGroup(self)
        self._view_btn_group.addButton(self._seg_btn_home, 0)
        self._view_btn_group.addButton(self._seg_btn_rules, 1)
        self._view_btn_group.idClicked.connect(lambda vid: self._on_view_switcher_clicked(vid))

        switcher_style = f"""
            QPushButton {{
                background: {COLORS['surface']};
                border: 1px solid {COLORS['border']};
                border-radius: {RADII['small']}px;
                color: {COLORS['textMuted']};
                padding: 6px 18px;
                font-weight: 600;
                font-size: 11px;
            }}
            QPushButton:hover {{
                background: {COLORS['surfaceHover']};
                border-color: {ACCENTS['downloadSorter']};
            }}
            QPushButton:checked {{
                background: {COLORS['surfaceRaised']};
                color: {ACCENTS['downloadSorter']};
                border: 1px solid {ACCENTS['downloadSorter']};
            }}
        """
        self._seg_btn_home.setStyleSheet(switcher_style)
        self._seg_btn_rules.setStyleSheet(switcher_style)

        switcher_row.addWidget(self._seg_btn_home)
        switcher_row.addWidget(self._seg_btn_rules)
        switcher_row.addStretch()
        main_layout.addWidget(self._view_switcher)

        # -------------------------------------------------------------
        # Central View Stack: Page 0 (Home), Page 1 (Customize)
        # -------------------------------------------------------------
        self._view_stack = QStackedWidget(self)

        # Page 0: Home Dashboard
        from onboarding import HomeDashboardWidget
        self._home_widget = HomeDashboardWidget(config=self._config, parent=self)
        self._home_widget.persona_changed.connect(self.set_active_persona)
        self._view_stack.addWidget(self._home_widget)

        # Page 1: Customize Rules Container
        self._customize_container = QWidget(parent=self)
        custom_layout = QVBoxLayout(self._customize_container)
        custom_layout.setContentsMargins(0, 0, 0, 0)
        custom_layout.setSpacing(10)

        # Section 1: Active Project Catch-All Mode
        self._active_project_card = ActiveProjectCard()
        self._active_project_card.state_changed.connect(self._on_rules_or_project_changed)
        custom_layout.addWidget(self._active_project_card)

        # Section 2: Rules Precedence Tabs (STRICT CONTRACT: EXACTLY 3 TABS!)
        self._tabs = QTabWidget()

        self._keyword_table = RuleTableWidget(rule_type="keyword")
        self._keyword_table.rules_changed.connect(self._on_rules_or_project_changed)
        self._tabs.addTab(self._keyword_table, "Keyword Rules (Priority Order)")

        self._extension_table = RuleTableWidget(rule_type="extension")
        self._extension_table.rules_changed.connect(self._on_rules_or_project_changed)
        self._tabs.addTab(self._extension_table, "Extension Rules (Fallback Categories)")

        self._remodel_widget = MachineRemodelWidget()
        self._tabs.addTab(self._remodel_widget, "Machine Remodel")

        custom_layout.addWidget(self._tabs, 1)

        # Section 3: Live Sorter Sandbox Simulator
        self._sandbox = LiveSandboxWidget()
        custom_layout.addWidget(self._sandbox)

        self._view_stack.addWidget(self._customize_container)
        main_layout.addWidget(self._view_stack, 1)

        # Footer credit (single-sourced from about.py)
        self._credit_lbl = QLabel(f"{about.APP_NAME} {about.__version__} · {about.CREDIT}")
        self._credit_lbl.setObjectName("creditFooter")
        self._credit_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._credit_lbl.setStyleSheet(f"color: {COLORS['textFaint']}; font-size: 10px;")
        main_layout.addWidget(self._credit_lbl)

    def switch_view(self, view_name: str) -> None:
        """Switch between 'home' (Page 0) and 'customize' (Page 1) views."""
        name = (view_name or "").lower().strip()
        if name in ("home", "dashboard", "0"):
            self._seg_btn_home.setChecked(True)
            self._view_stack.setCurrentIndex(0)
            self._on_view_changed(0)
        else:
            self._seg_btn_rules.setChecked(True)
            self._view_stack.setCurrentIndex(1)
            self._on_view_changed(1)

    def switch_to_view(self, view_name: str) -> None:
        """Alias for switch_view."""
        self.switch_view(view_name)

    def _on_view_switcher_clicked(self, view_id: int) -> None:
        self.switch_view("home" if view_id == 0 else "customize")

    def _on_view_changed(self, view_id: int) -> None:
        is_rules = (view_id == 1)
        if hasattr(self, "_preset_lib_btn"):
            self._preset_lib_btn.setVisible(is_rules)
        if hasattr(self, "_export_btn"):
            self._export_btn.setVisible(is_rules)
        if hasattr(self, "_import_btn"):
            self._import_btn.setVisible(is_rules)
        if hasattr(self, "_reset_btn"):
            self._reset_btn.setVisible(is_rules)
        if hasattr(self, "_save_btn"):
            self._save_btn.setVisible(is_rules)
        if view_id == 0 and hasattr(self, "_home_widget"):
            self._home_widget.refresh_data()

    def set_active_persona(self, persona_id: str) -> None:
        """Configure conditional maker elements and notify sandbox and home widget."""
        self._current_persona = persona_id
        is_maker = (persona_id == "maker_3d")
        if hasattr(self, "_active_project_card"):
            self._active_project_card.setVisible(is_maker)
        if hasattr(self, "_sandbox") and hasattr(self._sandbox, "set_persona"):
            self._sandbox.set_persona(persona_id)
        if hasattr(self, "_home_widget") and hasattr(self._home_widget, "set_persona"):
            self._home_widget.set_persona(persona_id)

    def set_persona(self, persona_id: str) -> None:
        """Alias for set_active_persona."""
        self.set_active_persona(persona_id)

    def load_data(self) -> None:
        """Load persistent config and active project state into UI."""
        self._config = suite_data.load_sorter_config()
        self._keyword_table.load_rules(self._config.get("keyword_rules", []))
        self._extension_table.load_rules(self._config.get("extension_rules", []))

        active_proj = suite_data.get_active_project()
        self._active_project_card.set_state(active_proj)

        # Apply active persona visibility rules
        active_persona = self._config.get("user_persona", "maker_3d")
        self.set_active_persona(active_persona)

        self._on_rules_or_project_changed()

    def get_current_draft_config(self) -> dict[str, Any]:
        """Return draft configuration dictionary reflecting current in-memory edits."""
        cfg = copy.deepcopy(self._config)
        cfg["keyword_rules"] = self._keyword_table.get_rules()
        cfg["extension_rules"] = self._extension_table.get_rules()
        return cfg

    def _on_rules_or_project_changed(self) -> None:
        """Sync simulator sandbox with current in-memory draft rules."""
        draft_config = self.get_current_draft_config()
        active_proj = self._active_project_card.get_state()
        self._sandbox.set_config_and_project(draft_config, active_proj)

    def save_changes(self) -> bool:
        """Atomically persist draft configuration and active project state."""
        try:
            draft_config = self.get_current_draft_config()
            ok = suite_data.save_sorter_config(draft_config)
            if not ok:
                QMessageBox.critical(self, "Save Failed", "Could not save configuration.")
                return False

            proj_state = self._active_project_card.get_state()
            suite_data.save_active_project(proj_state)

            self._save_btn.setText("✓ Saved!")
            if self.isVisible():
                if hasattr(self, "_save_timer") and self._save_timer is not None:
                    self._save_timer.stop()
                self._save_timer = QTimer(self)
                self._save_timer.setSingleShot(True)
                self._save_timer.timeout.connect(lambda: self._save_btn.setText("💾 Save Changes") if hasattr(self, "_save_btn") else None)
                self._save_timer.start(2000)
            return True
        except Exception as exc:
            QMessageBox.critical(self, "Save Error", f"Failed to save rules: {exc}")
            return False

    def open_preset_library(self) -> None:
        """Open Preset Library dialog and merge selected presets into draft configuration."""
        dlg = PresetLibraryDialog(parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            selected_ids = dlg.get_selected_preset_ids()
            draft_config = self.get_current_draft_config()
            added_total = 0
            for pid in selected_ids:
                draft_config, added = suite_data.apply_preset(draft_config, pid)
                added_total += added
            if added_total > 0:
                self._config = draft_config
                self._keyword_table.load_rules(draft_config.get("keyword_rules", []))
                self._on_rules_or_project_changed()
                QMessageBox.information(self, "Presets Added", f"Successfully added {added_total} rule(s) to your configuration. Click 'Save Changes' to commit.")

    def export_rules(self) -> None:
        """Export current draft rules to a user-selected JSON file."""
        path, _ = QFileDialog.getSaveFileName(self, "Export Sorter Rules", "download_sorter_rules.json", "JSON Files (*.json)")
        if path:
            draft_config = self.get_current_draft_config()
            ok = suite_data.export_rules_to_file(path, draft_config)
            if ok:
                QMessageBox.information(self, "Export Successful", f"Rules successfully exported to:\n{path}")

    def import_rules(self) -> None:
        """Import rules from a user-selected JSON file with merge or replace options."""
        path, _ = QFileDialog.getOpenFileName(self, "Import Sorter Rules", "", "JSON Files (*.json)")
        if not path:
            return
        try:
            imported = suite_data.import_rules_from_file(path)
        except Exception as exc:
            QMessageBox.critical(self, "Import Error", f"Failed to import rules: {exc}")
            return

        reply = QMessageBox.question(
            self,
            "Import Mode",
            "Do you want to MERGE imported rules with your current rules?\n\nClick 'Yes' to Merge (keeps current rules).\nClick 'No' to REPLACE all current rules.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
        )
        if reply == QMessageBox.StandardButton.Cancel:
            return

        draft_config = self.get_current_draft_config()
        if reply == QMessageBox.StandardButton.Yes:
            existing_kw = {r.get("name", "").lower() for r in draft_config.get("keyword_rules", [])}
            for r in imported.get("keyword_rules", []):
                if r.get("name", "").lower() not in existing_kw:
                    draft_config.setdefault("keyword_rules", []).append(copy.deepcopy(r))
                    existing_kw.add(r.get("name", "").lower())
            existing_ext = {r.get("name", "").lower() for r in draft_config.get("extension_rules", [])}
            for r in imported.get("extension_rules", []):
                if r.get("name", "").lower() not in existing_ext:
                    draft_config.setdefault("extension_rules", []).append(copy.deepcopy(r))
                    existing_ext.add(r.get("name", "").lower())
        else:
            if "keyword_rules" in imported:
                draft_config["keyword_rules"] = copy.deepcopy(imported["keyword_rules"])
            if "extension_rules" in imported:
                draft_config["extension_rules"] = copy.deepcopy(imported["extension_rules"])

        self._config = draft_config
        self._keyword_table.load_rules(draft_config.get("keyword_rules", []))
        self._extension_table.load_rules(draft_config.get("extension_rules", []))
        self._on_rules_or_project_changed()
        QMessageBox.information(self, "Import Complete", "Rules imported successfully into current editor session. Click 'Save Changes' to commit.")

    def reset_to_defaults(self) -> None:
        """Prompt to reset configuration to factory defaults."""
        reply = QMessageBox.question(
            self,
            "Reset to Defaults",
            "Are you sure you want to reset all rules to factory defaults?\nAny unsaved changes will be lost.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._config = suite_data.reset_sorter_config_to_defaults()
            self._keyword_table.load_rules(self._config.get("keyword_rules", []))
            self._extension_table.load_rules(self._config.get("extension_rules", []))
            self._on_rules_or_project_changed()
            QMessageBox.information(self, "Reset Complete", "Rules have been reset to factory defaults.")

    def closeEvent(self, event) -> None:
        if hasattr(self, "_save_timer") and self._save_timer is not None:
            self._save_timer.stop()
        if hasattr(self, "_active_project_card") and hasattr(self._active_project_card, "_countdown_timer"):
            self._active_project_card._countdown_timer.stop()
        if hasattr(self, "_home_widget") and hasattr(self._home_widget, "_refresh_timer"):
            self._home_widget._refresh_timer.stop()
        super().closeEvent(event)

    def hideEvent(self, event) -> None:
        if hasattr(self, "_save_timer") and self._save_timer is not None:
            self._save_timer.stop()
        if hasattr(self, "_active_project_card") and hasattr(self._active_project_card, "_countdown_timer"):
            self._active_project_card._countdown_timer.stop()
        if hasattr(self, "_home_widget") and hasattr(self._home_widget, "_refresh_timer"):
            self._home_widget._refresh_timer.stop()
        super().hideEvent(event)


def main(argv: list[str] | None = None) -> int:
    """Launch the Rules Manager dialog standalone."""
    if about.handle_version_flag():
        return 0
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication

    try:
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except Exception:
        pass

    import argparse
    parser = argparse.ArgumentParser(description="Visual Rules Manager for Download Sorter.")
    parser.add_argument("--version", action="version", version=about.version_string())
    parser.add_argument("--tab", type=str, default="", help="Active tab to display on startup.")
    parser.add_argument("--home", action="store_true", help="Launch directly into Home Dashboard view.")
    parser.add_argument("--welcome", action="store_true", help="Launch the First-Run Welcome Wizard.")
    raw_args = argv if argv is not None else sys.argv[1:]
    args, _unknown = parser.parse_known_args(raw_args)

    app = QApplication.instance()
    owns_app = False
    if app is None:
        app = QApplication(sys.argv)
        owns_app = True

    if args.welcome:
        from onboarding import WelcomeWizardDialog
        wizard = WelcomeWizardDialog()
        result = wizard.exec()
        if result == QDialog.DialogCode.Accepted:
            dialog = RulesManagerDialog(initial_view="home")
            dialog.show()
            if owns_app:
                return app.exec()
        return 0

    init_view = "home" if (args.home or not args.tab) else "customize"
    dialog = RulesManagerDialog(initial_view=init_view)
    if args.tab:
        dialog.switch_view("customize")
        if args.tab.lower() == "remodel":
            dialog._tabs.setCurrentWidget(dialog._remodel_widget)
        elif args.tab.lower() in ("keyword", "keywords"):
            dialog._tabs.setCurrentWidget(dialog._keyword_table)
        elif args.tab.lower() in ("extension", "extensions"):
            dialog._tabs.setCurrentWidget(dialog._extension_table)
    dialog.show()
    if owns_app:
        return app.exec()
    return 0


if __name__ == "__main__":
    sys.exit(main())
