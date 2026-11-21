"""Clean, beginner-friendly 3-tab PySide6 window: Home | Custom Rules | Settings."""

from __future__ import annotations

import copy
from datetime import datetime
import os
from pathlib import Path
import re
import subprocess
import sys
from collections.abc import Callable
import logging
from typing import Any

from PySide6.QtCore import QAbstractAnimation, QPoint, Qt, QTimer, Signal
from PySide6.QtCore import QUrl
from PySide6.QtGui import QColor, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from download_sorter.chrome_bridge import (
    EXTENSION_ID as CHROME_EXTENSION_ID,
    find_extension_dir,
    install_chrome_native_host,
    is_chrome_host_registered,
)
from download_sorter.config import (
    STARTER_PACK_LABELS,
    Config,
    Rule,
    carry_over_setup,
    export_setup_file,
    display_watch_folder,
    import_v2_config,
    stored_watch_folder,
)
from download_sorter.learning import dismiss_suggestion, get_rule_suggestions
from download_sorter.paths import AppPaths
from download_sorter.quick_switcher import normalize_hotkey, parse_hotkey
from download_sorter.service import SorterService
from download_sorter import update_check, updater
from download_sorter.ui.accessibility import name_controls_for_screen_readers
from download_sorter.ui.architecture_canvas import ArchitectureTab
from download_sorter.ui.text_scale import scaled_px
from download_sorter.ui.chrome_walkthrough import ChromeExtensionWalkthroughDialog
from download_sorter.ui.motion import (
    AnimatedBanner,
    AnimatedRibbon,
    CollapsibleRibbonBtn,
    ConfettiOverlayWidget,
    ElidedLabel,
    InteractiveRibbonBtn,
    SortingProgressHUD,
    SortyMascotWidget,
    WalkthroughDiagramCanvas,
    attach_press_feedback,
    breathe,
    install_global_button_feedback,
    fade_in,
    flash_rows,
    make_badge as _make_badge,
    make_vector_icon,
    refresh_style as _refresh_style,
    slide_toggle,
)
from download_sorter.ui.theme import STYLESHEET
from download_sorter.ui.toasts import ToastManager
from download_sorter.ui.workers import run_async


