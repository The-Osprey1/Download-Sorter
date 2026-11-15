"""Clean, beginner-friendly 3-tab PySide6 window: Home | Custom Rules | Settings."""

from __future__ import annotations

import copy
from datetime import datetime
import os
from pathlib import Path
import sys
from collections.abc import Callable
import logging
from typing import Any

from PySide6.QtCore import QAbstractAnimation, QPoint, Qt, QTimer, Signal
from PySide6.QtCore import QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsDropShadowEffect,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
)

from download_sorter.config import (
    Config,
    Rule,
    carry_over_setup,
    export_setup_file,
    import_v2_config,
    portable_path,
    readable_path,
)
from download_sorter.paths import AppPaths
from download_sorter.quick_switcher import normalize_hotkey, parse_hotkey
from download_sorter.service import SorterService
from download_sorter import update_check, updater
from download_sorter.ui.architecture_canvas import ArchitectureTab
from download_sorter.ui.motion import (
    AnimatedBanner,
    AnimatedRibbon,
    CollapsibleRibbonBtn,
    ConfettiOverlayWidget,
    ElidedLabel,
    InteractiveRibbonBtn,
    SortyMascotWidget,
    WalkthroughDiagramCanvas,
    breathe,
    fade_in,
    flash_rows,
    make_vector_icon,
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
            "MakerWorld / G-Code / Haas CNC destinations, hold timer, and Folder Architecture."
        )
        sub.setObjectName("MutedText")
        sub.setWordWrap(True)
        root.addWidget(sub)

        src_row = QHBoxLayout()
        src_row.setSpacing(8)
        self.edit_source = QLineEdit()
        v2_default = AppPaths.v2_config_file()
        if v2_default.exists():
            self.edit_source.setText(str(v2_default))
        self.edit_source.setPlaceholderText("Path to v2.8 config.json or exported v3 setup JSON...")
        self.edit_source.textChanged.connect(self._refresh_preview)
        src_row.addWidget(self.edit_source, 1)

        self.btn_browse = InteractiveRibbonBtn("Browse...")
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
        self.btn_export.clicked.connect(self._on_export_inline)
        btn_row.addWidget(self.btn_export)
        btn_row.addStretch(1)

        self.btn_cancel = InteractiveRibbonBtn("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_cancel)

        self.btn_apply = InteractiveRibbonBtn(
            "Carry Over Now",
            make_vector_icon("check", "#FFFFFF", 14),
        )
        self.btn_apply.setObjectName("PrimaryButton")
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
                f"Protected .3MF Hold: {hold_txt} · Watch folder: {readable_path(str(summary['watch_folder']))}\n"
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
            "Tell Download Sorter which files belong together and which folder inside Downloads to move them into."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #A8B4C8; margin-bottom: 6px;")
        layout.addWidget(intro)

        form = QFormLayout()

        self.edit_name = QLineEdit(rule.name if rule else "")
        self.edit_name.setPlaceholderText("e.g. Tax Receipts, Client PDFs, Class Notes")
        self.edit_dest = QLineEdit(rule.destination if rule else r"Documents\Custom")
        self.edit_dest.setPlaceholderText(r"e.g. Documents\Taxes")
        self.edit_keywords = QLineEdit(", ".join(rule.keywords) if rule else "")
        self.edit_keywords.setPlaceholderText("e.g. syllabus, lab_report, invoice (comma-separated)")
        self.edit_domains = QLineEdit(", ".join(rule.domains) if rule else "")
        self.edit_domains.setPlaceholderText("Optional website, e.g. canvas.edu, makerworld.com")
        self.edit_exts = QLineEdit(", ".join(rule.extensions) if rule else "")
        self.edit_exts.setPlaceholderText("Optional file types, e.g. .pdf, .docx (blank = any)")
        self.chk_enabled = QCheckBox("Rule is active")
        self.chk_enabled.setChecked(rule.enabled if rule else True)

        form.addRow("Rule Name:", self.edit_name)
        form.addRow("Move Into Folder:", self.edit_dest)
        form.addRow("Matches Words in Filename:", self.edit_keywords)
        form.addRow("Matches Website:", self.edit_domains)
        form.addRow("Only These File Types:", self.edit_exts)
        form.addRow("", self.chk_enabled)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
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
            enabled=self.chk_enabled.isChecked(),
        )


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


