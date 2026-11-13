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
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterable

import about
if about.handle_version_flag():
    sys.exit(0)

from PySide6.QtCore import QEvent, QPoint, QSettings, QSize, QStringListModel, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QIcon, QKeySequence, QPalette, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QMenu,
    QToolButton,
    QCheckBox,
    QComboBox,
    QCompleter,
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
import ui_icons
import ui_motion
import ui_style
import ui_widgets

try:
    import sorter
except ImportError:
    sorter = None

# Sample Haas programs for the "Try a filename" box, when the repository's test
# samples are present (running from source). The installed app uses a short
# built-in sample instead.
LOCAL_SAMPLES_DIR = ROOT / "tests" / "samples"
if LOCAL_SAMPLES_DIR.exists():
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

    def __init__(self, parent: QWidget | None = None, existing_names: Iterable[str] = ()):
        super().__init__(parent)
        self.setWindowTitle("Rule library")
        self.setMinimumSize(580, 500)
        self.resize(620, 540)
        self._selected_preset_ids: list[str] = []
        self._checkboxes: dict[str, QCheckBox] = {}
        self._existing = {str(name).lower() for name in existing_names}  # names of the rules you already have
        self._init_ui()

    def _init_ui(self) -> None:
        self.setStyleSheet(ui_style.stylesheet())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        # Header
        layout.addWidget(ui_style.label("Rule library", "title"))
        layout.addWidget(ui_style.label(
            "Pick sets of ready-made rules to add. Rules you already have aren't added twice.", "muted", wrap=True))

        # Scroll Area with Presets
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 4, 6, 4)
        container_layout.setSpacing(10)

        presets = suite_data.get_available_presets() if hasattr(suite_data, "get_available_presets") else []
        for p in presets:
            card, card_layout = ui_style.card(margins=(14, 12, 14, 12), spacing=4)

            rule_names = [r.get("name", "") for r in p.get("rules", [])]
            have = [name for name in rule_names if name.lower() in self._existing]
            all_added = bool(rule_names) and len(have) == len(rule_names)

            top_row = QHBoxLayout()
            top_row.setSpacing(8)
            cb = QCheckBox(f"{p.get('name', p.get('id'))}".replace("&", "&&"))  # a single & would hide itself
            cb.setStyleSheet("font-weight: 600;")
            self._checkboxes[p["id"]] = cb
            top_row.addWidget(cb)
            if p.get("category"):
                top_row.addWidget(ui_style.badge(p["category"]))
            if all_added:
                cb.setEnabled(False)
                cb.setToolTip("Every rule in this set is already in your list.")
                top_row.addWidget(ui_style.badge("Already added", "success"))
            elif have:
                top_row.addWidget(ui_style.badge(f"{len(have)} of {len(rule_names)} already added", "info"))
            top_row.addStretch()
            card_layout.addLayout(top_row)

            desc = QLabel(p.get("description", ""))
            desc.setProperty("role", "muted")
            desc.setWordWrap(True)
            card_layout.addWidget(desc)

            rules_lbl = QLabel(f"Includes: {', '.join(rule_names)}")
            rules_lbl.setProperty("role", "caption")
            rules_lbl.setWordWrap(True)
            card_layout.addWidget(rules_lbl)

            container_layout.addWidget(card)

        container_layout.addStretch()
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)

        # Buttons
        btn_layout = QHBoxLayout()
        select_all_btn = ui_style.button("Select all", "ghost")
        select_all_btn.clicked.connect(self._select_all)
        btn_layout.addWidget(select_all_btn)

        clear_btn = ui_style.button("Clear", "ghost")
        clear_btn.clicked.connect(self._clear_all)
        btn_layout.addWidget(clear_btn)

        btn_layout.addStretch()

        cancel_btn = ui_style.button("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        self._add_btn = ui_style.button("Add selected", "primary")
        self._add_btn.clicked.connect(self._on_accept)
        btn_layout.addWidget(self._add_btn)
        for cb in self._checkboxes.values():
            cb.toggled.connect(self._update_add_button)
        self._update_add_button()

        layout.addLayout(btn_layout)

    def _update_add_button(self) -> None:
        """Adding is possible once a set is ticked; until then the button says what to do."""
        ticked = any(cb.isChecked() for cb in self._checkboxes.values())
        self._add_btn.setEnabled(ticked)
        self._add_btn.setToolTip("" if ticked else "Tick at least one set of rules to add.")

    def _select_all(self) -> None:
        for cb in self._checkboxes.values():
            if cb.isEnabled():  # a set that is already in your list can't be ticked
                cb.setChecked(True)

    def _clear_all(self) -> None:
        for cb in self._checkboxes.values():
            cb.setChecked(False)

    def _on_accept(self) -> None:
        self._selected_preset_ids = [pid for pid, cb in self._checkboxes.items() if cb.isChecked()]
        if self._selected_preset_ids:
            self.accept()

    def get_selected_preset_ids(self) -> list[str]:
        return list(self._selected_preset_ids)


# =====================================================================
# 2. RULE EDIT MODAL DIALOG
# =====================================================================

def _parse_words(text: str) -> list[str]:
    """"Bank_Statement, statement of account" -> ["bank_statement", "statement of account"]."""
    return [word.strip().lower() for word in text.split(",") if word.strip()]


def _parse_sites(text: str) -> list[str]:
    """"https://www.MakerWorld.com/models, printables.com" -> ["makerworld.com", "printables.com"].

    Website rules match on the bare domain (sorter.py compares the download's host with it),
    so a scheme, a path and a leading "www." are dropped here.
    """
    found: list[str] = []
    for piece in text.split(","):
        host = piece.strip().lower()
        if not host:
            continue
        host = re.sub(r"^[a-z]+://", "", host).split("/")[0].split(":")[0].rstrip(".")
        if host.startswith("www."):
            host = host[4:]
        if host and host not in found:
            found.append(host)
    return found


def _parse_extensions(text: str) -> list[str]:
    """".PDF, docx; *.xlsx" -> [".pdf", ".docx", ".xlsx"]. Commas, semicolons or spaces may separate them."""
    found: list[str] = []
    for piece in re.split(r"[,;\s]+", text.strip().lower()):
        piece = piece.lstrip("*.")
        if piece and f".{piece}" not in found:
            found.append(f".{piece}")
    return found


def _known_folders(limit: int = 400) -> list[str]:
    """Folders inside Downloads, two levels deep, written the way rules write them ("Documents\\PDF")."""
    try:
        root = Path(sorter.watch_root(suite_data.load_sorter_config()))
    except Exception:
        return []
    internal = set(getattr(sorter, "INTERNAL_DIR_NAMES", ()))

    def folders_in(parent: Path) -> list[Path]:
        try:
            return sorted((e for e in parent.iterdir()
                           if e.is_dir() and not e.name.startswith((".", "_")) and e.name.lower() not in internal),
                          key=lambda e: e.name.lower())
        except OSError:
            return []

    found: list[str] = []
    for first in folders_in(root):
        found.append(first.name)
        found.extend(f"{first.name}\\{second.name}" for second in folders_in(first))
        if len(found) >= limit:
            break
    return found[:limit]


class RuleEditDialog(QDialog):
    """Modal dialog for creating and editing a keyword or extension rule.

    It says what is missing next to the field, instead of in a pop-up, and keeps
    Save switched off until the rule could actually do something.
    """

    def __init__(
        self,
        rule: dict[str, Any] | None = None,
        rule_type: str = "keyword",
        parent: QWidget | None = None,
        title: str | None = None,
        known_folders: list[str] | None = None,
    ):
        super().__init__(parent)
        self.rule_type = rule_type
        self.original_rule = rule or {}
        self._touched: set[str] = set()  # fields the person has typed in

        default_title = "Edit name rule" if rule_type == "keyword" else "Edit file type rule"
        if not rule:
            default_title = "Add name rule" if rule_type == "keyword" else "Add file type rule"
        title = title or default_title
        self.setWindowTitle(title)
        self.setMinimumWidth(500)
        self.setStyleSheet(ui_style.stylesheet())

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(20, 20, 20, 20)

        def error_label() -> QLabel:
            label = ui_style.label("", "error", wrap=True)
            label.setVisible(False)
            return label

        # Header Title
        layout.addWidget(ui_style.label(title, "title"))

        # Rule Name
        layout.addWidget(ui_style.label("Name", "muted"))
        self._name_input = QLineEdit()
        self._name_input.setPlaceholderText("For example: Bank statements")
        self._name_input.setText(self.original_rule.get("name", ""))
        layout.addWidget(self._name_input)
        self._name_error = error_label()
        layout.addWidget(self._name_error)

        # Destination Folder
        layout.addWidget(ui_style.label("Move to this folder (inside Downloads, or a full path)", "muted"))
        dest_box = QHBoxLayout()
        dest_box.setSpacing(8)
        self._folder_input = QLineEdit()
        self._folder_input.setPlaceholderText("For example: Documents\\Bank statements")
        self._folder_input.setText(self.original_rule.get("folder", ""))
        dest_box.addWidget(self._folder_input)

        browse_btn = ui_style.button("Browse...")
        browse_btn.setAutoDefault(False)
        browse_btn.clicked.connect(self._browse_folder)
        dest_box.addWidget(browse_btn)
        layout.addLayout(dest_box)
        self._folder_error = error_label()
        layout.addWidget(self._folder_error)
        self._suggest_folders(known_folders)

        # Keywords (for keyword rules)
        if self.rule_type == "keyword":
            layout.addWidget(ui_style.label("When the file name contains any of these words (separate with commas)", "muted"))
            self._keywords_input = QLineEdit()
            self._keywords_input.setPlaceholderText("For example: bank_statement, statement of account")
            matches = self.original_rule.get("match", [])
            self._keywords_input.setText(", ".join(matches) if isinstance(matches, list) else str(matches))
            layout.addWidget(self._keywords_input)
            self._words_error = error_label()
            layout.addWidget(self._words_error)

            # Websites (optional): the rule only fires for files downloaded from these sites.
            layout.addWidget(ui_style.label(
                "Only when downloaded from these websites (optional, separate with commas)", "muted"))
            self._sites_input = QLineEdit()
            self._sites_input.setPlaceholderText("For example: yourbank.com, makerworld.com")
            self._sites_input.setText(", ".join(self.original_rule.get("sites") or []))
            layout.addWidget(self._sites_input)

        # Allowed Extensions
        layout.addWidget(ui_style.label(
            "Only these file types (separate with commas; leave empty for any type)"
            if self.rule_type == "keyword" else "File types (separate with commas)", "muted"))
        self._exts_input = QLineEdit()
        self._exts_input.setPlaceholderText("For example: .pdf, .docx")
        exts = self.original_rule.get("extensions", [])
        self._exts_input.setText(", ".join(exts) if isinstance(exts, list) else str(exts))
        layout.addWidget(self._exts_input)
        self._exts_note = ui_style.label("", "caption")
        layout.addWidget(self._exts_note)
        self._types_error = error_label()
        layout.addWidget(self._types_error)

        # Active Toggle Checkbox
        self._active_chk = QCheckBox("Rule is on")
        self._active_chk.setChecked(self.original_rule.get("enabled", True))
        layout.addWidget(self._active_chk)

        layout.addSpacing(8)

        # Action Buttons
        btn_box = QHBoxLayout()
        btn_box.setSpacing(10)
        self._needs_note = ui_style.label("", "caption", wrap=True)  # why Save is off, without hovering
        btn_box.addWidget(self._needs_note, 1)

        cancel_btn = ui_style.button("Cancel")
        cancel_btn.setAutoDefault(False)
        cancel_btn.clicked.connect(self.reject)
        btn_box.addWidget(cancel_btn)

        self._save_btn = ui_style.button("Save rule", "primary")
        self._save_btn.setDefault(True)  # Enter saves, once the rule is complete
        self._save_btn.clicked.connect(self._validate_and_accept)
        btn_box.addWidget(self._save_btn)

        layout.addLayout(btn_box)

        fields = {"name": self._name_input, "folder": self._folder_input, "types": self._exts_input}
        if self.rule_type == "keyword":
            fields["words"] = self._keywords_input
        for key, edit in fields.items():
            edit.textChanged.connect(lambda _text: self._refresh_validation())
            edit.textEdited.connect(lambda _text, k=key: self._touched.add(k))
        self._refresh_validation()

    # ---- what is missing ------------------------------------------------

    def _problems(self) -> dict[str, str]:
        """What keeps this rule from being saved, by field: name, folder, words (name rules) or types."""
        problems: dict[str, str] = {}
        if not self._name_input.text().strip():
            problems["name"] = "Give the rule a name, for example “Bank statements”."
        if not self._folder_input.text().strip():
            problems["folder"] = "Choose the folder these files should go to."
        if self.rule_type == "keyword":
            # A name rule only fires on a word in the name (or a website it was made for): a file type
            # alone is not enough, and a rule with none of them never matches anything.
            if not _parse_words(self._keywords_input.text()) and not _parse_sites(self._sites_input.text()):
                problems["words"] = (
                    "Add at least one word to look for in the file name. A name rule without a word never "
                    "matches anything. To file every file of one type, use the File types tab."
                )
        elif not _parse_extensions(self._exts_input.text()):
            problems["types"] = "Add at least one file type, like .pdf."
        return problems

    def _refresh_validation(self) -> None:
        problems = self._problems()
        shown_fields = [("name", self._name_input, self._name_error),
                        ("folder", self._folder_input, self._folder_error)]
        if self.rule_type == "keyword":
            shown_fields.append(("words", self._keywords_input, self._words_error))
        else:
            shown_fields.append(("types", self._exts_input, self._types_error))
        for key, edit, label in shown_fields:
            # Say it next to a field the person has typed in, or when it is the only thing still missing.
            show = key in problems and (key in self._touched or len(problems) == 1)
            label.setText(problems.get(key, ""))
            label.setVisible(show)
            edit.setProperty("invalid", "true" if show else "")
            ui_style.polish(edit)

        types = _parse_extensions(self._exts_input.text())
        if types:
            self._exts_note.setText("Will use: " + ", ".join(types))
        else:
            self._exts_note.setText("Any file type" if self.rule_type == "keyword" else "")
        self._exts_note.setVisible(bool(self._exts_note.text()))

        self._save_btn.setEnabled(not problems)
        names = {"name": "a name", "folder": "a folder", "words": "a word to look for", "types": "a file type"}
        needed = "Still needed: " + ", ".join(names[key] for key in problems) if problems else ""
        self._save_btn.setToolTip(needed)
        self._needs_note.setText(needed)

    def _suggest_folders(self, known_folders: list[str] | None) -> None:
        """Offer the folders that already exist inside Downloads as you type in the folder box."""
        self._folder_model = QStringListModel(list(known_folders or []), self)
        completer = QCompleter(self._folder_model, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        popup = completer.popup()
        popup.setObjectName("completerPopup")
        popup.setStyleSheet(ui_style.stylesheet())  # the popup is its own window, so it needs the look itself
        self._folder_input.setCompleter(completer)
        if known_folders is None:
            QTimer.singleShot(0, self._load_folder_suggestions)

    def _load_folder_suggestions(self) -> None:
        try:
            self._folder_model.setStringList(_known_folders())
        except RuntimeError:
            pass  # the dialog was closed before this ran

    def _browse_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder")
        if folder:
            self._folder_input.setText(folder)

    def _validate_and_accept(self) -> None:
        problems = self._problems()
        if problems:
            self._touched.update(problems)
            self._refresh_validation()
            first = next(iter(problems))
            {"name": self._name_input, "folder": self._folder_input,
             "words": getattr(self, "_keywords_input", self._exts_input), "types": self._exts_input}[first].setFocus()
            return
        self.accept()

    def get_rule_data(self) -> dict[str, Any]:
        """Parse inputs into standard rule dictionary."""
        rule: dict[str, Any] = {
            "name": self._name_input.text().strip(),
            "folder": self._folder_input.text().strip(),
            "extensions": _parse_extensions(self._exts_input.text()),
            "enabled": self._active_chk.isChecked(),
        }
        if self.rule_type == "keyword":
            rule["match"] = _parse_words(self._keywords_input.text())
            # Websites the rule also checks (edited here, so the rule is the user's own).
            sites = _parse_sites(self._sites_input.text())
            if sites:
                rule["sites"] = sites
            if self.original_rule.get("origin"):
                rule["origin"] = "user"
        return rule


# =====================================================================
# 2. RULE TABLE WIDGET (Precedence, Reorder, Toggle)
# =====================================================================

# How a rule that wasn't typed in by hand came to be: ("badge text", badge kind, tooltip).
# Editing such a rule by hand makes it the person's own (see RuleEditDialog.get_rule_data).
ORIGIN_BADGES: dict[str, tuple[str, str, str]] = {
    "learned": ("Learned", "success",
                "Added when you said Yes to a suggestion. Edit it and it becomes your own rule."),
    "scan": ("From setup", "info",
             "Made from a folder you already had when you set up. Edit it and it becomes your own rule."),
}


class RuleTableWidget(QWidget):
    """Precedence-ordered table widget for keyword or extension rules."""

    rules_changed = Signal()
    rule_added = Signal(dict)  # a rule made with Add rule, for the page's checks

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
        toolbar.setSpacing(4)

        noun = "name rule" if self.rule_type == "keyword" else "file type rule"
        self._add_btn = ui_style.button(f"Add {noun}", None, "plus")
        self._add_btn.clicked.connect(self.add_rule_dialog)
        toolbar.addWidget(self._add_btn)
        toolbar.addSpacing(8)

        self._move_up_btn = ui_style.icon_button("up", "Move up (checked earlier), Alt+Up")
        self._move_up_btn.clicked.connect(self.move_rule_up)
        toolbar.addWidget(self._move_up_btn)
        self._move_down_btn = ui_style.icon_button("down", "Move down (checked later), Alt+Down")
        self._move_down_btn.clicked.connect(self.move_rule_down)
        toolbar.addWidget(self._move_down_btn)
        self._edit_btn = ui_style.icon_button("edit", "Edit")
        self._edit_btn.clicked.connect(self.edit_selected_rule)
        toolbar.addWidget(self._edit_btn)
        self._delete_btn = ui_style.icon_button("trash", "Delete")
        self._delete_btn.clicked.connect(self.delete_selected_rule)
        toolbar.addWidget(self._delete_btn)
        toolbar.addStretch()

        if self.rule_type == "keyword":
            hint = "Matches whole words in a file's name, like “invoice” in Invoice_2026.pdf."
        else:
            hint = "Used when no name rule matches."
        hint_label = ui_widgets.ElidedLabel(hint)  # shortens itself in a narrow window instead of widening the page
        hint_label.setProperty("role", "caption")
        toolbar.addWidget(hint_label)
        layout.addSpacing(6)
        layout.addLayout(toolbar)

        # Table
        self._table = QTableWidget()
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(38)
        self._table.setShowGrid(False)
        self._table.setWordWrap(False)

        if self.rule_type == "keyword":
            headers = ["#", "Name", "Moves to", "When the name contains", "File types", "On"]
        else:
            headers = ["#", "Name", "Moves to", "File types", "On"]

        self._table.setColumnCount(len(headers))
        self._table.setHorizontalHeaderLabels(headers)

        header = self._table.horizontalHeader()
        header.setHighlightSections(False)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(0, 40)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self._table.setColumnWidth(1, 190)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self._table.setColumnWidth(2, 150)
        self._table.setItemDelegateForColumn(1, ui_widgets.BadgeDelegate(self._table))  # "Learned", "From setup"
        if self.rule_type == "keyword":
            # The words are the part people look for, so they get what the other columns leave.
            header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
            self._table.setColumnWidth(4, 100)
            header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
            self._table.setColumnWidth(5, 48)
        else:
            header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
            self._table.setColumnWidth(4, 48)

        self._empty_lbl = ui_style.label(
            "No name rules yet. Add one, or pick some with “Add from library”."
            if self.rule_type == "keyword" else "No file type rules yet.",
            "muted",
        )
        self._empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_lbl.setMinimumHeight(80)
        self._empty_lbl.setVisible(False)
        layout.addWidget(self._empty_lbl, 1)  # fills the space while the list is empty

        self._table.itemChanged.connect(self._on_table_item_changed)
        self._table.cellDoubleClicked.connect(lambda row, col: self.edit_selected_rule())
        layout.addWidget(self._table, 1)

        # Reorder from the keyboard: the arrow keys move the selection, Alt+arrow moves the rule.
        for keys, move in (("Alt+Up", self.move_rule_up), ("Alt+Down", self.move_rule_down)):
            shortcut = QShortcut(QKeySequence(keys), self._table)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(move)

    def load_rules(self, rules: list[dict[str, Any]]) -> None:
        """Populate table from list of rule dictionaries."""
        self._rules = copy.deepcopy(rules)
        self._refresh_table()

    def get_rules(self) -> list[dict[str, Any]]:
        """Return current in-memory rules list reflecting all edits and ordering."""
        return copy.deepcopy(self._rules)

    def rule_count(self) -> int:
        return len(self._rules)

    def insert_rule(self, rule: dict[str, Any], index: int = 0) -> None:
        """Add a rule at a place in the list (the top by default, so it is checked first)."""
        index = max(0, min(index, len(self._rules)))
        self._rules.insert(index, copy.deepcopy(rule))
        self._refresh_table()
        self._table.setCurrentCell(index, 1)
        self.rules_changed.emit()

    def highlight_rule(self, name: str, index: int | None = None) -> bool:
        """Select a rule's row, scroll to it and flash it. It is found by its place in the list, else by name."""
        row = -1
        if index is not None and 0 <= index < len(self._rules) and self._rules[index].get("name") == name:
            row = index
        else:
            row = next((i for i, r in enumerate(self._rules)
                        if r.get("name") == name and r.get("enabled", True)), -1)
        if row < 0:
            return False
        self._table.setCurrentCell(row, 1)
        self._table.scrollToItem(self._table.item(row, 1))
        ui_motion.flash_rows(self._table, [row])
        return True

    def _refresh_table(self) -> None:
        self._block_signals = True
        self._table.setRowCount(len(self._rules))
        if hasattr(self, "_empty_lbl"):
            self._empty_lbl.setVisible(not self._rules)
            self._table.setVisible(bool(self._rules))

        for row_idx, rule in enumerate(self._rules):
            is_enabled = rule.get("enabled", True)
            fg = COLORS["text"] if is_enabled else COLORS["textFaint"]

            # Col 0: Precedence index
            prec_item = QTableWidgetItem(str(row_idx + 1))
            prec_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            prec_item.setForeground(QColor(COLORS["textMuted"]))
            prec_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self._table.setItem(row_idx, 0, prec_item)

            # Col 1: Rule Name. A rule made for you carries a small badge saying how.
            name_item = QTableWidgetItem(rule.get("name", "Unnamed Rule"))
            origin = ORIGIN_BADGES.get(str(rule.get("origin") or "")) if self.rule_type == "keyword" else None
            if origin:
                name_item.setData(ui_widgets.BADGE_ROLE, (origin[0], origin[1]))
                name_item.setToolTip(f"{name_item.text()}\n{origin[2]}")
            else:
                name_item.setToolTip(name_item.text())
            name_item.setForeground(QColor(fg))
            name_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self._table.setItem(row_idx, 1, name_item)

            # Col 2: Destination Folder
            folder_item = QTableWidgetItem(rule.get("folder", ""))
            folder_item.setIcon(ui_icons.icon("folder", fg))
            folder_item.setToolTip(folder_item.text())
            folder_item.setForeground(QColor(fg))
            folder_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self._table.setItem(row_idx, 2, folder_item)

            if self.rule_type == "keyword":
                # Col 3: Keywords
                matches = rule.get("match", [])
                match_str = ", ".join(matches) if isinstance(matches, list) else str(matches)
                sites = rule.get("sites") or []
                if sites:
                    site_str = "downloaded from " + ", ".join(str(x) for x in sites)
                    match_str = f"{match_str} ({site_str})" if match_str else site_str
                kw_item = QTableWidgetItem(match_str)
                kw_item.setToolTip(match_str)
                kw_item.setForeground(QColor(fg))
                kw_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self._table.setItem(row_idx, 3, kw_item)

                # Col 4: Extensions
                exts = rule.get("extensions", [])
                ext_str = ", ".join(exts) if isinstance(exts, list) else str(exts)
                ext_item = QTableWidgetItem(ext_str)
                ext_item.setToolTip(ext_str)
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
            self.rule_added.emit(new_rule)

    def add_from_presets_dialog(self) -> None:
        """Open Preset Library dialog and append selected preset rules."""
        dlg = PresetLibraryDialog(parent=self, existing_names=(r.get("name", "") for r in self._rules))
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
        self.setObjectName("card")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        # Header: Mode Toggle + Status Pill
        header = QHBoxLayout()
        header.setSpacing(8)

        self._enable_chk = QCheckBox("Project mode")
        self._enable_chk.setStyleSheet("font-weight: 600;")
        self._enable_chk.toggled.connect(self._on_enable_toggled)
        header.addWidget(self._enable_chk)

        header.addStretch()

        self._status_badge = QLabel("Off")
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
        proj_box.addWidget(ui_style.label("Project", "muted"))
        self._proj_combo = QComboBox()
        self._proj_combo.currentIndexChanged.connect(self._on_project_selected)
        proj_box.addWidget(self._proj_combo)
        row1.addLayout(proj_box, 3)

        dir_box = QVBoxLayout()
        dir_box.setSpacing(3)
        dir_box.addWidget(ui_style.label("Folder for CAD, 3D and CNC files", "muted"))
        dir_inner = QHBoxLayout()
        dir_inner.setSpacing(6)
        self._dir_edit = QLineEdit()
        self._dir_edit.setPlaceholderText("Choose a project or a folder")
        self._dir_edit.textChanged.connect(lambda: self.state_changed.emit())
        dir_inner.addWidget(self._dir_edit)

        browse_btn = ui_style.button("Browse...")
        browse_btn.clicked.connect(self._browse_custom_dir)
        dir_inner.addWidget(browse_btn)
        dir_box.addLayout(dir_inner)
        row1.addLayout(dir_box, 4)

        # Expiry Duration Selector
        exp_box = QVBoxLayout()
        exp_box.setSpacing(3)
        exp_box.addWidget(ui_style.label("Turn off after", "muted"))
        self._expiry_combo = QComboBox()
        self._expiry_combo.addItem("Never (turn off by hand)", None)
        self._expiry_combo.addItem("1 hour", 1.0)
        self._expiry_combo.addItem("4 hours", 4.0)
        self._expiry_combo.addItem("8 hours", 8.0)
        self._expiry_combo.addItem("24 hours", 24.0)
        self._expiry_combo.currentIndexChanged.connect(self._on_expiry_selected)
        exp_box.addWidget(self._expiry_combo)
        row1.addLayout(exp_box, 2)

        layout.addLayout(row1)

        # Live Status Summary line
        self._status_summary = QLabel("Off. Your normal rules apply.")
        self._status_summary.setProperty("role", "caption")
        layout.addWidget(self._status_summary)

        # Populate projects
        self.reload_projects()

    def reload_projects(self) -> None:
        """Load projects from Print Library SQLite database."""
        self._maker_projects = suite_data.get_maker_projects()
        self._proj_combo.blockSignals(True)
        self._proj_combo.clear()
        self._proj_combo.addItem("Choose a project", None)
        for proj in self._maker_projects:
            self._proj_combo.addItem(ui_icons.icon("folder"), proj['name'], proj)
        self._proj_combo.addItem("Another folder...", "custom")
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

    @staticmethod
    def _badge_style(fg: str, bg: str) -> str:
        return f"color: {fg}; background: {bg}; border-radius: 4px; padding: 2px 8px; font-size: 12px; font-weight: 600;"

    def _update_status_display(self) -> None:
        enabled = self._enable_chk.isChecked()
        now = time.time()

        if not enabled:
            if hasattr(self, "_countdown_timer") and self._countdown_timer.isActive():
                self._countdown_timer.stop()
            self._status_badge.setText("Off")
            self._status_badge.setStyleSheet(self._badge_style(COLORS["textMuted"], COLORS["surfaceRaised"]))
            self._status_summary.setText("Off. Your normal rules apply.")
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
                self._status_badge.setText("Ended")
                self._status_badge.setStyleSheet(self._badge_style(COLORS["warning"], COLORS["warningSubtle"]))
                self._status_summary.setText("Project mode has ended. Your normal rules apply again.")
                return
            else:
                hrs = int(rem // 3600)
                mins = int((rem % 3600) // 60)
                time_str = f"turns off in {hrs}h {mins}m" if hrs > 0 else f"turns off in {mins}m"
        else:
            if hasattr(self, "_countdown_timer") and self._countdown_timer.isActive():
                self._countdown_timer.stop()
            time_str = "stays on until you turn it off"

        self._status_badge.setText("On")
        self._status_badge.setStyleSheet(self._badge_style(COLORS["warning"], COLORS["warningSubtle"]))
        target = self._dir_edit.text().strip() or "no folder chosen yet"
        self._status_summary.setText(f"CAD, 3D and CNC files go to {target}; {time_str}.")

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

# Plain-language labels for how a file was routed (shown in "Try a filename").
SANDBOX_BADGES: dict[str, tuple[str, str]] = {
    # match type: (label, badge kind: see ui_style.badge)
    "waiting": ("Waiting", "neutral"),
    "keyword": ("Name rule", "success"),
    "extension": ("File type", "neutral"),
    "fallback": ("No rule matched", "warning"),
    "ignored": ("Skipped", "critical"),
    "active_project": ("Project mode", "warning"),
    "my_folder": ("Your folder", "info"),
    "makerworld": ("MakerWorld", "info"),
    "gcode_sniffer": ("G-code check", "info"),
}


class _ChipRow(QWidget):
    """The example chips on one line: as many as fit are shown, the rest wait for a wider window."""

    def minimumSizeHint(self) -> QSize:
        return QSize(0, super().minimumSizeHint().height())  # it may be narrower than all its chips

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.fit()

    def fit(self) -> None:
        layout = self.layout()
        if layout is None:
            return
        used = 0
        for index in range(layout.count()):
            widget = layout.itemAt(index).widget()
            if widget is None:  # the stretch at the end
                continue
            used += widget.sizeHint().width() + max(layout.spacing(), 0)
            widget.setVisible(index == 0 or used <= self.width())  # the "Examples:" label always stays


class LiveSandboxWidget(QFrame):
    """"Try a filename": shows where a file would go with the rules being edited."""

    # How the typed name was routed: match type, the winning rule's name, and its place
    # in its list (-1 when unknown). Lets the page show which row won.
    matched = Signal(str, str, int)
    make_rule_requested = Signal(str)   # "Make a rule for this..." was clicked, with the typed name
    compact_chosen = Signal(bool)       # the person clicked the button that hides or shows the details

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._config: dict[str, Any] = {}
        self._active_project: dict[str, Any] | None = None
        self._trace_expanded = False
        self._compact = False
        self._evaluated_name = ""

        self._init_ui()

    def _init_ui(self) -> None:
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(10)

        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        title_row.addWidget(ui_style.label("Try a filename", "section"))
        title_row.addWidget(ui_style.label("Uses your rules as they are now, including unsaved changes.", "caption"))
        title_row.addStretch()
        self._compact_btn = ui_style.icon_button("down", "Hide the examples and details")
        self._compact_btn.clicked.connect(self._on_compact_clicked)
        title_row.addWidget(self._compact_btn)
        layout.addLayout(title_row)

        self._input = QLineEdit()
        self._input.setProperty("role", "mono")
        self._input.setClearButtonEnabled(True)
        self._input.addAction(ui_icons.icon("search"), QLineEdit.ActionPosition.LeadingPosition)
        self._input.textChanged.connect(lambda: self.evaluate())
        layout.addWidget(self._input)

        self._current_persona = "everyday_home"
        self._chips_widget = _ChipRow()  # holds the example chips, so they can be hidden together
        self._chips_box = QHBoxLayout(self._chips_widget)
        self._chips_box.setContentsMargins(0, 0, 0, 0)
        self._chips_box.setSpacing(0)
        layout.addWidget(self._chips_widget)
        self._rebuild_chips()

        # Result
        result = QHBoxLayout()
        result.setSpacing(10)
        self._badge_lbl = ui_style.badge("", "neutral")
        result.addWidget(self._badge_lbl)
        goes = ui_style.label("goes to", "muted")
        result.addWidget(goes)
        self._dest_lbl = ui_style.label("—", "mono")
        self._dest_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        result.addWidget(self._dest_lbl, 1)
        layout.addLayout(result)

        self._detail_widget = QWidget()  # which rule won, why, and the full list checked
        detail = QHBoxLayout(self._detail_widget)
        detail.setContentsMargins(0, 0, 0, 0)
        detail.setSpacing(6)
        self._rule_lbl = ui_style.label("", "muted")
        detail.addWidget(self._rule_lbl)
        self._trigger_lbl = ui_style.label("", "caption")
        detail.addWidget(self._trigger_lbl, 1)
        self._make_rule_btn = ui_style.button("Make a rule for this...", None, "rule",
                                              "Add a name rule for this file name, at the top of the list")
        self._make_rule_btn.setVisible(False)
        self._make_rule_btn.clicked.connect(lambda: self.make_rule_requested.emit(self._evaluated_name))
        detail.addWidget(self._make_rule_btn)
        self._trace_btn = ui_style.button("Show all rules checked", "ghost")
        self._trace_btn.clicked.connect(self._toggle_trace)
        detail.addWidget(self._trace_btn)
        layout.addWidget(self._detail_widget)

        self._trace_list = QListWidget()
        self._trace_list.setVisible(False)
        self._trace_list.setMaximumHeight(140)
        self._trace_list.setStyleSheet(f"font-family: '{TYPE['mono']}'; font-size: 12px; color: {COLORS['textMuted']};")
        layout.addWidget(self._trace_list)

        self._show_badge("waiting")

    def _show_badge(self, match_type: str) -> None:
        text, kind = SANDBOX_BADGES.get(match_type, (match_type.replace("_", " ").title(), "neutral"))
        self._badge_lbl.setText(text)
        self._badge_lbl.setProperty("kind", kind)
        ui_style.polish(self._badge_lbl)

    def set_compact(self, compact: bool) -> None:
        """Show only the title, the input and the result (True), or the examples and details too (False)."""
        self._compact = bool(compact)
        self._chips_widget.setVisible(not self._compact)
        self._detail_widget.setVisible(not self._compact)
        self._trace_list.setVisible(self._trace_expanded and not self._compact)
        self._compact_btn.setIcon(ui_icons.icon("up" if self._compact else "down"))
        tip = "Show the examples and details" if self._compact else "Hide the examples and details"
        self._compact_btn.setToolTip(tip)
        self._compact_btn.setAccessibleName(tip)

    def is_compact(self) -> bool:
        return self._compact

    def _on_compact_clicked(self) -> None:
        self.set_compact(not self._compact)
        self.compact_chosen.emit(self._compact)

    def set_persona(self, persona: str) -> None:
        """Update the example filenames to fit the active profile."""
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

        self._chips_box.addWidget(ui_style.label("Examples:", "caption"))

        persona = getattr(self, "_current_persona", "everyday_home")
        if persona == "maker_3d":
            presets = [
                ("Haas program", "bracket_op1.nc"),
                ("Bambu G-code", "vase_multicolor.gcode"),
                ("3MF project", "turbine_bracket.3mf"),
                ("Invoice", "Invoice_2026.pdf"),
                ("Zip file", "hardware_pack.zip"),
            ]
            example = "bracket_op1.nc"
        else:
            presets = [(name, name) for name in (
                "Invoice_2026.pdf", "Family_Photo.jpg", "Syllabus_Fall.pdf",
                "Zoom_Installer.exe", "Vacation_Photos.zip",
            )]
            example = "Invoice_2026.pdf"
        self._input.setPlaceholderText(f"Type a file name, for example {example}")

        for label, fname in presets:
            btn = ui_style.button(label, "ghost")
            btn.setToolTip(fname)
            btn.setStyleSheet("padding: 2px 6px; font-weight: 500; font-size: 12px;")
            btn.clicked.connect(lambda _c=False, f=fname: self._set_input(f))
            self._chips_box.addWidget(btn)

        self._chips_box.addStretch()
        QTimer.singleShot(0, self._chips_widget.fit)  # once the new chips have their final size

    def _set_input(self, fname: str) -> None:
        self._input.setText(fname)

    def _toggle_trace(self) -> None:
        self._trace_expanded = not self._trace_expanded
        self._trace_list.setVisible(self._trace_expanded and not self._compact)
        self._trace_btn.setText("Hide rules checked" if self._trace_expanded else "Show all rules checked")

    def set_config_and_project(self, config: dict[str, Any], active_project: dict[str, Any] | None) -> None:
        """Update draft rules and active project model and re-evaluate immediately."""
        self._config = copy.deepcopy(config)
        self._active_project = copy.deepcopy(active_project) if active_project else None
        self.evaluate()

    def evaluate(self, filename: str | None = None) -> dict[str, Any] | None:
        """Simulate routing chain for hypothetical filename."""
        name = filename if filename is not None else self._input.text().strip()
        self._evaluated_name = name
        if not name:
            self._show_badge("waiting")
            self._rule_lbl.setText("Type a file name above, or pick an example.")
            self._dest_lbl.setText("—")
            self._trigger_lbl.setText("")
            self._trace_list.clear()
            self._make_rule_btn.setVisible(False)
            self.matched.emit("waiting", "", -1)
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

        self._show_badge(match_type)
        self._rule_lbl.setText(winning_rule or SANDBOX_BADGES.get(match_type, (match_type,))[0])
        self._dest_lbl.setText(dest or "None")
        # No name rule decided this one: offer to make one.
        self._make_rule_btn.setVisible(match_type in ("fallback", "extension") and bool(self._evaluated_name))

        if match_type == "ignored":
            why = "Temporary and partial files are left alone."
        elif trigger:
            why = f"Matched {trigger}"
        elif match_type == "active_project":
            why = "Project mode catches CAD, 3D and CNC files."
        elif match_type == "makerworld":
            why = "Read from the file's MakerWorld details."
        elif match_type == "gcode_sniffer":
            why = f"Read from the start of the file: {res.get('sniffer_details')}"
        elif match_type == "fallback":
            why = "No rule matched, so it goes to the catch-all folder."
        else:
            why = ""
        self._trigger_lbl.setText(why)

        self._trace_list.clear()
        for t in res.get("rule_trace", []):
            tier = t.get("tier", "")
            status = t.get("status", "")
            rule_name = t.get("rule", "") or tier
            if status == "matched":
                txt = f"✓ {rule_name}  →  {t.get('destination', '')}"
            else:
                txt = f"   {rule_name}: {status.replace('_', ' ')}"
            self._trace_list.addItem(txt)

        # Which row of which list won: each rule adds exactly one line to the trace, in order.
        tier = {"keyword": "keyword", "extension": "extension"}.get(match_type)
        place = -1
        if tier:
            place = sum(1 for t in res.get("rule_trace", []) if t.get("tier") == tier) - 1
        self.matched.emit(match_type, str(winning_rule) if tier else "", place)


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


def _leftover_summary() -> tuple[dict, str]:
    """(numbers, one-line summary) for the leftover-space card."""
    import space_report
    import sorter

    report = space_report.report(sorter.load_config())
    parts = [
        f"{report['duplicates']['files']} duplicate(s)",
        f"{report['quarantine']['files']} in Clean Up batches",
        f"{report['old_downloads']['files']} old installer(s) or archive(s)",
    ]
    total = sum(report[k]["bytes"] for k in report)
    import clean_old
    return report, f"Leftovers: {', '.join(parts)} ({clean_old.format_size(total)})"


def open_leftover_review(owner: QWidget) -> None:
    """A dialog listing each kind of leftover with a button that sends it to the Recycle Bin."""
    import space_report
    import sorter

    dialog = QDialog(owner)
    dialog.setWindowTitle("Leftovers the sorter keeps")
    dialog.setMinimumWidth(520)
    layout = QVBoxLayout(dialog)
    layout.addWidget(ui_style.label(
        "Duplicates set aside, Clean Up batches kept for 30 days so Undo can bring them back, and old "
        "installers and archives. Each goes to the Recycle Bin, never straight to deletion.", "muted", wrap=True))
    rows_box = QVBoxLayout()
    layout.addLayout(rows_box)
    names = {
        "duplicates": "Duplicates set aside",
        "quarantine": "Clean Up batches",
        "old_downloads": "Old installers and archives",
    }

    def refresh() -> None:
        while rows_box.count():
            item = rows_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        report = space_report.report(sorter.load_config())
        for key, title in names.items():
            row = QHBoxLayout()
            row.addWidget(ui_style.label(f"{title}: {space_report.format_line('', report[key]).lstrip(': ')}"), 1)
            send = ui_style.button("Send to Recycle Bin")
            send.setEnabled(report[key]["files"] > 0)

            def send_now(_checked=False, category=key, label=title) -> None:
                answer = QMessageBox.question(
                    dialog, "Send to the Recycle Bin?",
                    f"Send {label.lower()} to the Recycle Bin? You can restore them from there.")
                if answer != QMessageBox.StandardButton.Yes:
                    return
                sent = space_report.send_category_to_recycle_bin(sorter.load_config(), category)
                owner.notice.emit(f"Sent {sent} item(s) to the Recycle Bin.", "success", "", None)
                refresh()

            send.clicked.connect(send_now)
            row.addWidget(send)
            wrapper = QWidget()
            wrapper.setLayout(row)
            rows_box.addWidget(wrapper)

    refresh()
    close = ui_style.button("Close")
    close.clicked.connect(dialog.accept)
    layout.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)
    dialog.exec()


def build_leftover_card(owner: QWidget) -> QFrame:
    """One line on the Clean up page: what the sorter keeps, with a button to review and clear it."""
    frame, body = ui_style.card(spacing=6, margins=(18, 10, 18, 10))
    row = QHBoxLayout()
    try:
        _report, text = _leftover_summary()
    except Exception as exc:
        text = f"Leftovers: couldn't read the folders ({exc})"
    summary = ui_style.label(text, "muted", wrap=True)  # wraps, so the page can be as narrow as the window
    row.addWidget(summary, 1)
    review = ui_style.button("Review leftovers...")
    review.clicked.connect(lambda: open_leftover_review(owner))
    row.addWidget(review)
    body.addLayout(row)
    return frame


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


def _count(number: int, noun: str, plural: str | None = None) -> str:
    """"1 file", "3 files". A plural that isn't just "s" is passed in."""
    return f"{number} {noun if number == 1 else (plural or noun + 's')}"


def _size_text(num_bytes: int) -> str:
    """"under 1 KB", "340 KB", "2.5 MB": a size for a sentence."""
    if num_bytes < 1024:
        return "under 1 KB" if num_bytes else "0 KB"
    if num_bytes < 1024 * 1024:
        return f"{round(num_bytes / 1024)} KB"
    return f"{num_bytes / (1024 * 1024):.1f} MB"


class MachineRemodelWidget(QFrame):
    # Good news for the window's message bar: (text, kind, action text, action). The window shows it.
    notice = Signal(str, str, str, object)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("machineRemodelWidget")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._current_plan: Any = None
        self._card_buttons: dict[str, QButtonGroup] = {}
        self._card_frames: list[QFrame] = []
        self._stat_values: dict[str, QLabel] = {}
        self._stat_captions: dict[str, QLabel] = {}
        self._stat_numbers: dict[str, int] = {}
        import remodel
        self._engine = remodel.RemodelEngine()
        self._worker: RemodelWorker | None = None
        self._setup_ui()

    def _notify(self, text: str, kind: str = "success", action_text: str = "", action: Any = None) -> None:
        """Say it in the window's message bar, so nothing pops up in front of the page."""
        self.notice.emit(text, kind, action_text, action)

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(14)

        # Everything above the Apply row scrolls as one page. After a scan there is more here
        # than a 560 px tall window holds (the tiles and the summary are added), and without
        # this the folder row was squeezed until its words were cut off. In a taller window
        # nothing scrolls and the list takes the spare room, as before.
        self._page_scroll = QScrollArea()
        self._page_scroll.setWidgetResizable(True)
        self._page_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._page_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        self._page_scroll.setWidget(content)
        outer.addWidget(self._page_scroll, 1)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        header, _ = ui_style.page_header(
            "Clean up a folder",
            "Preview how a messy folder would be organized by file type. Nothing moves until you click "
            "Apply, and you can undo it afterwards.",
        )
        layout.addWidget(header)
        layout.addWidget(build_leftover_card(self))

        scope_card, scope = ui_style.card(spacing=10)
        scope_layout = QHBoxLayout()
        scope_layout.setSpacing(8)
        self._scope_combo = QComboBox()
        self._scope_combo.addItems([
            "Downloads",
            "Desktop (files older than 30 days)",
            "Documents (top level only)",
            "Another folder...",
        ])
        self._scope_combo.currentIndexChanged.connect(self._on_scope_changed)
        scope_layout.addWidget(self._scope_combo, 1)

        self._custom_path_edit = QLineEdit()
        self._custom_path_edit.setPlaceholderText("Choose a folder")
        self._custom_path_edit.setEnabled(False)
        self._custom_path_edit.setVisible(False)
        scope_layout.addWidget(self._custom_path_edit, 2)

        self._browse_btn = ui_style.button("Browse...")
        self._browse_btn.setEnabled(False)
        self._browse_btn.setVisible(False)
        self._browse_btn.clicked.connect(self._on_browse)
        scope_layout.addWidget(self._browse_btn)

        self._scan_btn = ui_style.button("Preview changes", "primary", "search")
        self._scan_btn.clicked.connect(self._start_scan)
        scope_layout.addWidget(self._scan_btn)
        scope.addLayout(scope_layout)
        scope.addWidget(ui_style.label(
            "Always left alone: program and game folders, code and CAD projects, folders changed in the "
            "last 14 days, and any folder containing a file named .download-sorter-keep.",
            "caption", wrap=True))
        layout.addWidget(scope_card)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(4)
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        # The numbers at a glance, once there is a scan to count.
        self._stats_strip = QWidget()
        stats = QHBoxLayout(self._stats_strip)
        stats.setContentsMargins(0, 0, 0, 0)
        stats.setSpacing(10)
        for key, caption, tip in (
            ("looked", "Looked at", "Files found in this folder and the folders inside it."),
            ("move", "Would move", "Files in the folders set to “Organize by file type”, plus loose files. "
                                   "A few may stay where they are while moving, such as a project inside a folder."),
            ("alone", "Left alone", "Folders and files the clean-up will not touch."),
            ("dupes", "Duplicates", "Extra copies. They'd be set aside for 30 days, not deleted."),
        ):
            tile, tile_layout = ui_style.card(margins=(14, 8, 14, 8), spacing=0)
            value = ui_style.label("0", "stat")
            note = ui_style.label(caption, "caption")
            tile_layout.addWidget(value)
            tile_layout.addWidget(note)
            tile.setToolTip(tip)
            stats.addWidget(tile, 1)
            self._stat_values[key] = value
            self._stat_captions[key] = note
            self._stat_numbers[key] = 0
        self._stats_strip.setVisible(False)
        layout.addWidget(self._stats_strip)

        self._summary_lbl = ui_style.label(
            "Pick a folder and click Preview changes to see what would happen to each folder inside it.",
            "muted", wrap=True)
        layout.addWidget(self._summary_lbl)

        self._sub_tabs = QTabWidget()
        self._sub_tabs.setDocumentMode(True)
        self._sub_tabs.tabBar().setDrawBase(False)

        self._cards_scroll = QScrollArea()
        self._cards_scroll.setWidgetResizable(True)
        self._cards_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._cards_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._cards_container = QWidget()
        self._cards_layout = QVBoxLayout(self._cards_container)
        self._cards_layout.setContentsMargins(0, 8, 6, 0)
        self._cards_layout.setSpacing(8)
        self._cards_empty = ui_style.label("", "muted", wrap=True)  # what to do (before a scan), or why there is nothing
        self._cards_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cards_empty.setMinimumHeight(140)
        self._cards_layout.addWidget(self._cards_empty)
        self._cards_layout.addStretch()
        self._cards_scroll.setWidget(self._cards_container)
        self._show_empty_hint(None)
        self._sub_tabs.addTab(self._cards_scroll, "Folders")

        self._prot_table = QTableWidget(0, 2)
        self._prot_table.setHorizontalHeaderLabels(["Folder or file", "Why it's left alone"])
        self._prot_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self._prot_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._prot_table.verticalHeader().setVisible(False)
        self._prot_table.setShowGrid(False)
        self._sub_tabs.addTab(self._prot_table, "Left alone (0)")

        self._dupe_table = QTableWidget(0, 3)
        self._dupe_table.setHorizontalHeaderLabels(["Duplicate file", "Size", "Copy that's kept"])
        self._dupe_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self._dupe_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._dupe_table.verticalHeader().setVisible(False)
        self._dupe_table.setShowGrid(False)
        self._sub_tabs.addTab(self._dupe_table, "Duplicates (0)")

        self._sub_tabs.setMinimumHeight(190)  # room for the tabs and a folder and a half; less, and the page scrolls
        layout.addWidget(self._sub_tabs, 1)

        action_layout = QHBoxLayout()
        action_layout.setSpacing(8)
        self._apply_btn = ui_style.button("Apply", None, "check",
                                          "Move the files as previewed. You can undo this afterwards.")
        self._apply_btn.setEnabled(False)
        self._apply_btn.clicked.connect(self._start_apply)
        action_layout.addWidget(self._apply_btn)

        self._undo_btn = ui_style.button("Undo last clean-up", "ghost", "undo")
        self._undo_btn.clicked.connect(self._on_undo)
        action_layout.addWidget(self._undo_btn)

        action_layout.addStretch()
        self._receipt_btn = ui_style.button("Where did my files go?", "ghost", "file",
                                            "Open the list of every file that was moved")
        self._receipt_btn.setEnabled(False)
        self._receipt_btn.clicked.connect(self._open_receipt)
        action_layout.addWidget(self._receipt_btn)

        outer.addLayout(action_layout)  # always in view, below the part that scrolls

    def _on_scope_changed(self, idx: int):
        is_custom = (idx == 3)
        self._custom_path_edit.setEnabled(is_custom)
        self._browse_btn.setEnabled(is_custom)
        self._custom_path_edit.setVisible(is_custom)
        self._browse_btn.setVisible(is_custom)

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
            message = f"{target} doesn't exist. Pick another folder."
            self._summary_lbl.setText(message)
            self._notify(message, "warning")
            return

        self._scan_btn.setEnabled(False)
        self._apply_btn.setEnabled(False)
        self._progress.setVisible(True)
        self._summary_lbl.setText(f"Looking through {target}...")

        self._worker = RemodelWorker("scan", self._engine, (scope, target))
        self._worker.scan_finished.connect(self._on_scan_finished)
        self._worker.error_occurred.connect(self._on_worker_error)
        self._worker.start()

    def _show_empty_hint(self, plan: Any) -> None:
        """Under the Folders tab: what to do before a scan, or why a finished scan has no folders to show."""
        if plan is None:
            text = ("Nothing to show yet.\n\nThe folders inside the one you pick are listed here, "
                    "each with a choice for what to do with it.")
        elif plan.cards:
            text = ""
        elif plan.loose_files_to_organize or plan.duplicates_to_quarantine:
            loose = len(plan.loose_files_to_organize)
            text = (f"No folders inside this one.\n\n{_count(loose, 'loose file')} would be sorted into folders by type."
                    if loose else "No folders inside this one.")
        else:
            text = "Nothing to clean up here."
        self._cards_empty.setText(text)
        self._cards_empty.setVisible(bool(text))

    def _summary_for(self, plan: Any) -> str:
        dupes = len(plan.duplicates_to_quarantine)
        text = f"Looked at {_count(plan.total_files_scanned, 'file')} in {_count(len(plan.cards), 'folder')}. "
        if dupes:
            text += (f"Found {_count(dupes, 'duplicate file')} ({_size_text(plan.reclaimable_duplicate_bytes)}); "
                     f"{'it' if dupes == 1 else 'they'}'d be set aside for 30 days, not deleted. ")
        else:
            text += "No duplicates found. "
        if plan.cards:
            return text + "Check each folder below, then click Apply."
        if plan.loose_files_to_organize or dupes:
            return text + "Click Apply to go ahead."
        return text + "There is nothing to move."

    def _set_stat(self, key: str, value: int, duration: int = 600) -> None:
        ui_motion.count_up(self._stat_values[key], self._stat_numbers.get(key, 0), value, str, duration)
        self._stat_numbers[key] = value

    def _would_move_count(self) -> int:
        """Files that Apply would move with the choices now on screen: loose files, and the folders set to Organize."""
        plan = self._current_plan
        if plan is None:
            return 0
        total = len(plan.loose_files_to_organize)
        for card in plan.cards:
            group = self._card_buttons.get(str(card.folder_path))
            if group is not None and group.checkedId() == 1:
                total += card.file_count
        return total

    def _update_would_move(self) -> None:
        """A folder's choice changed: the "Would move" number follows."""
        self._set_stat("move", self._would_move_count(), duration=250)

    def _show_stats(self, plan: Any) -> None:
        dupes = len(plan.duplicates_to_quarantine)
        self._set_stat("looked", plan.total_files_scanned)
        self._set_stat("move", self._would_move_count())
        self._set_stat("alone", len(plan.protected_items))
        self._set_stat("dupes", dupes)
        self._stat_captions["dupes"].setText(
            f"Duplicates · {_size_text(plan.reclaimable_duplicate_bytes)}" if dupes else "Duplicates")
        self._stats_strip.setVisible(True)

    def _build_card(self, card: Any) -> QFrame:
        """One folder: its name and size, why it is (or isn't) left alone, and what to do with it."""
        frame, body = ui_style.card(margins=(16, 12, 16, 12), spacing=6)

        top = QHBoxLayout()
        top.setSpacing(8)
        icon = QLabel()
        icon.setPixmap(ui_icons.pixmap("folder", COLORS["textMuted"], 16))
        top.addWidget(icon)
        name = ui_widgets.ElidedLabel(card.folder_path.name)
        name.setProperty("role", "section")
        top.addWidget(name, 1)

        group = QButtonGroup(frame)
        for button_id, text, tip in (
            (1, "Organize by file type", "Move the files in this folder into folders by type"),
            (2, "Keep intact", "Leave this folder and everything in it exactly as it is"),
            (3, "Skip for now", "Do nothing with this folder this time"),
        ):
            radio = QRadioButton(text)
            radio.setProperty("role", "segment")
            radio.setToolTip(tip)
            radio.setCursor(Qt.CursorShape.PointingHandCursor)
            group.addButton(radio, button_id)
            top.addWidget(radio)
        group.button({"organize": 1, "keep_intact": 2}.get(card.action, 3)).setChecked(True)
        group.idToggled.connect(lambda _id, on: on and self._update_would_move())
        self._card_buttons[str(card.folder_path)] = group
        body.addLayout(top)

        info = QHBoxLayout()
        info.setSpacing(10)
        noun = "file" if card.file_count == 1 else "files"
        size = f"{card.total_size_mb} MB" if card.total_size_mb >= 0.1 else "under 0.1 MB"
        info_lbl = ui_style.label(f"{card.file_count} {noun} · {size}", "caption")
        info_lbl.setToolTip(str(card.folder_path))
        info.addWidget(info_lbl)
        if card.protection.is_protected:
            badge_text, kind = f"Left alone: {card.protection.reason}", "success"
        elif card.action == "keep_intact":
            badge_text, kind = "Kept together: it has its own name", "neutral"
        else:
            badge_text, kind = "Looks unsorted: can be organized", "info"
        info.addWidget(ui_style.badge(badge_text, kind, label_class=ui_widgets.ElidedLabel))
        info.addStretch()
        body.addLayout(info)
        return frame

    def _on_scan_finished(self, plan: Any):
        self._progress.setVisible(False)
        self._scan_btn.setEnabled(True)
        self._current_plan = plan

        self._summary_lbl.setText(self._summary_for(plan))
        ui_style.set_role(self._apply_btn, "primary")
        self._apply_btn.setIcon(ui_icons.icon("check", COLORS["canvas"]))
        ui_style.set_role(self._scan_btn, None)
        self._scan_btn.setIcon(ui_icons.icon("search"))
        self._apply_btn.setEnabled(len(plan.cards) > 0 or len(plan.loose_files_to_organize) > 0 or len(plan.duplicates_to_quarantine) > 0)

        for frame in self._card_frames:
            self._cards_layout.removeWidget(frame)
            frame.deleteLater()
        self._card_frames.clear()
        self._card_buttons.clear()
        for card in plan.cards:
            frame = self._build_card(card)
            self._cards_layout.insertWidget(len(self._card_frames), frame)
            self._card_frames.append(frame)
        self._show_empty_hint(plan)
        self._show_stats(plan)

        self._prot_table.setRowCount(len(plan.protected_items))
        for row, (p, reason) in enumerate(plan.protected_items):
            self._prot_table.setItem(row, 0, QTableWidgetItem(str(p)))
            self._prot_table.setItem(row, 1, QTableWidgetItem(reason))
        self._sub_tabs.setTabText(1, f"Left alone ({len(plan.protected_items)})")

        self._dupe_table.setRowCount(len(plan.duplicates_to_quarantine))
        for row, (src, _q, keeper) in enumerate(plan.duplicates_to_quarantine):
            try:
                sz_str = f"{round(src.stat().st_size / 1024, 1)} KB"
            except OSError:
                sz_str = "0 KB"
            self._dupe_table.setItem(row, 0, QTableWidgetItem(src.name))
            self._dupe_table.setItem(row, 1, QTableWidgetItem(sz_str))
            self._dupe_table.setItem(row, 2, QTableWidgetItem(keeper))
        self._sub_tabs.setTabText(2, f"Duplicates ({len(plan.duplicates_to_quarantine)})")

    def _start_apply(self):
        if not self._current_plan:
            return

        confirm = QMessageBox.question(
            self,
            "Apply clean-up",
            f"Move the files in {self._current_plan.target_root} as previewed?\n\n"
            "You can put everything back with \u201cUndo last clean-up\u201d. A list of every move is saved "
            "in that folder as \u201cWhere did my files go.html\u201d.",
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
        self._summary_lbl.setText("Moving files...")

        self._worker = RemodelWorker("apply", self._engine, self._current_plan, overrides)
        self._worker.apply_finished.connect(self._on_apply_finished)
        self._worker.error_occurred.connect(self._on_worker_error)
        self._worker.start()

    def _on_apply_finished(self, receipt: Any):
        self._progress.setVisible(False)
        executed = len(receipt.get("executed_moves", []))
        skipped = len(receipt.get("skipped_items", []))
        result = f"Moved {_count(executed, 'file')} and left {_count(skipped, 'item')} alone."
        self._summary_lbl.setText(f"Done. {result} Click \u201cWhere did my files go?\u201d to see the full list.")
        self._receipt_btn.setEnabled(True)
        self._stats_strip.setVisible(False)  # its "would move" is no longer true
        self._notify(f"Clean-up finished. {result}", "success", "Where did my files go?", self._open_receipt)

    def _on_undo(self):
        confirm = QMessageBox.question(
            self,
            "Undo last clean-up",
            "Put every file from the most recent clean-up back where it was?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        res = self._engine.undo()
        if res.get("ok"):
            message = res.get("message", "Your files were put back.")
            self._summary_lbl.setText(message)
            self._stats_strip.setVisible(False)
            self._notify(message, "success")
        elif res.get("partial"):
            QMessageBox.warning(self, "Some files weren't put back", res.get("message", "Some files couldn't be put back."))
            self._summary_lbl.setText(res.get("message", ""))
        else:
            QMessageBox.critical(self, "Couldn't undo", res.get("message", "The clean-up couldn't be undone."))

    def _open_receipt(self):
        if self._current_plan:
            receipt_f = self._current_plan.target_root / "Where did my files go.html"
            if receipt_f.exists() and sys.platform == "win32":
                os.startfile(str(receipt_f))

    def _on_worker_error(self, err_msg: str):
        self._progress.setVisible(False)
        self._scan_btn.setEnabled(True)
        self._apply_btn.setEnabled(True)
        QMessageBox.critical(self, "Something went wrong", f"The clean-up stopped because of an error:\n\n{err_msg}")


class RulesManagerDialog(QDialog):
    """Main window: Home, Rules, Clean up a folder, and Settings, chosen from a sidebar."""

    # Page order in self._view_stack
    PAGES = ("home", "rules", "cleanup", "settings", "projects")

    def __init__(self, parent: QWidget | None = None, initial_view: str = "customize"):
        if isinstance(parent, str):
            initial_view = parent
            parent = None
        super().__init__(parent)
        self._accepting_close = False  # see reject() and closeEvent()
        self._discarded = False        # set on a leftover window that a newer one is closing
        self._update_available = False
        self._last_highlight: tuple[str, str, int] | None = None  # which rule row "Try a filename" last showed
        self._sandbox_pref: bool | None = None  # None: hide the sandbox's details only when the window is short
        self.setWindowTitle(about.APP_NAME)
        self.setMinimumSize(900, 560)
        self.resize(1040, 760)
        # A bare dialog only gets a close button, but this is the app's main window.
        self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        icon_file = ROOT / "DownloadSorter.ico"
        if icon_file.exists():
            self.setWindowIcon(QIcon(str(icon_file)))

        # Clean up any lingering top-level headless dialogs to prevent Qt window registry leaks
        app = QApplication.instance()
        if app is not None:
            for top_win in list(app.topLevelWidgets()):
                if isinstance(top_win, RulesManagerDialog) and top_win is not self:
                    try:
                        top_win._discarded = True  # tidying up: its size must not replace the remembered one
                        top_win.close()
                        top_win.deleteLater()
                    except Exception:
                        pass

        self._config: dict[str, Any] = {}
        self._init_ui()
        self.load_data()
        self.switch_view(initial_view)
        self._add_shortcuts()
        self._restore_window_state()

    # ------------------------------------------------------------------
    # layout
    # ------------------------------------------------------------------

    def _init_ui(self) -> None:
        self.setStyleSheet(ui_style.stylesheet())

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- sidebar -------------------------------------------------
        self._sidebar = QFrame()
        self._sidebar.setObjectName("sidebar")
        self._sidebar.setFixedWidth(216)
        side = QVBoxLayout(self._sidebar)
        side.setContentsMargins(14, 18, 14, 14)
        side.setSpacing(4)

        brand = QHBoxLayout()
        brand.setSpacing(10)
        try:
            from onboarding import SorterMascotWidget
            mascot = SorterMascotWidget()
            mascot.set_state("happy")
            logo = QLabel()
            logo.setPixmap(mascot.to_pixmap(84, 80).scaled(
                34, 32, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            brand.addWidget(logo)
        except Exception:
            pass
        brand.addWidget(ui_style.label(about.APP_NAME, "section"))
        brand.addStretch()
        side.addLayout(brand)
        side.addSpacing(18)

        self._view_btn_group = QButtonGroup(self)
        self._view_btn_group.setExclusive(True)
        self._nav_buttons: dict[str, QPushButton] = {}
        self._nav_dots: dict[str, QLabel] = {}
        self._nav_tips: dict[str, str] = {}
        dot_colors = {"home": COLORS["info"], "rules": COLORS["warning"]}
        for page_id, text, icon_name in (
            ("home", "Home", "home"),
            ("rules", "Rules", "rules"),
            ("cleanup", "Clean up a folder", "cleanup"),
            ("projects", "Projects", "folder"),
            ("settings", "Settings", "settings"),
        ):
            btn = ui_style.button(text, "nav", icon_name)
            tip = f"{text} (Ctrl+{self.PAGES.index(page_id) + 1})"
            btn.setToolTip(tip)
            self._nav_tips[page_id] = tip
            btn.setCheckable(True)
            self._view_btn_group.addButton(btn, self.PAGES.index(page_id))
            self._nav_buttons[page_id] = btn
            side.addWidget(btn)
            if page_id in dot_colors:
                # A small dot at the right edge: "something on this page needs a look".
                dot = ui_style.status_dot(dot_colors[page_id], 8)
                dot.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                dot.setVisible(False)
                dot_layout = QHBoxLayout(btn)
                dot_layout.setContentsMargins(0, 0, 12, 0)
                dot_layout.addStretch()
                dot_layout.addWidget(dot, 0, Qt.AlignmentFlag.AlignVCenter)
                self._nav_dots[page_id] = dot
        self._view_btn_group.idClicked.connect(self._on_view_switcher_clicked)

        # Older names, kept for callers and tests
        self._seg_btn_home = self._view_btn_home = self._nav_buttons["home"]
        self._seg_btn_rules = self._view_btn_rules = self._nav_buttons["rules"]
        self._nav_btn_cleanup = self._nav_buttons["cleanup"]
        self._nav_btn_settings = self._nav_buttons["settings"]

        side.addStretch()

        # The sorter's state, always in view. Click it to go to Home.
        self._status_chip = ui_widgets.ClickableFrame()
        self._status_chip.setProperty("role", "chip")
        status_row = QHBoxLayout(self._status_chip)
        status_row.setContentsMargins(12, 6, 12, 6)
        status_row.setSpacing(8)
        self._sorter_status_dot = ui_style.status_dot(COLORS["textFaint"])
        status_row.addWidget(self._sorter_status_dot)
        self._sorter_status_pill = ui_style.label("", "muted")
        status_row.addWidget(self._sorter_status_pill, 1)
        self._status_chip.clicked.connect(lambda: self.switch_view("home"))
        side.addWidget(self._status_chip)
        side.addSpacing(6)
        self._credit_lbl = ui_style.label(f"Version {about.__version__}\n{about.CREDIT}", "caption")
        self._credit_lbl.setContentsMargins(12, 0, 0, 0)
        self._credit_lbl.setObjectName("creditFooter")
        side.addWidget(self._credit_lbl)
        root.addWidget(self._sidebar)

        # ---- pages (a message bar on top, then the page) -------------
        content = QWidget(self)
        column = QVBoxLayout(content)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        self._notice = ui_widgets.NoticeBar(content)
        column.addWidget(self._notice)
        self._view_stack = QStackedWidget(content)
        column.addWidget(self._view_stack, 1)
        root.addWidget(content, 1)

        from onboarding import HomeDashboardWidget, SettingsWidget

        self._home_widget = HomeDashboardWidget(config=self._config, parent=self)
        self._home_widget.persona_changed.connect(self.set_active_persona)
        self._home_widget.status_changed.connect(self._apply_sidebar_status)
        self._home_widget.update_available.connect(self._on_update_available)
        self._home_widget.navigate_requested.connect(self.switch_view)
        self._view_stack.addWidget(self._padded(self._home_widget))

        self._customize_container = self._build_rules_page()
        self._view_stack.addWidget(self._padded(self._customize_container))

        self._remodel_widget = MachineRemodelWidget()
        self._view_stack.addWidget(self._padded(self._remodel_widget))
        self._remodel_widget.notice.connect(
            lambda text, kind, action_text, action: self.notify(
                text, kind, action_text, action, timeout_ms=15000 if action else 7000))  # time to click the action

        self._settings_widget = SettingsWidget(config=self._config, parent=self)
        self._settings_widget.persona_changed.connect(self._on_profile_changed)
        self._settings_widget.projects_page_toggled.connect(self._show_projects_nav)
        self._view_stack.addWidget(self._padded(self._settings_widget))

        from projects_page import ProjectsWidget

        self._projects_widget = ProjectsWidget(parent=self)
        self._projects_widget.notice.connect(
            lambda text, kind, action_text, action: self.notify(text, kind, action_text, action))
        self._view_stack.addWidget(self._padded(self._projects_widget))  # index follows PAGES

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(5000)
        self._status_timer.timeout.connect(self._refresh_sorter_status)
        self._status_timer.start()
        self._refresh_sorter_status()

    @staticmethod
    def _padded(widget: QWidget) -> QWidget:
        wrapper = QWidget()
        lay = QVBoxLayout(wrapper)
        lay.setContentsMargins(32, 28, 32, 24)
        lay.setSpacing(0)
        lay.addWidget(widget)
        return wrapper

    def _build_rules_page(self) -> QWidget:
        page = QWidget(parent=self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        header, actions = ui_style.page_header(
            "Rules",
            "Rules are checked from top to bottom. The first one that matches decides where a file goes.",
        )
        self._preset_lib_btn = ui_style.button("Add from library", None, "library",
                                               "Add ready-made rules for school, office, 3D printing and more")
        self._preset_lib_btn.clicked.connect(self.open_preset_library)
        actions.addWidget(self._preset_lib_btn, 0, Qt.AlignmentFlag.AlignTop)

        self._more_btn = QToolButton()
        self._more_btn.setProperty("role", "ghost")
        self._more_btn.setIcon(ui_icons.icon("more"))
        self._more_btn.setToolTip("More")
        self._more_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._more_btn.setFixedSize(36, 36)
        self._more_btn.setIconSize(QSize(18, 18))
        self._more_btn.setStyleSheet("padding: 0;")
        more_menu = QMenu(self._more_btn)
        self._import_action = more_menu.addAction(ui_icons.icon("import"), "Import rules...", self.import_rules)
        self._export_action = more_menu.addAction(ui_icons.icon("export"), "Export rules...", self.export_rules)
        more_menu.addSeparator()
        self._reset_action = more_menu.addAction(ui_icons.icon("reset"), "Reset to defaults...", self.reset_to_defaults)
        self._more_btn.setMenu(more_menu)
        actions.addWidget(self._more_btn, 0, Qt.AlignmentFlag.AlignTop)

        self._unsaved_chip = ui_style.badge("Unsaved changes", "warning")
        self._unsaved_chip.setVisible(False)
        chip_holder = QVBoxLayout()
        chip_holder.setContentsMargins(0, 7, 0, 0)
        chip_holder.addWidget(self._unsaved_chip)
        chip_holder.addStretch()
        actions.addLayout(chip_holder)

        self._save_btn = ui_style.button("Save changes", "primary")
        self._save_btn.setToolTip("Save changes (Ctrl+S)")
        self._save_btn.setEnabled(False)
        self._save_btn.clicked.connect(self.save_changes)
        actions.addWidget(self._save_btn, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(header)

        # Project mode (maker profile only)
        self._active_project_card = ActiveProjectCard()
        self._active_project_card.state_changed.connect(self._on_rules_or_project_changed)
        layout.addWidget(self._active_project_card)

        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        self._tabs.tabBar().setDrawBase(False)
        self._keyword_table = RuleTableWidget(rule_type="keyword")
        self._keyword_table.rules_changed.connect(self._on_rules_or_project_changed)
        self._keyword_table.rule_added.connect(self._on_rule_added)
        self._tabs.addTab(self._keyword_table, "Name rules")
        self._extension_table = RuleTableWidget(rule_type="extension")
        self._extension_table.rules_changed.connect(self._on_rules_or_project_changed)
        self._tabs.addTab(self._extension_table, "File types")
        layout.addWidget(self._tabs, 1)

        self._sandbox = LiveSandboxWidget()
        self._sandbox.matched.connect(self._on_sandbox_matched)
        self._sandbox.make_rule_requested.connect(self._on_make_rule)
        self._sandbox.compact_chosen.connect(self._on_sandbox_compact_chosen)
        layout.addWidget(self._sandbox)
        return page

    # ------------------------------------------------------------------
    # navigation
    # ------------------------------------------------------------------

    def _add_shortcuts(self) -> None:
        """Ctrl+1..5 switch pages, Ctrl+S saves rules, Ctrl+Z on Home undoes the last sort, F5 refreshes."""
        for index, page in enumerate(self.PAGES, start=1):
            QShortcut(QKeySequence(f"Ctrl+{index}"), self, activated=lambda p=page: self.switch_view(p))
        QShortcut(QKeySequence.StandardKey.Save, self, activated=self._save_shortcut)
        QShortcut(QKeySequence.StandardKey.Undo, self, activated=self._undo_shortcut)
        QShortcut(QKeySequence.StandardKey.Refresh, self, activated=self._refresh_shortcut)

    def current_page(self) -> str:
        return self.PAGES[self._view_stack.currentIndex()]

    def _save_shortcut(self) -> None:
        if getattr(self, "_dirty", False):
            self.save_changes()

    def _undo_shortcut(self) -> None:
        if self.current_page() == "home" and hasattr(self, "_home_widget"):
            self._home_widget.undo_last()

    def _refresh_shortcut(self) -> None:
        if self.current_page() == "home" and hasattr(self, "_home_widget"):
            self._home_widget.refresh_data()
            self._home_widget.refresh_waiting()

    def switch_view(self, view_name: str) -> None:
        """Show a page: 'home', 'rules' (or 'customize'), 'cleanup' (or 'remodel'), 'settings'."""
        name = (view_name or "").lower().strip()
        aliases = {
            "home": "home", "dashboard": "home", "0": "home",
            "rules": "rules", "customize": "rules", "1": "rules", "keyword": "rules", "keywords": "rules",
            "extension": "rules", "extensions": "rules",
            "cleanup": "cleanup", "remodel": "cleanup", "2": "cleanup",
            "settings": "settings", "3": "settings",
            "projects": "projects", "project": "projects", "4": "projects",
        }
        page = aliases.get(name, "rules")
        idx = self.PAGES.index(page)
        self._nav_buttons[page].setChecked(True)
        ui_motion.crossfade_to(self._view_stack, idx)
        if name in ("extension", "extensions"):
            self._tabs.setCurrentWidget(self._extension_table)
        elif name in ("keyword", "keywords"):
            self._tabs.setCurrentWidget(self._keyword_table)
        self._on_view_changed(idx)

    def switch_to_view(self, view_name: str) -> None:
        """Alias for switch_view."""
        self.switch_view(view_name)

    def _on_view_switcher_clicked(self, view_id: int) -> None:
        self.switch_view(self.PAGES[view_id])

    def _on_view_changed(self, view_id: int) -> None:
        if view_id == 0 and hasattr(self, "_home_widget"):
            self._home_widget.refresh_data()
        if view_id == 1:
            self._reload_rules_if_stale()
        if view_id == 3 and hasattr(self, "_settings_widget"):
            self._settings_widget.refresh_data()
        self._refresh_nav_dots()

    def _reload_rules_if_stale(self) -> None:
        """Show rules added elsewhere (from Home, or a learning ribbon) when Rules opens, unless you're mid-edit."""
        if getattr(self, "_dirty", False):
            return
        try:
            fresh = suite_data.load_sorter_config()
        except Exception:
            return

        def rules_of(cfg: dict[str, Any]) -> str:
            return json.dumps([cfg.get("keyword_rules", []), cfg.get("extension_rules", [])],
                              sort_keys=True, default=str)

        if rules_of(fresh) != rules_of(self._config):
            self.load_data()

    def _refresh_sorter_status(self) -> None:
        from onboarding import current_sorter_state

        state = current_sorter_state()
        # Home knows the sorter was just asked to start; don't flash "off" over its "Starting...".
        if state != "running" and getattr(self._home_widget, "_last_state", None) == "starting":
            state = "starting"
        self._apply_sidebar_status(state)

    def _apply_sidebar_status(self, state: str) -> None:
        text, color = {
            "running": ("Sorting is on", COLORS["success"]),
            "paused": ("Sorting is paused", COLORS["warning"]),
            "starting": ("Starting...", COLORS["info"]),
        }.get(state, ("Sorting is off", COLORS["textFaint"]))
        self._sorter_status_pill.setText(text)
        ui_style.set_status_dot(self._sorter_status_dot, color)
        self._status_chip.setAccessibleName(f"{text}. Open Home.")

    def _on_update_available(self, available: bool) -> None:
        self._update_available = bool(available)
        self._refresh_nav_dots()

    def _refresh_nav_dots(self) -> None:
        """Dots on the sidebar: unsaved rules on Rules, a waiting update on Home (while you're elsewhere)."""
        if not hasattr(self, "_nav_dots") or not hasattr(self, "_view_stack"):
            return
        unsaved = bool(getattr(self, "_dirty", False))
        self._nav_dots["rules"].setVisible(unsaved)
        rules_tip = self._nav_tips["rules"]
        self._nav_buttons["rules"].setToolTip(f"{rules_tip}: unsaved changes" if unsaved else rules_tip)

        update_waiting = self._update_available and self.current_page() != "home"
        self._nav_dots["home"].setVisible(update_waiting)
        home_tip = self._nav_tips["home"]
        self._nav_buttons["home"].setToolTip(f"{home_tip}: an update is available" if update_waiting else home_tip)

    def notify(
        self,
        text: str,
        kind: str = "info",
        action_text: str = "",
        action=None,
        timeout_ms: int = 7000,
    ) -> None:
        """Say something short on any page: a message bar under the top edge, with an optional action."""
        self._notice.show_message(text, kind, action_text, action, timeout_ms)

    # ------------------------------------------------------------------
    # "Try a filename" and the two rule lists
    # ------------------------------------------------------------------

    SANDBOX_COMPACT_BELOW = 720  # a window shorter than this hides the sandbox's examples and details

    def _update_tab_tips(self) -> None:
        for index, table in enumerate((self._keyword_table, self._extension_table)):
            count = table.rule_count()
            self._tabs.setTabToolTip(index, f"{count} {'rule' if count == 1 else 'rules'}")

    def _on_rule_added(self, rule: dict) -> None:
        """A rule was just added: say if it can never run, or offer to file what it already catches."""
        import rule_check
        rules = self._keyword_table.get_rules()
        index = next((i for i, r in enumerate(rules) if r is rule), len(rules) - 1)
        earlier = rule_check.shadowed_by(rules, index)
        if earlier:
            self.notify(f"“{rule.get('name')}” will never run: {', '.join(earlier)} is checked first "
                        "and takes its files.", "warning")
            return
        draft = dict(self.get_current_draft_config())
        draft["keyword_rules"] = rules
        found = rule_check.matching_downloads(draft, rule)
        if not found:
            return
        folder = str(rule.get("folder") or "its folder")

        def move_now() -> None:
            moved = rule_check.move_matching(draft, rule)
            self.notify(f"Filed {moved} file(s) into {folder}.", "success")

        noun = "file" if len(found) == 1 else "files"
        self.notify(f"{len(found)} {noun} in Downloads match this rule.", "info", "Move them", move_now,
                    timeout_ms=15000)

    def _on_sandbox_matched(self, match_type: str, rule_name: str, place: int) -> None:
        """Show which rule decided the typed name: select its row, on the tab it belongs to."""
        key = (match_type, rule_name, place)
        if key == self._last_highlight:
            return
        self._last_highlight = key
        table = {"keyword": self._keyword_table, "extension": self._extension_table}.get(match_type)
        if table is None or not rule_name or self.current_page() != "rules":
            return
        if table.highlight_rule(rule_name, place if place >= 0 else None):
            self._tabs.setCurrentWidget(table)

    def _on_make_rule(self, filename: str) -> None:
        """"Make a rule for this...": the rule editor filled in from the name. The new rule goes first."""
        import refile  # here, not at the top: it pulls in the ribbon code, which must wait for the QApplication

        if not filename:
            return
        draft = refile.rule_draft_for(filename, "", None)
        dialog = RuleEditDialog(rule=draft, rule_type="keyword", parent=self, title="Make a rule for this file name")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        rule = dialog.get_rule_data()
        rule["origin"] = "user"
        self._keyword_table.insert_rule(rule, 0)
        self._tabs.setCurrentWidget(self._keyword_table)
        self.notify("Rule added at the top of the list. Click Save changes to keep it.", "success")

    def _on_sandbox_compact_chosen(self, compact: bool) -> None:
        self._sandbox_pref = bool(compact)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._sandbox_pref is None and hasattr(self, "_sandbox"):
            self._sandbox.set_compact(self.height() < self.SANDBOX_COMPACT_BELOW)

    # ------------------------------------------------------------------
    # the window itself
    # ------------------------------------------------------------------

    def _window_settings(self) -> QSettings | None:
        """Where the window's size is kept: a small file next to the settings (no registry entries)."""
        if sorter is None:
            return None
        try:
            folder = Path(sorter.LOG_DIR)
            folder.mkdir(parents=True, exist_ok=True)
            return QSettings(str(folder / "window.ini"), QSettings.Format.IniFormat)
        except OSError:
            return None

    def _restore_window_state(self) -> None:
        settings = self._window_settings()
        if settings is None:
            return
        try:
            saved = settings.value("geometry")
            if saved and not isinstance(saved, str):
                self.restoreGeometry(saved)  # Qt moves it back on screen if the monitors changed
            compact = settings.value("sandbox_compact")
            if compact is not None:
                self._sandbox_pref = str(compact).lower() in ("true", "1")
                self._sandbox.set_compact(self._sandbox_pref)
        except Exception:
            logging.debug("Could not restore the window's size", exc_info=True)

    def _save_window_state(self) -> None:
        if self._discarded or not self.isVisible():
            return  # only a window you were looking at says where the window belongs
        settings = self._window_settings()
        if settings is None:
            return
        try:
            settings.setValue("geometry", self.saveGeometry())
            if self._sandbox_pref is not None:
                settings.setValue("sandbox_compact", self._sandbox_pref)
            settings.sync()
        except Exception:
            logging.debug("Could not save the window's size", exc_info=True)

    def reject(self) -> None:
        """Esc closes the window like its close button, so unsaved rules are asked about first.

        (A dialog's own Esc hides it without a close event, which skipped the question.)
        """
        if self._accepting_close:
            super().reject()
        else:
            self.close()

    def _show_projects_nav(self, shown: bool) -> None:
        """The Projects page is for people with project folders; the welcome choice or Settings hides it."""
        btn = self._nav_buttons.get("projects")
        if btn is None:
            return
        btn.setVisible(bool(shown))
        if not shown and btn.isChecked():
            self.switch_view("home")

    def _on_profile_changed(self, persona_id: str) -> None:
        """Settings changed the profile (which may add rules): reload and re-apply."""
        self.load_data()
        self.set_active_persona(persona_id)

    def set_active_persona(self, persona_id: str) -> None:
        """Show maker-only controls for the maker profile and update the try-it examples."""
        self._current_persona = persona_id
        is_maker = persona_id == "maker_3d" or bool(getattr(self, "_config", {}).get("show_maker_features"))
        if hasattr(self, "_active_project_card"):
            self._active_project_card.setVisible(is_maker)
        if hasattr(self, "_sandbox") and hasattr(self._sandbox, "set_persona"):
            self._sandbox.set_persona(persona_id)
        if hasattr(self, "_home_widget") and hasattr(self._home_widget, "set_persona"):
            self._home_widget.set_persona(persona_id)
        if hasattr(self, "_settings_widget"):
            self._settings_widget.set_persona(persona_id)

    def set_persona(self, persona_id: str) -> None:
        """Alias for set_active_persona."""
        self.set_active_persona(persona_id)

    def load_data(self) -> None:
        """Load persistent config and active project state into UI."""
        self._config = suite_data.load_sorter_config()
        self._show_projects_nav(self._config.get("show_projects_page", True))
        self._loaded_rule_names = {str(r.get("name") or "").lower() for r in self._config.get("keyword_rules", [])}
        self._keyword_table.load_rules(self._config.get("keyword_rules", []))
        self._extension_table.load_rules(self._config.get("extension_rules", []))

        active_proj = suite_data.get_active_project()
        self._active_project_card.set_state(active_proj)

        self.set_active_persona(self._config.get("user_persona", "custom"))

        self._on_rules_or_project_changed()
        self._set_dirty(False)

    def _set_dirty(self, dirty: bool) -> None:
        self._dirty = dirty
        if hasattr(self, "_save_btn"):
            self._save_btn.setEnabled(dirty)
            if dirty:
                self._save_btn.setText("Save changes")
        if hasattr(self, "_unsaved_chip"):
            self._unsaved_chip.setVisible(dirty)
        self._refresh_nav_dots()

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
        self._update_tab_tips()
        self._set_dirty(True)

    def config_to_save(self) -> dict[str, Any]:
        """The settings file as it is now, with this page's rules put in.

        Only the rules belong to this page. Everything else (Settings options,
        and name rules added elsewhere since the window opened, such as one
        you said Yes to in a learning ribbon) is kept as it is on disk.
        """
        draft = self.get_current_draft_config()
        current = suite_data.current_sorter_config(draft) if hasattr(suite_data, "current_sorter_config") else draft
        if current is draft:
            return draft
        current = copy.deepcopy(current)
        loaded = getattr(self, "_loaded_rule_names", set())
        mine = {str(r.get("name") or "").lower() for r in draft["keyword_rules"]}
        added_elsewhere = [
            r for r in current.get("keyword_rules", [])
            if str(r.get("name") or "").lower() not in loaded | mine
        ]
        current["keyword_rules"] = added_elsewhere + draft["keyword_rules"]
        current["extension_rules"] = draft["extension_rules"]
        return current

    def save_changes(self) -> bool:
        """Atomically persist draft configuration and active project state."""
        try:
            draft_config = self.config_to_save()
            ok = suite_data.save_sorter_config(draft_config)
            if not ok:
                QMessageBox.critical(self, "Couldn't save", "Your rules couldn't be saved. Check that the settings file isn't open in another program.")
                return False

            proj_state = self._active_project_card.get_state()
            suite_data.save_active_project(proj_state)

            self._config = draft_config
            self._loaded_rule_names = {str(r.get("name") or "").lower() for r in draft_config["keyword_rules"]}
            if len(draft_config["keyword_rules"]) != len(self._keyword_table.get_rules()):
                self._keyword_table.load_rules(draft_config["keyword_rules"])  # show rules added elsewhere
            self._set_dirty(False)
            self._save_btn.setText("Saved")
            return True
        except Exception as exc:
            QMessageBox.critical(self, "Couldn't save", f"Your rules couldn't be saved: {exc}")
            return False

    def open_preset_library(self) -> None:
        """Open Preset Library dialog and merge selected presets into draft configuration."""
        have = (r.get("name", "") for r in self.get_current_draft_config().get("keyword_rules", []))
        dlg = PresetLibraryDialog(parent=self, existing_names=have)
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
                one = added_total == 1
                self.notify(f"Added {added_total} rule{'' if one else 's'}. "
                            f"Click Save changes to keep {'it' if one else 'them'}.", "success")

    def export_rules(self) -> None:
        """Export current draft rules to a user-selected JSON file."""
        path, _ = QFileDialog.getSaveFileName(self, "Export rules", "download_sorter_rules.json", "Rule files (*.json)")
        if path:
            draft_config = self.get_current_draft_config()
            ok = suite_data.export_rules_to_file(path, draft_config)
            if ok:
                self.notify(f"Your rules were saved to {path}.", "success")

    def import_rules(self) -> None:
        """Import rules from a user-selected JSON file with merge or replace options."""
        path, _ = QFileDialog.getOpenFileName(self, "Import rules", "", "Rule files (*.json)")
        if not path:
            return
        try:
            imported = suite_data.import_rules_from_file(path)
        except Exception as exc:
            QMessageBox.critical(self, "Couldn't import", f"That file couldn't be read as a rules file: {exc}")
            return

        box = QMessageBox(self)
        box.setWindowTitle("Import rules")
        box.setText("Add the imported rules to yours, or replace yours with them?")
        add_btn = box.addButton("Add to my rules", QMessageBox.ButtonRole.AcceptRole)
        replace_btn = box.addButton("Replace my rules", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(add_btn)
        box.exec()
        clicked = box.clickedButton()
        if clicked not in (add_btn, replace_btn):
            return

        draft_config = self.get_current_draft_config()
        if clicked is add_btn:
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
        self.notify("The rules were imported. Click Save changes to keep them.", "success")

    def reset_to_defaults(self) -> None:
        """Prompt to reset configuration to factory defaults."""
        reply = QMessageBox.question(
            self,
            "Reset to defaults",
            "Replace all your rules with the built-in ones? Unsaved changes will be lost.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._config = suite_data.reset_sorter_config_to_defaults()
            self._keyword_table.load_rules(self._config.get("keyword_rules", []))
            self._extension_table.load_rules(self._config.get("extension_rules", []))
            self._on_rules_or_project_changed()
            self.notify("Your rules were reset. Click Save changes to keep this.", "success")

    def ask_to_save_changes(self) -> str:
        """"save", "discard" or "cancel" for unsaved rule changes."""
        if os.environ.get("DOWNLOAD_SORTER_NO_DIALOGS"):
            return "discard"  # automated tests
        box = QMessageBox(self)
        box.setWindowTitle("Save your rule changes?")
        box.setText("You changed some rules but didn't save them.")
        box.setInformativeText("Save them now, or close without saving?")
        save = box.addButton("Save changes", QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton("Don't save", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(save)
        box.exec()
        clicked = box.clickedButton()
        return "save" if clicked is save else "discard" if clicked is discard else "cancel"

    def closeEvent(self, event) -> None:
        if getattr(self, "_dirty", False):
            app = QApplication.instance()
            # Closing for an update: there's no time to ask, so keep the changes.
            updating = bool(app is not None and app.property(QUITTING_FOR_UPDATE))
            answer = "save" if updating else self.ask_to_save_changes()
            if answer == "cancel" or (answer == "save" and not self.save_changes()):
                event.ignore()
                return
        if hasattr(self, "_save_timer") and self._save_timer is not None:
            self._save_timer.stop()
        if hasattr(self, "_active_project_card") and hasattr(self._active_project_card, "_countdown_timer"):
            self._active_project_card._countdown_timer.stop()
        if hasattr(self, "_home_widget") and hasattr(self._home_widget, "_refresh_timer"):
            self._home_widget._refresh_timer.stop()
        if hasattr(self, "_status_timer"):
            self._status_timer.stop()
        self._save_window_state()
        self._accepting_close = True  # QDialog.closeEvent calls reject(); let that one through
        try:
            super().closeEvent(event)
        finally:
            self._accepting_close = False

    def hideEvent(self, event) -> None:
        if hasattr(self, "_save_timer") and self._save_timer is not None:
            self._save_timer.stop()
        if hasattr(self, "_active_project_card") and hasattr(self._active_project_card, "_countdown_timer"):
            self._active_project_card._countdown_timer.stop()
        if hasattr(self, "_home_widget") and hasattr(self._home_widget, "_refresh_timer"):
            self._home_widget._refresh_timer.stop()
        super().hideEvent(event)


def _start_background_sorter() -> None:
    """Start the tray sorter in the background if it is not already running (Windows)."""
    if sys.platform != "win32":
        return
    try:
        if suite_data.is_sorter_running():
            return
        import subprocess
        subprocess.Popen(
            suite_data._get_launch_command(["--tray"]),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        logging.exception("Could not start the background sorter after setup")


# Set on the app when it closes for an update (installer --quit, or Restart and install).
QUITTING_FOR_UPDATE = "downloadSorterQuittingForUpdate"


def _close_when_installer_asks(app) -> None:
    """Close this window process when ``DownloadSorter.exe --quit`` is run
    (the installer does that before replacing the app's files)."""
    try:
        import quit_signal
        listener = quit_signal.QuitListener()
    except Exception:
        return
    timer = QTimer(app)
    timer.setInterval(500)
    def quit_for_update() -> None:
        app.setProperty(QUITTING_FOR_UPDATE, True)  # windows save instead of asking
        app.quit()

    timer.timeout.connect(lambda: listener.requested() and quit_for_update())
    timer.start()
    app._quit_listener = listener  # keep it alive with the app


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
        _close_when_installer_asks(app)

    if args.welcome:
        from onboarding import WelcomeWizardDialog
        wizard = WelcomeWizardDialog()
        result = wizard.exec()
        if result == QDialog.DialogCode.Accepted:
            # The installer opens the wizard on its own; without this the sorter
            # would not run until the next sign-in.
            _start_background_sorter()
            dialog = RulesManagerDialog(initial_view="home")
            dialog.show()
            if owns_app:
                return app.exec()
        return 0

    init_view = "home" if (args.home or not args.tab) else args.tab
    dialog = RulesManagerDialog(initial_view=init_view)
    dialog.show()
    if owns_app:
        return app.exec()
    return 0


if __name__ == "__main__":
    sys.exit(main())