class UpgradeCarryOverDialog(QDialog):
    """Carry over an existing v2.8 config or exported v3 setup cleanly without losing rules."""

    def __init__(self, service: SorterService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.carried_config: Config | None = None
        self.carry_summary: dict[str, Any] = {}
        self.should_shift_now = False

        self.setWindowTitle("Carry Over Setup & Rules (Upgrade Assistant)")
        self.setMinimumWidth(600)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        hdr = QLabel("Carry Over Your Previous Setup")
        hdr.setStyleSheet("font-size: 16px; font-weight: 600; color: #F5F5F7;")
        root.addWidget(hdr)

        sub = QLabel(
            "Bring over your custom keyword rules, extension overrides, subfolder names, "
            "MakerWorld / G-Code / Haas CNC destinations, hold timer, and Folder Architecture.",
            self,
        )
        sub.setVisible(False)
        self.setToolTip(
            "Bring over custom rules, extension overrides, folder destinations, and Folder Architecture from a v2.8 config or exported v3 setup."
        )

        src_row = QHBoxLayout()
        src_row.setSpacing(8)
        self.edit_source = QLineEdit()
        v2_default = AppPaths.v2_config_file()
        if v2_default.exists():
            self.edit_source.setText(str(v2_default))
        self.edit_source.setPlaceholderText("Path to v2.8 config.json or exported v3 setup JSON...")
        self.edit_source.setToolTip("Path to an existing Download Sorter v2.8 config.json or exported v3 JSON file")
        self.edit_source.textChanged.connect(self._refresh_preview)
        src_row.addWidget(self.edit_source, 1)

        self.btn_browse = InteractiveRibbonBtn("Browse...")
        self.btn_browse.setToolTip("Browse for a v2.8 config.json or exported v3 setup JSON file")
        self.btn_browse.clicked.connect(self._on_browse)
        src_row.addWidget(self.btn_browse)
        root.addLayout(src_row)

        preview_card = QFrame()
        preview_card.setObjectName("SurfaceCard")
        pl = QVBoxLayout(preview_card)
        pl.setContentsMargins(14, 12, 14, 12)
        pl.setSpacing(6)
        preview_hdr = QLabel("Setup Preview")
        preview_hdr.setStyleSheet("font-weight: 600; color: #F5F5F7; font-size: 12px;")
        pl.addWidget(preview_hdr)
        self.lbl_preview = QLabel("")
        self.lbl_preview.setWordWrap(True)
        self.lbl_preview.setObjectName("MutedText")
        pl.addWidget(self.lbl_preview)
        root.addWidget(preview_card)

        mode_group_box = QFrame()
        mode_group_box.setObjectName("SurfaceCard")
        ml = QVBoxLayout(mode_group_box)
        ml.setContentsMargins(14, 12, 14, 12)
        ml.setSpacing(6)
        self.mode_group = QButtonGroup(self)
        self.rad_full = QRadioButton(
            "Carry over full setup, folders && rules (Recommended for upgraders)"
        )
        self.rad_full.setChecked(True)
        self.rad_merge = QRadioButton(
            "Merge rules && category overrides into my current setup"
        )
        self.mode_group.addButton(self.rad_full)
        self.mode_group.addButton(self.rad_merge)
        ml.addWidget(self.rad_full)
        ml.addWidget(self.rad_merge)
        self.chk_shift_now = QCheckBox(
            "Also shift existing managed files into carried-over folders right away"
        )
        ml.addWidget(self.chk_shift_now)
        root.addWidget(mode_group_box)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.btn_export = InteractiveRibbonBtn(
            "Export Current Setup...",
            make_vector_icon("folder", "#F5F5F7", 14),
        )
        self.btn_export.setObjectName("GhostButton")
        self.btn_export.setToolTip("Export your current rules, settings, and Folder Architecture to a portable JSON file")
        self.btn_export.clicked.connect(self._on_export_inline)
        btn_row.addWidget(self.btn_export)
        btn_row.addStretch(1)

        self.btn_cancel = InteractiveRibbonBtn("Cancel")
        self.btn_cancel.setToolTip("Close without changing your current configuration")
        self.btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_cancel)

        self.btn_apply = InteractiveRibbonBtn(
            "Carry Over Now",
            make_vector_icon("check", "#FFFFFF", 14),
        )
        self.btn_apply.setObjectName("PrimaryButton")
        self.btn_apply.setToolTip("Import the selected setup and optionally reorganize matching files now")
        self.btn_apply.clicked.connect(self._on_accept)
        btn_row.addWidget(self.btn_apply)
        root.addLayout(btn_row)

        self._refresh_preview()

    def _on_browse(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            "Select v2.8 config.json or Exported Setup File",
            str(Path.home()),
            "JSON Files (*.json);;All Files (*)",
        )
        if chosen:
            self.edit_source.setText(chosen)

    def _refresh_preview(self) -> None:
        raw_path = self.edit_source.text().strip()
        if not raw_path:
            self.lbl_preview.setText(
                "No setup file selected yet. Browse to your old Download-Sorter config.json or exported v3 setup JSON."
            )
            self.btn_apply.setEnabled(False)
            return
        src = Path(raw_path).expanduser()
        if not src.exists():
            self.lbl_preview.setText(f"File not found: {src}")
            self.btn_apply.setEnabled(False)
            return
        try:
            _cfg, summary = carry_over_setup(src, self.service.config, merge_rules_only=False)
            hold_txt = (
                f"ON ({summary['hold_3mf_days']} days)" if summary["hold_3mf_enabled"] else "OFF"
            )
            schema_txt = f"v{summary['source_schema']} config"
            self.lbl_preview.setText(
                f"Detected {schema_txt} · {summary['rules_imported']} custom rule(s) · "
                f"{summary['categories_imported']} extension rule(s) · "
                f"{summary['subfolders_imported']} type subfolder(s)\n"
                f"Protected .3MF Hold: {hold_txt} · Watch folder: {display_watch_folder(str(summary['watch_folder']))}\n"
                f"Resulting Folder Architecture: {summary['tier_count']} tier(s), "
                f"{summary['arch_nodes']} folder node(s)."
            )
            self.btn_apply.setEnabled(True)
        except Exception as exc:
            self.lbl_preview.setText(f"Could not read setup file: {exc}")
            self.btn_apply.setEnabled(False)

    def _on_export_inline(self) -> None:
        out_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Complete Setup & Folder Architecture",
            str(Path.home() / "DownloadSorter-Setup-Backup.json"),
            "JSON Files (*.json)",
        )
        if not out_path:
            return
        try:
            export_setup_file(self.service.config, Path(out_path))
            QMessageBox.information(
                self,
                "Setup Exported",
                f"Saved your rules, preferences, and Folder Architecture to:\n{out_path}",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Export Failed", str(exc))

    def _on_accept(self) -> None:
        src = Path(self.edit_source.text().strip()).expanduser()
        if not src.exists():
            QMessageBox.warning(self, "File Not Found", f"No setup file found at:\n{src}")
            return
        try:
            cfg, summary = carry_over_setup(
                src,
                self.service.config,
                merge_rules_only=self.rad_merge.isChecked(),
            )
            self.carried_config = cfg
            self.carry_summary = summary
            self.should_shift_now = self.chk_shift_now.isChecked()
            self.accept()
        except Exception as exc:
            QMessageBox.critical(self, "Carry Over Failed", str(exc))
from download_sorter.windows_integration import (
    is_context_menu_enabled,
    is_startup_enabled,
    set_context_menu_enabled,
    set_startup_enabled,
)


class RuleEditorDialog(QDialog):
    """Modal dialog for adding or editing a single custom sorting rule."""

    def __init__(self, rule: Rule | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit Custom Rule" if rule else "Create New Custom Rule")
        self.setMinimumWidth(460)
        self._base_id = rule.id if rule else ""

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Tell Download Sorter which files belong together and which folder inside Downloads to move them into.",
            self,
        )
        intro.setVisible(False)
        self.setToolTip(
            "Define matching filenames, websites, extensions, and the target folder inside Downloads."
        )

        form = QFormLayout()

        self.edit_name = QLineEdit(rule.name if rule else "")
        self.edit_name.setPlaceholderText("e.g. Tax Receipts, Client PDFs, Class Notes")
        self.edit_dest = QLineEdit(rule.destination if rule else r"Documents\Custom")
        self.edit_dest.setPlaceholderText(r"e.g. Documents\Taxes\{year} or Web\{host}")
        self.edit_keywords = QLineEdit(", ".join(rule.keywords) if rule else "")
        self.edit_keywords.setPlaceholderText("e.g. syllabus, lab_report, invoice (comma-separated)")
        self.edit_domains = QLineEdit(", ".join(rule.domains) if rule else "")
        self.edit_domains.setPlaceholderText("Optional website, e.g. canvas.edu, makerworld.com")
        self.edit_exts = QLineEdit(", ".join(rule.extensions) if rule else "")
        self.edit_exts.setPlaceholderText("Optional file types, e.g. .pdf, .docx (blank = any)")
        self.edit_content_keywords = QLineEdit(", ".join(rule.content_keywords) if rule else "")
        self.edit_content_keywords.setPlaceholderText("Optional PDF text, e.g. Invoice, Packing Slip")
        self.edit_exclude_keywords = QLineEdit(", ".join(rule.exclude_keywords) if rule else "")
        self.edit_exclude_keywords.setPlaceholderText("Optional words to exclude, e.g. draft, sample")
        self.edit_regex = QLineEdit(rule.regex_pattern if rule else "")
        self.edit_regex.setPlaceholderText(r"Optional regex, e.g. ^INV-\d{4} or (?P<client>\w+)_")
        self.combo_match_mode = QComboBox()
        self.combo_match_mode.addItem("Match ANY condition (OR)", "any")
        self.combo_match_mode.addItem("Match ALL conditions (AND)", "all")
        if rule and rule.match_mode == "all":
            self.combo_match_mode.setCurrentIndex(1)

        bounds_row = QHBoxLayout()
        bounds_row.setSpacing(6)
        self.spin_min_mb = QDoubleSpinBox()
        self.spin_min_mb.setRange(0.0, 100000.0)
        self.spin_min_mb.setDecimals(1)
        self.spin_min_mb.setSuffix(" MB min")
        self.spin_min_mb.setValue(rule.min_size_mb if rule else 0.0)
        self.spin_max_mb = QDoubleSpinBox()
        self.spin_max_mb.setRange(0.0, 100000.0)
        self.spin_max_mb.setDecimals(1)
        self.spin_max_mb.setSuffix(" MB max")
        self.spin_max_mb.setValue(rule.max_size_mb if rule else 0.0)
        self.spin_min_age = QSpinBox()
        self.spin_min_age.setRange(0, 3650)
        self.spin_min_age.setSuffix(" d old")
        self.spin_min_age.setValue(rule.min_age_days if rule else 0)
        bounds_row.addWidget(self.spin_min_mb)
        bounds_row.addWidget(self.spin_max_mb)
        bounds_row.addWidget(self.spin_min_age)

        self.edit_rename = QLineEdit(rule.rename_template if rule else "")
        self.edit_rename.setPlaceholderText("Optional rename, e.g. {year}-{month}_{stem_slug}.{ext}")
        self.chk_auto_unzip = QCheckBox("Automatically extract .zip archives matched by this rule")
        self.chk_auto_unzip.setChecked(rule.auto_unzip if rule else False)
        self.chk_enabled = QCheckBox("Rule is active")
        self.chk_enabled.setChecked(rule.enabled if rule else True)

        form.addRow("Rule Name:", self.edit_name)
        form.addRow("Move Into Folder:", self.edit_dest)
        form.addRow("Matches Words in Filename:", self.edit_keywords)
        form.addRow("Matches Website:", self.edit_domains)
        form.addRow("Only These File Types:", self.edit_exts)
        form.addRow("", self.chk_enabled)
        layout.addLayout(form)

        from download_sorter.ui.emblems import TokenChipBar

        has_advanced = bool(
            rule
            and (
                rule.exclude_keywords
                or rule.regex_pattern
                or rule.match_mode == "all"
                or rule.min_size_mb > 0
                or rule.max_size_mb > 0
                or rule.min_age_days > 0
                or rule.rename_template
                or rule.content_keywords
                or rule.auto_unzip
            )
        )
        btn_adv = QPushButton(
            "▾ Advanced Conditions (Exclude, Regex, Size/Age, Rename)"
            if has_advanced
            else "▸ Advanced Conditions (Exclude, Regex, Size/Age, Rename)"
        )
        btn_adv.setObjectName("GhostButton")
        btn_adv.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_adv.setToolTip("Expand or collapse advanced rule filters (exclusions, regex, PDF text, file size/age, renaming)")
        layout.addWidget(btn_adv)

        adv_box = QFrame()
        adv_box.setObjectName("SurfaceCard")
        adv_form = QFormLayout(adv_box)
        adv_form.setContentsMargins(12, 10, 12, 10)
        adv_form.addRow("Exclude Words:", self.edit_exclude_keywords)
        adv_form.addRow("Regex Pattern:", self.edit_regex)
        adv_form.addRow("Matches Inside PDF:", self.edit_content_keywords)
        adv_form.addRow("Condition Logic:", self.combo_match_mode)
        adv_form.addRow("Size / Age (0 = off):", bounds_row)
        rename_col = QVBoxLayout()
        rename_col.setSpacing(4)
        rename_col.addWidget(self.edit_rename)
        rename_col.addWidget(TokenChipBar(self.edit_rename))
        adv_form.addRow("Rename Template:", rename_col)
        adv_form.addRow("", self.chk_auto_unzip)
        adv_box.setVisible(has_advanced)
        layout.addWidget(adv_box)

        def _toggle_adv() -> None:
            now_vis = not adv_box.isVisible()
            adv_box.setVisible(now_vis)
            btn_adv.setText(
                "▾ Advanced Conditions (Exclude, Regex, Size/Age, Rename)"
                if now_vis
                else "▸ Advanced Conditions (Exclude, Regex, Size/Age, Rename)"
            )
            self.adjustSize()

        btn_adv.clicked.connect(_toggle_adv)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn is not None:
            ok_btn.setToolTip("Save this custom sorting rule")
        cancel_btn = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_btn is not None:
            cancel_btn.setToolTip("Discard changes to this rule")
        buttons.accepted.connect(self._validate_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _validate_accept(self) -> None:
        if not self.edit_name.text().strip():
            QMessageBox.warning(self, "Missing Name", "Please give this rule a name.")
            return
        if not self.edit_dest.text().strip():
            QMessageBox.warning(self, "Missing Folder", "Please enter a destination folder name.")
            return
        self.accept()

    def build_rule(self) -> Rule:
        split_csv = lambda s: [x.strip() for x in s.split(",") if x.strip()]
        return Rule(
            id=self._base_id,
            name=self.edit_name.text().strip(),
            destination=self.edit_dest.text().strip(),
            keywords=split_csv(self.edit_keywords.text()),
            domains=split_csv(self.edit_domains.text()),
            extensions=split_csv(self.edit_exts.text()),
            content_keywords=split_csv(self.edit_content_keywords.text()),
            exclude_keywords=split_csv(self.edit_exclude_keywords.text()),
            regex_pattern=self.edit_regex.text().strip(),
            match_mode=str(self.combo_match_mode.currentData() or "any"),
            min_size_mb=float(self.spin_min_mb.value()),
            max_size_mb=float(self.spin_max_mb.value()),
            min_age_days=int(self.spin_min_age.value()),
            rename_template=self.edit_rename.text().strip(),
            auto_unzip=self.chk_auto_unzip.isChecked(),
            enabled=self.chk_enabled.isChecked(),
        )


class DetoxDialog(QDialog):
    """Downloads Detox dialog for reviewing old installers (>30d) and empty folders."""

    def __init__(self, service: SorterService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from download_sorter.ui.emblems import DetoxEmblemWidget

        self.service = service
        self.setWindowTitle("Downloads Detox — Age-Based Housekeeping")
        self.setMinimumSize(620, 440)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        hdr = QHBoxLayout()
        hdr.setSpacing(12)
        emblem = DetoxEmblemWidget(size=48, active=True)
        self.lbl_summary = QLabel("Scanning for installers older than 30 days and empty folders...")
        self.lbl_summary.setObjectName("MutedText")
        self.lbl_summary.setWordWrap(True)
        hdr.addWidget(emblem)
        hdr.addWidget(self.lbl_summary, 1)
        root.addLayout(hdr)

        self.tbl_installers = QTableWidget(0, 3)
        self.tbl_installers.setHorizontalHeaderLabels(["Installer", "Age (Days)", "Size"])
        self.tbl_installers.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tbl_installers.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        root.addWidget(self.tbl_installers, 1)

        btn_row = QHBoxLayout()
        self.btn_recycle = QPushButton("Recycle Selected Installers")
        self.btn_recycle.setObjectName("DangerButton")
        self.btn_recycle.setToolTip("Send the highlighted old installer files (.exe/.msi) to the Recycle Bin")
        self.btn_recycle.clicked.connect(self._on_recycle_installers)
        self.btn_prune = QPushButton("Prune Empty Folders")
        self.btn_prune.setToolTip("Remove empty subfolders discovered inside Downloads")
        self.btn_prune.clicked.connect(self._on_prune_folders)
        btn_close = QPushButton("Close")
        btn_close.setToolTip("Close the Downloads Detox dialog")
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(self.btn_recycle)
        btn_row.addWidget(self.btn_prune)
        btn_row.addStretch(1)
        btn_row.addWidget(btn_close)
        root.addLayout(btn_row)

        self._empty_folders: list[str] = []
        self._reload()

    def _reload(self) -> None:
        scan = self.service.scan_detox_now(old_days=30)
        installers = scan.get("old_installers") or []
        self._empty_folders = list(scan.get("empty_folders") or [])
        total_mb = (int(scan.get("total_bytes") or 0)) / (1024 * 1024)
        self.lbl_summary.setText(
            f"Found {len(installers)} old installer(s) ({total_mb:.1f} MB) and "
            f"{len(self._empty_folders)} empty folder(s)."
        )
        self.btn_prune.setEnabled(bool(self._empty_folders))
        self.tbl_installers.setRowCount(len(installers))
        for idx, item in enumerate(installers):
            name_it = QTableWidgetItem(str(item["name"]))
            name_it.setData(Qt.ItemDataRole.UserRole, str(item["path"]))
            name_it.setToolTip(str(item["path"]))
            age_it = QTableWidgetItem(f"{item['age_days']}d")
            sz_it = QTableWidgetItem(f"{int(item['size_bytes']) / (1024 * 1024):.1f} MB")
            self.tbl_installers.setItem(idx, 0, name_it)
            self.tbl_installers.setItem(idx, 1, age_it)
            self.tbl_installers.setItem(idx, 2, sz_it)

    def _on_recycle_installers(self) -> None:
        rows = {idx.row() for idx in self.tbl_installers.selectionModel().selectedRows()}
        if not rows:
            rows = set(range(self.tbl_installers.rowCount()))
        paths = [
            str(self.tbl_installers.item(r, 0).data(Qt.ItemDataRole.UserRole))
            for r in sorted(rows)
            if self.tbl_installers.item(r, 0) is not None
        ]
        if not paths:
            return
        self.service.delete_detox_installers(paths, use_recycle_bin=True)
        self._reload()

    def _on_prune_folders(self) -> None:
        if self._empty_folders:
            self.service.prune_detox_folders(self._empty_folders)
            self._reload()


def _refresh_style(widget: QWidget) -> None:
    """Re-polish widget after dynamic property mutation for PySide6 QSS rules."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def _relayout_up(widget: QWidget) -> None:
    """Recompute the layouts above ``widget`` now.

    A page that is not the current tab does not lay itself out again when a child
    is hidden, so the window kept the old, larger minimum size.
    """
    parent = widget.parentWidget()
    while parent is not None:
        if parent.layout() is not None:
            parent.layout().invalidate()
        parent.updateGeometry()
        if parent.isWindow():
            break
        parent = parent.parentWidget()


def _style_table(table: QTableWidget) -> None:
    """Apply clean borderless-grid Argon table chrome and 40px row spacing."""
    table.setShowGrid(False)
    table.setAlternatingRowColors(True)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(scaled_px(40))
    table.horizontalHeader().setHighlightSections(False)


class WalkthroughDialog(QDialog):
    """Calm Apple-minimal 3-step onboarding modal with Sorty and finite entrance transitions."""

    def __init__(self, watch_root: Path, hold_days: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("WalkthroughModal")
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(600, 440)
        self._drag_offset: QPoint | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)

        container = QFrame(self)
        container.setObjectName("ModalContainer")
        shadow = QGraphicsDropShadowEffect(container)
        shadow.setBlurRadius(32)
        shadow.setOffset(0, 10)
        shadow.setColor(QColor(0, 0, 0, 165))
        container.setGraphicsEffect(shadow)
        outer.addWidget(container)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(16)

        # Clean unboxed Apple header: calm Sorty + eyebrow + title + concise summary
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(14)

        self.mascot_widget = SortyMascotWidget(size=58, parent=container)
        self.mascot_widget.setToolTip("Click Sorty for a tip & cute animation!")
        self.mascot_widget.clicked.connect(
            lambda tip: self.lbl_mascot_speech.setText(f'Sorty Tip: "{tip}"')
        )
        header_row.addWidget(self.mascot_widget, 0, Qt.AlignmentFlag.AlignTop)

        header_text = QVBoxLayout()
        header_text.setContentsMargins(0, 0, 0, 0)
        header_text.setSpacing(4)

        self.lbl_step_badge = QLabel("STEP 1 OF 3")
        self.lbl_step_badge.setStyleSheet(
            "color: #0A84FF; font-size: 11px; font-weight: 700; letter-spacing: 0.7px;"
        )
        header_text.addWidget(self.lbl_step_badge)

        self.lbl_banner_title = QLabel("Welcome to Download Sorter")
        self.lbl_banner_title.setWordWrap(True)
        self.lbl_banner_title.setStyleSheet("font-size: 18px; font-weight: 600; color: #F5F5F7;")
        header_text.addWidget(self.lbl_banner_title)

        self.lbl_mascot_speech = QLabel("")
        self.lbl_mascot_speech.setWordWrap(True)
        self.lbl_mascot_speech.setStyleSheet("color: #98989D; font-size: 13px; line-height: 1.35;")
        header_text.addWidget(self.lbl_mascot_speech)

        header_row.addLayout(header_text, 1)
        layout.addLayout(header_row)

        # Calm Recessed Diagram Stage; step 2 shows the rule pack chooser in its place.
        self.stage_canvas = WalkthroughDiagramCanvas(container)
        self.stage_canvas.setMinimumSize(500, 148)
        self.packs_panel = self._build_packs_panel()
        self.stage = QStackedWidget()
        self.stage.setFixedHeight(148)
        self.stage.addWidget(self.stage_canvas)
        self.stage.addWidget(self.packs_panel)
        layout.addWidget(self.stage)

        # Concise 1-line footnote per step (unboxed, zero nested border clutter)
        self.pages = QStackedWidget()
        self.pages.setFixedHeight(28)
        self.pages.addWidget(self._build_page(f"Watching top-level only: {watch_root}"))
        self.pages.addWidget(
            self._build_page(
                "Everything else is still sorted by file type · Every move can be undone"
            )
        )
        self.pages.addWidget(
            self._build_page(
                "Notifications slide in at the top-right · Reopen '? Guide' anytime in the toolbar"
            )
        )
        layout.addWidget(self.pages)

        layout.addStretch(1)

        # Footer: StepDots on left, Skip / Back / Next on right
        footer = QHBoxLayout()
        footer.setSpacing(8)
        self._dots: list[QLabel] = []
        for _ in range(3):
            dot = QLabel("")
            dot.setObjectName("StepDot")
            self._dots.append(dot)
            footer.addWidget(dot)

        footer.addStretch(1)

        self.btn_chrome_setup = InteractiveRibbonBtn(
            "Chrome Guide",
            make_vector_icon("help", "#69E6AD", 14),
        )
        self.btn_chrome_setup.setObjectName("GhostButton")
        self.btn_chrome_setup.setToolTip("Open the 4-step picture guide to set up the Chrome extension")
        self.btn_chrome_setup.clicked.connect(self._on_open_chrome_guide_clicked)
        self.btn_chrome_setup.setVisible(False)

        self.btn_skip = InteractiveRibbonBtn("Skip")
        self.btn_skip.setObjectName("GhostButton")
        self.btn_skip.setToolTip("Skip the onboarding guide and go straight to Download Sorter")
        self.btn_skip.clicked.connect(self.accept)

        self.btn_back = InteractiveRibbonBtn("Back", make_vector_icon("arrow_left", "#F5F5F7", 15))
        self.btn_back.setToolTip("Go to previous step")
        self.btn_back.clicked.connect(lambda: self._go(self.pages.currentIndex() - 1))

        self.btn_next = InteractiveRibbonBtn("Next", make_vector_icon("arrow_right", "#FFFFFF", 15))
        self.btn_next.setObjectName("PrimaryButton")
        self.btn_next.setToolTip("Continue to next walkthrough step or finish setup")
        self.btn_next.clicked.connect(self._on_next_clicked)

        footer.addWidget(self.btn_chrome_setup)
        footer.addWidget(self.btn_skip)
        footer.addWidget(self.btn_back)
        footer.addWidget(self.btn_next)
        layout.addLayout(footer)

        self._step_meta = [
            (
                "STEP 1 OF 3",
                "Top-Level Downloads, Kept Tidy",
                "I'm Sorty — loose downloads flow into organized category folders while your existing subfolders stay untouched.",
            ),
            (
                "STEP 2 OF 3",
                "What Do You Download Most?",
                "Tick any that fit and I'll add rules for them. You can add or remove rule packs later on the Custom Rules page.",
            ),
            (
                "STEP 3 OF 3",
                "Custom Rules & Top-Right Side Ribbons",
                "Match filenames or URLs in Rules, and glance at sorted files via the top-right Side Ribbon with pause-on-hover.",
            ),
        ]
        self._go(0)

    def _build_packs_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("WalkthroughPacks")
        grid = QGridLayout(panel)
        grid.setContentsMargins(4, 6, 4, 6)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(10)
        self.pack_boxes: dict[str, QCheckBox] = {}
        for index, (key, (label, description)) in enumerate(STARTER_PACK_LABELS.items()):
            box = QCheckBox(label.replace("&", "&&"))  # a single & would mark a shortcut key
            box.setAccessibleName(label)
            box.setToolTip(description)
            box.setAccessibleDescription(description)
            self.pack_boxes[key] = box
            grid.addWidget(box, index // 2, index % 2)
        return panel

    def selected_packs(self) -> list[str]:
        """The rule packs ticked on step 2, in the order they are offered."""
        return [key for key, box in self.pack_boxes.items() if box.isChecked()]

    def _build_page(self, highlight_text: str) -> QWidget:
        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(2, 0, 2, 0)
        pl.setSpacing(0)
        pill = QLabel(highlight_text)
        pill.setWordWrap(True)
        pill.setStyleSheet("color: #8E8E93; font-size: 12px;")
        pl.addWidget(pill)
        return page

    def _go(self, index: int) -> None:
        idx = max(0, min(2, index))
        self.pages.setCurrentIndex(idx)
        self.stage_canvas.set_step(idx)
        self.stage.setCurrentWidget(self.packs_panel if idx == 1 else self.stage_canvas)
        self.mascot_widget.set_step(idx)

        badge_txt, title_txt, speech_txt = self._step_meta[idx]
        self.lbl_step_badge.setText(badge_txt)
        self.lbl_banner_title.setText(title_txt)
        self.lbl_mascot_speech.setText(speech_txt)

        for i, dot in enumerate(self._dots):
            dot.setProperty("active", "true" if i == idx else "false")
            dot.setProperty("done", "true" if i < idx else "false")
            _refresh_style(dot)

        self.btn_back.setEnabled(idx > 0)
        self.btn_chrome_setup.setVisible(idx == 2)
        if idx == 2:
            self.btn_next.setText("Finish Guide")
            self.btn_next.setIcon(make_vector_icon("check", "#FFFFFF", 15))
        else:
            self.btn_next.setText("Next")
            self.btn_next.setIcon(make_vector_icon("arrow_right", "#FFFFFF", 15))

    def _on_open_chrome_guide_clicked(self) -> None:
        parent = self.parentWidget()
        self.accept()
        if parent is not None and hasattr(parent, "open_chrome_walkthrough"):
            parent.open_chrome_walkthrough()

    def _on_next_clicked(self) -> None:
        idx = self.pages.currentIndex()
        if idx < 2:
            self._go(idx + 1)
        else:
            self.accept()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_offset = None
        super().mouseReleaseEvent(event)


def _make_surface_card(
    title: str,
    subtitle: str | QWidget = "",
    right_widget: QWidget | None = None,
) -> tuple[QFrame, QVBoxLayout]:
    """Create a borderless-interior Apple-inspired surface card with a 1px hairline divider."""
    card = QFrame()
    card.setObjectName("SurfaceCard")
    root = QVBoxLayout(card)
    root.setContentsMargins(16, 14, 16, 14)
    root.setSpacing(10)

    hdr_row = QHBoxLayout()
    hdr_row.setSpacing(10)
    title_col = QVBoxLayout()
    title_col.setSpacing(2)
    lbl_title = QLabel(title)
    lbl_title.setObjectName("CardTitle")
    title_col.addWidget(lbl_title)
    if isinstance(subtitle, QWidget):
        subtitle.setParent(card)
        subtitle.setVisible(False)
        tip_txt = subtitle.text() if isinstance(subtitle, QLabel) else ""
        if tip_txt:
            lbl_title.setToolTip(tip_txt)
            card.setToolTip(tip_txt)
    elif subtitle:
        lbl_title.setToolTip(subtitle)
        card.setToolTip(subtitle)
    hdr_row.addLayout(title_col, 1)
    if right_widget is not None:
        hdr_row.addWidget(right_widget)
    root.addLayout(hdr_row)

    divider = QFrame()
    divider.setObjectName("CardDivider")
    root.addWidget(divider)
    return card, root


def _format_bytes(size_bytes: int) -> str:
    """Format byte counts concisely for Apple-style storage pills and table columns."""
    value = float(max(0, int(size_bytes)))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024.0 or unit == "TB":
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}" if value < 100 else f"{value:.0f} {unit}"
        value /= 1024.0
    return "0 B"


def _reveal_in_explorer(target: Path) -> None:
    """Open Windows Explorer selecting ``target`` when present, or its parent folder."""
    if sys.platform != "win32":
        return
    try:
        if target.exists():
            subprocess.Popen(["explorer.exe", f"/select,{target}"])
        elif target.parent.exists() and hasattr(os, "startfile"):
            os.startfile(str(target.parent))
    except Exception as exc:
        logging.debug("Could not reveal %s in Explorer: %s", target, exc)


def _dup_group_key(item: dict[str, Any]) -> tuple[int, str]:
    """Group quarantined copies sharing exact size and normalized base filename stem."""
    stem = Path(str(item["name"])).stem
    normalized = re.sub(r"(?:\s+\(\d+\)|\s+\(Restored(?:\s+\d+)?\))$", "", stem, flags=re.IGNORECASE)
    return (int(item.get("size_bytes", 0)), normalized.lower())


class DuplicateManagerDialog(QDialog):
    """Native Apple Sequoia dialog to inspect and safely delete files quarantined in ``_Duplicates``."""

    duplicates_changed = Signal()

    def __init__(self, service: SorterService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self._entries: list[dict[str, Any]] = []
        self._checkboxes: list[QCheckBox] = []
        self._syncing_checks = False

        self.setWindowTitle("Manage Duplicate Files")
        self.setMinimumSize(780, 460)
        self.resize(860, 540)
        self.setStyleSheet(STYLESHEET)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)

        hdr_row = QHBoxLayout()
        hdr_row.setSpacing(12)
        title_col = QVBoxLayout()
        title_col.setSpacing(3)
        self.lbl_title = QLabel("Duplicate Files")
        self.lbl_title.setObjectName("CardTitle")
        dup_sub_text = (
            "Duplicate or browser-numbered downloads isolated inside _Duplicates\\. "
            "Your sorted originals stay untouched until you choose Replace Old, Delete & Locate Old, or Keep Both."
        )
        lbl_sub = QLabel(dup_sub_text, self)
        lbl_sub.setVisible(False)
        self.setToolTip(dup_sub_text)
        self.lbl_title.setToolTip(dup_sub_text)
        title_col.addWidget(self.lbl_title)
        hdr_row.addLayout(title_col, 1)

        self.lbl_reclaim_pill = QLabel("0 B")
        self.lbl_reclaim_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_reclaim_pill.setToolTip("Total size of selected or quarantined duplicate files")
        hdr_row.addWidget(self.lbl_reclaim_pill)
        root.addLayout(hdr_row)

        # Smart selection toolbar
        tools = QHBoxLayout()
        tools.setSpacing(8)
        self.btn_select_all = InteractiveRibbonBtn("Select All")
        self.btn_select_all.setToolTip("Check all duplicate files in the table")
        self.btn_select_all.clicked.connect(self.select_all)
        self.btn_keep_newest = InteractiveRibbonBtn("Keep Newest Copy")
        self.btn_keep_newest.setToolTip(
            "Leave the newest quarantined copy in each group unchecked and select older duplicate copies"
        )
        self.btn_keep_newest.clicked.connect(self.select_all_except_newest)
        self.btn_clear_sel = InteractiveRibbonBtn("Clear")
        self.btn_clear_sel.setObjectName("GhostButton")
        self.btn_clear_sel.setToolTip("Uncheck all selected duplicate files")
        self.btn_clear_sel.clicked.connect(self.clear_selection)
        tools.addWidget(self.btn_select_all)
        tools.addWidget(self.btn_keep_newest)
        tools.addWidget(self.btn_clear_sel)
        tools.addStretch(1)
        root.addLayout(tools)

        self.tbl_dups = QTableWidget(0, 6)
        self.tbl_dups.setHorizontalHeaderLabels(
            ["", "Duplicate File", "Original Copy", "Quarantine Folder", "Modified", "Size"]
        )
        _style_table(self.tbl_dups)
        hdr = self.tbl_dups.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self.tbl_dups.setColumnWidth(0, scaled_px(38))
        self.tbl_dups.setColumnWidth(3, scaled_px(136))
        self.tbl_dups.setColumnWidth(4, scaled_px(126))
        self.tbl_dups.setColumnWidth(5, scaled_px(84))
        self.tbl_dups.cellClicked.connect(self._on_cell_clicked)
        self.tbl_dups.itemSelectionChanged.connect(self._update_summary)
        self.tbl_dups.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tbl_dups.customContextMenuRequested.connect(self._show_context_menu)
        root.addWidget(self.tbl_dups, 1)

        self.lbl_empty = QLabel("No quarantined duplicates right now — your _Duplicates folder is clean!")
        self.lbl_empty.setObjectName("MutedText")
        self.lbl_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.lbl_empty)

        resolve_bar = QHBoxLayout()
        resolve_bar.setSpacing(8)
        self.btn_replace_old = InteractiveRibbonBtn("Replace Old")
        self.btn_replace_old.setToolTip(
            "Recycle the matched older file inside Downloads and replace it with this newer duplicate"
        )
        self.btn_replace_old.setStyleSheet(
            "QPushButton { background: rgba(255, 159, 10, 32); color: #FF9F0A; "
            "border: 1px solid rgba(255, 159, 10, 120); border-radius: 7px; font-weight: 600; padding: 6px 12px; }"
            "QPushButton:hover { background: rgba(255, 159, 10, 56); }"
            "QPushButton:disabled { background: #1C1C1E; color: #636366; border-color: #2C2C2E; }"
        )
        self.btn_replace_old.clicked.connect(lambda: self.replace_old_for_rows())

        self.btn_delete_locate_old = InteractiveRibbonBtn("Delete New && Locate Old")
        self.btn_delete_locate_old.setToolTip(
            "Move the newly downloaded duplicate to the Recycle Bin and highlight the older file in File Explorer"
        )
        self.btn_delete_locate_old.clicked.connect(lambda: self.delete_new_and_locate_old_for_rows())

        self.btn_keep_both = InteractiveRibbonBtn("Keep Both")
        self.btn_keep_both.setToolTip(
            "Move the duplicate beside its older copy in the destination folder using numbered naming (e.g. name (2))"
        )
        self.btn_keep_both.clicked.connect(lambda: self.keep_both_for_rows())

        resolve_bar.addWidget(self.btn_replace_old)
        resolve_bar.addWidget(self.btn_delete_locate_old)
        resolve_bar.addWidget(self.btn_keep_both)
        resolve_bar.addStretch(1)
        root.addLayout(resolve_bar)

        footer = QHBoxLayout()
        footer.setSpacing(10)
        self.btn_open_dups = InteractiveRibbonBtn(
            "Open _Duplicates Folder",
            make_vector_icon("folder", "#F5F5F7", 14),
        )
        self.btn_open_dups.setObjectName("GhostButton")
        self.btn_open_dups.setToolTip("Open the _Duplicates quarantine folder in File Explorer")
        self.btn_open_dups.clicked.connect(self._open_duplicates_folder)
        footer.addWidget(self.btn_open_dups)
        footer.addStretch(1)

        btn_close = QPushButton("Close")
        btn_close.setToolTip("Close the duplicate files manager")
        btn_close.clicked.connect(self.accept)
        footer.addWidget(btn_close)

        self.btn_delete_checked = InteractiveRibbonBtn(
            "Delete Selected to Recycle Bin",
            make_vector_icon("delete", "#FFFFFF", 14),
        )
        self.btn_delete_checked.setObjectName("DangerButton")
        self.btn_delete_checked.setToolTip("Move all checked duplicate files to the Windows Recycle Bin")
        self.btn_delete_checked.clicked.connect(lambda: self.delete_checked_files(confirm=True))
        footer.addWidget(self.btn_delete_checked)
        root.addLayout(footer)

        self.reload()

    def reload(self) -> None:
        self._entries = self.service.list_duplicate_files()
        self._checkboxes.clear()
        self.tbl_dups.setRowCount(len(self._entries))
        has_rows = len(self._entries) > 0
        self.tbl_dups.setVisible(has_rows)
        self.lbl_empty.setVisible(not has_rows)

        for idx, entry in enumerate(self._entries):
            chk = QCheckBox()
            chk.setAccessibleName(f"Select {entry.get('name') or 'this duplicate'}")
            chk.stateChanged.connect(lambda _s: self._update_summary())
            holder = QWidget()
            hl = QHBoxLayout(holder)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hl.addWidget(chk)
            self.tbl_dups.setCellWidget(idx, 0, holder)
            self._checkboxes.append(chk)

            name_item = QTableWidgetItem(str(entry["name"]))
            name_item.setToolTip(str(entry["path"]))
            name_item.setData(Qt.ItemDataRole.UserRole, str(entry["path"]))
            name_item.setData(Qt.ItemDataRole.UserRole + 1, str(entry.get("existing_path") or ""))
            self.tbl_dups.setItem(idx, 1, name_item)

            orig_rel = str(entry.get("existing_rel_path") or "").strip()
            orig_abs = str(entry.get("existing_path") or "").strip()
            orig_item = QTableWidgetItem(orig_rel if orig_rel else "— (not matched)")
            orig_item.setToolTip(orig_abs if orig_abs else "No matching older file found inside Downloads")
            orig_item.setForeground(QColor("#64D2FF" if orig_rel else "#636366"))
            self.tbl_dups.setItem(idx, 2, orig_item)

            sub_item = QTableWidgetItem(str(entry["subfolder"]))
            sub_item.setForeground(QColor("#8E8E93"))
            self.tbl_dups.setItem(idx, 3, sub_item)

            ts = datetime.fromtimestamp(float(entry["mtime"])).strftime("%Y-%m-%d %H:%M")
            ts_item = QTableWidgetItem(ts)
            ts_item.setForeground(QColor("#8E8E93"))
            self.tbl_dups.setItem(idx, 4, ts_item)

            sz_item = QTableWidgetItem(_format_bytes(int(entry["size_bytes"])))
            sz_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.tbl_dups.setItem(idx, 5, sz_item)

        self._update_summary()

    def _target_rows(self) -> list[int]:
        checked = [i for i, chk in enumerate(self._checkboxes) if i < len(self._entries) and chk.isChecked()]
        if checked:
            return checked
        cur = self.tbl_dups.currentRow()
        if 0 <= cur < len(self._entries):
            return [cur]
        if len(self._entries) == 1:
            return [0]
        return []

    def _on_cell_clicked(self, row: int, col: int) -> None:
        if col != 0 and 0 <= row < len(self._checkboxes):
            chk = self._checkboxes[row]
            chk.setChecked(not chk.isChecked())

    def checked_paths(self) -> list[Path]:
        return [
            Path(str(self._entries[idx]["path"]))
            for idx, chk in enumerate(self._checkboxes)
            if idx < len(self._entries) and chk.isChecked()
        ]

    def select_all(self) -> None:
        self._syncing_checks = True
        try:
            for chk in self._checkboxes:
                chk.setChecked(True)
        finally:
            self._syncing_checks = False
        self._update_summary()

    def clear_selection(self) -> None:
        self._syncing_checks = True
        try:
            for chk in self._checkboxes:
                chk.setChecked(False)
        finally:
            self._syncing_checks = False
        self._update_summary()

    def select_all_except_newest(self) -> None:
        """Select older duplicate copies within each matching group, leaving the newest unchecked."""
        newest_index_by_key: dict[tuple[int, str], int] = {}
        for idx, entry in enumerate(self._entries):
            key = _dup_group_key(entry)
            if key not in newest_index_by_key:
                # _entries is sorted newest-first, so the first seen index is newest.
                newest_index_by_key[key] = idx
        keep_indices = set(newest_index_by_key.values())
        self._syncing_checks = True
        try:
            for idx, chk in enumerate(self._checkboxes):
                chk.setChecked(idx not in keep_indices)
        finally:
            self._syncing_checks = False
        self._update_summary()

    def _update_summary(self) -> None:
        if self._syncing_checks:
            return
        total_count = len(self._entries)
        total_bytes = sum(int(e["size_bytes"]) for e in self._entries)
        selected_indices = [i for i, chk in enumerate(self._checkboxes) if chk.isChecked()]
        sel_count = len(selected_indices)
        sel_bytes = sum(int(self._entries[i]["size_bytes"]) for i in selected_indices)

        self.lbl_title.setText(f"Duplicate Files ({total_count})" if total_count else "Duplicate Files")
        if sel_count > 0:
            self.lbl_reclaim_pill.setText(f"Reclaim {_format_bytes(sel_bytes)} ({sel_count} selected)")
            self.lbl_reclaim_pill.setStyleSheet(
                "background-color: rgba(48, 209, 88, 0.16); color: #30D158; "
                "border: 1px solid rgba(48, 209, 88, 0.42); border-radius: 12px; "
                "padding: 5px 12px; font-size: 12px; font-weight: 600;"
            )
            self.btn_delete_checked.setText(f"Delete Selected ({sel_count})")
            self.btn_delete_checked.setEnabled(True)
        else:
            self.lbl_reclaim_pill.setText(
                f"Total Quarantined: {_format_bytes(total_bytes)}" if total_count else "0 B Quarantined"
            )
            self.lbl_reclaim_pill.setStyleSheet(
                "background-color: rgba(255, 159, 10, 0.14); color: #FF9F0A; "
                "border: 1px solid rgba(255, 159, 10, 0.38); border-radius: 12px; "
                "padding: 5px 12px; font-size: 12px; font-weight: 600;"
            )
            self.btn_delete_checked.setText("Delete Selected to Recycle Bin")
            self.btn_delete_checked.setEnabled(False)

        self.btn_select_all.setEnabled(total_count > 0)
        self.btn_keep_newest.setEnabled(total_count > 1)
        self.btn_clear_sel.setEnabled(sel_count > 0)

        target_rows = self._target_rows()
        has_target = len(target_rows) > 0
        has_replaceable = any(bool(self._entries[r].get("existing_path")) for r in target_rows)
        self.btn_replace_old.setEnabled(has_replaceable)
        self.btn_delete_locate_old.setEnabled(has_target)
        self.btn_keep_both.setEnabled(has_target)

    def _show_context_menu(self, pos: QPoint) -> None:
        row = self.tbl_dups.rowAt(pos.y())
        if row < 0 or row >= len(self._entries):
            return
        entry = self._entries[row]
        has_old = bool(entry.get("existing_path"))
        menu = QMenu(self)
        act_replace = menu.addAction("Replace Older Copy with This")
        act_replace.setEnabled(has_old)
        act_del_loc = menu.addAction("Delete New && Locate Old")
        act_keep_both = menu.addAction("Keep Both (Move Beside Original)")
        menu.addSeparator()
        act_reveal = menu.addAction("Show Duplicate in File Explorer")
        act_reveal_old = menu.addAction("Show Original in File Explorer")
        act_reveal_old.setEnabled(has_old)
        act_toggle = menu.addAction(
            "Uncheck File" if self._checkboxes[row].isChecked() else "Select File for Deletion"
        )
        chosen = menu.exec(self.tbl_dups.viewport().mapToGlobal(pos))
        if chosen == act_replace:
            self.replace_old_for_rows([row])
        elif chosen == act_del_loc:
            self.delete_new_and_locate_old_for_rows([row])
        elif chosen == act_keep_both:
            self.keep_both_for_rows([row])
        elif chosen == act_reveal:
            _reveal_in_explorer(Path(str(entry["path"])))
        elif chosen == act_reveal_old and has_old:
            _reveal_in_explorer(Path(str(entry["existing_path"])))
        elif chosen == act_toggle:
            self._checkboxes[row].setChecked(not self._checkboxes[row].isChecked())

    def replace_old_for_rows(self, rows: list[int] | None = None) -> dict[str, Any]:
        target_rows = self._target_rows() if rows is None else list(rows)
        replaced = 0
        errors: list[str] = []
        for idx in target_rows:
            if not (0 <= idx < len(self._entries)):
                continue
            entry = self._entries[idx]
            existing_raw = str(entry.get("existing_path") or "").strip()
            if not existing_raw:
                errors.append(f"{entry['name']}: original file not found")
                continue
            res = self.service.replace_duplicate_with_new(Path(str(entry["path"])), Path(existing_raw))
            if res.get("ok"):
                replaced += 1
            else:
                errors.append(f"{entry['name']}: {res.get('error') or 'failed'}")
        self.reload()
        self.duplicates_changed.emit()
        if errors:
            QMessageBox.warning(self, "Replace Older Copy", "\n".join(errors))
        return {"replaced": replaced, "errors": errors}

    def delete_new_and_locate_old_for_rows(self, rows: list[int] | None = None) -> dict[str, Any]:
        target_rows = self._target_rows() if rows is None else list(rows)
        deleted = 0
        located_paths: list[str] = []
        errors: list[str] = []
        for idx in target_rows:
            if not (0 <= idx < len(self._entries)):
                continue
            entry = self._entries[idx]
            existing_raw = str(entry.get("existing_path") or "").strip()
            res = self.service.delete_duplicate_and_locate_old(
                Path(str(entry["path"])),
                Path(existing_raw) if existing_raw else Path(""),
            )
            if res.get("ok"):
                deleted += 1
                resolved_old = str(res.get("existing_path") or "")
                if resolved_old and Path(resolved_old).exists():
                    located_paths.append(resolved_old)
            else:
                errors.append(f"{entry['name']}: {res.get('error') or 'failed'}")
        if located_paths:
            _reveal_in_explorer(Path(located_paths[0]))
        self.reload()
        self.duplicates_changed.emit()
        if errors:
            QMessageBox.warning(self, "Delete & Locate Old", "\n".join(errors))
        return {"deleted": deleted, "located_paths": located_paths, "errors": errors}

    def keep_both_for_rows(self, rows: list[int] | None = None) -> dict[str, Any]:
        target_rows = self._target_rows() if rows is None else list(rows)
        kept = 0
        final_paths: list[str] = []
        errors: list[str] = []
        for idx in target_rows:
            if not (0 <= idx < len(self._entries)):
                continue
            entry = self._entries[idx]
            existing_raw = str(entry.get("existing_path") or "").strip()
            res = self.service.keep_both_duplicate(
                Path(str(entry["path"])),
                Path(existing_raw) if existing_raw else Path(""),
            )
            if res.get("ok"):
                kept += 1
                if res.get("final_path"):
                    final_paths.append(str(res["final_path"]))
            else:
                errors.append(f"{entry['name']}: {res.get('error') or 'failed'}")
        self.reload()
        self.duplicates_changed.emit()
        if errors:
            QMessageBox.warning(self, "Keep Both Files", "\n".join(errors))
        return {"kept": kept, "final_paths": final_paths, "errors": errors}

    def _open_duplicates_folder(self) -> None:
        dup_dir = self.service.watch_root / "_Duplicates"
        target = dup_dir if dup_dir.exists() else self.service.watch_root
        if sys.platform == "win32" and hasattr(os, "startfile") and target.exists():
            os.startfile(str(target))

    def delete_checked_files(self, *, confirm: bool = True) -> dict[str, Any]:
        paths = self.checked_paths()
        if not paths:
            return {"deleted": [], "skipped_locked": [], "failed": [], "missing": [], "freed_bytes": 0}
        sel_bytes = sum(
            int(self._entries[i]["size_bytes"])
            for i, chk in enumerate(self._checkboxes)
            if i < len(self._entries) and chk.isChecked()
        )
        if confirm:
            ans = QMessageBox.question(
                self,
                "Move Duplicates to Recycle Bin",
                f"Move {len(paths)} duplicate file(s) ({_format_bytes(sel_bytes)}) to the Recycle Bin?\n\n"
                "Your sorted originals stay safe, and you can still recover deleted copies from the Recycle Bin.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return {"deleted": [], "skipped_locked": [], "failed": [], "missing": [], "freed_bytes": 0}

        res = self.service.delete_duplicate_files(paths)
        self.reload()
        self.duplicates_changed.emit()
        if res["skipped_locked"]:
            QMessageBox.warning(
                self,
                "Some Files Skipped",
                f"Deleted {len(res['deleted'])} file(s), but {len(res['skipped_locked'])} file(s) were open in another app.",
            )
        return res


class MainWindow(QMainWindow):
    """Unified, Apple-clean Download Sorter control window."""

    moves_received = Signal(str, list)

    def __init__(self, service: SorterService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self._first_run_prompted = False
        # Background refresh / action bookkeeping (see refresh_all, _run_service_action).
        self._refresh_running = False
        self._refresh_again = False
        self._refresh_callbacks: list[Callable[[], None]] = []
        self._footer_at_refresh: str | None = None
        self._held_count = 0
        self._dup_count = 0
        self._dup_bytes = 0
        self._action_running = False
        self._update_release: dict[str, Any] | None = None
        self._update_downloading = False
        self._preview_toast_mgr = ToastManager(
            on_replace_old=self._on_toast_replace_old,
            on_delete_locate_old=self._on_toast_delete_locate_old,
            on_keep_both=self._on_toast_keep_both,
            default_duration_ms=int(self.service.config.ribbon_timeout_seconds * 1000),
            parent=self,
        )
        self._confetti = ConfettiOverlayWidget(self)
        self.setWindowTitle("Download Sorter")
        self.resize(1100, 760)
        self.setStyleSheet(STYLESHEET)

        central = QWidget(self)
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(20, 16, 20, 14)
        root_layout.setSpacing(14)

        root_layout.addWidget(self._build_header())

        self.banner = AnimatedBanner(self)
        root_layout.addWidget(self.banner)

        self.progress_hud = SortingProgressHUD(self)
        root_layout.addWidget(self.progress_hud)

        self._tab_icon_names = ["home", "layers", "rules", "settings"]
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_home_tab(), make_vector_icon("home", "#F5F5F7", 15), "Home")
        self.arch_tab = ArchitectureTab(self.service, self)
        self.arch_tab.architecture_applied.connect(self._on_architecture_applied)
        self.arch_tab.undo_requested.connect(self.refresh_all)
        self.arch_tab.carry_over_requested.connect(self.open_carry_over_dialog)
        self.arch_tab.action_started.connect(self._on_arch_action_started)
        self.arch_tab.action_progress.connect(self._on_arch_action_progress)
        self.arch_tab.action_finished.connect(self._on_arch_action_finished)
        self.tabs.addTab(
            self.arch_tab,
            make_vector_icon("layers", "#8E8E93", 15),
            "Folder Architecture",
        )
        self.tabs.addTab(self._build_rules_tab(), make_vector_icon("rules", "#8E8E93", 15), "Custom Rules")
        self.tabs.addTab(self._build_settings_tab(), make_vector_icon("settings", "#8E8E93", 15), "Settings")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        root_layout.addWidget(self.tabs, 1)

        self.lbl_footer = QLabel("")
        self.lbl_footer.setObjectName("MutedText")
        root_layout.addWidget(self.lbl_footer)

        self.moves_received.connect(self._on_background_moves_received)
        self.refresh_all()
        install_global_button_feedback(self, self.lbl_footer)
        # Before a narrow window drops the header buttons' text.
        name_controls_for_screen_readers(self)

    # ------------------------------------------------------------------
    # Single-Row Apple Toolbar
    # ------------------------------------------------------------------
    def _build_header(self) -> AnimatedRibbon:
        bar = AnimatedRibbon(self)
        row = QHBoxLayout(bar)
        row.setContentsMargins(16, 10, 16, 10)
        row.setSpacing(12)

        self.header_mascot = SortyMascotWidget(size=42, parent=bar)
        self.header_mascot.setToolTip("Click Sorty for a tip & cute animation!")
        self.header_mascot.clicked.connect(self._on_sorty_clicked)
        self.header_mascot.easter_egg_triggered.connect(self.trigger_easter_egg)
        row.addWidget(self.header_mascot)

        brand_col = QVBoxLayout()
        brand_col.setSpacing(2)

        brand_top = QHBoxLayout()
        brand_top.setSpacing(8)
        title = QLabel("Download Sorter")
        title.setObjectName("AppTitle")
        self.lbl_status_dot = QLabel("●")
        self.lbl_status_dot.setStyleSheet("color: #30D158; font-size: 11px;")
        self.lbl_status_badge = _make_badge("WATCHER ACTIVE", "success")
        # Hidden in a narrow window (see resizeEvent); the dot and the Auto-Sort
        # button say the same thing.
        self.lbl_status_badge.setMinimumWidth(1)
        brand_top.addWidget(title)
        brand_top.addWidget(self.lbl_status_dot)
        brand_top.addWidget(self.lbl_status_badge)
        brand_top.addStretch(1)
        brand_col.addLayout(brand_top)

        self.lbl_watch_path = ElidedLabel(
            "",
            mode=Qt.TextElideMode.ElideMiddle,
            tooltip="Automatically organizes loose top-level files in this folder into subfolders.",
        )
        self.lbl_watch_path.setStyleSheet("color: #8E8E93; font-size: 11.5px;")
        brand_col.addWidget(self.lbl_watch_path)
        row.addLayout(brand_col, 1)

        self.btn_pause = CollapsibleRibbonBtn(
            "Auto-Sort: ON",
            make_vector_icon("pause", "#30D158", 14),
        )
        self.btn_pause.setToolTip("Pause or resume automatic background sorting of Downloads")
        self.btn_pause.clicked.connect(self._toggle_pause)
        row.addWidget(self.btn_pause)

        self.btn_file_held = CollapsibleRibbonBtn(
            "Move Held .3MF",
            make_vector_icon("cube", "#FF9F0A", 14),
        )
        self.btn_file_held.setToolTip("Immediately move held .3mf slicer project files into their 3D Printing folders")
        self.btn_file_held.clicked.connect(self._on_file_held_now)
        row.addWidget(self.btn_file_held)

        div_left = QFrame()
        div_left.setObjectName("RibbonDivider")
        row.addWidget(div_left)

        self.btn_undo_last = CollapsibleRibbonBtn(
            "Undo",
            make_vector_icon("undo", "#F5F5F7", 14),
        )
        self.btn_undo_last.setToolTip("Undo Last Sort")
        self.btn_undo_last.clicked.connect(self._on_undo_last)
        row.addWidget(self.btn_undo_last)

        self.btn_open_downloads = CollapsibleRibbonBtn(
            "Open Folder",
            make_vector_icon("folder", "#F5F5F7", 14),
        )
        self.btn_open_downloads.setToolTip("Open Downloads Folder")
        self.btn_open_downloads.clicked.connect(self._open_downloads_folder)
        row.addWidget(self.btn_open_downloads)

        self.btn_walkthrough = CollapsibleRibbonBtn(
            "Guide",
            make_vector_icon("help", "#98989D", 14),
        )
        self.btn_walkthrough.setObjectName("GhostButton")
        self.btn_walkthrough.setToolTip("Open Walkthrough")
        self.btn_walkthrough.clicked.connect(self.open_walkthrough)
        row.addWidget(self.btn_walkthrough)

        self.btn_sort_now = InteractiveRibbonBtn(
            "Clean Up Now",
            make_vector_icon("bolt", "#FFFFFF", 14),
        )
        self.btn_sort_now.setObjectName("PrimaryButton")
        self.btn_sort_now.setToolTip("Sort all settled top-level files in Downloads right now")
        self.btn_sort_now.clicked.connect(self._on_sort_now)
        row.addWidget(self.btn_sort_now)

        attach_press_feedback(self.btn_undo_last, "#5AC8FA")
        attach_press_feedback(self.btn_open_downloads, "#0A84FF")
        attach_press_feedback(self.btn_sort_now, "#0A84FF")
        return bar

    # ------------------------------------------------------------------
    # Tab 1: Home (Onboarding Card + Downloads Preview + Recent Activity)
    # ------------------------------------------------------------------
    def _build_step_card(self, step_title: str, step_body: str) -> QFrame:
        card = QFrame()
        card.setObjectName("stepCard")
        card.setMinimumHeight(86)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(4)
        lbl_title = QLabel(step_title)
        lbl_title.setStyleSheet("font-weight: 600; color: #F5F5F7; font-size: 13px;")
        lbl_body = QLabel(step_body)
        lbl_body.setWordWrap(True)
        lbl_body.setMinimumHeight(38)
        lbl_body.setObjectName("MutedText")
        card_layout.addWidget(lbl_title)
        card_layout.addWidget(lbl_body)
        return card

    def _build_home_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(14)

        # Collapsible Onboarding Card (hidden completely once walkthrough completes)
        self.instructions_widget = QFrame()
        self.instructions_widget.setObjectName("heroBanner")
        self.instructions_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        inst_layout = QVBoxLayout(self.instructions_widget)
        inst_layout.setContentsMargins(16, 14, 16, 14)
        inst_layout.setSpacing(12)

        inst_hdr_row = QHBoxLayout()
        inst_hdr_row.setSpacing(10)
        title_stack = QVBoxLayout()
        title_stack.setSpacing(2)
        hero_hdr = QLabel("How Download Sorter Works")
        hero_hdr.setObjectName("CardTitle")
        self.lbl_autosort_explain = QLabel(
            "Automatic Sorting is ON — New downloads are organized automatically."
        )
        self.lbl_autosort_explain.setWordWrap(True)
        self.lbl_autosort_explain.setStyleSheet("color: #30D158; font-weight: 500; font-size: 12px;")
        title_stack.addWidget(hero_hdr)
        title_stack.addWidget(self.lbl_autosort_explain)
        inst_hdr_row.addLayout(title_stack, 1)

        self.btn_home_autosort = InteractiveRibbonBtn(
            "Auto-Sort: ON",
            make_vector_icon("pause", "#30D158", 14),
        )
        self.btn_home_autosort.setToolTip("Pause or resume automatic background sorting of new downloads")
        self.btn_home_autosort.clicked.connect(self._toggle_pause)
        inst_hdr_row.addWidget(self.btn_home_autosort)

        self.btn_hide_instructions = InteractiveRibbonBtn(
            "Dismiss",
            make_vector_icon("check", "#98989D", 14),
        )
        self.btn_hide_instructions.setObjectName("GhostButton")
        self.btn_hide_instructions.setToolTip("Hide instructions")
        self.btn_hide_instructions.clicked.connect(self.complete_walkthrough)
        inst_hdr_row.addWidget(self.btn_hide_instructions)
        inst_layout.addLayout(inst_hdr_row)

        cards_row = QHBoxLayout()
        cards_row.setSpacing(10)
        cards_row.addWidget(
            self._build_step_card(
                "1. Download Normally",
                "Loose files land in Downloads as usual. Folders you already made are never touched.",
            ),
            1,
        )
        cards_row.addWidget(
            self._build_step_card(
                "2. Sorted Into Folders",
                "PDFs go to Documents, photos to Images, installers to Installers, archives to Archives, and 3D files to 3D Models.",
            ),
            1,
        )
        cards_row.addWidget(
            self._build_step_card(
                "3. Safe .3MF Hold & Undo",
                "New .3MF print files stay in Downloads for 7 days while you slice them. Made a mistake? Click Undo anytime.",
            ),
            1,
        )
        inst_layout.addLayout(cards_row)
        self.instructions_widget.setVisible(not self.service.config.tutorial_completed)
        layout.addWidget(self.instructions_widget)

        # Card A: Downloads Folder Preview
        self.lbl_sim_summary = QLabel("Scanning Downloads...")
        self.lbl_sim_summary.setObjectName("MutedText")
        self.btn_delete_selected = InteractiveRibbonBtn(
            "Delete",
            make_vector_icon("delete", "#FFFFFF", 14),
        )
        self.btn_delete_selected.setObjectName("DangerButton")
        self.btn_delete_selected.setToolTip("Move selected files from Downloads to Recycle Bin (Del)")
        self.btn_delete_selected.setVisible(False)
        self.btn_delete_selected.clicked.connect(lambda: self._delete_selected_sim_files(confirm=True))

        self.btn_duplicates = InteractiveRibbonBtn(
            "Duplicates",
            make_vector_icon("duplicates", "#FF9F0A", 14),
        )
        self.btn_duplicates.setToolTip("Inspect and delete quarantined duplicate files inside _Duplicates/")
        self.btn_duplicates.clicked.connect(self._open_duplicates_dialog)

        self.btn_refresh_preview = InteractiveRibbonBtn(
            "Refresh",
            make_vector_icon("refresh", "#F5F5F7", 14),
        )
        self.btn_refresh_preview.setToolTip("Refresh Preview")
        self.btn_refresh_preview.clicked.connect(self._on_refresh_preview_clicked)
        self.btn_sort_tiers = InteractiveRibbonBtn(
            "Sort Lower Tiers",
            make_vector_icon("layers", "#F5F5F7", 14),
        )
        self.btn_sort_tiers.setToolTip(
            "Re-sort files already inside Tier 1 & Tier 2 folders into their proper subfolders."
        )
        self.btn_sort_tiers.clicked.connect(self._on_sort_tiers_now)

        self.btn_detox = CollapsibleRibbonBtn(
            "Detox",
            make_vector_icon("trash", "#8E8E93", 14),
        )
        self.btn_detox.setToolTip("Review old installers (>30 days) and empty folders inside Downloads")
        self.btn_detox.set_collapsed(True)
        self.btn_detox.clicked.connect(self._open_detox_dialog)

        self.btn_quick_search = CollapsibleRibbonBtn(
            "Search",
            make_vector_icon("search", "#5AC8FA", 14),
        )
        self.btn_quick_search.setToolTip("Quick Search sorted files and history (Ctrl+Shift+Space)")
        self.btn_quick_search.set_collapsed(True)
        self.btn_quick_search.clicked.connect(self._open_quick_search)
        QShortcut(QKeySequence("Ctrl+Shift+Space"), self, activated=self._open_quick_search)

        preview_actions = QWidget()
        preview_actions_row = QHBoxLayout(preview_actions)
        preview_actions_row.setContentsMargins(0, 0, 0, 0)
        preview_actions_row.setSpacing(6)
        preview_actions_row.addWidget(self.btn_delete_selected)
        preview_actions_row.addWidget(self.btn_duplicates)
        preview_actions_row.addWidget(self.btn_sort_tiers)
        preview_actions_row.addWidget(self.btn_refresh_preview)
        sim_card, sim_layout = _make_surface_card(
            "Downloads Folder Preview",
            self.lbl_sim_summary,
            right_widget=preview_actions,
        )

        self.tbl_sim = QTableWidget(0, 4)
        self.tbl_sim.setHorizontalHeaderLabels(
            ["File in Downloads", "Action", "Destination Folder", "Why"]
        )
        _style_table(self.tbl_sim)
        self.tbl_sim.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl_sim.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tbl_sim.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tbl_sim.customContextMenuRequested.connect(self._show_sim_context_menu)
        self.tbl_sim.selectionModel().selectionChanged.connect(self._on_sim_selection_changed)
        self._sim_delete_shortcut = QShortcut(QKeySequence.StandardKey.Delete, self.tbl_sim)
        self._sim_delete_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._sim_delete_shortcut.activated.connect(lambda: self._delete_selected_sim_files(confirm=True))
        s_hdr = self.tbl_sim.horizontalHeader()
        s_hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        s_hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        s_hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        s_hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.tbl_sim.setColumnWidth(0, scaled_px(240))
        self.tbl_sim.setColumnWidth(1, scaled_px(148))
        sim_layout.addWidget(self.tbl_sim, 1)
        layout.addWidget(sim_card, 3)

        # Card B: Recent Activity
        self.lbl_activity_empty = QLabel(
            "No files moved yet — click 'Clean Up Now' above or drop a file in Downloads."
        )
        self.lbl_activity_empty.setObjectName("MutedText")
        act_right = QWidget()
        act_right_row = QHBoxLayout(act_right)
        act_right_row.setContentsMargins(0, 0, 0, 0)
        act_right_row.setSpacing(6)
        act_right_row.addWidget(self.lbl_activity_empty)
        act_right_row.addWidget(self.btn_detox)
        act_right_row.addWidget(self.btn_quick_search)
        act_card, act_layout = _make_surface_card(
            "Recent Activity",
            right_widget=act_right,
        )

        self.tbl_activity = QTableWidget(0, 5)
        self.tbl_activity.setHorizontalHeaderLabels(["When", "File", "Moved Into Folder", "Why It Moved", "Undo"])
        _style_table(self.tbl_activity)
        header = self.tbl_activity.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_activity.setColumnWidth(1, scaled_px(220))
        act_layout.addWidget(self.tbl_activity, 1)
        layout.addWidget(act_card, 2)
        return tab

    # ------------------------------------------------------------------
    # Tab 2: Custom Rules & Quick File Tester
    # ------------------------------------------------------------------
    def _build_rules_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(14)

        packs_wrap = QWidget()
        packs_row = QHBoxLayout(packs_wrap)
        packs_row.setContentsMargins(0, 0, 0, 0)
        packs_row.setSpacing(6)
        self.btn_rule_packs = InteractiveRibbonBtn("+ Rule Pack")
        self.btn_rule_packs.setToolTip("Add a ready-made set of rules for School, Bills & receipts, Travel and more")
        self.menu_rule_packs = QMenu(self.btn_rule_packs)
        for tpl_key, (label, description) in STARTER_PACK_LABELS.items():
            action = self.menu_rule_packs.addAction(f"{label} — {description}".replace("&", "&&"))
            action.setData(tpl_key)
            action.triggered.connect(lambda _=False, k=tpl_key: self._apply_template(k))
        self.btn_rule_packs.clicked.connect(
            lambda: self.menu_rule_packs.exec(
                self.btn_rule_packs.mapToGlobal(self.btn_rule_packs.rect().bottomLeft())
            )
        )
        packs_row.addWidget(self.btn_rule_packs)
        btn_import = InteractiveRibbonBtn("Carry Over v2.8 / Setup...")
        btn_import.setObjectName("GhostButton")
        btn_import.setToolTip("Import rules, overrides, or folder layouts from Download Sorter v2.8 or an exported v3 JSON file")
        btn_import.clicked.connect(self.open_carry_over_dialog)
        packs_row.addWidget(btn_import)

        rules_card, rules_layout = _make_surface_card(
            "Custom Rules",
            "Checked top-to-bottom — first match wins. Standard file types sort automatically.",
            right_widget=packs_wrap,
        )

        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        btn_new = InteractiveRibbonBtn("New Rule", make_vector_icon("plus", "#FFFFFF", 14))
        btn_new.setObjectName("PrimaryButton")
        btn_new.setToolTip("Create a new custom sorting rule")
        btn_new.clicked.connect(self._add_rule)
        toolbar.addWidget(btn_new)
        _rule_tips = {
            "Edit": "Edit the selected sorting rule",
            "Delete": "Delete the selected sorting rule",
            "Move Up": "Increase priority (move selected rule higher)",
            "Move Down": "Decrease priority (move selected rule lower)",
            "Smart Order": "Order rules from most specific (domain/content/keywords) to broadest",
        }
        for label, icon_name, cb in (
            ("Edit", "edit", self._edit_rule),
            ("Delete", "delete", self._delete_rule),
            ("Move Up", "up", lambda: self._move_rule(-1)),
            ("Move Down", "down", lambda: self._move_rule(1)),
            ("Smart Order", "bolt", self._sort_rules_by_specificity),
        ):
            btn = InteractiveRibbonBtn(label, make_vector_icon(icon_name, "#F5F5F7", 14))
            btn.setToolTip(_rule_tips[label])
            btn.clicked.connect(cb)
            toolbar.addWidget(btn)
        toolbar.addStretch(1)
        # In a short window the suggestion bar below folds into this button, so the
        # tab keeps fitting 900 x 560; it offers the same two choices from a menu.
        self.btn_rule_suggestion_compact = InteractiveRibbonBtn("Suggestion", make_vector_icon("sparkle", "#64D2FF", 14))
        self.btn_rule_suggestion_compact.setToolTip("Review a suggested rule learned from your manual file moves")
        self.btn_rule_suggestion_compact.clicked.connect(self._show_rule_suggestion_menu)
        self.btn_rule_suggestion_compact.setVisible(False)
        toolbar.addWidget(self.btn_rule_suggestion_compact)
        rules_layout.addLayout(toolbar)

        # Learned suggestion: shown when the same kind of file has been moved into the
        # same folder by hand a few times (see learning.py). Hidden when there is none.
        self._current_suggestion: dict[str, Any] | None = None
        self.frm_rule_suggestion = QFrame()
        self.frm_rule_suggestion.setObjectName("RuleSuggestion")
        self.frm_rule_suggestion.setStyleSheet(
            "#RuleSuggestion { background: rgba(10, 132, 255, 0.10);"
            " border: 1px solid rgba(10, 132, 255, 0.35); border-radius: 8px; }"
        )
        sugg_row = QHBoxLayout(self.frm_rule_suggestion)
        sugg_row.setContentsMargins(12, 6, 8, 6)
        sugg_row.setSpacing(8)
        self.lbl_rule_suggestion = ElidedLabel("")
        self.lbl_rule_suggestion.setStyleSheet("color: #F5F5F7; background: transparent; border: none;")
        sugg_row.addWidget(self.lbl_rule_suggestion, 1)
        self.btn_accept_suggestion = InteractiveRibbonBtn("Make a Rule...", make_vector_icon("plus", "#FFFFFF", 14))
        self.btn_accept_suggestion.setObjectName("PrimaryButton")
        self.btn_accept_suggestion.setToolTip("Open a new rule filled in from what you did; check it, then save")
        self.btn_accept_suggestion.clicked.connect(self._accept_rule_suggestion)
        sugg_row.addWidget(self.btn_accept_suggestion)
        self.btn_dismiss_suggestion = InteractiveRibbonBtn("Not Now")
        self.btn_dismiss_suggestion.setToolTip("Don't suggest this rule again")
        self.btn_dismiss_suggestion.clicked.connect(self._dismiss_rule_suggestion)
        sugg_row.addWidget(self.btn_dismiss_suggestion)
        self.frm_rule_suggestion.setVisible(False)
        rules_layout.addWidget(self.frm_rule_suggestion)

        self.tbl_rules = QTableWidget(0, 6)
        self.tbl_rules.setHorizontalHeaderLabels(
            ["Active", "Rule Name", "Moves Into Folder", "Matches Words in Filename", "Matches Website", "File Types"]
        )
        _style_table(self.tbl_rules)
        self.tbl_rules.doubleClicked.connect(self._edit_rule)
        r_hdr = self.tbl_rules.horizontalHeader()
        r_hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        r_hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        r_hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        r_hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        r_hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
        r_hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.Interactive)
        self.tbl_rules.setColumnWidth(1, scaled_px(180))
        self.tbl_rules.setColumnWidth(2, scaled_px(175))
        self.tbl_rules.setColumnWidth(4, scaled_px(140))
        self.tbl_rules.setColumnWidth(5, scaled_px(120))
        rules_layout.addWidget(self.tbl_rules, 1)
        layout.addWidget(rules_card, 1)

        # The answer goes on its own line under the boxes, shortened to fit, so a long
        # reason never makes the window wider.
        self.lbl_test_result = ElidedLabel("Type a filename below to see which folder it moves into.")
        self.lbl_test_result.setStyleSheet("font-weight: 600; color: #64D2FF;")
        tester_card, tester_layout = _make_surface_card("Test a Filename")
        inputs = QHBoxLayout()
        inputs.setSpacing(8)
        self.edit_test_name = QLineEdit()
        self.edit_test_name.setPlaceholderText("Type any filename to test (e.g. Resume_2026.pdf or Benchy.3mf)")
        self.edit_test_url = QLineEdit()
        self.edit_test_url.setPlaceholderText("Optional website it came from (e.g. canvas.edu or makerworld.com)")
        self.edit_test_name.textChanged.connect(self._update_live_tester)
        self.edit_test_url.textChanged.connect(self._update_live_tester)
        inputs.addWidget(self.edit_test_name, 2)
        inputs.addWidget(self.edit_test_url, 2)
        tester_layout.addLayout(inputs)
        tester_layout.addWidget(self.lbl_test_result)

        self.list_test_trace = QListWidget()
        self.list_test_trace.setMaximumHeight(72)
        tester_layout.addWidget(self.list_test_trace)
        layout.addWidget(tester_card)
        return tab

    # ------------------------------------------------------------------
    # Tab 3: Settings (Apple Preferences Card)
    # ------------------------------------------------------------------
    def _build_settings_tab(self) -> QWidget:
        from download_sorter.ui.settings_widgets import (
            DependentSubGroup,
            MacToggle,
            SettingCard,
            SettingRow,
        )
        from download_sorter.ui.theme import PALETTE, set_widget_prop

        page = QScrollArea()
        page.setObjectName("SettingsScroll")
        page.setWidgetResizable(True)
        page.setFrameShape(QFrame.Shape.NoFrame)
        page.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        page.setStyleSheet(
            "QScrollArea#SettingsScroll, QScrollArea#SettingsScroll > QWidget > QWidget { background: transparent; }"
        )

        viewport = QWidget()
        viewport.setObjectName("SettingsViewport")
        page.setWidget(viewport)

        outer_layout = QHBoxLayout(viewport)
        outer_layout.setContentsMargins(6, 4, 10, 14)

        content_col = QWidget()
        content_col.setMaximumWidth(820)
        layout = QVBoxLayout(content_col)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # Top header bar with Save Settings CTA
        header_bar = QHBoxLayout()
        header_bar.setContentsMargins(2, 0, 2, 2)
        header_title = QLabel("Settings")
        header_title.setObjectName("CardTitle")
        header_sub_txt = "Organized into simple groups — changes apply when you click Save Settings."
        header_sub = QLabel(header_sub_txt, content_col)
        header_sub.setVisible(False)
        header_title.setToolTip(header_sub_txt)
        header_text = QVBoxLayout()
        header_text.setSpacing(1)
        header_text.addWidget(header_title)
        header_bar.addLayout(header_text, 1)

        btn_save = InteractiveRibbonBtn("Save Settings", make_vector_icon("check", "#FFFFFF", 14))
        btn_save.setObjectName("PrimaryButton")
        btn_save.setToolTip("Apply and save all changed settings to disk")
        btn_save.clicked.connect(self._save_settings_form)
        attach_press_feedback(btn_save, "#0A84FF")
        header_bar.addWidget(btn_save)
        layout.addLayout(header_bar)

        # ------------------------------------------------------------------
        # Card 1: Core Routing & Watch Folder
        # ------------------------------------------------------------------
        card_core = SettingCard(
            "Core Routing & Watch Folder",
            "Control background watching, folder style, and unrecognized files.",
        )

        self.chk_auto_sort = MacToggle()
        card_core.add_row(
            SettingRow(
                "Automatic Sorting",
                "Automatically sort new files in Downloads in the background.",
                controls=self.chk_auto_sort,
            )
        )

        self.edit_watch_folder = QLineEdit()
        self.edit_watch_folder.setMinimumWidth(220)
        btn_browse = InteractiveRibbonBtn("Browse...")
        btn_browse.setToolTip("Choose which folder Download Sorter monitors and organizes")
        btn_browse.clicked.connect(self._browse_watch_folder)
        card_core.add_row(
            SettingRow(
                "Folder to Organize",
                "Primary folder monitored by Download Sorter.",
                controls=[self.edit_watch_folder, btn_browse],
            )
        )

        self.chk_type_subfolders = MacToggle()
        card_core.add_row(
            SettingRow(
                "File-Type Subfolders",
                "Sort into subfolders (e.g. Documents\\PDF, Images\\PNG) when no custom rule matches.",
                controls=self.chk_type_subfolders,
            )
        )

        self.combo_fallback = QComboBox()
        self.combo_fallback.addItem("Put them in an 'Other' folder", "other")
        self.combo_fallback.addItem("Leave them in Downloads", "leave")
        card_core.add_row(
            SettingRow(
                "Unrecognized File Types",
                "What to do when a file doesn't match any rule or extension category.",
                controls=self.combo_fallback,
            )
        )

        self.spin_settle = QDoubleSpinBox()
        self.spin_settle.setRange(0.0, 60.0)
        self.spin_settle.setSingleStep(0.5)
        self.spin_settle.setSuffix(" s")
        card_core.add_row(
            SettingRow(
                "Safety Delay",
                "Wait a few seconds after a download finishes before moving it.",
                controls=self.spin_settle,
            )
        )
        layout.addWidget(card_core)

        # ------------------------------------------------------------------
        # Card 2: Archives & 3D Print Workflows
        # ------------------------------------------------------------------
        card_archives = SettingCard(
            "Archives & 3D Print Workflows",
            "Automatic ZIP extraction with zip-bomb protection and 3D slicer staging.",
        )

        self.chk_auto_unzip_all = MacToggle()
        card_archives.add_row(
            SettingRow(
                "Automatically extract .zip archives",
                "Unpacks sorted .zip files into a subfolder named after the archive.",
                controls=self.chk_auto_unzip_all,
                badge_text="Verified vs Zip Bombs",
                badge_variant="green",
            )
        )

        zip_sub = DependentSubGroup(self.chk_auto_unzip_all, collapse_when_off=False)
        sec_banner = QFrame()
        set_widget_prop(sec_banner, "securityBanner", True)
        sec_row = QHBoxLayout(sec_banner)
        sec_row.setContentsMargins(10, 6, 10, 6)
        sec_row.setSpacing(8)
        lbl_shield = QLabel("Pre-extraction guard:")
        lbl_shield.setStyleSheet(
            f"font-size: 11px; font-weight: 600; color: {PALETTE['accent_green']}; background: transparent;"
        )
        lbl_specs = QLabel(
            "Streams bytes in 64 KB blocks • Max 100:1 ratio • Max 500 MB • Blocks zip-slip & symlinks"
        )
        lbl_specs.setWordWrap(True)
        set_widget_prop(lbl_specs, "securitySpec", True)
        sec_row.addWidget(lbl_shield)
        sec_row.addWidget(lbl_specs, 1)

        sec_wrap = QWidget()
        sec_wrap_l = QVBoxLayout(sec_wrap)
        sec_wrap_l.setContentsMargins(14, 4, 14, 2)
        sec_wrap_l.addWidget(sec_banner)
        zip_sub.add_row(sec_wrap)

        self.chk_zip_keep_original = MacToggle()
        zip_sub.add_row(
            SettingRow(
                "Keep original .zip alongside extracted folder",
                "When off, the original .zip is safely archived to Archives\\_Extracted.",
                controls=self.chk_zip_keep_original,
                compact=True,
            )
        )
        card_archives.add_row(zip_sub, with_divider=False)

        self.chk_hold_3mf = MacToggle()
        card_archives.add_row(
            SettingRow(
                "3D Printing (.3MF) Hold",
                "Keep new .3MF files in Downloads while you slice, before moving to 3D Models.",
                controls=self.chk_hold_3mf,
                badge_text="3D Print",
                badge_variant="cyan",
            )
        )

        hold_sub = DependentSubGroup(self.chk_hold_3mf, collapse_when_off=False)
        self.spin_hold_days = QSpinBox()
        self.spin_hold_days.setRange(1, 60)
        self.spin_hold_days.setSuffix(" days")
        hold_sub.add_row(
            SettingRow(
                "Hold Duration",
                "Days to keep .3MF files in Downloads before filing.",
                controls=self.spin_hold_days,
                compact=True,
            )
        )
        card_archives.add_row(hold_sub, with_divider=False)
        layout.addWidget(card_archives)

        # ------------------------------------------------------------------
        # Card 3: Notifications & Quick Switcher HUD
        # ------------------------------------------------------------------
        card_hud = SettingCard(
            "Notifications & Quick Switcher",
            "Side ribbon Undo alerts and recent-file picker for Open/Upload dialogs.",
        )

        self.chk_notifications = MacToggle()
        card_hud.add_row(
            SettingRow(
                "Side Ribbon Notifications",
                "Show a ribbon with 1-click Undo in the top-right corner when files are sorted.",
                controls=self.chk_notifications,
            )
        )

        notif_sub = DependentSubGroup(self.chk_notifications, collapse_when_off=False)
        self.spin_ribbon_timeout = QDoubleSpinBox()
        self.spin_ribbon_timeout.setRange(1.0, 30.0)
        self.spin_ribbon_timeout.setDecimals(0)
        self.spin_ribbon_timeout.setSingleStep(1.0)
        self.spin_ribbon_timeout.setSuffix(" s")
        self.spin_ribbon_timeout.setFixedWidth(90)
        self.spin_ribbon_timeout.setToolTip(
            "How many seconds before Side Ribbon notifications automatically close."
        )
        self.btn_preview_ribbon = InteractiveRibbonBtn("Preview Ribbon")
        self.btn_preview_ribbon.setToolTip("Display a sample Side Ribbon notification on the top-right screen corner")
        self.btn_preview_ribbon.clicked.connect(self._on_preview_ribbon)
        self.btn_close_ribbons = InteractiveRibbonBtn("Close Ribbons")
        self.btn_close_ribbons.setObjectName("GhostButton")
        self.btn_close_ribbons.setToolTip("Immediately dismiss any active Side Ribbon notifications.")
        self.btn_close_ribbons.clicked.connect(self._on_close_all_ribbons)
        notif_sub.add_row(
            SettingRow(
                "Ribbon Timeout & Preview",
                "Auto-close delay and quick test controls.",
                controls=[self.spin_ribbon_timeout, self.btn_preview_ribbon, self.btn_close_ribbons],
                compact=True,
            )
        )
        card_hud.add_row(notif_sub, with_divider=False)

        self.chk_quick_switcher = MacToggle()
        card_hud.add_row(
            SettingRow(
                "Quick Switcher",
                "Offer files Download Sorter just sorted inside Open and Upload file dialogs.",
                controls=self.chk_quick_switcher,
            )
        )

        qs_sub = DependentSubGroup(self.chk_quick_switcher, collapse_when_off=False)
        self.spin_qs_timeout = QDoubleSpinBox()
        self.spin_qs_timeout.setRange(10.0, 3600.0)
        self.spin_qs_timeout.setDecimals(0)
        self.spin_qs_timeout.setSingleStep(30.0)
        self.spin_qs_timeout.setSuffix(" s")
        self.spin_qs_timeout.setFixedWidth(86)
        self.spin_qs_timeout.setToolTip("How many seconds general downloads stay offered in Quick Switcher.")

        self.spin_qs_3d_hours = QDoubleSpinBox()
        self.spin_qs_3d_hours.setRange(0.5, 72.0)
        self.spin_qs_3d_hours.setDecimals(1)
        self.spin_qs_3d_hours.setSingleStep(1.0)
        self.spin_qs_3d_hours.setSuffix(" hrs")
        self.spin_qs_3d_hours.setFixedWidth(86)
        self.spin_qs_3d_hours.setToolTip("How many hours 3D/CAD files (.3mf, .stl, .step, etc.) stay offered.")

        self.edit_qs_hotkey = QLineEdit()
        self.edit_qs_hotkey.setFixedWidth(80)
        self.edit_qs_hotkey.setToolTip(
            "Picks the newest file while the pill is showing. Ctrl or Alt plus a letter, "
            "number or F-key (default: Ctrl+G)."
        )

        qs_sub.add_row(
            SettingRow(
                "Offer Window & Hotkey",
                "General seconds, 3D/CAD hours, and keyboard pick shortcut.",
                controls=[self.spin_qs_timeout, self.spin_qs_3d_hours, self.edit_qs_hotkey],
                compact=True,
            )
        )
        card_hud.add_row(qs_sub, with_divider=False)
        layout.addWidget(card_hud)

        # ------------------------------------------------------------------
        # Card 4: Chrome Extension Integration
        # ------------------------------------------------------------------
        self.lbl_chrome_status = _make_badge("READY TO SETUP", "neutral")
        card_chrome = SettingCard(
            "Chrome Extension",
            "Route Chrome downloads straight into sorted subfolders so Chrome's bubble stays draggable.",
            header_right_widget=self.lbl_chrome_status,
        )

        chrome_body = QWidget()
        chrome_col = QVBoxLayout(chrome_body)
        chrome_col.setContentsMargins(14, 10, 14, 10)
        chrome_col.setSpacing(8)

        chrome_row = QHBoxLayout()
        chrome_row.setSpacing(8)
        self.btn_install_chrome_host = InteractiveRibbonBtn(
            "Install Chrome Host",
            make_vector_icon("bolt", "#FFFFFF", 14),
        )
        self.btn_install_chrome_host.setToolTip(
            "Register the Chrome/Edge Native Messaging host so Chrome saves straight into sorted folders"
        )
        self.btn_install_chrome_host.clicked.connect(self._on_install_chrome_host)
        chrome_row.addWidget(self.btn_install_chrome_host)

        self.btn_open_chrome_ext = InteractiveRibbonBtn(
            "Extension Folder",
            make_vector_icon("folder", "#F5F5F7", 14),
        )
        self.btn_open_chrome_ext.setObjectName("GhostButton")
        self.btn_open_chrome_ext.setToolTip("Open the Chrome extension folder to load in chrome://extensions")
        self.btn_open_chrome_ext.clicked.connect(self._on_open_chrome_extension)
        chrome_row.addWidget(self.btn_open_chrome_ext)

        self.btn_copy_chrome_path = InteractiveRibbonBtn(
            "Copy Path",
            make_vector_icon("copy", "#F5F5F7", 14),
        )
        self.btn_copy_chrome_path.setObjectName("GhostButton")
        self.btn_copy_chrome_path.setToolTip("Copy the extension folder path for Chrome 'Load unpacked'")
        self.btn_copy_chrome_path.clicked.connect(self._on_copy_chrome_extension_path)
        chrome_row.addWidget(self.btn_copy_chrome_path)

        self.btn_chrome_guide = InteractiveRibbonBtn(
            "Setup Guide",
            make_vector_icon("help", "#69E6AD", 15),
        )
        self.btn_chrome_guide.setToolTip(
            "Open the step-by-step picture walkthrough with arrows for installing the Chrome extension"
        )
        self.btn_chrome_guide.clicked.connect(self.open_chrome_walkthrough)
        chrome_row.addWidget(self.btn_chrome_guide)
        chrome_row.addStretch(1)
        chrome_col.addLayout(chrome_row)

        self.lbl_chrome_hint = QLabel(
            f'Keep Chrome\'s download bubble draggable: <a href="guide" style="color: #64D2FF; text-decoration: none;"><b>Follow the step-by-step picture guide →</b></a> (ID: {CHROME_EXTENSION_ID}).'
        )
        self.lbl_chrome_hint.setObjectName("MutedText")
        self.lbl_chrome_hint.setWordWrap(True)
        self.lbl_chrome_hint.setOpenExternalLinks(False)
        self.lbl_chrome_hint.linkActivated.connect(lambda _href: self.open_chrome_walkthrough())
        chrome_col.addWidget(self.lbl_chrome_hint)
        card_chrome.add_row(chrome_body)
        layout.addWidget(card_chrome)

        # ------------------------------------------------------------------
        # Card 5: Windows, Updates & Backup
        # ------------------------------------------------------------------
        card_sys = SettingCard(
            "System, Updates & Backup",
            "Windows integration, release checks, and portable configuration backup.",
        )

        self.chk_startup = MacToggle()
        card_sys.add_row(
            SettingRow(
                "Start at Windows Sign-In",
                "Launch Download Sorter automatically in the system tray when you log in.",
                controls=self.chk_startup,
            )
        )

        self.chk_context_menu = MacToggle()
        card_sys.add_row(
            SettingRow(
                "Explorer Context Menu",
                "Add 'Sort with Download Sorter' when you right-click a folder in File Explorer.",
                controls=self.chk_context_menu,
            )
        )

        self.chk_check_updates = MacToggle()
        self.chk_check_updates.setToolTip(
            "Asks GitHub which version is newest. Nothing about you or your files is sent."
        )
        self.btn_check_updates = InteractiveRibbonBtn(
            "Check for Updates Now",
            make_vector_icon("refresh", "#F5F5F7", 14),
        )
        self.btn_check_updates.setToolTip(
            "Check GitHub Releases right now for a newer version of Download Sorter."
        )
        self.btn_check_updates.clicked.connect(self._on_check_updates_now)
        card_sys.add_row(
            SettingRow(
                "Automatic Update Checks",
                "Check GitHub once a day for a newer release.",
                controls=[self.btn_check_updates, self.chk_check_updates],
            )
        )

        self.btn_carry_over_settings = InteractiveRibbonBtn(
            "Carry Over Setup && Rules...",
            make_vector_icon("layers", "#F5F5F7", 14),
        )
        self.btn_carry_over_settings.setToolTip(
            "Carry over your v2.8 config or exported setup file without losing any rules"
        )
        self.btn_carry_over_settings.clicked.connect(self.open_carry_over_dialog)

        self.btn_export_setup = InteractiveRibbonBtn(
            "Export Setup...",
            make_vector_icon("folder", "#F5F5F7", 14),
        )
        self.btn_export_setup.setObjectName("GhostButton")
        self.btn_export_setup.setToolTip(
            "Save your complete setup, rules & Folder Architecture to a JSON file"
        )
        self.btn_export_setup.clicked.connect(self._export_current_setup)
        card_sys.add_row(
            SettingRow(
                "Upgrade & Backup",
                "Import rules from v2.8 or export your complete setup to JSON.",
                controls=[self.btn_carry_over_settings, self.btn_export_setup],
            )
        )
        layout.addWidget(card_sys)
        layout.addStretch(1)

        outer_layout.addStretch(1)
        outer_layout.addWidget(content_col, 10)
        outer_layout.addStretch(1)
        return page

    # ------------------------------------------------------------------
    # Refresh & Actions
    # ------------------------------------------------------------------
    def _on_tab_changed(self, index: int) -> None:
        for i, icon_name in enumerate(self._tab_icon_names):
            col = "#F5F5F7" if i == index else "#8E8E93"
            self.tabs.setTabIcon(i, make_vector_icon(icon_name, col, 15))
        page = self.tabs.widget(index)
        if page is not None:
            fade_in(page, 180)

    def _on_background_moves_received(self, _batch_id: str, moves: list[dict[str, Any]]) -> None:
        def _after_refresh() -> None:
            if moves:
                self.header_mascot.celebrate()
                if self.tbl_activity.rowCount() > 0:
                    flash_rows(
                        self.tbl_activity,
                        list(range(min(len(moves), self.tbl_activity.rowCount()))),
                        "#30D158",
                    )

        self.refresh_all(then=_after_refresh)

    def _sync_autosort_controls(self, paused: bool, held_exists: bool | None = None) -> None:
        if held_exists is None:
            held_exists = self._held_count > 0
        if paused:
            self.header_mascot.set_pose("sleeping")
            self.lbl_status_dot.setStyleSheet("color: #FF9F0A; font-size: 11px;")
            breathe(self.lbl_status_dot, False)
            self.lbl_status_badge.setText("PAUSED")
            self.lbl_status_badge.setProperty("variant", "warning")
            _refresh_style(self.lbl_status_badge)

            toggle_text = "Turn Auto-Sort ON"
            toggle_icon = make_vector_icon("play", "#082411", 14)
            toggle_style = (
                "background-color: #30D158; border: 1px solid #30D158; "
                "color: #082411; font-weight: 600;"
            )
            self.lbl_autosort_explain.setText(
                "Automatic Sorting is PAUSED — Click 'Turn Auto-Sort ON' to resume watching."
            )
            self.lbl_autosort_explain.setStyleSheet("color: #FF9F0A; font-weight: 500; font-size: 12px;")
        else:
            if self.header_mascot.pose != "celebrating":
                self.header_mascot.set_pose("safe" if held_exists else "smart")
            self.lbl_status_dot.setStyleSheet("color: #30D158; font-size: 11px;")
            breathe(self.lbl_status_dot, True)
            self.lbl_status_badge.setText("PROTECTED HOLD" if held_exists else "WATCHER ACTIVE")
            self.lbl_status_badge.setProperty("variant", "primary" if held_exists else "success")
            _refresh_style(self.lbl_status_badge)

            toggle_text = "Auto-Sort: ON"
            toggle_icon = make_vector_icon("pause", "#30D158", 14)
            toggle_style = (
                "background-color: rgba(48, 209, 88, 28); border: 1px solid rgba(48, 209, 88, 85); "
                "color: #30D158; font-weight: 600;"
            )
            self.lbl_autosort_explain.setText(
                "Automatic Sorting is ON — New downloads are organized automatically."
            )
            self.lbl_autosort_explain.setStyleSheet("color: #30D158; font-weight: 500; font-size: 12px;")

        for btn in (self.btn_pause, self.btn_home_autosort):
            btn.setText(toggle_text)
            btn.setIcon(toggle_icon)
            btn.setStyleSheet(toggle_style)
            btn.setToolTip(
                "Automatic sorting is PAUSED — click to resume watching Downloads"
                if paused
                else "Automatic sorting is ON — click to pause watching Downloads"
            )

    def refresh_all(self, then: Callable[[], None] | None = None) -> None:
        """Refresh every view. Config-only widgets update at once; everything that
        needs the disk or the database is collected off the GUI thread and applied
        when it arrives. ``then`` runs after the fresh data is on screen.

        Under pytest the whole refresh (and ``then``) completes before this returns.
        """
        cfg = self.service.config
        self._sync_autosort_controls(cfg.paused)  # last known hold state; corrected by the snapshot
        slide_anim = getattr(self.instructions_widget, "_motion_slide", None)
        if slide_anim is None or slide_anim.state() != QAbstractAnimation.State.Running:
            self.instructions_widget.setVisible(self._show_home_intro())

        self._refresh_rules()
        self._refresh_settings_fields()
        self._update_live_tester()
        if hasattr(self, "arch_tab"):
            self.arch_tab.sync_from_service()

        if then is not None:
            self._refresh_callbacks.append(then)
        if self._footer_at_refresh is None:
            self._footer_at_refresh = self.lbl_footer.text()
        self._start_snapshot()

    def _start_snapshot(self) -> None:
        if self._refresh_running:
            # One collection at a time; the running one is stale, so go again after it.
            self._refresh_again = True
            return
        self._refresh_running = True
        run_async(self._collect_snapshot, self._on_snapshot_ready, self._on_snapshot_failed, parent=self)

    def _collect_snapshot(self) -> dict[str, Any]:
        """Disk / ADS / SQLite reads only. Runs on a worker thread: no widgets here."""
        service = self.service
        cfg = service.config
        dups = service.list_duplicate_files()
        return {
            "paused": cfg.paused,
            "hold_days": cfg.hold_3mf_days,
            "rules_enabled": sum(1 for r in cfg.rules if r.enabled),
            "watch_dir": service.watch_root,
            "held_count": len(service.list_held_3mf()),
            "dup_count": len(dups),
            "dup_bytes": sum(int(d.get("size_bytes", 0)) for d in dups),
            "simulation": service.simulate_top_level(),
            "activity": service.db.list_active_moves(limit=150),
            "stats": service.db.stats(),
        }

    def _on_snapshot_ready(self, snap: dict[str, Any]) -> None:
        self._refresh_running = False
        if self._refresh_again:
            self._refresh_again = False
            self._start_snapshot()
            return
        self._apply_snapshot(snap)
        self._run_refresh_callbacks()

    def _on_snapshot_failed(self, message: str) -> None:
        self._refresh_running = False
        self._refresh_again = False
        self._footer_at_refresh = None
        logging.error("Could not refresh the window: %s", message)
        self._run_refresh_callbacks()

    def _run_refresh_callbacks(self) -> None:
        callbacks, self._refresh_callbacks = self._refresh_callbacks, []
        for callback in callbacks:
            try:
                callback()
            except Exception:
                logging.exception("Error in post-refresh callback")

    def _apply_snapshot(self, snap: dict[str, Any]) -> None:
        """GUI thread: paint a collected snapshot into the widgets."""
        held_count = int(snap["held_count"])
        self._held_count = held_count
        self._sync_autosort_controls(bool(snap["paused"]), held_count > 0)
        self.lbl_watch_path.setText(str(snap["watch_dir"]))

        self.btn_file_held.setText(
            f"Move Held .3MF ({held_count})" if held_count else "Move Held .3MF"
        )
        self.btn_file_held.setEnabled(held_count > 0)
        self.btn_file_held.setVisible(held_count > 0)

        dup_count = int(snap.get("dup_count", 0))
        dup_bytes = int(snap.get("dup_bytes", 0))
        self._dup_count = dup_count
        self._dup_bytes = dup_bytes
        if hasattr(self, "btn_duplicates"):
            self.btn_duplicates.setText(f"Duplicates ({dup_count})" if dup_count else "Duplicates")
            self.btn_duplicates.setToolTip(
                f"{dup_count} quarantined duplicate(s) ({_format_bytes(dup_bytes)} reclaimable) in _Duplicates/"
                if dup_count
                else "No duplicate files quarantined in _Duplicates/"
            )
            self.btn_duplicates.setStyleSheet(
                "background-color: rgba(255, 159, 10, 0.14); border: 1px solid rgba(255, 159, 10, 0.45); "
                "color: #FFB340; font-weight: 600;"
                if dup_count
                else ""
            )

        self._run_simulation(snap["simulation"], hold_days=int(snap["hold_days"]))
        self._refresh_activity(snap["activity"], watch_dir=snap["watch_dir"])

        # A message a handler wrote to the footer while the data was being collected
        # is newer than this snapshot; do not paint the totals over it.
        footer_before = self._footer_at_refresh
        self._footer_at_refresh = None
        if footer_before is None or self.lbl_footer.text() == footer_before:
            stats = snap["stats"]
            self.lbl_footer.setText(
                f"Files sorted so far: {stats['active_moves']}  |  Undone: {stats['undone_moves']}  "
                f"|  Custom rules active: {snap['rules_enabled']}"
            )

    # ------------------------------------------------------------------
    # Newer version available
    # ------------------------------------------------------------------
    def _on_check_updates_now(self) -> None:
        self.lbl_footer.setText("Checking GitHub for updates...")
        self.check_for_updates(force=True, announce_up_to_date=True)

    def check_for_updates(self, force: bool = False, *, announce_up_to_date: bool = False) -> None:
        """Ask, off the GUI thread, whether a newer version has been published.

        At most one request a day reaches GitHub (see update_check.check); calling
        this more often is free. Never runs on its own inside the test suite.
        """
        if not force and os.environ.get("PYTEST_CURRENT_TEST"):
            return
        enabled = bool(self.service.config.check_for_updates)
        if not force and not update_check.is_enabled(enabled):
            return
        data_dir = self.service.paths.data_dir
        if announce_up_to_date and hasattr(self, "btn_check_updates"):
            self.btn_check_updates.setEnabled(False)

        def _work() -> dict[str, Any] | None:
            try:
                updater.clean_old_downloads(data_dir)
            except Exception:
                logging.debug("Could not tidy old update downloads", exc_info=True)
            return update_check.check(data_dir, enabled=enabled, force=force)

        def _done(release: dict[str, Any] | None) -> None:
            if hasattr(self, "btn_check_updates"):
                self.btn_check_updates.setEnabled(True)
            self._on_update_checked(release, announce_up_to_date=announce_up_to_date)

        def _failed(_message: str) -> None:
            if hasattr(self, "btn_check_updates"):
                self.btn_check_updates.setEnabled(True)
            self._on_update_checked(None, announce_up_to_date=announce_up_to_date)

        run_async(_work, _done, _failed, parent=self)

    def _on_update_checked(
        self, release: dict[str, Any] | None, *, announce_up_to_date: bool = False
    ) -> None:
        if release and not self._update_downloading:
            self._update_release = dict(release)
            self._offer_update()
            return
        if not release and announce_up_to_date:
            msg = f"You're on the latest version (v{update_check.__version__})."
            self.banner.show_message(msg, kind="success")
            self.lbl_footer.setText(msg)

    def _offer_update(self) -> None:
        release = self._update_release or {}
        version = update_check.display_version(release)
        if updater.can_self_update() and updater.has_installer(release):
            self.banner.show_message(
                f"Download Sorter {version} is available. Your rules and settings are kept.",
                kind="info",
                action_text="Download and install",
                action=self._start_update_download,
                timeout_ms=0,
            )
        else:
            url = str(release.get("url") or update_check.RELEASES_PAGE)
            self.banner.show_message(
                f"Download Sorter {version} is available. Download it, then run the installer; "
                "your rules and settings are kept.",
                kind="info",
                action_text="Open download page",
                action=lambda: QDesktopServices.openUrl(QUrl(url)),
                timeout_ms=0,
            )

    def _start_update_download(self) -> None:
        release = dict(self._update_release or {})
        if self._update_downloading or not updater.has_installer(release):
            return
        self._update_downloading = True
        version = update_check.display_version(release)
        data_dir = self.service.paths.data_dir
        self.banner.show_message(f"Downloading Download Sorter {version}...", kind="info", timeout_ms=0)

        def _work() -> str:
            # Downloads, then checks size and SHA-256 against the release's checksums.txt.
            return str(updater.download(release, data_dir))

        def _done(installer_path: str) -> None:
            try:
                updater.start_install(Path(installer_path))
            except updater.UpdateError as exc:
                _failed(str(exc))
                return
            self.banner.show_message(
                f"Installing Download Sorter {version}. It will close and open again in a moment.",
                kind="success",
                timeout_ms=0,
            )

        def _failed(message: str) -> None:
            self._update_downloading = False
            # run_async reports "ExceptionName: text"; the user only needs the text.
            text = message.split(": ", 1)[-1] or "Couldn't download the update."
            self.banner.show_message(
                text, kind="warning", action_text="Try again", action=self._start_update_download, timeout_ms=0
            )

        try:
            run_async(_work, _done, _failed, parent=self)
        except updater.UpdateError as exc:  # inline (test) mode raises directly
            _failed(str(exc))

    def _restore_mascot_resting(self) -> None:
        self.header_mascot.set_working(False)
        self.header_mascot.set_pose("sleeping" if self.service.paused else ("safe" if self._held_count > 0 else "smart"))

    def _on_arch_action_started(self, title: str) -> None:
        self.header_mascot.set_working(True, mode="scanning")
        self.progress_hud.start(title, detail="Preparing...", total=0)
        self.lbl_footer.setText(title)

    def _on_arch_action_progress(self, done: int, total: int, label: str) -> None:
        if total > 0:
            self.header_mascot.set_working(True, mode="working")
        self.progress_hud.update_progress(done, total, label)

    def _on_arch_action_finished(self, summary: str, kind: str) -> None:
        self._restore_mascot_resting()
        self.progress_hud.finish(summary, kind="warning" if kind == "warning" else "success")

    def _run_service_action(
        self,
        work: Callable[..., Any],
        on_done: Callable[[Any], None],
        busy_text: str = "Working...",
        *,
        pass_progress: bool = False,
        finish_text: str = "Done",
    ) -> None:
        """Run a file-moving service call off the GUI thread, then ``on_done(result)``.

        Only one such action runs at a time; a second click while one is in flight
        is ignored rather than queued, so a double-click cannot sort twice.
        """
        if self._action_running:
            self.lbl_footer.setText("Still working on the previous request...")
            return
        self._action_running = True
        self.header_mascot.set_working(True, mode="scanning")
        self.progress_hud.start(busy_text, detail="Preparing...", total=0)
        self.lbl_footer.setText(busy_text)

        def _on_progress(done: int, total: int, label: str) -> None:
            if total > 0:
                self.header_mascot.set_working(True, mode="working")
            self.progress_hud.update_progress(done, total, label)

        def _finished(result: Any) -> None:
            self._action_running = False
            self._restore_mascot_resting()
            self.progress_hud.finish(finish_text, kind="success")
            on_done(result)

        def _failed(message: str) -> None:
            self._action_running = False
            self._restore_mascot_resting()
            self.progress_hud.finish("Action did not finish", detail=message, kind="warning")
            self.banner.show_message(f"That did not finish: {message}", kind="warning")
            self.lbl_footer.setText(f"That did not finish: {message}")
            self.refresh_all()

        try:
            run_async(
                work,
                _finished,
                _failed,
                parent=self,
                on_progress=_on_progress,
                pass_progress=pass_progress,
            )
        except Exception:
            self._action_running = False
            self._restore_mascot_resting()
            self.progress_hud.collapse()
            raise

    def _refresh_activity(
        self,
        rows: list[dict[str, Any]] | None = None,
        watch_dir: Path | None = None,
    ) -> None:
        if rows is None:
            rows = self.service.db.list_active_moves(limit=150)
        if watch_dir is None:
            watch_dir = self.service.watch_root
        self.lbl_activity_empty.setVisible(len(rows) == 0)
        self.tbl_activity.setRowCount(len(rows))
        for r_idx, rec in enumerate(rows):
            ts = datetime.fromtimestamp(float(rec["moved_at"])).strftime("%Y-%m-%d %H:%M:%S")
            dest_rel = str(rec["dest_path"])
            try:
                dest_rel = str(Path(dest_rel).relative_to(watch_dir))
            except ValueError:
                pass
            self.tbl_activity.setItem(r_idx, 0, QTableWidgetItem(ts))
            self.tbl_activity.setItem(r_idx, 1, QTableWidgetItem(str(rec["filename"])))
            self.tbl_activity.setItem(r_idx, 2, QTableWidgetItem(dest_rel))
            self.tbl_activity.setItem(r_idx, 3, QTableWidgetItem(str(rec["reason"])))

            btn = QPushButton("Undo")
            btn.setToolTip("Restore this file back to Downloads")
            move_id = int(rec["id"])
            btn.clicked.connect(lambda _=False, mid=move_id: self._on_undo_single(mid))
            self.tbl_activity.setCellWidget(r_idx, 4, btn)

    def _refresh_rules(self) -> None:
        rules = self.service.config.rules
        self.tbl_rules.setRowCount(len(rules))
        for idx, rule in enumerate(rules):
            chk = QCheckBox()
            chk.setAccessibleName(f"Rule {rule.name} is on")
            chk.setToolTip(f"Turn the rule '{rule.name}' on or off")
            chk.setChecked(rule.enabled)
            chk.toggled.connect(lambda checked, i=idx: self._toggle_rule_enabled(i, checked))
            holder = QWidget()
            hl = QHBoxLayout(holder)
            hl.setContentsMargins(6, 0, 6, 0)
            hl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hl.addWidget(chk)
            self.tbl_rules.setCellWidget(idx, 0, holder)

            self.tbl_rules.setItem(idx, 1, QTableWidgetItem(rule.name))
            self.tbl_rules.setItem(idx, 2, QTableWidgetItem(rule.destination))
            self.tbl_rules.setItem(idx, 3, QTableWidgetItem(", ".join(rule.keywords)))
            self.tbl_rules.setItem(idx, 4, QTableWidgetItem(", ".join(rule.domains)))
            self.tbl_rules.setItem(idx, 5, QTableWidgetItem(", ".join(rule.extensions)))
        self._refresh_rule_suggestion()

    def _refresh_rule_suggestion(self) -> None:
        try:
            suggestions = get_rule_suggestions(self.service.config, self.service.paths.data_dir)
        except Exception:
            logging.debug("Could not read learned rule suggestions", exc_info=True)
            suggestions = []
        self._current_suggestion = suggestions[0] if suggestions else None
        if self._current_suggestion is not None:
            more = len(suggestions) - 1
            text = f"{self._current_suggestion['summary']}. Make that a rule?"
            if more > 0:
                text += f"  ({more} more suggestion{'s' if more != 1 else ''})"
            self.lbl_rule_suggestion.setText(text)
            self.btn_rule_suggestion_compact.setToolTip(text)
        self._place_rule_suggestion()

    def _place_rule_suggestion(self) -> None:
        """Show the suggestion as a full bar, or as a toolbar button in a short window."""
        has = self._current_suggestion is not None
        roomy = self.height() >= self.TESTER_TRACE_MIN_HEIGHT
        show_bar, show_btn = has and roomy, has and not roomy
        changed = False
        if self.frm_rule_suggestion.isHidden() == show_bar:
            self.frm_rule_suggestion.setVisible(show_bar)
            changed = True
        if self.btn_rule_suggestion_compact.isHidden() == show_btn:
            self.btn_rule_suggestion_compact.setVisible(show_btn)
            changed = True
        if changed:
            _relayout_up(self.frm_rule_suggestion)

    def _show_rule_suggestion_menu(self) -> None:
        if self._current_suggestion is None:
            return
        menu = QMenu(self)
        title = menu.addAction(str(self._current_suggestion["summary"]))
        title.setEnabled(False)
        menu.addSeparator()
        menu.addAction("Make a Rule...", self._accept_rule_suggestion)
        menu.addAction("Not Now", self._dismiss_rule_suggestion)
        btn = self.btn_rule_suggestion_compact
        menu.exec(btn.mapToGlobal(QPoint(0, btn.height())))

    def _accept_rule_suggestion(self) -> None:
        sugg = self._current_suggestion
        if sugg is None:
            return
        host = str(sugg.get("host") or "")
        ext = str(sugg.get("ext") or "")
        draft = Rule(
            id="",
            name=f"Files from {host}" if host else f"{ext.lstrip('.').upper()} files",
            destination=str(sugg["destination"]),
            domains=[host] if host else [],
            extensions=[ext] if ext and not host else [],
        )
        dlg = RuleEditorDialog(draft, parent=self)
        dlg.setWindowTitle("Make a Rule From What You Did")
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        cfg = copy.deepcopy(self.service.config)
        # Added last, after your own rules, so a learned rule never jumps ahead of them.
        cfg.rules.append(dlg.build_rule())
        self._commit_config(cfg)
        last = len(self.service.config.rules) - 1
        self.tbl_rules.selectRow(last)
        flash_rows(self.tbl_rules, [last], "#0A84FF")

    def _dismiss_rule_suggestion(self) -> None:
        sugg = self._current_suggestion
        if sugg is None:
            return
        try:
            dismiss_suggestion(str(sugg["key"]), self.service.paths.data_dir)
        except OSError as exc:
            self.banner.show_message(f"Could not save that choice: {exc}", kind="warning")
        self._refresh_rule_suggestion()

    def _refresh_settings_fields(self) -> None:
        cfg = self.service.config
        self.chk_auto_sort.setChecked(not cfg.paused)
        self.edit_watch_folder.setText(display_watch_folder(cfg.watch_folder))
        self.spin_settle.setValue(cfg.settle_seconds)
        idx = self.combo_fallback.findData(cfg.fallback_mode)
        self.combo_fallback.setCurrentIndex(max(0, idx))
        self.chk_type_subfolders.setChecked(cfg.use_type_subfolders)
        self.chk_notifications.setChecked(cfg.notifications_enabled)
        self.chk_check_updates.setChecked(cfg.check_for_updates)
        self.spin_ribbon_timeout.setValue(max(1.0, min(30.0, float(cfg.ribbon_timeout_seconds))))
        self.spin_ribbon_timeout.setEnabled(cfg.notifications_enabled)
        self.chk_quick_switcher.setChecked(cfg.quick_switcher_enabled)
        self.spin_qs_timeout.setValue(cfg.quick_switcher_timeout_seconds)
        self.spin_qs_3d_hours.setValue(cfg.quick_switcher_3d_timeout_hours)
        self.edit_qs_hotkey.setText(cfg.quick_switcher_hotkey)
        self.spin_qs_timeout.setEnabled(cfg.quick_switcher_enabled)
        self.spin_qs_3d_hours.setEnabled(cfg.quick_switcher_enabled)
        self.edit_qs_hotkey.setEnabled(cfg.quick_switcher_enabled)
        self.chk_hold_3mf.setChecked(cfg.hold_3mf_enabled)
        self.spin_hold_days.setValue(max(1, int(cfg.hold_3mf_days)))
        self.spin_hold_days.setEnabled(cfg.hold_3mf_enabled)
        self.chk_auto_unzip_all.setChecked(bool(cfg.auto_unzip_all))
        self.chk_zip_keep_original.setChecked(bool(cfg.zip_keep_original))
        self.chk_zip_keep_original.setEnabled(bool(cfg.auto_unzip_all))
        self.chk_startup.setChecked(is_startup_enabled())
        self.chk_context_menu.setChecked(is_context_menu_enabled())
        self._refresh_chrome_status()

    def _refresh_chrome_status(self) -> None:
        if not hasattr(self, "lbl_chrome_status"):
            return
        registered = is_chrome_host_registered(self.service.paths.data_dir)
        ext_dir = find_extension_dir()
        if registered:
            self.lbl_chrome_status.setText("HOST REGISTERED")
            self.lbl_chrome_status.setProperty("variant", "success")
            self.btn_install_chrome_host.setText("Repair Host")
        else:
            self.lbl_chrome_status.setText("HOST NOT SET")
            self.lbl_chrome_status.setProperty("variant", "warning")
            self.btn_install_chrome_host.setText("Install Chrome Host")
        _refresh_style(self.lbl_chrome_status)
        self.btn_open_chrome_ext.setEnabled(ext_dir is not None)
        self.btn_copy_chrome_path.setEnabled(ext_dir is not None)

    def _on_install_chrome_host(self) -> None:
        info = install_chrome_native_host(self.service.paths.data_dir)
        self._refresh_chrome_status()
        msg = f"Chrome Native Messaging host registered ({Path(info['host_target']).name})."
        self.banner.show_message(msg, kind="success")
        self.lbl_footer.setText(msg)

    def _on_open_chrome_extension(self) -> None:
        ext_dir = find_extension_dir()
        if ext_dir is None:
            QMessageBox.warning(self, "Chrome Extension", "Could not find the chrome_extension folder.")
            return
        if sys.platform == "win32" and hasattr(os, "startfile"):
            os.startfile(str(ext_dir))

    def _on_copy_chrome_extension_path(self) -> None:
        ext_dir = find_extension_dir()
        if ext_dir is None:
            QMessageBox.warning(self, "Chrome Extension", "Could not find the chrome_extension folder.")
            return
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText(str(ext_dir))
        msg = f"Copied extension path to clipboard: {ext_dir}"
        self.banner.show_message("Copied Chrome extension folder path — paste it in 'Load unpacked'.", kind="info")
        self.lbl_footer.setText(msg)

    def open_chrome_walkthrough(self) -> None:
        """Open the interactive 4-step picture guide for loading the Chrome extension."""
        dlg = ChromeExtensionWalkthroughDialog(
            self.service.paths.data_dir,
            on_host_updated=self._refresh_chrome_status,
            parent=self,
        )
        dlg.exec()

    def _update_live_tester(self) -> None:
        filename = self.edit_test_name.text().strip()
        if not filename:
            self.lbl_test_result.setText("Type a filename above to see which folder it will move into.")
            self.list_test_trace.clear()
            return
        if filename.lower() in ("sorty", "party", "disco", "easteregg", "benchy.party"):
            self.trigger_easter_egg(from_tester=True)
            self.lbl_test_result.setText(
                "SECRET EASTER EGG UNLOCKED: Sorty Party Mode! -> Downloads\\Party Lounge\\Sorty_VIP"
            )
            self.list_test_trace.clear()
            self.list_test_trace.addItem("EASTER EGG MATCH: Secret guest pass recognized.")
            self.list_test_trace.addItem("Sorty equipped the golden crown & Deal With It shades!")
            return
        decision = self.service.explain(filename, self.edit_test_url.text().strip())
        if decision.action == "move":
            dest_label = f"-> {decision.relative_dest.parent}" if decision.relative_dest else ""
            self.lbl_test_result.setText(f"{dest_label}   ({decision.reason})")
        else:
            self.lbl_test_result.setText(f"[{decision.action.upper()}]   ({decision.reason})")
        fade_in(self.lbl_test_result, 150)

        self.list_test_trace.clear()
        for step in decision.trace:
            tag = "MATCH" if step.matched else "SKIP "
            item = QListWidgetItem(f"[{tag}] {step.stage} | {step.label}: {step.detail}")
            if step.matched:
                item.setForeground(QColor("#30D158"))
            self.list_test_trace.addItem(item)

    def _commit_config(self, cfg: Config) -> None:
        try:
            self.service.update_config(cfg)
        except Exception as exc:
            QMessageBox.critical(self, "Save Failed", f"Could not save settings:\n{exc}")
            return
        self.refresh_all()

    def _open_downloads_folder(self) -> None:
        target = self.service.watch_root
        if sys.platform == "win32" and hasattr(os, "startfile") and target.exists():
            os.startfile(str(target))

    def _toggle_pause(self) -> None:
        new_paused = not self.service.paused
        try:
            self.service.set_paused(new_paused)
        except Exception as exc:
            QMessageBox.critical(self, "Save Failed", str(exc))
            return
        self.refresh_all()
        if new_paused:
            self.banner.show_message("Automatic sorting paused.", kind="warning")
        else:
            self.banner.show_message(
                "Automatic sorting turned ON — watching Downloads in the background.",
                kind="success",
            )

    def _on_preview_ribbon(self) -> None:
        watch_root = self.service.config.resolved_watch_folder(self.service.paths)
        sample = [
            {
                "file_name": "bracket_assembly.3mf",
                "dest_path": str(watch_root / "3D Models" / "bracket_assembly.3mf"),
                "folder_display": "3D Models",
            }
        ]
        timeout_ms = int(self.spin_ribbon_timeout.value() * 1000)
        self._preview_toast_mgr.default_duration_ms = timeout_ms
        self._preview_toast_mgr.show_moves("preview-ribbon", sample, duration_ms=timeout_ms)
        self.lbl_footer.setText(
            f"Previewing top-right Side Ribbon ({int(self.spin_ribbon_timeout.value())}s timer, hover to pause)."
        )

    def _on_close_all_ribbons(self) -> None:
        self._preview_toast_mgr.close_all()
        self.lbl_footer.setText("Closed active Side Ribbon notifications.")

    def _on_toast_replace_old(self, dup_path: Path, existing_path: Path | None) -> None:
        if existing_path is None:
            self.banner.show_message("Original file path not matched automatically.", kind="warning")
            return
        res = self.service.replace_duplicate_with_new(dup_path, existing_path)
        self.refresh_all()
        if res.get("ok"):
            self.banner.show_message(f"Replaced older copy with {dup_path.name}.", kind="success")
        else:
            self.banner.show_message(str(res.get("error") or "Could not replace older copy."), kind="warning")

    def _on_toast_delete_locate_old(self, dup_path: Path, existing_path: Path | None) -> None:
        res = self.service.delete_duplicate_and_locate_old(dup_path, existing_path or Path(""))
        self.refresh_all()
        if res.get("ok"):
            resolved_old = str(res.get("existing_path") or (existing_path or ""))
            if resolved_old and Path(resolved_old).exists():
                _reveal_in_explorer(Path(resolved_old))
                self.banner.show_message(
                    f"Deleted duplicate {dup_path.name} and located the older copy.",
                    kind="success",
                )
            else:
                self.banner.show_message(
                    f"Deleted duplicate {dup_path.name} (older copy could not be located).",
                    kind="info",
                )
        else:
            self.banner.show_message(str(res.get("error") or "Could not delete duplicate."), kind="warning")

    def _on_toast_keep_both(self, dup_path: Path, existing_path: Path | None) -> None:
        res = self.service.keep_both_duplicate(dup_path, existing_path or Path(""))
        self.refresh_all()
        if res.get("ok"):
            final_name = Path(str(res.get("final_path") or dup_path)).name
            self.banner.show_message(f"Kept both copies ({final_name}).", kind="success")
        else:
            self.banner.show_message(str(res.get("error") or "Could not keep both copies."), kind="warning")

    def _on_sort_now(self) -> None:
        self._run_service_action(
            lambda report: self.service.scan_top_level_now(on_progress=report),
            lambda res: self.refresh_all(then=lambda: self._after_sort_now(res)),
            busy_text="Sorting Downloads...",
            pass_progress=True,
            finish_text="Sorting Complete",
        )

    def _after_sort_now(self, res: dict[str, Any]) -> None:
        moved_n = len(res["moved"])
        if moved_n:
            self.header_mascot.celebrate()
            flash_rows(
                self.tbl_activity,
                list(range(min(moved_n, self.tbl_activity.rowCount()))),
                "#30D158",
            )
            msg = f"Cleaned up Downloads: moved {moved_n} file(s)."
            self.progress_hud.finish(msg, detail="All eligible files organized", kind="success")
            self.banner.show_message(
                msg,
                kind="success",
                action_text="Undo",
                action=self._on_undo_last,
            )
            self.lbl_footer.setText(msg)
        else:
            held_n = res.get("held", 0)
            self.progress_hud.finish(
                "Downloads looks clean",
                detail=f"0 moved ({held_n} .3MF held, {res['left']} left)",
                kind="success",
            )
            self.banner.show_message("Downloads looks clean — nothing needed moving right now.", kind="info")
            self.lbl_footer.setText(
                f"Everything looks clean: 0 moved ({held_n} .3MF held for 3D printing, {res['left']} left in Downloads)."
            )

    def _on_sort_tiers_now(self) -> None:
        self._run_service_action(
            lambda report: self.service.sort_managed_tiers_now(on_progress=report),
            lambda res: self.refresh_all(then=lambda: self._after_sort_tiers_now(res)),
            busy_text="Sorting lower tiers...",
            pass_progress=True,
            finish_text="Lower Tiers Organized",
        )

    def _after_sort_tiers_now(self, res: dict[str, Any]) -> None:
        moved_n = len(res["moved"])
        if moved_n:
            self.header_mascot.celebrate()
            flash_rows(
                self.tbl_activity,
                list(range(min(moved_n, self.tbl_activity.rowCount()))),
                "#30D158",
            )
            msg = f"Sorted lower tiers: moved {moved_n} file(s) into their target folders."
            self.progress_hud.finish(msg, kind="success")
            self.banner.show_message(msg, kind="success", action_text="Undo", action=self._on_undo_last)
            self.lbl_footer.setText(msg)
        else:
            msg = "Lower tiers are already organized — nothing needed moving."
            self.progress_hud.finish("Lower tiers are already organized", kind="success")
            self.banner.show_message(msg, kind="info")
            self.lbl_footer.setText(msg)

    def _on_file_held_now(self) -> None:
        self._run_service_action(
            lambda report: self.service.file_held_3mf_now(on_progress=report),
            lambda res: self.refresh_all(then=lambda: self._after_file_held_now(res)),
            busy_text="Moving held .3MF files...",
            pass_progress=True,
            finish_text="Moved Held .3MF Files",
        )

    def _after_file_held_now(self, res: dict[str, Any]) -> None:
        moved_n = len(res["moved"])
        if moved_n:
            self.header_mascot.celebrate()
            flash_rows(
                self.tbl_activity,
                list(range(min(moved_n, self.tbl_activity.rowCount()))),
                "#30D158",
            )
            self.banner.show_message(
                f"Moved {moved_n} held .3MF file(s) into 3D Models.",
                kind="success",
                action_text="Undo",
                action=self._on_undo_last,
            )
            self.lbl_footer.setText(f"Moved {moved_n} held .3MF file(s) into 3D Models.")
        else:
            self.lbl_footer.setText("No held .3MF files waiting in Downloads.")

    @staticmethod
    def _undo_notes(res: dict[str, Any]) -> str:
        """Plain-language tail for anything an undo could not put back as-is."""
        notes: list[str] = []
        locked = int(res.get("skipped_locked", 0) or 0)
        if locked:
            names = ", ".join(Path(p).name for p in list(res.get("locked_files", []))[:2])
            more = "" if locked <= 2 else f" and {locked - 2} more"
            notes.append(f"{locked} still open in another app ({names}{more}) - close it and undo again")
        renamed = len(res.get("restored_renamed", []) or [])
        if renamed:
            notes.append(f"{renamed} restored as '(Restored)' because a newer file has that name")
        orphaned = int(res.get("orphaned", 0) or 0)
        if orphaned:
            notes.append(f"{orphaned} no longer exist and were dropped from history")
        return ("  " + "; ".join(notes) + ".") if notes else ""

    def _on_undo_last(self) -> None:
        def _work(report: Callable[[int, int, str], None]) -> dict[str, Any]:
            return self.service.undo_architecture_or_last_batch(on_progress=report)

        def _done(res: dict[str, Any]) -> None:
            if hasattr(self, "arch_tab"):
                self.arch_tab._dirty = False
            self.refresh_all(then=lambda: self._after_undo_last(res))

        self._run_service_action(
            _work,
            _done,
            busy_text="Undoing...",
            pass_progress=True,
            finish_text="Undo Complete",
        )

    def _after_undo_last(self, res: dict[str, Any]) -> None:
        restored = res.get("restored", 0)
        cfg_restored = res.get("config_restored", False)
        notes = self._undo_notes(res)
        if restored or cfg_restored:
            flash_rows(self.tbl_sim, list(range(min(5, self.tbl_sim.rowCount()))), "#0A84FF")
            if cfg_restored and not restored:
                msg = "Undid last Folder Architecture change and restored your previous rules."
            elif cfg_restored:
                msg = f"Undid architecture change and restored {restored} shifted file(s)."
            else:
                msg = f"Undid last sort: restored {restored} file(s) back to Downloads."
            msg += notes
            self.banner.show_message(msg, kind="warning" if res.get("skipped_locked") else "info")
            self.lbl_footer.setText(msg)
        elif notes:
            msg = "Nothing was restored." + notes
            self.banner.show_message(msg, kind="warning")
            self.lbl_footer.setText(msg)
        else:
            self.lbl_footer.setText("Nothing to undo.")

    def _on_undo_single(self, move_id: int) -> None:
        self._run_service_action(
            lambda report: self.service.undo_single_move(move_id, on_progress=report),
            lambda res: self.refresh_all(then=lambda: self._after_undo_single(res)),
            busy_text="Restoring file...",
            pass_progress=True,
            finish_text="File Restored",
        )

    def _after_undo_single(self, res: dict[str, Any]) -> None:
        notes = self._undo_notes(res)
        if res.get("restored"):
            flash_rows(self.tbl_sim, list(range(min(3, self.tbl_sim.rowCount()))), "#0A84FF")
            self.banner.show_message("Restored file back to Downloads." + notes, kind="info")
        elif notes:
            self.banner.show_message("Nothing was restored." + notes, kind="warning")

    def _on_refresh_preview_clicked(self) -> None:
        def _show(results: list[tuple[Path, Any]]) -> None:
            self._run_simulation(results)
            if self.tbl_sim.rowCount() > 0:
                flash_rows(self.tbl_sim, list(range(min(5, self.tbl_sim.rowCount()))), "#0A84FF")

        run_async(self.service.simulate_top_level, _show, parent=self)

    def _selected_rule_row(self) -> int:
        row = self.tbl_rules.currentRow()
        return row if 0 <= row < len(self.service.config.rules) else -1

    def _toggle_rule_enabled(self, index: int, enabled: bool) -> None:
        cfg = copy.deepcopy(self.service.config)
        if 0 <= index < len(cfg.rules):
            cfg.rules[index].enabled = enabled
            self._commit_config(cfg)

    def _move_rule(self, delta: int) -> None:
        row = self._selected_rule_row()
        if row < 0:
            return
        new_row = row + delta
        cfg = copy.deepcopy(self.service.config)
        if not (0 <= new_row < len(cfg.rules)):
            return
        cfg.rules[row], cfg.rules[new_row] = cfg.rules[new_row], cfg.rules[row]
        self._commit_config(cfg)
        self.tbl_rules.selectRow(new_row)
        flash_rows(self.tbl_rules, [new_row], "#0A84FF")

    def _sort_rules_by_specificity(self) -> None:
        from download_sorter.router import sort_rules_by_specificity

        cfg = copy.deepcopy(self.service.config)
        if len(cfg.rules) < 2:
            return
        cfg.rules = sort_rules_by_specificity(cfg.rules)
        self._commit_config(cfg)
        self.banner.show_message("Sorted rules by specificity (narrowest rules first).", kind="info")

    def _add_rule(self) -> None:
        dlg = RuleEditorDialog(parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            cfg = copy.deepcopy(self.service.config)
            cfg.rules.insert(0, dlg.build_rule())
            self._commit_config(cfg)
            self.tbl_rules.selectRow(0)
            flash_rows(self.tbl_rules, [0], "#0A84FF")

    def _edit_rule(self) -> None:
        row = self._selected_rule_row()
        if row < 0:
            return
        dlg = RuleEditorDialog(self.service.config.rules[row], parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            cfg = copy.deepcopy(self.service.config)
            cfg.rules[row] = dlg.build_rule()
            self._commit_config(cfg)
            self.tbl_rules.selectRow(row)
            flash_rows(self.tbl_rules, [row], "#0A84FF")

    def _delete_rule(self) -> None:
        row = self._selected_rule_row()
        if row < 0:
            return
        cfg = copy.deepcopy(self.service.config)
        cfg.rules.pop(row)
        self._commit_config(cfg)

    def _apply_template(self, template_key: str) -> None:
        cfg = copy.deepcopy(self.service.config)
        added = cfg.apply_template(template_key)
        self._commit_config(cfg)
        label = STARTER_PACK_LABELS.get(template_key, (template_key, ""))[0]
        message = (
            f"Added the {label} rule pack: {added} new rule(s)."
            if added
            else f"The {label} rule pack is already in your rules."
        )
        self.banner.show_message(message, kind="success" if added else "info")
        self.lbl_footer.setText(message)

    def _on_sorty_clicked(self, tip: str) -> None:
        if "SECRET UNLOCKED" in tip or "PARTY MODE" in tip.upper():
            return
        msg = f"Sorty Tip: {tip}"
        self.banner.show_message(msg, kind="info")
        self.lbl_footer.setText(msg)

    def trigger_easter_egg(self, *, from_tester: bool = False) -> None:
        """Activate Sorty Party Mode + confetti celebration Easter Egg."""
        if getattr(self, "_in_easter_egg", False):
            return
        self._in_easter_egg = True
        try:
            if not self.header_mascot._party_mode:
                self.header_mascot.start_party_mode()
            self._confetti.burst(duration_ms=2800)
            msg = (
                "Secret Easter Egg unlocked via Rule Tester! Sorty joined the VIP Party Lounge!"
                if from_tester
                else "Secret Easter Egg unlocked! Sorty found the Golden Crown & Deal With It shades!"
            )
            self.banner.show_message(msg, kind="success")
            self.lbl_footer.setText(msg)
        finally:
            self._in_easter_egg = False

    def _on_architecture_applied(self, summary_msg: str) -> None:
        self.refresh_all(then=lambda: self._after_architecture_applied(summary_msg))

    def _after_architecture_applied(self, summary_msg: str) -> None:
        if (
            "Nothing to undo" in summary_msg
            or summary_msg.startswith("Auto-created")
            or summary_msg.startswith("Scanned ")
            or summary_msg.startswith("Loaded ")
            or summary_msg.startswith("Restored ")
        ):
            self.banner.show_message(summary_msg, kind="info")
        elif summary_msg.startswith("Undid"):
            flash_rows(self.tbl_sim, list(range(min(5, self.tbl_sim.rowCount()))), "#0A84FF")
            self.banner.show_message(summary_msg, kind="info")
        else:
            self.header_mascot.celebrate()
            flash_rows(self.tbl_sim, list(range(min(5, self.tbl_sim.rowCount()))), "#30D158")
            self.banner.show_message(
                summary_msg,
                kind="success",
                action_text="Undo",
                action=self._on_undo_last,
            )
        self.lbl_footer.setText(summary_msg)

    def open_carry_over_dialog(self) -> None:
        dlg = UpgradeCarryOverDialog(self.service, self)
        if dlg.exec() != QDialog.DialogCode.Accepted or dlg.carried_config is None:
            return
        carried_config = dlg.carried_config
        shift_now = bool(dlg.should_shift_now)
        summary = dict(dlg.carry_summary)

        def _work(report: Callable[[int, int, str], None]) -> dict[str, Any]:
            return self.service.apply_architecture_and_shift(
                carried_config,
                shift_files=shift_now,
                create_empty_folders=False,
                on_progress=report,
            )

        def _announce(res: dict[str, Any]) -> None:
            shifted = res.get("shifted_count", 0)
            msg = (
                f"Carried over {summary.get('rules_imported', 0)} rule(s) & "
                f"{summary.get('tier_count', 2)}-tier setup"
                + (f" (shifted {shifted} file(s))." if shifted else ".")
            )
            self.header_mascot.celebrate()
            self.progress_hud.finish("Setup Carried Over", detail=msg, kind="success")
            self.banner.show_message(msg, kind="success", action_text="Undo", action=self._on_undo_last)
            self.lbl_footer.setText(msg)

        def _done(res: dict[str, Any]) -> None:
            if hasattr(self, "arch_tab"):
                self.arch_tab._dirty = False
                self.arch_tab.sync_from_service(force=True)
            self.refresh_all(then=lambda: _announce(res))

        self._run_service_action(
            _work,
            _done,
            busy_text="Carrying over your setup...",
            pass_progress=True,
            finish_text="Setup Carried Over",
        )

    def _export_current_setup(self) -> None:
        out_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Complete Setup & Folder Architecture",
            str(Path.home() / "DownloadSorter-Setup-Backup.json"),
            "JSON Files (*.json)",
        )
        if not out_path:
            return
        try:
            export_setup_file(self.service.config, Path(out_path))
            msg = f"Exported complete setup & Folder Architecture to {Path(out_path).name}."
            self.banner.show_message(msg, kind="success")
            self.lbl_footer.setText(msg)
        except Exception as exc:
            QMessageBox.critical(self, "Export Failed", str(exc))

    def _import_v2_rules(self) -> None:
        v2_path = AppPaths.v2_config_file()
        if not v2_path.exists():
            QMessageBox.information(self, "No Old Rules Found", f"No v2.8 config was found at:\n{v2_path}")
            return
        try:
            imported = import_v2_config(v2_path)
            cfg = copy.deepcopy(self.service.config)
            existing = {r.name.lower() for r in cfg.rules}
            added = 0
            for rule in imported.rules:
                if rule.name.lower() not in existing:
                    cfg.rules.append(rule)
                    existing.add(rule.name.lower())
                    added += 1
            self._commit_config(cfg)
            self.banner.show_message(f"Imported {added} rule(s) from {v2_path.name}.", kind="success")
            self.lbl_footer.setText(f"Imported {added} rule(s) from {v2_path.name}.")
        except Exception as exc:
            QMessageBox.critical(self, "Import Failed", str(exc))

    def _browse_watch_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Select Folder to Organize", str(self.service.watch_root))
        if chosen:
            self.edit_watch_folder.setText(chosen)

    def _save_settings_form(self) -> None:
        cfg = copy.deepcopy(self.service.config)
        cfg.paused = not self.chk_auto_sort.isChecked()
        typed = self.edit_watch_folder.text().strip()
        cfg.watch_folder = stored_watch_folder(typed)
        cfg.settle_seconds = float(self.spin_settle.value())
        cfg.fallback_mode = str(self.combo_fallback.currentData() or "other")
        cfg.use_type_subfolders = self.chk_type_subfolders.isChecked()
        cfg.notifications_enabled = self.chk_notifications.isChecked()
        cfg.check_for_updates = self.chk_check_updates.isChecked()
        cfg.ribbon_timeout_seconds = float(self.spin_ribbon_timeout.value())
        self._preview_toast_mgr.default_duration_ms = int(cfg.ribbon_timeout_seconds * 1000)
        cfg.quick_switcher_enabled = self.chk_quick_switcher.isChecked()
        cfg.quick_switcher_timeout_seconds = float(self.spin_qs_timeout.value())
        cfg.quick_switcher_3d_timeout_hours = float(self.spin_qs_3d_hours.value())
        typed_hotkey = self.edit_qs_hotkey.text().strip()
        hotkey_note = ""
        if typed_hotkey and parse_hotkey(typed_hotkey) is None:
            hotkey_note = (
                f"Settings saved, but kept {cfg.quick_switcher_hotkey} as the hotkey: \"{typed_hotkey}\" "
                "can't be used. A hotkey needs Ctrl or Alt plus a letter, number or F-key, like Alt+F2."
            )
        else:
            cfg.quick_switcher_hotkey = normalize_hotkey(typed_hotkey, "Ctrl+G")
        self.edit_qs_hotkey.setText(cfg.quick_switcher_hotkey)
        cfg.hold_3mf_enabled = self.chk_hold_3mf.isChecked()
        cfg.hold_3mf_days = int(self.spin_hold_days.value())
        cfg.auto_unzip_all = self.chk_auto_unzip_all.isChecked()
        cfg.zip_keep_original = self.chk_zip_keep_original.isChecked()
        self._commit_config(cfg)

        warnings: list[str] = []
        want_startup = self.chk_startup.isChecked()
        if want_startup != is_startup_enabled() and not set_startup_enabled(want_startup):
            warnings.append("Could not update Windows Startup setting.")
        want_ctx = self.chk_context_menu.isChecked()
        if want_ctx != is_context_menu_enabled() and not set_context_menu_enabled(want_ctx):
            warnings.append("Could not update File Explorer right-click menu setting.")

        if warnings:
            QMessageBox.warning(self, "Windows Integration Warning", "\n".join(warnings))
            self.banner.show_message("Saved settings, but Windows integration update failed.", kind="warning")
            self.lbl_footer.setText("Saved settings, but Windows integration update failed.")
        elif hotkey_note:
            self.banner.show_message(hotkey_note, kind="warning")
            self.lbl_footer.setText(hotkey_note)
        else:
            self.banner.show_message("Settings saved.", kind="success")
            self.lbl_footer.setText("Settings saved.")

    def _run_simulation(
        self,
        results: list[tuple[Path, Any]] | None = None,
        hold_days: int | None = None,
    ) -> None:
        """Fill the preview table. With no ``results`` it reads the folder itself."""
        if results is None:
            results = self.service.simulate_top_level()
        if hold_days is None:
            hold_days = self.service.config.hold_3mf_days
        self.tbl_sim.clearSelection()
        self.tbl_sim.setRowCount(len(results))
        move_count = sum(1 for _p, d in results if d.action == "move")
        held_count = sum(1 for _p, d in results if d.match_type == "hold")

        for idx, (path, dec) in enumerate(results):
            planned_dest = str(dec.relative_dest.parent) if dec.relative_dest else ""
            if dec.action == "move":
                what = "Will Move"
                badge_variant = "primary"
                dest_text = planned_dest or "Sorted folder"
                why_text = dec.reason
            elif dec.match_type == "hold":
                what = "3D Print Hold"
                badge_variant = "warning"
                dest_text = (
                    f"{planned_dest} (after {hold_days} days, or click 'Move Held .3MF Files')"
                    if planned_dest
                    else f"Held ({hold_days} days)"
                )
                why_text = "New .3MF file kept in Downloads for easy slicing"
            elif dec.action == "ignore":
                what = "Skipped"
                badge_variant = "neutral"
                dest_text = "Stays in Downloads"
                why_text = dec.reason
            else:
                what = "Stays"
                badge_variant = "neutral"
                dest_text = "Downloads (unchanged)"
                why_text = dec.reason

            name_item = QTableWidgetItem(path.name)
            name_item.setData(Qt.ItemDataRole.UserRole, str(path))
            name_item.setToolTip(f"{path}\nRight-click or press Delete to move to Recycle Bin")
            self.tbl_sim.setItem(idx, 0, name_item)
            holder = QWidget()
            hl = QHBoxLayout(holder)
            hl.setContentsMargins(6, 0, 6, 0)
            hl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge = _make_badge(what, badge_variant)
            badge.setMinimumWidth(scaled_px(108))
            hl.addWidget(badge)
            self.tbl_sim.setCellWidget(idx, 1, holder)

            dest_item = QTableWidgetItem(dest_text)
            dest_item.setForeground(QColor("#8E8E93"))
            self.tbl_sim.setItem(idx, 2, dest_item)
            self.tbl_sim.setItem(idx, 3, QTableWidgetItem(why_text))

        if not results:
            self.lbl_sim_summary.setText(
                "Your Downloads folder is completely clean! New downloads will be sorted automatically."
            )
        else:
            self.lbl_sim_summary.setText(
                f"{len(results)} file(s) currently in Downloads "
                f"({move_count} ready to move now, {held_count} held for 3D printing)."
            )
        self._on_sim_selection_changed()

    # ------------------------------------------------------------------
    # Preview Selection Deletion & Native Duplicate Manager
    # ------------------------------------------------------------------
    def _selected_sim_paths(self) -> list[Path]:
        if not hasattr(self, "tbl_sim") or self.tbl_sim.selectionModel() is None:
            return []
        rows = sorted({idx.row() for idx in self.tbl_sim.selectionModel().selectedRows()})
        paths: list[Path] = []
        for row in rows:
            item = self.tbl_sim.item(row, 0)
            if item is None:
                continue
            raw = item.data(Qt.ItemDataRole.UserRole)
            if raw:
                paths.append(Path(str(raw)))
        return paths

    def _on_sim_selection_changed(self) -> None:
        if not hasattr(self, "btn_delete_selected"):
            return
        selected = self._selected_sim_paths()
        count = len(selected)
        self.btn_delete_selected.setText(f"Delete ({count})" if count else "Delete")
        self.btn_delete_selected.setVisible(count > 0)
        self.btn_delete_selected.setEnabled(count > 0)

    def _show_sim_context_menu(self, pos: QPoint) -> None:
        selected = self._selected_sim_paths()
        if not selected:
            row = self.tbl_sim.rowAt(pos.y())
            if row >= 0:
                self.tbl_sim.selectRow(row)
                selected = self._selected_sim_paths()
        if not selected:
            return
        menu = QMenu(self)
        act_reveal = menu.addAction("Show in File Explorer")
        menu.addSeparator()
        act_delete = menu.addAction(
            f"Move {len(selected)} File(s) to Recycle Bin\tDel"
            if len(selected) > 1
            else f"Move '{selected[0].name}' to Recycle Bin\tDel"
        )
        chosen = menu.exec(self.tbl_sim.viewport().mapToGlobal(pos))
        if chosen == act_reveal:
            _reveal_in_explorer(selected[0])
        elif chosen == act_delete:
            self._delete_selected_sim_files(confirm=True)

    def _delete_selected_sim_files(self, *, confirm: bool = True) -> None:
        paths = self._selected_sim_paths()
        if not paths:
            return
        total_bytes = 0
        for p in paths:
            try:
                if p.exists():
                    total_bytes += int(p.stat().st_size)
            except OSError:
                pass
        if confirm:
            preview_names = "\n".join(f"• {p.name}" for p in paths[:6])
            if len(paths) > 6:
                preview_names += f"\n• ...and {len(paths) - 6} more"
            ans = QMessageBox.question(
                self,
                "Move Selected Files to Recycle Bin",
                f"Move {len(paths)} selected file(s) ({_format_bytes(total_bytes)}) to the Windows Recycle Bin?\n\n"
                f"{preview_names}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return

        self._run_service_action(
            lambda report: self.service.delete_watch_files(paths, on_progress=report),
            lambda res: self.refresh_all(then=lambda: self._after_delete_sim_files(res)),
            busy_text=f"Deleting {len(paths)} selected file(s)...",
            pass_progress=True,
            finish_text="Files Removed",
        )

    def _after_delete_sim_files(self, res: dict[str, Any]) -> None:
        deleted_n = len(res.get("deleted", []))
        locked_n = len(res.get("skipped_locked", []))
        freed = _format_bytes(int(res.get("freed_bytes", 0)))
        if deleted_n and not locked_n:
            where = "Recycle Bin" if res.get("recycled") else "disk"
            msg = f"Moved {deleted_n} file(s) ({freed}) to {where}."
            self.progress_hud.finish("Files Moved to Recycle Bin", detail=msg, kind="success")
            self.banner.show_message(msg, kind="success")
            self.lbl_footer.setText(msg)
        elif deleted_n and locked_n:
            msg = f"Deleted {deleted_n} file(s) ({freed}); {locked_n} file(s) are open in another app."
            self.progress_hud.finish("Deleted with Warnings", detail=msg, kind="warning")
            self.banner.show_message(msg, kind="warning")
            self.lbl_footer.setText(msg)
        elif locked_n:
            msg = f"Could not delete {locked_n} file(s) because they are open in another app."
            self.progress_hud.finish("Files Locked", detail=msg, kind="warning")
            self.banner.show_message(msg, kind="warning")
            self.lbl_footer.setText(msg)

    def _open_duplicates_dialog(self) -> DuplicateManagerDialog:
        dlg = DuplicateManagerDialog(self.service, self)
        dlg.duplicates_changed.connect(self.refresh_all)
        dlg.exec()
        self.refresh_all()
        return dlg

    def _open_detox_dialog(self) -> DetoxDialog:
        dlg = DetoxDialog(self.service, self)
        dlg.exec()
        self.refresh_all()
        return dlg

    def _open_quick_search(self) -> None:
        from download_sorter.ui.quick_search_palette import QuickSearchPalette

        dlg = QuickSearchPalette(self.service.db, self.service.watch_root, self)
        dlg.exec()

    def complete_walkthrough(self) -> None:
        """Persist tutorial completion and smoothly hide the Home instructions block."""
        slide_toggle(
            self.instructions_widget,
            False,
            animate=not bool(os.environ.get("PYTEST_CURRENT_TEST")),
        )
        cfg = copy.deepcopy(self.service.config)
        if not cfg.tutorial_completed:
            cfg.tutorial_completed = True
            self._commit_config(cfg)
        self.header_mascot.celebrate()

    def open_walkthrough(self) -> None:
        """Launch the interactive 3-step onboarding walkthrough modal."""
        dlg = WalkthroughDialog(self.service.watch_root, self.service.config.hold_3mf_days, self)
        dlg.exec()
        chosen = dlg.selected_packs()
        if chosen:
            cfg = copy.deepcopy(self.service.config)
            added = sum(cfg.apply_template(key) for key in chosen)
            if added:
                self._commit_config(cfg)
        # Finished, skipped or closed with Esc: the tour has been seen either way.
        # Automatic sorting waits for this, so it must not stay pending forever.
        self.complete_walkthrough()

    def _maybe_show_first_run_walkthrough(self) -> None:
        if self._first_run_prompted or os.environ.get("PYTEST_CURRENT_TEST"):
            return
        self._first_run_prompted = True
        if not self.service.config.tutorial_completed and self.isVisible():
            self.open_walkthrough()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if (
            not self._first_run_prompted
            and not self.service.config.tutorial_completed
            and not os.environ.get("PYTEST_CURRENT_TEST")
        ):
            QTimer.singleShot(250, self._maybe_show_first_run_walkthrough)

    # Narrow window: below the first width the header drops its status badge and
    # Undo / Open Folder / Guide show only their icons; below the second, Auto-Sort
    # and Move Held do too.
    HEADER_COMPACT_WIDTH = 1240
    HEADER_TIGHT_WIDTH = 1000
    # Short window: Home hides its "How Download Sorter Works" card, and the
    # filename tester on Custom Rules hides its step-by-step list.
    HOME_INTRO_MIN_HEIGHT = 740
    TESTER_TRACE_MIN_HEIGHT = 700

    def _fit_to_window(self) -> None:
        width, height = self.width(), self.height()
        if hasattr(self, "lbl_status_badge"):
            self.lbl_status_badge.setVisible(width >= scaled_px(self.HEADER_COMPACT_WIDTH))
            # The widths were tuned for 100% text; bigger text needs room sooner.
            compact, tight = scaled_px(self.HEADER_COMPACT_WIDTH), scaled_px(self.HEADER_TIGHT_WIDTH)
            for btn in (self.btn_undo_last, self.btn_open_downloads, self.btn_walkthrough):
                btn.set_collapsed(width < compact)
            for btn in (self.btn_pause, self.btn_file_held):
                btn.set_collapsed(width < tight)
            for btn in (getattr(self, "btn_detox", None), getattr(self, "btn_quick_search", None)):
                if btn is not None:
                    btn.set_collapsed(width < scaled_px(1200))
        if hasattr(self, "list_test_trace"):
            show_trace = height >= self.TESTER_TRACE_MIN_HEIGHT
            if self.list_test_trace.isHidden() == show_trace:
                self.list_test_trace.setVisible(show_trace)
                _relayout_up(self.list_test_trace)
        if hasattr(self, "frm_rule_suggestion"):
            self._place_rule_suggestion()
        if hasattr(self, "instructions_widget"):
            slide_anim = getattr(self.instructions_widget, "_motion_slide", None)
            if slide_anim is None or slide_anim.state() != QAbstractAnimation.State.Running:
                show_intro = self._show_home_intro()
                if self.instructions_widget.isHidden() == show_intro:
                    self.instructions_widget.setVisible(show_intro)
                    _relayout_up(self.instructions_widget)

    def _show_home_intro(self) -> bool:
        return not self.service.config.tutorial_completed and self.height() >= self.HOME_INTRO_MIN_HEIGHT

    def resizeEvent(self, event) -> None:  # noqa: ANN001
        super().resizeEvent(event)
        self._fit_to_window()
        if hasattr(self, "_confetti") and self._confetti.isVisible():
            self._confetti.setGeometry(self.rect())

    def closeEvent(self, event) -> None:
        breathe(self.lbl_status_dot, False)
        self._preview_toast_mgr.close_all()
        super().closeEvent(event)