def _make_badge(text: str, variant: str = "neutral") -> QLabel:
    """Create a pill badge styled via QLabel#Badge dynamic variant selector."""
    lbl = QLabel(text)
    lbl.setObjectName("Badge")
    lbl.setProperty("variant", variant)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    _refresh_style(lbl)
    return lbl


def _style_table(table: QTableWidget) -> None:
    """Apply clean borderless-grid Argon table chrome and 40px row spacing."""
    table.setShowGrid(False)
    table.setAlternatingRowColors(True)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(40)
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

        # Calm Recessed Diagram Stage
        self.stage_canvas = WalkthroughDiagramCanvas(container)
        self.stage_canvas.setMinimumSize(500, 148)
        layout.addWidget(self.stage_canvas)

        # Concise 1-line footnote per step (unboxed, zero nested border clutter)
        self.pages = QStackedWidget()
        self.pages.setFixedHeight(28)
        self.pages.addWidget(self._build_page(f"Watching top-level only: {watch_root}"))
        self.pages.addWidget(
            self._build_page(
                f".3MF files wait {hold_days} days by default · Every move has 1-Click Undo"
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

        self.btn_skip = InteractiveRibbonBtn("Skip")
        self.btn_skip.setObjectName("GhostButton")
        self.btn_skip.clicked.connect(self.accept)

        self.btn_back = InteractiveRibbonBtn("Back", make_vector_icon("arrow_left", "#F5F5F7", 15))
        self.btn_back.clicked.connect(lambda: self._go(self.pages.currentIndex() - 1))

        self.btn_next = InteractiveRibbonBtn("Next", make_vector_icon("arrow_right", "#FFFFFF", 15))
        self.btn_next.setObjectName("PrimaryButton")
        self.btn_next.clicked.connect(self._on_next_clicked)

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
                "Safe Slicer Hold & Instant Undo",
                f"New .3MF print files stay in Downloads for {hold_days} days so you can slice first, and every sort is 1-click reversible.",
            ),
            (
                "STEP 3 OF 3",
                "Custom Rules & Top-Right Side Ribbons",
                "Match filenames or URLs in Rules, and glance at sorted files via the top-right Side Ribbon with pause-on-hover.",
            ),
        ]
        self._go(0)

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
        if idx == 2:
            self.btn_next.setText("Finish Guide")
            self.btn_next.setIcon(make_vector_icon("check", "#FFFFFF", 15))
        else:
            self.btn_next.setText("Next")
            self.btn_next.setIcon(make_vector_icon("arrow_right", "#FFFFFF", 15))

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
        title_col.addWidget(subtitle)
    elif subtitle:
        lbl_sub = QLabel(subtitle)
        lbl_sub.setObjectName("MutedText")
        lbl_sub.setWordWrap(True)
        title_col.addWidget(lbl_sub)
    hdr_row.addLayout(title_col, 1)
    if right_widget is not None:
        hdr_row.addWidget(right_widget)
    root.addLayout(hdr_row)

    divider = QFrame()
    divider.setObjectName("CardDivider")
    root.addWidget(divider)
    return card, root


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
        self._action_running = False
        self._update_release: dict[str, Any] | None = None
        self._update_downloading = False
        self._preview_toast_mgr = ToastManager(
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

        self._tab_icon_names = ["home", "layers", "rules", "settings"]
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_home_tab(), make_vector_icon("home", "#F5F5F7", 15), "Home")
        self.arch_tab = ArchitectureTab(self.service, self)
        self.arch_tab.architecture_applied.connect(self._on_architecture_applied)
        self.arch_tab.undo_requested.connect(self.refresh_all)
        self.arch_tab.carry_over_requested.connect(self.open_carry_over_dialog)
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
        self.btn_pause.clicked.connect(self._toggle_pause)
        row.addWidget(self.btn_pause)

        self.btn_file_held = CollapsibleRibbonBtn(
            "Move Held .3MF",
            make_vector_icon("cube", "#FF9F0A", 14),
        )
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
        self.btn_sort_now.clicked.connect(self._on_sort_now)
        row.addWidget(self.btn_sort_now)
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
        btn_sim = InteractiveRibbonBtn("Refresh Preview", make_vector_icon("refresh", "#F5F5F7", 14))
        btn_sim.clicked.connect(self._on_refresh_preview_clicked)
        sim_card, sim_layout = _make_surface_card(
            "Downloads Folder Preview",
            self.lbl_sim_summary,
            right_widget=btn_sim,
        )

        self.tbl_sim = QTableWidget(0, 4)
        self.tbl_sim.setHorizontalHeaderLabels(
            ["File in Downloads", "Action", "Destination Folder", "Why"]
        )
        _style_table(self.tbl_sim)
        s_hdr = self.tbl_sim.horizontalHeader()
        s_hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        s_hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        s_hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        s_hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.tbl_sim.setColumnWidth(0, 240)
        self.tbl_sim.setColumnWidth(1, 148)
        sim_layout.addWidget(self.tbl_sim, 1)
        layout.addWidget(sim_card, 3)

        # Card B: Recent Activity
        self.lbl_activity_empty = QLabel(
            "No files moved yet — click 'Clean Up Now' above or drop a file in Downloads."
        )
        self.lbl_activity_empty.setObjectName("MutedText")
        act_card, act_layout = _make_surface_card(
            "Recent Activity",
            right_widget=self.lbl_activity_empty,
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
        self.tbl_activity.setColumnWidth(1, 220)
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
        for label, tpl_key in (
            ("+ School", "school"),
            ("+ Career", "career"),
            ("+ 3D Printing", "3d_printing"),
        ):
            btn = InteractiveRibbonBtn(label)
            btn.setToolTip(f"Add starter pack: {label.lstrip('+ ')}")
            btn.clicked.connect(lambda _=False, k=tpl_key: self._apply_template(k))
            packs_row.addWidget(btn)
        btn_import = InteractiveRibbonBtn("Carry Over v2.8 / Setup...")
        btn_import.setObjectName("GhostButton")
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
        btn_new.clicked.connect(self._add_rule)
        toolbar.addWidget(btn_new)
        for label, icon_name, cb in (
            ("Edit", "edit", self._edit_rule),
            ("Delete", "delete", self._delete_rule),
            ("Move Up", "up", lambda: self._move_rule(-1)),
            ("Move Down", "down", lambda: self._move_rule(1)),
        ):
            btn = InteractiveRibbonBtn(label, make_vector_icon(icon_name, "#F5F5F7", 14))
            btn.clicked.connect(cb)
            toolbar.addWidget(btn)
        toolbar.addStretch(1)
        rules_layout.addLayout(toolbar)

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
        self.tbl_rules.setColumnWidth(1, 180)
        self.tbl_rules.setColumnWidth(2, 175)
        self.tbl_rules.setColumnWidth(4, 140)
        self.tbl_rules.setColumnWidth(5, 120)
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
        page = QScrollArea()
        page.setObjectName("SettingsScroll")
        page.setWidgetResizable(True)
        page.setFrameShape(QFrame.Shape.NoFrame)
        page.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        page.setStyleSheet("QScrollArea#SettingsScroll, QScrollArea#SettingsScroll > QWidget > QWidget { background: transparent; }")
        tab = QWidget()
        page.setWidget(tab)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 4, 6, 0)
        layout.setSpacing(14)

        btn_save = InteractiveRibbonBtn("Save Settings", make_vector_icon("check", "#FFFFFF", 14))
        btn_save.setObjectName("PrimaryButton")
        btn_save.clicked.connect(self._save_settings_form)

        cfg_card, cfg_layout = _make_surface_card(
            "Preferences",
            "Configure background watching, folder organization, and Windows integration.",
            right_widget=btn_save,
        )

        form = QFormLayout()
        form.setVerticalSpacing(12)
        form.setHorizontalSpacing(18)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.chk_auto_sort = QCheckBox(
            "Automatically sort new files in Downloads in the background"
        )
        form.addRow("Automatic Sorting:", self.chk_auto_sort)

        folder_row = QHBoxLayout()
        folder_row.setSpacing(8)
        self.edit_watch_folder = QLineEdit()
        btn_browse = InteractiveRibbonBtn("Browse...")
        btn_browse.clicked.connect(self._browse_watch_folder)
        folder_row.addWidget(self.edit_watch_folder, 1)
        folder_row.addWidget(btn_browse)
        form.addRow("Folder to Organize:", folder_row)

        self.chk_type_subfolders = QCheckBox(
            "Sort into file-type subfolders (e.g. Documents\\PDF, Images\\PNG)"
        )
        form.addRow("Subfolder Style:", self.chk_type_subfolders)

        self.combo_fallback = QComboBox()
        self.combo_fallback.addItem("Put them in an 'Other' folder", "other")
        self.combo_fallback.addItem("Leave them in Downloads", "leave")
        form.addRow("Unrecognized File Types:", self.combo_fallback)

        hold_row = QHBoxLayout()
        hold_row.setSpacing(8)
        self.chk_hold_3mf = QCheckBox("Keep new .3MF files in Downloads for")
        self.spin_hold_days = QSpinBox()
        self.spin_hold_days.setRange(1, 60)
        self.spin_hold_days.setSuffix(" days")
        self.chk_hold_3mf.toggled.connect(self.spin_hold_days.setEnabled)
        hold_after = QLabel("before moving to 3D Models")
        hold_after.setStyleSheet("color: #8E8E93;")
        hold_row.addWidget(self.chk_hold_3mf)
        hold_row.addWidget(self.spin_hold_days)
        hold_row.addWidget(hold_after)
        hold_row.addStretch(1)
        form.addRow("3D Printing (.3MF) Hold:", hold_row)

        notif_row = QHBoxLayout()
        notif_row.setSpacing(8)
        self.chk_notifications = QCheckBox(
            "Show a ribbon with Undo in the top-right corner when files are sorted"
        )
        self.spin_ribbon_timeout = QDoubleSpinBox()
        self.spin_ribbon_timeout.setRange(1.0, 30.0)
        self.spin_ribbon_timeout.setDecimals(0)
        self.spin_ribbon_timeout.setSingleStep(1.0)
        self.spin_ribbon_timeout.setSuffix(" s")
        self.spin_ribbon_timeout.setFixedWidth(104)
        self.spin_ribbon_timeout.setToolTip(
            "How many seconds before Side Ribbon notifications automatically close."
        )
        self.chk_notifications.toggled.connect(self.spin_ribbon_timeout.setEnabled)

        self.btn_preview_ribbon = InteractiveRibbonBtn("Preview Ribbon")
        self.btn_preview_ribbon.clicked.connect(self._on_preview_ribbon)
        self.btn_close_ribbons = InteractiveRibbonBtn("Close Ribbons")
        self.btn_close_ribbons.setObjectName("GhostButton")
        self.btn_close_ribbons.setToolTip("Immediately dismiss any active Side Ribbon notifications.")
        self.btn_close_ribbons.clicked.connect(self._on_close_all_ribbons)

        notif_opts = QHBoxLayout()
        notif_opts.setSpacing(8)
        notif_opts.addWidget(QLabel("Close after"))
        notif_opts.addWidget(self.spin_ribbon_timeout)
        notif_opts.addWidget(self.btn_preview_ribbon)
        notif_opts.addWidget(self.btn_close_ribbons)
        notif_opts.addStretch(1)
        notif_col = QVBoxLayout()
        notif_col.setSpacing(6)
        notif_row.addWidget(self.chk_notifications)
        notif_row.addStretch(1)
        notif_col.addLayout(notif_row)
        notif_col.addLayout(notif_opts)
        form.addRow("Notifications:", notif_col)

        updates_row = QHBoxLayout()
        updates_row.setSpacing(8)
        self.chk_check_updates = QCheckBox("Check for new versions once a day")
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
        updates_row.addWidget(self.chk_check_updates)
        updates_row.addWidget(self.btn_check_updates)
        updates_row.addStretch(1)
        form.addRow("Updates:", updates_row)

        qs_row = QHBoxLayout()
        qs_row.setSpacing(8)
        self.chk_quick_switcher = QCheckBox(
            "Offer the files it just sorted in Open and Upload dialogs"
        )
        self.spin_qs_timeout = QDoubleSpinBox()
        self.spin_qs_timeout.setRange(10.0, 3600.0)
        self.spin_qs_timeout.setDecimals(0)
        self.spin_qs_timeout.setSingleStep(30.0)
        self.spin_qs_timeout.setSuffix(" s")
        self.spin_qs_timeout.setFixedWidth(90)
        self.spin_qs_timeout.setToolTip("How many seconds general downloads stay offered in Quick Switcher.")

        self.spin_qs_3d_hours = QDoubleSpinBox()
        self.spin_qs_3d_hours.setRange(0.5, 72.0)
        self.spin_qs_3d_hours.setDecimals(1)
        self.spin_qs_3d_hours.setSingleStep(1.0)
        self.spin_qs_3d_hours.setSuffix(" hrs")
        self.spin_qs_3d_hours.setFixedWidth(90)
        self.spin_qs_3d_hours.setToolTip("How many hours 3D/CAD files (.3mf, .stl, .step, etc.) stay offered.")

        self.edit_qs_hotkey = QLineEdit()
        self.edit_qs_hotkey.setFixedWidth(80)
        self.edit_qs_hotkey.setToolTip(
            "Picks the newest file while the pill is showing. Ctrl or Alt plus a letter, "
            "number or F-key (default: Ctrl+G)."
        )

        self.chk_quick_switcher.toggled.connect(self.spin_qs_timeout.setEnabled)
        self.chk_quick_switcher.toggled.connect(self.spin_qs_3d_hours.setEnabled)
        self.chk_quick_switcher.toggled.connect(self.edit_qs_hotkey.setEnabled)

        qs_opts = QHBoxLayout()
        qs_opts.setSpacing(8)
        qs_opts.addWidget(QLabel("Offer files for"))
        qs_opts.addWidget(self.spin_qs_timeout)
        qs_opts.addWidget(QLabel("3D/CAD files for"))
        qs_opts.addWidget(self.spin_qs_3d_hours)
        qs_opts.addWidget(QLabel("Pick with"))
        qs_opts.addWidget(self.edit_qs_hotkey)
        qs_opts.addStretch(1)
        qs_col = QVBoxLayout()
        qs_col.setSpacing(6)
        qs_row.addWidget(self.chk_quick_switcher)
        qs_row.addStretch(1)
        qs_col.addLayout(qs_row)
        qs_col.addLayout(qs_opts)
        form.addRow("Quick Switcher:", qs_col)

        self.spin_settle = QDoubleSpinBox()
        self.spin_settle.setRange(0.0, 60.0)
        self.spin_settle.setSingleStep(0.5)
        self.spin_settle.setSuffix(" s")
        settle_row = QHBoxLayout()
        settle_row.setSpacing(8)
        settle_row.addWidget(self.spin_settle)
        settle_hint = QLabel("Wait a few seconds after a download finishes before moving it")
        settle_hint.setStyleSheet("color: #8E8E93;")
        settle_row.addWidget(settle_hint, 1)
        form.addRow("Safety Delay:", settle_row)

        self.chk_startup = QCheckBox("Start Download Sorter automatically when I sign in to Windows")
        form.addRow("Windows Integration:", self.chk_startup)

        self.chk_context_menu = QCheckBox(
            "Add 'Sort with Download Sorter' when I right-click a folder in File Explorer"
        )
        form.addRow("", self.chk_context_menu)

        backup_row = QHBoxLayout()
        backup_row.setSpacing(8)
        self.btn_carry_over_settings = InteractiveRibbonBtn(
            "Carry Over Setup && Rules...",
            make_vector_icon("layers", "#F5F5F7", 14),
        )
        self.btn_carry_over_settings.setToolTip(
            "Carry over your v2.8 config or exported setup file without losing any rules"
        )
        self.btn_carry_over_settings.clicked.connect(self.open_carry_over_dialog)
        backup_row.addWidget(self.btn_carry_over_settings)

        self.btn_export_setup = InteractiveRibbonBtn(
            "Export Setup...",
            make_vector_icon("folder", "#F5F5F7", 14),
        )
        self.btn_export_setup.setObjectName("GhostButton")
        self.btn_export_setup.setToolTip(
            "Save your complete setup, rules & Folder Architecture to a JSON file"
        )
        self.btn_export_setup.clicked.connect(self._export_current_setup)
        backup_row.addWidget(self.btn_export_setup)
        backup_row.addStretch(1)
        form.addRow("Upgrade & Backup", backup_row)

        cfg_layout.addLayout(form)
        layout.addWidget(cfg_card)
        layout.addStretch(1)
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
        return {
            "paused": cfg.paused,
            "hold_days": cfg.hold_3mf_days,
            "rules_enabled": sum(1 for r in cfg.rules if r.enabled),
            "watch_dir": service.watch_root,
            "held_count": len(service.list_held_3mf()),
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

    def _run_service_action(
        self,
        work: Callable[[], Any],
        on_done: Callable[[Any], None],
        busy_text: str = "Working...",
    ) -> None:
        """Run a file-moving service call off the GUI thread, then ``on_done(result)``.

        Only one such action runs at a time; a second click while one is in flight
        is ignored rather than queued, so a double-click cannot sort twice.
        """
        if self._action_running:
            self.lbl_footer.setText("Still working on the previous request...")
            return
        self._action_running = True

        def _finished(result: Any) -> None:
            self._action_running = False
            on_done(result)

        def _failed(message: str) -> None:
            self._action_running = False
            self.banner.show_message(f"That did not finish: {message}", kind="warning")
            self.lbl_footer.setText(f"That did not finish: {message}")
            self.refresh_all()

        try:
            dispatched = run_async(work, _finished, _failed, parent=self)
        except Exception:
            self._action_running = False
            raise
        if dispatched:
            self.lbl_footer.setText(busy_text)

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
            move_id = int(rec["id"])
            btn.clicked.connect(lambda _=False, mid=move_id: self._on_undo_single(mid))
            self.tbl_activity.setCellWidget(r_idx, 4, btn)

    def _refresh_rules(self) -> None:
        rules = self.service.config.rules
        self.tbl_rules.setRowCount(len(rules))
        for idx, rule in enumerate(rules):
            chk = QCheckBox()
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

    def _refresh_settings_fields(self) -> None:
        cfg = self.service.config
        self.chk_auto_sort.setChecked(not cfg.paused)
        self.edit_watch_folder.setText(readable_path(cfg.watch_folder))
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
        self.chk_startup.setChecked(is_startup_enabled())
        self.chk_context_menu.setChecked(is_context_menu_enabled())

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

    def _on_sort_now(self) -> None:
        self._run_service_action(
            self.service.scan_top_level_now,
            lambda res: self.refresh_all(then=lambda: self._after_sort_now(res)),
            busy_text="Sorting Downloads...",
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
            self.banner.show_message(
                f"Cleaned up Downloads: moved {moved_n} file(s).",
                kind="success",
                action_text="Undo",
                action=self._on_undo_last,
            )
            self.lbl_footer.setText(f"Cleaned up Downloads: moved {moved_n} file(s).")
        else:
            held_n = res.get("held", 0)
            self.banner.show_message("Downloads looks clean — nothing needed moving right now.", kind="info")
            self.lbl_footer.setText(
                f"Everything looks clean: 0 moved ({held_n} .3MF held for 3D printing, {res['left']} left in Downloads)."
            )

    def _on_file_held_now(self) -> None:
        self._run_service_action(
            self.service.file_held_3mf_now,
            lambda res: self.refresh_all(then=lambda: self._after_file_held_now(res)),
            busy_text="Moving held .3MF files...",
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
        def _work() -> dict[str, Any]:
            return self.service.undo_architecture_or_last_batch()

        def _done(res: dict[str, Any]) -> None:
            if hasattr(self, "arch_tab"):
                self.arch_tab._dirty = False
            self.refresh_all(then=lambda: self._after_undo_last(res))

        self._run_service_action(_work, _done, busy_text="Undoing...")

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
            lambda: self.service.undo_single_move(move_id),
            lambda res: self.refresh_all(then=lambda: self._after_undo_single(res)),
            busy_text="Undoing...",
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
        self.banner.show_message(
            f"Added starter rule pack '{template_key}': {added} new rule(s).",
            kind="success" if added else "info",
        )
        self.lbl_footer.setText(f"Added starter rule pack '{template_key}': {added} new rule(s).")

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
        if "Nothing to undo" in summary_msg or summary_msg.startswith("Auto-created"):
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

        def _work() -> dict[str, Any]:
            return self.service.apply_architecture_and_shift(
                carried_config,
                shift_files=shift_now,
                create_empty_folders=False,
            )

        def _announce(res: dict[str, Any]) -> None:
            shifted = res.get("shifted_count", 0)
            msg = (
                f"Carried over {summary.get('rules_imported', 0)} rule(s) & "
                f"{summary.get('tier_count', 2)}-tier setup"
                + (f" (shifted {shifted} file(s))." if shifted else ".")
            )
            self.header_mascot.celebrate()
            self.banner.show_message(msg, kind="success", action_text="Undo", action=self._on_undo_last)
            self.lbl_footer.setText(msg)

        def _done(res: dict[str, Any]) -> None:
            if hasattr(self, "arch_tab"):
                self.arch_tab._dirty = False
                self.arch_tab.sync_from_service(force=True)
            self.refresh_all(then=lambda: _announce(res))

        self._run_service_action(_work, _done, busy_text="Carrying over your setup...")

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
        cfg.watch_folder = portable_path(typed) if typed else r"${USERPROFILE}\Downloads"
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

            self.tbl_sim.setItem(idx, 0, QTableWidgetItem(path.name))
            holder = QWidget()
            hl = QHBoxLayout(holder)
            hl.setContentsMargins(6, 0, 6, 0)
            hl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge = _make_badge(what, badge_variant)
            badge.setMinimumWidth(108)
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
            self.lbl_status_badge.setVisible(width >= self.HEADER_COMPACT_WIDTH)
            for btn in (self.btn_undo_last, self.btn_open_downloads, self.btn_walkthrough):
                btn.set_collapsed(width < self.HEADER_COMPACT_WIDTH)
            for btn in (self.btn_pause, self.btn_file_held):
                btn.set_collapsed(width < self.HEADER_TIGHT_WIDTH)
        if hasattr(self, "list_test_trace"):
            show_trace = height >= self.TESTER_TRACE_MIN_HEIGHT
            if self.list_test_trace.isHidden() == show_trace:
                self.list_test_trace.setVisible(show_trace)
                _relayout_up(self.list_test_trace)
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
