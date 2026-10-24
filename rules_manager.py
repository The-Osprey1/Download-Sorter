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

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QIcon, QKeySequence, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
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
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
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


# =====================================================================
# 1. RULE EDIT MODAL DIALOG
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

        # Timer for countdown refresh
        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(15000)  # 15 seconds
        self._countdown_timer.timeout.connect(self._update_status_display)
        self._countdown_timer.start()

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
        if self._expires_at is not None and self._expires_at > 0:
            rem = self._expires_at - now
            if rem <= 0:
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
        chips_box = QHBoxLayout()
        chips_box.setSpacing(6)
        chips_box.addWidget(QLabel("TEST PRESETS:"))

        presets = [
            ("ME3210 Exam", "ME3210_Exam_Review.pdf"),
            ("Haas CNC .nc", "bracket_op1.nc"),
            ("Bambu Slicer .gcode", "vase_multicolor.gcode"),
            ("3MF Model", "turbine_bracket.3mf"),
            ("ZIP Archive", "hardware_pack.zip"),
        ]
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
            chips_box.addWidget(btn)

        chips_box.addStretch()
        layout.addLayout(chips_box)

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

class RulesManagerDialog(QDialog):
    """Visual Rules Manager & Live Sorter Sandbox Dialog for Suite Central."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Visual Rules Manager & Live Sorter Sandbox")
        self.setMinimumSize(880, 800)
        self.resize(920, 850)

        self._config: dict[str, Any] = {}
        self._init_ui()
        self.load_data()

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
        close_btn.setFixedSize(26, 26)
        close_btn.clicked.connect(self.close)
        header.addWidget(close_btn)

        main_layout.addLayout(header)

        # -------------------------------------------------------------
        # Section 1: Active Project Catch-All Mode
        # -------------------------------------------------------------
        self._active_project_card = ActiveProjectCard()
        self._active_project_card.state_changed.connect(self._on_rules_or_project_changed)
        main_layout.addWidget(self._active_project_card)

        # -------------------------------------------------------------
        # Section 2: Rules Precedence Tabs
        # -------------------------------------------------------------
        self._tabs = QTabWidget()

        self._keyword_table = RuleTableWidget(rule_type="keyword")
        self._keyword_table.rules_changed.connect(self._on_rules_or_project_changed)
        self._tabs.addTab(self._keyword_table, "Keyword Rules (Priority Order)")

        self._extension_table = RuleTableWidget(rule_type="extension")
        self._extension_table.rules_changed.connect(self._on_rules_or_project_changed)
        self._tabs.addTab(self._extension_table, "Extension Rules (Fallback Categories)")

        main_layout.addWidget(self._tabs, 1)

        # -------------------------------------------------------------
        # Section 3: Live Sorter Sandbox Simulator
        # -------------------------------------------------------------
        self._sandbox = LiveSandboxWidget()
        main_layout.addWidget(self._sandbox)

    def load_data(self) -> None:
        """Load persistent config and active project state into UI."""
        self._config = suite_data.load_sorter_config()
        self._keyword_table.load_rules(self._config.get("keyword_rules", []))
        self._extension_table.load_rules(self._config.get("extension_rules", []))

        active_proj = suite_data.get_active_project()
        self._active_project_card.set_state(active_proj)

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
            QTimer.singleShot(2000, lambda: self._save_btn.setText("💾 Save Changes"))
            return True
        except Exception as exc:
            QMessageBox.critical(self, "Save Error", f"Failed to save rules: {exc}")
            return False


def main() -> int:
    """Launch the Rules Manager dialog standalone."""
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication

    try:
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except Exception:
        pass

    app = QApplication.instance()
    owns_app = False
    if app is None:
        app = QApplication(sys.argv)
        owns_app = True
    dialog = RulesManagerDialog()
    dialog.show()
    if owns_app:
        return app.exec()
    return 0


if __name__ == "__main__":
    sys.exit(main())
