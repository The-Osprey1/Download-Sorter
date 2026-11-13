"""First-run onboarding wizard and consumer Home Dashboard GUI for Download Sorter.

Provides:
- WelcomeWizardDialog: 3-step first-run wizard (your folders, preview, system setup).
- HomeDashboardWidget: Everyday main view (status hero, recent activity, quick settings).
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta
import html
import json
import logging
import os
import re
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Any
import uuid

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QRadioButton,
    QSpinBox,
    QProgressBar,
    QScrollArea,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import about
import folder_scan
import problem_report
from suite_theme import ACCENTS, COLORS, RADII
import learning
import refile
import ui_categories
import ui_icons
import ui_motion
import ui_style
import ui_widgets
import update_check
import updater

try:
    import config_manager as suite_data
except ImportError:
    import suite_data

try:
    import sorter
except ImportError:
    sorter = None


def _sorter_is_running() -> bool:
    """True when the background tray/watcher holds the single-instance lock."""
    if hasattr(suite_data, "is_sorter_running"):
        try:
            return bool(suite_data.is_sorter_running())
        except Exception:
            return False
    return False


def _sorter_is_waiting_for_setup() -> bool:
    """True when a running background sorter is holding off until this wizard is saved.

    That sorter does the initial sort itself once it sees the saved settings, so
    the wizard must not sort at the same time.  When the tour is re-run later the
    sorter is not waiting, and the wizard does the sort.
    """
    if not _sorter_is_running() or sorter is None:
        return False
    try:
        state = json.loads(Path(sorter.RUNTIME_STATE_FILE).read_text(encoding="utf-8"))
        return state.get("status") == "waiting_for_setup"
    except Exception:
        return False


def current_sorter_state() -> str:
    """"paused", "running" or "stopped": what the background sorter is doing right now.

    Home and the window's sidebar both read this, so they always agree.
    """
    is_paused = False
    if sorter and hasattr(sorter, "is_paused"):
        try:
            is_paused = sorter.is_paused()
        except Exception:
            pass
    if is_paused:
        return "paused"
    is_running = True
    if hasattr(suite_data, "is_sorter_running"):
        try:
            is_running = suite_data.is_sorter_running()
        except Exception:
            pass
    return "running" if is_running else "stopped"


def _auto_unzip_enabled(config: dict[str, Any]) -> bool:
    """Same lookup the sorter uses when it decides whether to extract a zip."""
    watch_block = config.get("watch")
    if isinstance(watch_block, dict) and "auto_unzip" in watch_block:
        return bool(watch_block["auto_unzip"])
    return bool(config.get("auto_unzip", False))


def _set_auto_unzip(config: dict[str, Any], enabled: bool) -> None:
    """Turn unzipping on or off. The sorter reads watch.auto_unzip first and the
    top-level key as a fallback, so keep both in step."""
    watch_block = config.get("watch")
    if not isinstance(watch_block, dict):
        watch_block = {}
        config["watch"] = watch_block
    watch_block["auto_unzip"] = bool(enabled)
    config["auto_unzip"] = bool(enabled)


try:
    import sort_undo
except ImportError:
    sort_undo = None


# ============================================================================
# 0. SorterMascotWidget (Sorty: Interactive Folder Companion)
# ============================================================================

class SorterMascotWidget(QWidget):
    """Interactive animated/stateful mascot: 'Sorty' the Folder Companion.

    Rendered with QPainter/QPixmap matching suite_theme.py design tokens and #69E6AD.
    Zero forbidden hex codes.
    States:
    - 'smart' / 'waving' (Step 1: smart file recommendation & friendly greeting)
    - 'safe' / 'protective' (Step 2: reassuring 100% offline privacy and 1-Click Undo)
    - 'celebrating' / 'happy' (Step 3: celebrating setup completion)
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state: str = "smart"
        self._expression: str = "normal"
        self.setFixedSize(84, 80)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    @property
    def state(self) -> str:
        return self._state

    def set_state(self, state: str, expression: str | None = None) -> None:
        """Set mascot pose/state ('smart', 'waving', 'safe', 'protective', 'celebrating', 'happy')."""
        self._state = str(state).lower()
        if expression is not None:
            self._expression = str(expression).lower()
        self.update()

    def set_step(self, step_index: int) -> None:
        """Update pose matching the wizard step index (0=smart, 1=safe, 2=celebrating)."""
        if step_index == 0:
            self.set_state("smart")
        elif step_index == 1:
            self.set_state("safe")
        elif step_index == 2:
            self.set_state("celebrating")
        else:
            self.set_state("smart")

    def to_pixmap(self, width: int = 84, height: int = 80) -> QPixmap:
        """Render the current mascot state directly onto a QPixmap."""
        pixmap = QPixmap(width, height)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        try:
            self._draw_mascot(painter, width, height)
        finally:
            painter.end()
        return pixmap

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        try:
            self._draw_mascot(painter, self.width(), self.height())
        finally:
            painter.end()

    def _draw_mascot(self, painter: QPainter, width: int, height: int) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        mint_color = QColor(ACCENTS["downloadSorter"])
        slate_surface = QColor(COLORS["surface"])
        border_strong = QColor(COLORS["borderStrong"])
        text_color = QColor(COLORS["text"])
        text_muted = QColor(COLORS["textMuted"])
        warning_color = QColor(COLORS["warning"])
        success_subtle = QColor(COLORS["successSubtle"])

        state = self._state

        # Scale coordinates relative to 84x80 canvas
        sx = width / 84.0
        sy = height / 80.0
        painter.save()
        painter.scale(sx, sy)

        # 1. Back folder tab and body
        back_tab = QPainterPath()
        back_tab.moveTo(14, 24)
        back_tab.lineTo(14, 16)
        back_tab.quadTo(14, 12, 18, 12)
        back_tab.lineTo(34, 12)
        back_tab.quadTo(38, 12, 42, 16)
        back_tab.lineTo(46, 24)
        back_tab.closeSubpath()
        painter.setPen(QPen(border_strong, 1.5))
        painter.setBrush(QBrush(slate_surface))
        painter.drawPath(back_tab)

        back_body = QPainterPath()
        back_body.addRoundedRect(QRectF(12, 20, 60, 48), 6, 6)
        painter.drawPath(back_body)

        # 2. Peeking document sheet
        doc_path = QPainterPath()
        doc_path.addRoundedRect(QRectF(22, 10, 38, 30), 4, 4)
        painter.setPen(QPen(border_strong, 1.2))
        painter.setBrush(QBrush(text_color))
        painter.drawPath(doc_path)

        # Lines on peeking document
        painter.setPen(QPen(text_muted, 1.5))
        painter.drawLine(28, 16, 52, 16)
        painter.drawLine(28, 21, 46, 21)

        # 3. Front folder flap (main Sorty face body)
        front_path = QPainterPath()
        front_path.addRoundedRect(QRectF(10, 26, 64, 44), 8, 8)
        painter.setPen(QPen(mint_color, 2.0))
        painter.setBrush(QBrush(success_subtle))
        painter.drawPath(front_path)

        # 4. Cheeks (soft blush circles)
        painter.setPen(Qt.PenStyle.NoPen)
        blush_color = QColor(mint_color.red(), mint_color.green(), mint_color.blue(), 55)
        painter.setBrush(QBrush(blush_color))
        painter.drawEllipse(QRectF(16, 44, 8, 5))
        painter.drawEllipse(QRectF(60, 44, 8, 5))

        # 5. Eyes & Expression depending on state
        if state in ("celebrating", "happy"):
            # Joyful curved eyes (^ ^)
            painter.setPen(QPen(mint_color, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            eye_left = QPainterPath()
            eye_left.moveTo(26, 43)
            eye_left.quadTo(31, 37, 36, 43)
            painter.drawPath(eye_left)

            eye_right = QPainterPath()
            eye_right.moveTo(48, 43)
            eye_right.quadTo(53, 37, 58, 43)
            painter.drawPath(eye_right)

            # Wide open happy mouth
            mouth = QPainterPath()
            mouth.moveTo(37, 50)
            mouth.quadTo(42, 58, 47, 50)
            painter.setPen(QPen(mint_color, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.setBrush(QBrush(slate_surface))
            painter.drawPath(mouth)

            # Sparkles / Starbursts
            painter.setPen(QPen(mint_color, 1.2))
            painter.drawLine(6, 12, 6, 20)
            painter.drawLine(2, 16, 10, 16)

            painter.setPen(QPen(warning_color, 1.2))
            painter.drawLine(76, 10, 76, 18)
            painter.drawLine(72, 14, 80, 14)

            # Cheering arms raised up
            painter.setPen(QPen(mint_color, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(10, 42, 4, 30)
            painter.drawLine(74, 42, 80, 30)

        elif state in ("safe", "protective"):
            # Reassuring wink on left eye, open smiling eye on right
            painter.setPen(QPen(mint_color, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            wink = QPainterPath()
            wink.moveTo(26, 42)
            wink.quadTo(31, 38, 36, 42)
            painter.drawPath(wink)

            # Right open eye
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(text_color))
            painter.drawEllipse(QRectF(50, 38, 7, 7))
            painter.setBrush(QBrush(mint_color))
            painter.drawEllipse(QRectF(53, 39, 2.5, 2.5))

            # Gentle confident smile
            painter.setPen(QPen(mint_color, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            smile = QPainterPath()
            smile.moveTo(38, 51)
            smile.quadTo(42, 55, 46, 51)
            painter.drawPath(smile)

            # Protective shield badge on right corner
            shield = QPainterPath()
            shield.moveTo(64, 52)
            shield.lineTo(74, 52)
            shield.lineTo(74, 60)
            shield.quadTo(74, 68, 69, 72)
            shield.quadTo(64, 68, 64, 60)
            shield.closeSubpath()
            painter.setPen(QPen(mint_color, 1.5))
            painter.setBrush(QBrush(slate_surface))
            painter.drawPath(shield)
            painter.setPen(QPen(mint_color, 1.2))
            painter.drawRoundedRect(QRectF(67, 60, 4, 4), 1, 1)
            painter.drawArc(QRectF(67, 57, 4, 4), 0, 180 * 16)

        else:
            # "smart" or "waving" (Default Step 1 pose)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(text_color))
            painter.drawEllipse(QRectF(27, 38, 7.5, 7.5))
            painter.drawEllipse(QRectF(49, 38, 7.5, 7.5))

            painter.setBrush(QBrush(mint_color))
            painter.drawEllipse(QRectF(31, 39, 2.5, 2.5))
            painter.drawEllipse(QRectF(53, 39, 2.5, 2.5))

            if state == "smart":
                painter.setPen(QPen(mint_color, 1.4, Qt.PenStyle.SolidLine))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(QRectF(24, 35, 13, 13), 3, 3)
                painter.drawRoundedRect(QRectF(47, 35, 13, 13), 3, 3)
                painter.drawLine(37, 41, 47, 41)
                painter.drawLine(24, 41, 18, 39)
                painter.drawLine(60, 41, 66, 39)

            painter.setPen(QPen(mint_color, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            smile = QPainterPath()
            smile.moveTo(38, 51)
            smile.quadTo(42, 56, 46, 51)
            painter.drawPath(smile)

            if state == "waving":
                painter.setPen(QPen(mint_color, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                painter.drawLine(74, 44, 79, 32)
                painter.setPen(QPen(text_muted, 1.0))
                painter.drawArc(QRectF(78, 25, 6, 8), -30 * 16, 60 * 16)

        painter.restore()


# ============================================================================
# 2. WelcomeWizardDialog (3-Step Guided Setup)
# ============================================================================

# What the person mostly downloads, asked in the first welcome step. It decides which
# folders start ticked and which pages show, so someone sorting bank statements and photos
# is not shown 3D printing and project tools.
USE_PROFILES: list[tuple[str, str, str]] = [
    ("everyday", "Everyday files", "Documents, pictures, music, installers"),
    ("maker", "3D printing", "3D printing, CAD and CNC. Adds the 3D Models and CAD folders, and the Projects page"),
    ("developer", "Software", "Writing software. Adds the Projects page for code folders"),
]
MAKER_CATEGORY_FOLDERS = ("3d models", "cad")


def suggested_use(scan: dict[str, Any], config: dict[str, Any]) -> str:
    """The choice to start on: the saved one, 3D printing when such files are already there, else everyday."""
    saved = str(config.get("use_profile") or "")
    if saved in {use for use, _text, _hint in USE_PROFILES}:
        return saved
    if int(scan.get("maker_files", 0) or 0) >= 3 or config.get("show_maker_features"):
        return "maker"
    return "everyday"


def apply_use_profile(config: dict[str, Any], use: str) -> dict[str, Any]:
    """Record the choice and switch the maker tools and the Projects page to match it."""
    config["use_profile"] = use
    config["show_maker_features"] = use == "maker"
    config["show_projects_page"] = use in ("maker", "developer")
    return config


def projects_page_shown(config: dict[str, Any]) -> bool:
    """Shown unless a choice turned it off: installs from before the choice existed keep the page."""
    return bool(config.get("show_projects_page", True)) if isinstance(config, dict) else True


class WelcomeWizardDialog(QDialog):
    """3-step first-run wizard: the folders already in Downloads, how it works, and system setup."""

    def __init__(self, config: dict[str, Any] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Welcome to {about.APP_NAME}")
        self.setMinimumSize(720, 640)
        self.resize(760, 680)

        self._config = copy.deepcopy(config) if config is not None else (
            suite_data.load_sorter_config() if hasattr(suite_data, "load_sorter_config") else {}
        )
        self._current_step = 0
        self._folder_checks: dict[str, QCheckBox] = {}
        self._category_checks: dict[str, QCheckBox] = {}
        self._starter_checks: dict[str, QCheckBox] = {}
        self._use_buttons: dict[str, QRadioButton] = {}

        # Step 1 is built from the folders already in Downloads (folder_scan.py).
        scan_config = self._config if self._config.get("extension_rules") else (
            sorter.get_default_config() if sorter else {}
        )
        try:
            self._scan = folder_scan.scan_downloads(scan_config)
        except Exception:
            logging.debug("Folder scan failed", exc_info=True)
            self._scan = {"folders": [], "empty": True, "files_scanned": 0, "maker_files": 0, "examples": []}
        usable = [f for f in self._scan["folders"] if f.get("rule") or f.get("kind") == "category"]
        if usable:
            noun = "folder" if len(usable) == 1 else "folders"
            self._step1_speech = (
                f"I found {len(usable)} {noun} you already use in Downloads. "
                "Tick the ones I should keep filing into."
            )
        else:
            self._step1_speech = (
                "Your Downloads folder doesn't have folders of its own yet. "
                "Pick the folders you'd like, and I'll learn more as you go."
            )

        self._init_ui()

    def _init_ui(self) -> None:
        self.setStyleSheet(ui_style.stylesheet())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(14)

        # Header Bar with Step Indicators
        header_layout = QHBoxLayout()
        header_title = ui_style.label(f"Welcome to {about.APP_NAME}", "section")
        header_layout.addWidget(header_title)

        header_layout.addStretch()

        self._step_pill_1 = QLabel("1  Your folders")
        self._step_pill_2 = QLabel("2  How it works")
        self._step_pill_3 = QLabel("3  Finish")
        for pill in (self._step_pill_1, self._step_pill_2, self._step_pill_3):
            header_layout.addWidget(pill)

        layout.addLayout(header_layout)

        # Mascot Banner Row (Sorty Folder Companion & Speech Bubble)
        self._mascot_container = QFrame(self)
        self._mascot_container.setObjectName("mascotCard")
        self._mascot_container.setStyleSheet(f"""
            QFrame#mascotCard {{
                background-color: {COLORS['surface']};
                border: 1px solid {COLORS['border']};
                border-radius: {RADII['card']}px;
            }}
        """)
        mascot_row = QHBoxLayout(self._mascot_container)
        mascot_row.setContentsMargins(12, 10, 14, 10)
        mascot_row.setSpacing(14)

        self._mascot_widget = SorterMascotWidget(self._mascot_container)
        mascot_row.addWidget(self._mascot_widget)

        bubble_col = QVBoxLayout()
        bubble_col.setSpacing(4)

        bubble_col.addStretch()
        self._mascot_speech_lbl = QLabel(f"Hi! I'm Sorty! {self._step1_speech}")
        self._mascot_speech_lbl.setWordWrap(True)
        bubble_col.addWidget(self._mascot_speech_lbl)

        bubble_col.addStretch()
        mascot_row.addLayout(bubble_col, 1)
        layout.addWidget(self._mascot_container)

        # Main Stacked Pages
        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._build_step1_folders())
        self._stack.addWidget(self._build_step2_preview())
        self._stack.addWidget(self._build_step3_preferences())
        layout.addWidget(self._stack, 1)

        # Footer Navigation
        footer = QHBoxLayout()
        self._btn_back = ui_style.button("Back", None, "back")
        self._btn_back.clicked.connect(self._on_back_clicked)
        self._btn_back.setEnabled(False)
        self._btn_back.setVisible(False)
        footer.addWidget(self._btn_back)

        footer.addStretch()

        self._credit_lbl = ui_style.label(f"{about.APP_NAME} {about.__version__} · {about.CREDIT}", "caption")
        self._credit_lbl.setObjectName("creditFooter")
        footer.addWidget(self._credit_lbl)

        footer.addStretch()

        self._btn_finish = ui_style.button("Finish setup", "primary")
        self._btn_finish.setMinimumWidth(120)
        self._btn_finish.clicked.connect(self._on_finish_clicked)
        self._btn_finish.setVisible(False)
        footer.addWidget(self._btn_finish)

        self._btn_next = ui_style.button("Next", "primary")
        self._btn_next.setMinimumWidth(120)
        self._btn_next.clicked.connect(self._on_next_clicked)
        footer.addWidget(self._btn_next)

        layout.addLayout(footer)

        self._update_navigation()

    def _build_step1_folders(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(10)

        # The step's title and the choice share a line, so the step still fits 640 px of height.
        folders = self._scan.get("folders", [])
        use_row = QHBoxLayout()
        use_row.setSpacing(8)
        use_row.addWidget(ui_style.label("Your folders" if folders else "Pick your folders", "title"))
        use_row.addStretch()
        use_row.addWidget(ui_style.label("I mostly download:", "muted"))
        start_use = suggested_use(self._scan, self._config)
        for use, text, hint in USE_PROFILES:
            btn = QRadioButton(text)
            btn.setProperty("role", "segment")
            btn.setToolTip(hint)
            btn.setChecked(use == start_use)
            btn.toggled.connect(lambda checked, u=use: checked and self._on_use_changed(u))
            use_row.addWidget(btn)
            self._use_buttons[use] = btn
        layout.addLayout(use_row)

        existing = {str(r.get("folder") or "").lower(): r for r in self._config.get("keyword_rules", [])}
        if folders:
            layout.addWidget(ui_style.label(
                "These folders are already in your Downloads folder. New files that fit one go "
                "into it. Untick any you'd rather not use.", "muted", wrap=True))
            folder_card, folder_box = ui_style.card(spacing=2)
            for entry in folders:
                usable = bool(entry.get("rule")) or entry.get("kind") == "category"
                files = int(entry.get("files", 0))
                count = f"  ·  {files} file{'s' if files != 1 else ''}" if files else ""
                chk = QCheckBox(f"{entry['name']}{count}")
                previous = existing.get(entry["name"].lower())
                chk.setChecked(usable and bool(entry.get("checked", True))
                               and (previous is None or previous.get("enabled", True)))
                chk.setEnabled(usable)
                folder_box.addWidget(chk)
                note = ui_style.label(entry.get("summary", ""), "caption", wrap=True)
                note.setContentsMargins(26, 0, 0, 6)
                folder_box.addWidget(note)
                self._folder_checks[entry["name"]] = chk
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidget(folder_card)
            scroll.setMaximumHeight(250)
            layout.addWidget(scroll)
            layout.addWidget(ui_style.label("Everything else is sorted by type into:", "muted"))
        else:
            layout.addWidget(ui_style.label(
                "Each kind of file gets its own folder inside Downloads. Untick any you don't "
                "want; those files go to Other. You can add folders of your own any time.",
                "muted", wrap=True))

        cat_card, cat_box = ui_style.card(spacing=4)
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(6)
        seen: set[str] = set()
        rules = self._config.get("extension_rules") or (
            sorter.get_default_config().get("extension_rules", []) if sorter else [])
        for rule in rules:
            name = str(rule.get("name") or "")
            folder = str(rule.get("folder") or name)
            if not name or folder.lower() in seen:
                continue
            seen.add(folder.lower())
            chk = QCheckBox(folder)
            chk.setChecked(bool(rule.get("enabled", True)))
            chk.setProperty("maker_folder", folder.split("\\")[0].lower() in MAKER_CATEGORY_FOLDERS)
            idx = len(self._category_checks)
            grid.addWidget(chk, idx // 4, idx % 4)
            self._category_checks[name] = chk
        cat_box.addLayout(grid)
        layout.addWidget(cat_card)

        # Ready-made folders: all of them when Downloads has none of its own,
        # otherwise just the one for course websites.
        starters = folder_scan.STARTER_FOLDERS if not folders else [
            s for s in folder_scan.STARTER_FOLDERS if s[0] == "school_sites"]
        if starters:
            layout.addWidget(ui_style.label("Extra folders inside Documents (optional):", "muted"))
            starter_card, starter_box = ui_style.card(spacing=4)
            for key, label, _rule in starters:
                chk = QCheckBox(label)
                chk.setChecked(False)
                starter_box.addWidget(chk)
                self._starter_checks[key] = chk
            layout.addWidget(starter_card)

        layout.addStretch()
        if start_use != "maker":
            self._on_use_changed(start_use)
        return page

    def chosen_use(self) -> str:
        return next((use for use, btn in self._use_buttons.items() if btn.isChecked()), "everyday")

    def _on_use_changed(self, use: str) -> None:
        """The 3D Models and CAD folders are ticked for 3D printing and unticked for the others."""
        for chk in self._category_checks.values():
            if chk.property("maker_folder"):
                chk.setChecked(use == "maker")

    def _build_step2_preview(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(10)

        layout.addWidget(ui_style.label("Here's what happens", "title"))
        layout.addWidget(ui_style.label(
            "Download Sorter runs quietly in the background. When a download finishes, "
            "it moves the file into a folder for its type, inside your Downloads folder.",
            "muted", wrap=True))
        layout.addSpacing(4)

        comp_card, comp = ui_style.card(spacing=0)
        row = QHBoxLayout()
        row.setSpacing(18)
        comp.addLayout(row)

        def column(title: str, items: list[tuple[str, str]], icon_name: str) -> QVBoxLayout:
            col = QVBoxLayout()
            col.setSpacing(8)
            col.addWidget(ui_style.label(title, "muted"))
            for name, note in items:
                line = QHBoxLayout()
                line.setSpacing(8)
                ic = QLabel()
                ic.setPixmap(ui_icons.pixmap(icon_name, COLORS["textMuted"], 16))
                line.addWidget(ic)
                line.addWidget(ui_style.label(name))
                if note:
                    line.addWidget(ui_style.label(note, "caption"))
                line.addStretch()
                col.addLayout(line)
            col.addStretch()
            return col

        row.addLayout(column("Downloads, before", [
            ("invoice_march.pdf", ""), ("setup_v2.exe", ""), ("vacation_photo.jpg", ""),
            ("homework_draft.docx", ""), ("model_parts.zip", ""),
        ], "file"), 1)
        arrow = QLabel()
        arrow.setPixmap(ui_icons.pixmap("next", ACCENTS["downloadSorter"], 20))
        row.addWidget(arrow, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addLayout(column("Downloads, after", [
            ("Documents", "2 files"), ("Installers", "1 file"),
            ("Images", "1 file"), ("Archives", "1 file"),
        ], "folder"), 1)
        layout.addWidget(comp_card)

        facts = QHBoxLayout()
        facts.setSpacing(10)
        for icon_name, title, text in (
            ("lock", "Stays on your PC", "Nothing is uploaded. Download Sorter works completely offline."),
            ("undo", "Easy to undo", "Any sort can be undone from the Home screen or the tray icon."),
            ("copy", "Never overwrites", "If a file with the same name is already there, the new one gets a number."),
        ):
            fact_card, fact = ui_style.card(margins=(14, 12, 14, 12), spacing=6)
            head = QHBoxLayout()
            head.setSpacing(8)
            ic = QLabel()
            ic.setPixmap(ui_icons.pixmap(icon_name, ACCENTS["downloadSorter"], 16))
            head.addWidget(ic)
            head.addWidget(ui_style.label(title))
            head.addStretch()
            fact.addLayout(head)
            fact.addWidget(ui_style.label(text, "muted", wrap=True))
            facts.addWidget(fact_card, 1)
        layout.addLayout(facts)

        layout.addStretch()
        return page

    def _build_step3_preferences(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(10)

        layout.addWidget(ui_style.label("Almost done", "title"))
        layout.addWidget(ui_style.label("You can change all of these later in Settings.", "muted"))
        layout.addSpacing(4)

        pref_card, prefs = ui_style.card(spacing=4)

        def option(text: str, hint: str, checked: bool = True) -> QCheckBox:
            chk = QCheckBox(text)
            chk.setChecked(checked)
            prefs.addWidget(chk)
            note = ui_style.label(hint, "muted", wrap=True)
            note.setContentsMargins(26, 0, 0, 10)
            prefs.addWidget(note)
            return chk

        self._chk_startup = option(
            "Start Download Sorter when I sign in",
            "Keeps sorting new downloads without you having to open the app.")
        self._chk_context = option(
            "Add “Sort with Download Sorter” to the folder right-click menu",
            "Lets you tidy any folder by right-clicking it in File Explorer.")
        self._chk_sort_now = option(
            "Sort the files already in my Downloads folder",
            "Untick this to sort only new downloads. The first sort can be undone in one click.")
        self._chk_unzip = option(
            "Unzip downloaded .zip files",
            "Puts what's inside a zip into the Extracted folder and files the zip in Archives. "
            "Leave this off to keep zips as they are.",
            checked=False)
        layout.addWidget(pref_card)
        layout.addStretch()
        return page

    def _update_navigation(self) -> None:
        idx = self._current_step
        self._btn_back.setEnabled(idx > 0)
        self._btn_back.setVisible(idx > 0)

        # Update mascot pose and speech text across wizard steps
        if hasattr(self, "_mascot_widget"):
            self._mascot_widget.set_step(idx)

        if hasattr(self, "_mascot_speech_lbl"):
            if idx == 0:
                self._mascot_speech_lbl.setText(f"Hi! I'm Sorty! {self._step1_speech}")
            elif idx == 1:
                self._mascot_speech_lbl.setText(
                    "I work offline and only move files around on your PC. Every move can be "
                    "undone, so you can always put things back."
                )
            elif idx == 2:
                self._mascot_speech_lbl.setText(
                    "Almost done. Pick how I should run, then click Finish setup."
                )

        if idx == 2:
            self._btn_next.setText("Finish setup")
            self._btn_next.setVisible(False)
            self._btn_finish.setVisible(True)
        else:
            self._btn_next.setText("Next")
            self._btn_next.setVisible(True)
            self._btn_finish.setVisible(False)

        pills = [self._step_pill_1, self._step_pill_2, self._step_pill_3]
        for i, pill in enumerate(pills):
            if i == idx:
                pill.setStyleSheet(
                    f"color: {COLORS['text']}; font-weight: 600; padding: 4px 2px;"
                    f" border-bottom: 2px solid {ACCENTS['downloadSorter']};"
                )
            else:
                done = i < idx
                pill.setStyleSheet(
                    f"color: {COLORS['textMuted'] if done else COLORS['textFaint']}; padding: 4px 2px;"
                    " border-bottom: 2px solid transparent;"
                )

    def _on_next_clicked(self) -> None:
        if self._current_step < 2:
            self._current_step += 1
            self._stack.setCurrentIndex(self._current_step)
            self._update_navigation()
        else:
            self._on_finish_clicked()

    def _on_back_clicked(self) -> None:
        if self._current_step > 0:
            self._current_step -= 1
            self._stack.setCurrentIndex(self._current_step)
            self._update_navigation()

    def _on_finish_clicked(self) -> None:
        # Show what the first sort would do before doing it, so nobody is
        # surprised by hundreds of files moving at once.
        if hasattr(self, "_chk_sort_now") and self._chk_sort_now.isChecked() and sorter:
            try:
                plan = sorter.preview_existing(self._chosen_config(), limit=5000)
            except Exception:
                logging.debug("Could not preview the first sort", exc_info=True)
                plan = []
            if plan and not self._confirm_first_sort(plan):
                self._chk_sort_now.setChecked(False)
        self.accept()

    def _chosen_config(self) -> dict[str, Any]:
        """The settings with the folders, categories and starters ticked in step 1."""
        chosen = [n for n, c in self._folder_checks.items() if c.isEnabled() and c.isChecked()]
        off = [n for n, c in self._category_checks.items() if not c.isChecked()]
        starters = [k for k, c in self._starter_checks.items() if c.isChecked()]
        return apply_use_profile(
            folder_scan.apply_scan(self._config, self._scan, chosen, off, starters), self.chosen_use())

    def _confirm_first_sort(self, plan: list) -> bool:
        """Ask before the first big sort. True = sort them now."""
        if os.environ.get("DOWNLOAD_SORTER_NO_DIALOGS"):
            return True  # automated tests
        root = sorter.watch_root(self._config) if sorter else None
        counts = first_sort_summary(plan, root)
        total = len(plan)
        lines = [f"{folder}: {n} file{'s' if n != 1 else ''}" for folder, n in counts[:8]]
        if len(counts) > 8:
            lines.append(f"…and {len(counts) - 8} more folders")
        box = QMessageBox(self)
        box.setWindowTitle("Sort the files already in Downloads?")
        box.setText(f"Download Sorter would file {total} file{'s' if total != 1 else ''} already in "
                    "your Downloads folder:")
        box.setInformativeText("\n".join(lines) + "\n\nYou can put them all back with one Undo.")
        sort_btn = box.addButton("Sort them", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Not now", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return box.clickedButton() is sort_btn

    def accept(self) -> None:
        """Set up from the ticked folders, persist preferences and onboarding completion, then close."""
        try:
            try:
                self._config = self._chosen_config()
            except Exception:
                logging.exception("Could not apply the folder choices")
                self._config["user_persona"] = "custom"

            self._config["onboarding_completed"] = True

            sort_now = hasattr(self, "_chk_sort_now") and self._chk_sort_now.isChecked()
            if sort_now:
                self._config.pop("skip_files_before", None)
            else:
                # "Leave my existing files alone": automatic sweeps (now and after
                # every restart) skip anything already in Downloads at this moment.
                self._config["skip_files_before"] = time.time()

            if hasattr(self, "_chk_unzip"):
                _set_auto_unzip(self._config, self._chk_unzip.isChecked())

            # Saving with onboarding_completed=True is what lets a waiting
            # background sorter start; it then does the initial sort itself.
            if hasattr(suite_data, "save_sorter_config"):
                suite_data.save_sorter_config(self._config)
            # The learner starts out knowing which files live in which folder.
            folder_scan.seed_learning(self._scan, self._config)

            if hasattr(self, "_chk_startup") and hasattr(suite_data, "set_startup_enabled"):
                try:
                    suite_data.set_startup_enabled(self._chk_startup.isChecked())
                except Exception:
                    pass

            if hasattr(self, "_chk_context") and hasattr(suite_data, "set_context_menu_enabled"):
                try:
                    suite_data.set_context_menu_enabled(self._chk_context.isChecked())
                except Exception:
                    pass

            if sort_now and not _sorter_is_waiting_for_setup():
                # No background sorter to hand the initial sort to: do it here,
                # as one batch so "Undo last sort" can put everything back.
                if sorter and hasattr(sorter, "sort_existing"):
                    try:
                        sorter.sort_existing(self._config, dry_run=False, batch_id=uuid.uuid4().hex[:8])
                    except Exception:
                        pass
        except Exception:
            pass

        super().accept()


# ============================================================================
# 3. Home and Settings pages (shown inside RulesManagerDialog)
# ============================================================================

PERSONA_LABELS: list[tuple[str, str]] = [
    ("custom", "Just my folders"),
    ("everyday_home", "Everyday"),
    ("academic_student", "Student"),
    ("office_admin", "Office and work"),
    ("maker_3d", "3D printing, CAD and CNC"),
]


def first_sort_summary(plan: list, root: Path | None) -> list[tuple[str, int]]:
    """[("Images", 80), ("Documents", 40), ...]: where a sort would send files, most first."""
    counts: dict[str, int] = {}
    for _src, dest in plan:
        dest = Path(dest)
        try:
            folder = str(dest.parent.relative_to(root)) if root else dest.parent.name
        except ValueError:
            folder = str(dest.parent)
        counts[folder] = counts.get(folder, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def _storage_sense_days() -> int | None:
    try:
        return suite_data.storage_sense_downloads_days()
    except Exception:
        return None


def _open_storage_sense_settings() -> None:
    if sys.platform == "win32":
        try:
            os.startfile("ms-settings:storagepolicies")  # type: ignore[attr-defined]
        except OSError:
            logging.debug("Could not open Storage Sense settings", exc_info=True)


def _local_time(raw: str) -> datetime | None:
    """A history timestamp as a local datetime, or None when it can't be read."""
    try:
        when = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return when.astimezone() if when.tzinfo is not None else when


def _now_like(when: datetime) -> datetime:
    """Now in local time, as the same kind of datetime (with or without a time zone) as ``when``."""
    return datetime.now().astimezone() if when.tzinfo is not None else datetime.now()


def _friendly_time(raw: str, now: datetime | None = None) -> str:
    """'2026-11-08T14:05:00+00:00' -> 'Today, 2:05 PM' in local time."""
    when = _local_time(raw)
    if when is None:
        return raw
    clock = when.strftime("%I:%M %p").lstrip("0")
    today = (now or _now_like(when)).date()
    if when.date() == today:
        return f"Today, {clock}"
    if when.date() == today - timedelta(days=1):
        return f"Yesterday, {clock}"
    return f"{when.strftime('%b')} {when.day}, {clock}"


def _relative_time(raw: str, now: datetime | None = None) -> str:
    """"Just now", "3 min ago", "2 h ago", then "Today, 2:05 PM", "Yesterday, ..." and dates."""
    when = _local_time(raw)
    if when is None:
        return raw
    now = now or _now_like(when)
    seconds = (now - when).total_seconds()
    if seconds < 45:
        return "Just now"  # also a moment "in the future": another clock was a little ahead
    if seconds < 3600:
        return f"{max(1, int(seconds // 60))} min ago"
    if seconds < 6 * 3600 and when.date() == now.date():
        return f"{int(seconds // 3600)} h ago"
    return _friendly_time(raw, now)


def _full_time(raw: str) -> str:
    """"Monday, Nov 9, 2026, 2:05 PM": the whole date, for a tooltip."""
    when = _local_time(raw)
    if when is None:
        return str(raw)
    clock = when.strftime("%I:%M %p").lstrip("0")
    return f"{when.strftime('%A')}, {when.strftime('%b')} {when.day}, {when.year}, {clock}"


class HomeDashboardWidget(QWidget):
    """Home page: whether sorting is on, the main actions, and recently sorted files.

    The page reacts as things happen, so it is clear the sorter is working:
    the status dot pulses while Downloads is being watched, files appear in
    "Recently sorted" (briefly highlighted) seconds after they are filed, the
    count of files organized today counts up, and every action ends with a
    short message saying what happened ("Sorted 8 files into 3 folders").
    """

    persona_changed = Signal(str)
    status_changed = Signal(str)       # "running", "paused", "stopped" or "starting"
    update_available = Signal(bool)    # a newer version was found
    navigate_requested = Signal(str)   # ask the window to show another page ("rules", ...)
    # Results from background work, delivered on the window's thread.
    _sort_finished = Signal(int, str)
    _preview_ready = Signal(int, int)
    _update_ready = Signal(object)
    _update_progress = Signal(int, int)
    _update_finished = Signal(object, str)

    POLL_MS = 2000
    START_TIMEOUT_S = 15.0
    FIRST_LIMIT = 25   # files shown at first in "Recently sorted"
    MORE_LIMIT = 100   # ... and after "Show more"

    def __init__(self, config: dict[str, Any] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = copy.deepcopy(config) if config is not None else (
            suite_data.load_sorter_config() if hasattr(suite_data, "load_sorter_config") else {}
        )
        self._shown_ids: set[str] | None = None
        self._records: list[dict[str, Any]] = []  # what the list was loaded with, before the filter
        self._limit = self.FIRST_LIMIT
        self._tone: str | None = None
        self._times_refreshed = 0.0
        self._last_filed_raw = ""
        self._last_filed_what = ""
        self._history_key: tuple[int, int] | None = None
        self._moves_today: int | None = None
        self._last_state: str | None = None
        self._announced_state: str | None = None
        self._starting_until = 0.0
        self._sorting = False
        self._waiting = 0
        self._preview_generation = 0

        self._update_checked = False
        self._release: dict[str, Any] | None = None
        self._update_downloading = False
        self._update_cancel = False
        self._update_shown_percent = -1
        self._sort_finished.connect(self._finish_sort)
        self._preview_ready.connect(self._on_preview_ready)
        self._update_ready.connect(self._on_update_ready)
        self._update_progress.connect(self._on_update_progress)
        self._update_finished.connect(self._on_update_finished)

        self._init_ui()

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(self.POLL_MS)
        self._refresh_timer.timeout.connect(self._tick)
        self._refresh_timer.start()

        self.refresh_data()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        header, _ = ui_style.page_header("Home")
        layout.addWidget(header)

        # "Download Sorter 2.1.0 is available. [Download]"
        self._update_banner = ui_motion.Banner(self)
        layout.addWidget(self._update_banner)
        self._update_bar = QProgressBar(self)
        self._update_bar.setTextVisible(False)
        self._update_bar.setFixedHeight(4)
        self._update_bar.setStyleSheet(
            f"QProgressBar {{ background: {COLORS['surfaceHover']}; border: none; border-radius: 2px; }}"
            f" QProgressBar::chunk {{ background: {COLORS['info']}; border-radius: 2px; }}"
        )
        self._update_bar.setVisible(False)
        layout.addWidget(self._update_bar)

        # Status and the main actions
        self._status_card, hero = ui_style.card(spacing=6)
        self._status_card.setProperty("hero", "true")  # the soft gradient; its tint follows the sorter's state
        self._hero_card = self._status_card  # older name kept for callers

        status_row = QHBoxLayout()
        status_row.setSpacing(10)
        self._status_dot = ui_style.status_dot(COLORS["success"], 10)
        status_row.addWidget(self._status_dot)
        self._status_badge = ui_style.label("Sorting is on", "section")
        status_row.addWidget(self._status_badge)
        status_row.addStretch()
        self._files_today_lbl = ui_style.label("0 files organized today", "muted")
        self._metric_lbl = self._files_today_lbl
        status_row.addWidget(self._files_today_lbl)
        hero.addLayout(status_row)

        self._status_desc_lbl = ui_style.label("", "muted", wrap=True)
        hero.addWidget(self._status_desc_lbl)
        self._last_filed_lbl = ui_style.label("", "caption")
        self._last_filed_lbl.setVisible(False)
        hero.addWidget(self._last_filed_lbl)
        self._waiting_lbl = ui_style.label("", wrap=True)
        self._waiting_lbl.setStyleSheet(f"color: {COLORS['info']};")
        self._waiting_lbl.setVisible(False)
        hero.addWidget(self._waiting_lbl)
        hero.addSpacing(10)

        # The row wraps: at the narrowest window the four buttons don't fit side by side,
        # so "Open Downloads" moves to a second row instead of being cut off.
        actions = ui_widgets.FlowLayout(spacing=8, push_last=True)
        self._actions_row = actions
        self._btn_sort_now = ui_style.button("Sort Downloads now", "primary", "sort",
                                             "Sort the files that are in your Downloads folder right now")
        self._btn_sort_now.clicked.connect(self.start_sort_now)
        actions.addWidget(self._btn_sort_now)

        self._btn_undo_last = ui_style.button("Undo last sort", None, "undo",
                                              "Put the files from the most recent sort back where they were")
        self._btn_undo_last.clicked.connect(self.undo_last)
        actions.addWidget(self._btn_undo_last)

        self._btn_pause_resume = ui_style.button("Pause sorting", None, "pause")
        self._btn_pause_resume.clicked.connect(self.toggle_pause)
        actions.addWidget(self._btn_pause_resume)

        self._btn_open_downloads = ui_style.button("Open Downloads", "ghost", "folder")
        self._btn_open_downloads.clicked.connect(self.open_downloads)
        actions.addWidget(self._btn_open_downloads)
        hero.addLayout(actions)
        layout.addWidget(self._status_card)

        # What just happened ("Sorted 8 files into 3 folders. [Undo]")
        self._banner = ui_motion.Banner(self)
        layout.addWidget(self._banner)

        # Recently sorted files
        feed_header = QHBoxLayout()
        feed_header.addWidget(ui_style.label("Recently sorted", "section"))
        feed_header.addStretch()
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("Filter by name or folder")
        self._filter_edit.setClearButtonEnabled(True)
        self._filter_edit.addAction(ui_icons.icon("filter"), QLineEdit.ActionPosition.LeadingPosition)
        self._filter_edit.setAccessibleName("Filter recently sorted files")
        self._filter_edit.setFixedWidth(230)
        self._filter_edit.setVisible(False)  # only shown when there is something to filter
        self._filter_edit.textChanged.connect(self._on_filter_changed)
        feed_header.addWidget(self._filter_edit)
        self._refresh_btn = ui_style.icon_button("refresh", "Refresh")
        self._refresh_btn.clicked.connect(self._manual_refresh)
        feed_header.addWidget(self._refresh_btn)
        layout.addLayout(feed_header)

        self._activity_layout = QVBoxLayout()
        self._activity_layout.setSpacing(0)

        # The card fills the room that is left and centers its content, so these margins only
        # matter in the shortest window, where a taller card would squeeze the buttons above it.
        self._empty_card, empty = ui_style.card(margins=(24, 16, 24, 16), spacing=8)
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._mascot = SorterMascotWidget(self._empty_card)
        self._mascot.set_state("happy")
        empty.addWidget(self._mascot, 0, Qt.AlignmentFlag.AlignHCenter)
        self._empty_state_lbl = ui_style.label("Nothing sorted yet", "section")
        self._empty_state_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_widget = self._empty_card
        empty.addWidget(self._empty_state_lbl)
        self._empty_hint_lbl = ui_style.label(
            "Files you download will show up here a few seconds after they're sorted.", "muted")
        self._empty_hint_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty.addWidget(self._empty_hint_lbl)
        self._activity_layout.addWidget(self._empty_card)

        self._activity_table = QTableWidget()
        self._activity_table.setColumnCount(4)
        self._activity_table.setHorizontalHeaderLabels(["File", "Moved to", "When", ""])
        hdr = self._activity_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        hdr.setHighlightSections(False)
        hdr.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._activity_table.setColumnWidth(1, 190)
        self._activity_table.setColumnWidth(2, 150)
        self._activity_table.setColumnWidth(3, 84)  # "Show in folder" and the "..." menu side by side
        self._activity_table.setIconSize(QSize(28, 28))
        self._activity_table.verticalHeader().setVisible(False)
        self._activity_table.verticalHeader().setDefaultSectionSize(44)
        self._activity_table.setShowGrid(False)
        self._activity_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._activity_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._activity_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._activity_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._activity_table.customContextMenuRequested.connect(self._show_row_menu)
        self._activity_table.cellDoubleClicked.connect(self._on_row_double_clicked)
        self._activity_layout.addWidget(self._activity_table)

        self._more_btn = ui_style.button("Show more", "ghost")
        self._more_btn.setToolTip(f"Show up to {self.MORE_LIMIT} recently sorted files")
        self._more_btn.setVisible(False)
        self._more_btn.clicked.connect(self.show_more)
        self._activity_layout.addWidget(self._more_btn, 0, Qt.AlignmentFlag.AlignHCenter)

        layout.addLayout(self._activity_layout, 1)

    # ---- state ---------------------------------------------------------

    def _sorter_state(self) -> str:
        """'paused', 'running' or 'stopped'."""
        return current_sorter_state()

    def _watched_folder_text(self) -> str:
        root = None
        if sorter and hasattr(sorter, "watch_root"):
            try:
                root = Path(sorter.watch_root(self._config))
            except Exception:
                root = None
        if root is None or root == Path.home() / "Downloads":
            return "your Downloads folder"
        return str(root)

    def refresh_status(self) -> None:
        """Update the status line, the files-today count, and the pause button."""
        state = self._sorter_state()
        starting = False
        if self._starting_until:
            if state == "running":
                self._starting_until = 0.0
                self._banner.show_message(
                    f"Sorting is on. Download Sorter is now watching {self._watched_folder_text()}, "
                    "and new downloads are filed a few seconds after they finish.",
                    "success",
                )
                self.refresh_waiting()
            elif time.monotonic() < self._starting_until:
                starting = True
            else:
                self._starting_until = 0.0
                self._banner.show_message(
                    "Download Sorter didn't start. Try again, or open Download Sorter from the Start menu.",
                    "error", timeout_ms=0,
                )

        if starting:
            title, desc, color = ("Starting...", "Opening the background sorter.", COLORS["info"])
            btn_text, btn_icon = "Starting...", "play"
        elif state == "paused":
            title, desc, color = ("Sorting is paused",
                                  "New downloads stay where they land until you resume.",
                                  COLORS["warning"])
            btn_text, btn_icon = "Resume sorting", "play"
        elif state == "running":
            title, desc, color = ("Sorting is on",
                                  f"Watching {self._watched_folder_text()}. New files are filed "
                                  "automatically a few seconds after they finish downloading.",
                                  COLORS["success"])
            btn_text, btn_icon = "Pause sorting", "pause"
        else:
            title, desc, color = ("Sorting is off",
                                  "Download Sorter isn't running, so new downloads aren't filed. "
                                  "You can still sort by hand.",
                                  COLORS["textFaint"])
            btn_text, btn_icon = "Start sorting", "play"
        self._status_badge.setText(title)
        self._status_desc_lbl.setText(desc)
        ui_style.set_status_dot(self._status_dot, color)
        # The dot "breathes" while Downloads is being watched (or the sorter is starting).
        ui_motion.breathe(self._status_dot, (state == "running" or starting) and self.isVisible())
        self._btn_pause_resume.setText(btn_text)
        # The most useful action is the green one: starting or resuming when
        # sorting is off, otherwise sorting what's waiting.
        sorting_stopped = not starting and state != "running"
        ui_style.set_role(self._btn_pause_resume, "primary" if sorting_stopped else None, btn_icon)
        ui_style.set_role(self._btn_sort_now, None if sorting_stopped else "primary", "sort")
        self._btn_pause_resume.setEnabled(not starting)
        tone = "info" if starting else {"running": "success", "paused": "warning"}.get(state)
        if tone != self._tone:  # restyling every two seconds would be wasted work
            self._tone = tone
            ui_style.set_tone(self._status_card, tone)
        self._last_state = "starting" if starting else state
        self._update_empty_text()
        if self._last_state != self._announced_state:
            self._announced_state = self._last_state
            self.status_changed.emit(self._last_state)

        moves_today = 0
        if sorter and hasattr(sorter, "IncrementalHistoryReader") and hasattr(sorter, "HISTORY_FILE"):
            try:
                # Keep one reader so each poll reads only the lines added since the last one.
                if getattr(self, "_history_reader", None) is None:
                    self._history_reader = sorter.IncrementalHistoryReader(sorter.HISTORY_FILE)
                moves_today = self._history_reader.moves_today()
            except Exception:
                pass
        previous = self._moves_today if self._moves_today is not None else moves_today
        self._moves_today = moves_today
        ui_motion.count_up(self._files_today_lbl, previous, moves_today, self._files_today_text)

    @staticmethod
    def _files_today_text(count: int) -> str:
        noun = "file" if count == 1 else "files"
        return f"{count} {noun} organized today"

    def _history_signature(self) -> tuple[int, int] | None:
        path = getattr(sorter, "HISTORY_FILE", None) if sorter else None
        if path is None:
            return None
        try:
            st = Path(path).stat()
            return (st.st_size, st.st_mtime_ns)
        except OSError:
            return None

    def _tick(self) -> None:
        """Every couple of seconds: status, and new activity from the background sorter."""
        self.refresh_status()
        signature = self._history_signature()
        if signature != self._history_key and not self._sorting:
            self.refresh_data(announce=True)
        elif time.monotonic() - self._times_refreshed >= 30:
            self._refresh_relative_times()  # "3 min ago" must keep counting while the window sits open

    def refresh_data(self, announce: bool = False) -> None:
        """Reload settings and the recent activity list.

        With ``announce``, files that were filed since the last look are
        highlighted and mentioned in a short message.
        """
        if hasattr(suite_data, "load_sorter_config"):
            self._config = suite_data.load_sorter_config()

        self.refresh_status()
        self._history_key = self._history_signature()

        records: list[dict[str, Any]] = []
        if hasattr(suite_data, "get_recent_activity"):
            try:
                records = self._label_folders(suite_data.get_recent_activity(limit=self._limit))
            except Exception:
                records = []
        self._records = records
        self._times_refreshed = time.monotonic()

        previous_ids = self._shown_ids
        self._shown_ids = {str(r.get("id", "")) for r in records}
        new_ids: set[str] = set()
        if previous_ids is not None:
            new_ids = self._shown_ids - previous_ids

        self._set_last_filed(records[0] if records else None)
        self._filter_edit.setVisible(bool(records))
        self._more_btn.setVisible(len(records) >= self._limit and self._limit < self.MORE_LIMIT)

        if not records:
            self._show_empty()
            return

        new_rows = self._render_rows(new_ids)
        if new_rows:
            ui_motion.flash_rows(self._activity_table, new_rows)
        fresh = [r for r in records if str(r.get("id", "")) in new_ids] if new_ids else []
        if fresh and announce:
            if len(fresh) == 1:
                text = f"Filed {fresh[0].get('filename', 'a file')} in {fresh[0].get('folder', 'its folder')}."
            else:
                text = f"Filed {len(fresh)} new files. {self._folder_breakdown(fresh)}"
            self._banner.show_message(text.strip(), "success", "Undo", self.undo_last)
            self.refresh_waiting()

    # ---- the "Recently sorted" list ------------------------------------

    def _show_empty(self) -> None:
        self._update_empty_text()
        self._empty_card.setVisible(True)
        self._activity_table.setVisible(False)
        self._activity_table.setRowCount(0)

    def _empty_hint(self) -> str:
        if self._last_state == "stopped":
            return "Sorting is off. Click “Start sorting” above to file new downloads."
        if self._waiting > 0:
            noun = "file is" if self._waiting == 1 else "files are"
            button = f"Sort {self._waiting} file{'s' if self._waiting != 1 else ''} now"
            return f"{self._waiting} {noun} waiting in Downloads. Click “{button}” above to file them."
        return "Files you download will show up here a few seconds after they're sorted."

    def _update_empty_text(self) -> None:
        query = self._filter_edit.text().strip()
        if self._records and query:
            title, hint = "No matches", f"Nothing in the list matches “{query}”."
        else:
            title, hint = "Nothing sorted yet", self._empty_hint()
        self._empty_state_lbl.setText(title)
        self._empty_hint_lbl.setText(hint)

    def _visible_records(self) -> list[dict[str, Any]]:
        query = self._filter_edit.text().strip().lower()
        if not query:
            return list(self._records)
        # 3D print files also match on their print details (printer, filament, plates).
        try:
            import prints
            print_names = prints.matching_files(query)
        except Exception:
            print_names = set()
        return [r for r in self._records
                if query in str(r.get("filename", "")).lower() or query in str(r.get("folder", "")).lower()
                or str(r.get("filename", "")) in print_names]

    def _render_rows(self, new_ids: set[str]) -> list[int]:
        """Fill the table from the loaded records (through the filter). Returns the rows that are new."""
        records = self._visible_records()
        if not records:
            self._show_empty()
            return []
        if not self._activity_table.isVisible() and self.isVisible():
            ui_motion.fade_in(self._activity_table)
        self._empty_card.setVisible(False)
        self._activity_table.setVisible(True)
        self._activity_table.setRowCount(0)
        self._activity_table.setRowCount(len(records))
        new_rows: list[int] = []
        for row, rec in enumerate(records):
            self._fill_row(row, rec)
            if str(rec.get("id", "")) in new_ids:
                new_rows.append(row)
        return new_rows

    def _fill_row(self, row: int, rec: dict[str, Any]) -> None:
        filename = str(rec.get("filename", ""))
        folder = str(rec.get("folder", ""))
        dest = str(rec.get("dest", ""))
        raw_time = str(rec.get("timestamp", ""))
        gone = not bool(rec.get("exists", True))  # moved or deleted since it was filed
        main_color = QColor(COLORS["textFaint"] if gone else COLORS["text"])
        quiet_color = QColor(COLORS["textFaint"] if gone else COLORS["textMuted"])

        f_item = QTableWidgetItem(filename)
        f_item.setToolTip(f"{dest or filename}\nThis file has been moved or deleted since it was filed." if gone
                          else "\n".join(part for part in (dest or filename, suite_data.describe_why(rec)) if part))
        f_item.setIcon(ui_widgets.category_tile_icon(ui_categories.kind_of(dest or filename, folder)))
        f_item.setData(Qt.ItemDataRole.UserRole, rec)
        f_item.setForeground(main_color)
        self._activity_table.setItem(row, 0, f_item)

        folder_item = QTableWidgetItem(folder)
        folder_item.setIcon(ui_widgets.padded_icon("folder", quiet_color.name()))
        folder_item.setForeground(quiet_color)
        folder_item.setToolTip(folder)
        self._activity_table.setItem(row, 1, folder_item)

        ts_item = QTableWidgetItem(_relative_time(raw_time))
        ts_item.setForeground(quiet_color)
        ts_item.setToolTip(_full_time(raw_time))
        ts_item.setData(Qt.ItemDataRole.UserRole, raw_time)
        self._activity_table.setItem(row, 2, ts_item)

        self._activity_table.setCellWidget(row, 3, self._row_actions(rec))

    def _row_actions(self, rec: dict[str, Any]) -> QWidget:
        """The two buttons at the end of a row: "Show in folder" and the "..." menu."""
        cell = QWidget()
        layout = QHBoxLayout(cell)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(0)
        dest = str(rec.get("dest", ""))
        name = str(rec.get("filename", "")) or "this file"
        reveal = ui_style.icon_button("reveal", "Show in folder")
        reveal.setAccessibleName(f"Show {name} in folder")  # a screen reader hears which file
        reveal.clicked.connect(lambda _c=False, p=dest: self._show_in_folder(p))
        layout.addWidget(reveal)
        menu = ui_widgets.RowMenuButton("More actions")
        menu.setAccessibleName(f"More actions for {name}")
        menu.set_actions(self._row_entries(rec))
        layout.addWidget(menu)
        return cell

    def _row_entries(self, rec: dict[str, Any]) -> list:
        """What can be done with one filed file. Fixing a misfile needs the file to still be where it was filed."""
        dest = str(rec.get("dest", ""))
        can_fix = bool(rec.get("exists", True)) and bool(rec.get("id"))
        return [
            ("Show in folder", "reveal", lambda p=dest: self._show_in_folder(p), True),
            None,
            ("Put back where it was", "undo", lambda r=rec: self.put_back(r), can_fix),
            ("Move to...", "move", lambda r=rec: self.move_to(r), can_fix),
            ("Always file like this...", "rule", lambda r=rec: self.always_file_like(r), can_fix),
        ]

    def _row_record(self, row: int) -> dict[str, Any] | None:
        item = self._activity_table.item(row, 0)
        rec = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return rec if isinstance(rec, dict) else None

    def _show_row_menu(self, pos) -> None:
        item = self._activity_table.itemAt(pos)
        rec = self._row_record(item.row()) if item is not None else None
        if rec is None:
            return
        menu = QMenu(self)
        ui_widgets.fill_menu(menu, self._row_entries(rec))
        menu.exec(self._activity_table.viewport().mapToGlobal(pos))

    def _on_row_double_clicked(self, row: int, column: int) -> None:
        rec = self._row_record(row)
        if rec is not None and column != 3:
            self._show_in_folder(str(rec.get("dest", "")))

    def _on_filter_changed(self, _text: str) -> None:
        if self._records:
            self._render_rows(set())

    def show_more(self) -> None:
        """Show up to MORE_LIMIT files instead of the first FIRST_LIMIT."""
        self._limit = self.MORE_LIMIT
        self.refresh_data()

    def _set_last_filed(self, rec: dict[str, Any] | None) -> None:
        if not rec or _local_time(str(rec.get("timestamp", ""))) is None:
            self._last_filed_raw = ""
            self._last_filed_lbl.setVisible(False)
            return
        self._last_filed_raw = str(rec.get("timestamp", ""))
        self._last_filed_what = f"{rec.get('filename', 'a file')} in {rec.get('folder', 'its folder')}"
        self._update_last_filed_text()

    def _update_last_filed_text(self) -> None:
        if not self._last_filed_raw:
            return
        when = _relative_time(self._last_filed_raw)
        if when.startswith("Today, "):
            when = "today at " + when[len("Today, "):]
        elif when.startswith("Yesterday, "):
            when = "yesterday at " + when[len("Yesterday, "):]
        elif when == "Just now":
            when = "just now"
        elif not when.endswith(" ago"):
            when = "on " + when
        self._last_filed_lbl.setText(f"Last filed: {self._last_filed_what}, {when}")
        self._last_filed_lbl.setToolTip(_full_time(self._last_filed_raw))
        self._last_filed_lbl.setVisible(True)

    def _refresh_relative_times(self) -> None:
        self._times_refreshed = time.monotonic()
        for row in range(self._activity_table.rowCount()):
            item = self._activity_table.item(row, 2)
            raw = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
            if raw:
                item.setText(_relative_time(str(raw)))
        self._update_last_filed_text()

    # ---- fixing a misfiled file ----------------------------------------

    def _watch_root(self) -> Path | None:
        if sorter and hasattr(sorter, "watch_root"):
            try:
                return Path(sorter.watch_root(self._config))
            except Exception:
                return None
        return None

    def _no_longer_in_history(self, rec: dict[str, Any]) -> None:
        self._banner.show_message(
            f"{rec.get('filename', 'That file')} can't be changed here: its move was already undone "
            "or is no longer in the history.", "warning")
        self.refresh_data()

    def put_back(self, rec: dict[str, Any]) -> bool:
        """Put one filed file back where it was before it was filed. True when it was."""
        record = refile.active_record(str(rec.get("id", "")))
        if record is None:
            self._no_longer_in_history(rec)
            return False
        outcome = refile.put_back(record, self._watch_root())
        self.refresh_data()
        self._banner.show_message(outcome.message, "info" if outcome.ok else "warning")
        self.refresh_waiting()
        return outcome.ok

    def move_to(self, rec: dict[str, Any], folder: str | Path | None = None) -> bool:
        """Move one filed file to a folder you pick (``folder`` skips the picker). True when it moved."""
        record = refile.active_record(str(rec.get("id", "")))
        if record is None:
            self._no_longer_in_history(rec)
            return False
        if folder is None:
            name = Path(str(record.get("dest", ""))).name
            start = self._watch_root() or Path.home()
            folder = QFileDialog.getExistingDirectory(self, f"Move {name} to...", str(start))
            if not folder:
                return False
        outcome = refile.move_to_folder(record, Path(folder), self._config, self._watch_root())
        self.refresh_data()
        self._banner.show_message(outcome.message, "success" if outcome.ok else "warning")
        if outcome.ok and outcome.suggestion:
            refile.ask_about_suggestion(outcome.suggestion, self._config)
        self.refresh_waiting()
        return outcome.ok

    def always_file_like(self, rec: dict[str, Any]) -> bool:
        """Open the rule editor filled in from this file, and save the rule as the first (winning) one."""
        from rules_manager import RuleEditDialog

        dest = Path(str(rec.get("dest", "")))
        draft = refile.rule_draft_for(dest.name, dest.parent, self._watch_root())
        dialog = RuleEditDialog(rule=draft, rule_type="keyword", parent=self.window(), title="Always file like this")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        rule = dialog.get_rule_data()
        rule["origin"] = "user"
        try:
            refile.add_rule_first(rule)
        except Exception as exc:
            logging.exception("Could not add a rule from Home")
            self._banner.show_message(f"The rule couldn't be saved: {exc}", "error", timeout_ms=0)
            return False
        if hasattr(suite_data, "load_sorter_config"):
            self._config = suite_data.load_sorter_config()
        self._banner.show_message(
            f"Rule “{rule['name']}” added. Matching files will go to {rule['folder']}.",
            "success", "Open Rules", lambda: self.navigate_requested.emit("rules"), timeout_ms=10000,
        )
        return True

    def _label_folders(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Name each file's folder from Downloads down: "Documents\\PDF", not just "PDF"."""
        root = None
        if sorter and hasattr(sorter, "watch_root"):
            try:
                root = Path(sorter.watch_root(self._config))
            except Exception:
                root = None
        if root is None:
            return records
        for rec in records:
            try:
                parts = Path(str(rec.get("dest") or "")).parent.relative_to(root).parts
            except (ValueError, OSError):
                continue
            if parts:
                rec["folder"] = "\\".join(parts)
        return records

    @staticmethod
    def _folder_breakdown(records: list[dict[str, Any]], shown: int = 3) -> str:
        counts: dict[str, int] = {}
        for rec in records:
            folder = str(rec.get("folder", "")) or "Downloads"
            counts[folder] = counts.get(folder, 0) + 1
        ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))
        parts = [f"{name} ({n})" for name, n in ordered[:shown]]
        if len(ordered) > shown:
            parts.append(f"{len(ordered) - shown} more")
        return ", ".join(parts) + "." if parts else ""

    def _manual_refresh(self) -> None:
        self.refresh_data()
        self.refresh_update_notice(force=True)

    # ---- newer version -------------------------------------------------

    def refresh_update_notice(self, force: bool = False) -> None:
        """Ask (in the background) whether a newer version exists."""
        if (self._update_checked and not force) or (not force and not update_check.is_enabled(self._config)):
            return
        self._update_checked = True
        config = copy.deepcopy(self._config)

        def work() -> None:
            try:
                updater.clean_old_downloads()
            except Exception:
                pass
            try:
                release = update_check.check(config, force=force)
            except Exception:
                release = None
            try:
                self._update_ready.emit(release)
            except RuntimeError:
                pass  # the window was closed meanwhile

        threading.Thread(target=work, name="home-update-check", daemon=True).start()

    def _on_update_ready(self, release: Any) -> None:
        if not release:
            return
        self._release = dict(release)
        self._offer_update()
        self.update_available.emit(True)

    def _offer_update(self) -> None:
        release = self._release or {}
        version = update_check.display_version(release)
        url = str(release.get("url") or update_check.RELEASES_PAGE)
        self._update_bar.setVisible(False)
        if updater.can_self_update() and updater.has_installer(release):
            self._update_banner.show_message(
                f"Download Sorter {version} is available. Your rules and settings are kept.",
                "info", "Download and install", self.start_update_download, timeout_ms=0,
            )
        else:
            self._update_banner.show_message(
                f"Download Sorter {version} is available. "
                "Download it, then run the installer; your rules and settings are kept.",
                "info", "Download", lambda: self._open_url(url), timeout_ms=0,
            )

    # ---- in-app update ---------------------------------------------------

    def start_update_download(self) -> None:
        """Download the new installer in the background, with progress."""
        release = dict(self._release or {})
        if self._update_downloading or not updater.has_installer(release):
            return
        self._update_downloading = True
        self._update_cancel = False
        self._update_shown_percent = -1
        version = update_check.display_version(release)
        self._update_banner.show_message(
            f"Downloading Download Sorter {version}...", "info", "Cancel", self.cancel_update_download,
            timeout_ms=0,
        )
        self._update_bar.setRange(0, 0)  # "busy" until the size is known
        self._update_bar.setVisible(True)

        def work() -> None:
            path, error = None, ""
            try:
                path = updater.download(
                    release,
                    progress=lambda done, total: self._update_progress.emit(done, total),
                    cancelled=lambda: self._update_cancel,
                )
            except updater.Cancelled:
                error = "cancelled"
            except updater.UpdateError as exc:
                error = str(exc) or "Couldn't download the update."
            except Exception:
                error = "Couldn't download the update."
            try:
                self._update_finished.emit(str(path) if path else None, error)
            except RuntimeError:
                pass  # the window was closed meanwhile

        threading.Thread(target=work, name="home-update-download", daemon=True).start()

    def cancel_update_download(self) -> None:
        self._update_cancel = True

    @staticmethod
    def _megabytes(count: int) -> str:
        return f"{count / (1024 * 1024):.0f}"

    def _on_update_progress(self, done: int, total: int) -> None:
        if not self._update_downloading:
            return
        if total <= 0:
            return
        percent = int(done * 100 / total)
        if percent == self._update_shown_percent:
            return
        self._update_shown_percent = percent
        self._update_bar.setRange(0, 100)
        self._update_bar.setValue(percent)
        version = update_check.display_version(self._release or {})
        self._update_banner.show_message(
            f"Downloading Download Sorter {version}: {self._megabytes(done)} of {self._megabytes(total)} MB",
            "info", "Cancel", self.cancel_update_download, timeout_ms=0,
        )

    def _on_update_finished(self, path: Any, error: str) -> None:
        self._update_downloading = False
        self._update_bar.setVisible(False)
        release = self._release or {}
        version = update_check.display_version(release)
        if path:
            self._downloaded_installer = str(path)
            self._update_banner.show_message(
                f"Download Sorter {version} is ready. Download Sorter will close for a few seconds "
                "and open again; your rules and settings are kept.",
                "success", "Restart and install", self.install_update, timeout_ms=0,
            )
        elif error == "cancelled":
            self._offer_update()
        else:
            url = str(release.get("url") or update_check.RELEASES_PAGE)
            self._update_banner.show_message(
                f"{error} You can download it from the release page instead.",
                "error", "Open download page", lambda: self._open_url(url), timeout_ms=0,
            )

    def install_update(self) -> None:
        """Hand over to the installer, then close; it reopens Download Sorter."""
        path = getattr(self, "_downloaded_installer", "")
        try:
            updater.start_install(Path(path))
        except Exception as exc:
            url = str((self._release or {}).get("url") or update_check.RELEASES_PAGE)
            message = str(exc) if isinstance(exc, updater.UpdateError) else "Couldn't start the installer."
            self._update_banner.show_message(
                f"{message} You can download it from the release page instead.",
                "error", "Open download page", lambda: self._open_url(url), timeout_ms=0,
            )
            return
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            # Windows with unsaved rules save them instead of asking (see rules_manager).
            app.setProperty("downloadSorterQuittingForUpdate", True)
            app.quit()

    @staticmethod
    def _open_url(url: str) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl(url))

    # ---- files still waiting in Downloads --------------------------------

    def refresh_waiting(self) -> None:
        """Count, in the background, the files in Downloads that a sort would file."""
        if not (sorter and hasattr(sorter, "preview_existing")):
            return
        self._preview_generation += 1
        generation = self._preview_generation
        config = copy.deepcopy(self._config)

        def work() -> None:
            try:
                count = len(sorter.preview_existing(config))
            except Exception:
                count = 0
            try:
                self._preview_ready.emit(count, generation)
            except RuntimeError:
                pass  # the window was closed meanwhile

        threading.Thread(target=work, name="home-preview", daemon=True).start()

    def _on_preview_ready(self, count: int, generation: int) -> None:
        if generation != self._preview_generation:
            return
        self._waiting = count
        self._update_empty_text()
        if count > 0:
            noun = "file is" if count == 1 else "files are"
            self._waiting_lbl.setText(
                f"{count} {noun} waiting in Downloads. Files that were there before sorting "
                "started stay put until you sort them."
            )
            if not self._waiting_lbl.isVisible():
                ui_motion.fade_in(self._waiting_lbl)
        else:
            self._waiting_lbl.setVisible(False)
        if not self._sorting:
            self._btn_sort_now.setText(
                f"Sort {count} file{'s' if count != 1 else ''} now" if count > 0 else "Sort Downloads now"
            )

    # ---- actions -------------------------------------------------------

    def _show_in_folder(self, target_path: str) -> None:
        """Highlight target file in Windows Explorer."""
        if not target_path:
            return
        p = Path(target_path)
        try:
            if p.exists():
                # The path is its own argument after "/select,". Joined into one
                # ("/select,C:\a b\c.pdf"), Windows quotes the whole thing and Explorer
                # then ignores the request when the path has a space in it.
                subprocess.Popen(["explorer.exe", "/select,", str(p)])
            elif p.parent.exists():
                subprocess.Popen(["explorer.exe", str(p.parent)])
        except Exception:
            pass

    def _run_sort(self, batch_id: str) -> int:
        if not (sorter and hasattr(sorter, "sort_existing")):
            return 0
        try:
            cfg = suite_data.load_sorter_config() if hasattr(suite_data, "load_sorter_config") else self._config
            return int(sorter.sort_existing(cfg, dry_run=False, batch_id=batch_id) or 0)
        except Exception:
            return 0

    def _set_sorting(self, busy: bool) -> None:
        self._sorting = busy
        self._btn_sort_now.setEnabled(not busy)
        self._btn_undo_last.setEnabled(not busy)
        if busy:
            self._btn_sort_now.setText("Sorting...")
        else:
            self._btn_sort_now.setText("Sort Downloads now")
        ui_motion.breathe(self._btn_sort_now, busy)

    def start_sort_now(self) -> None:
        """"Sort Downloads now" without freezing the window: sort in the background."""
        if self._sorting:
            return
        batch_id = uuid.uuid4().hex[:8]
        self._set_sorting(True)

        def work() -> None:
            count = self._run_sort(batch_id)
            try:
                self._sort_finished.emit(count, batch_id)
            except RuntimeError:
                pass  # the window was closed meanwhile

        threading.Thread(target=work, name="home-sort-now", daemon=True).start()

    def sort_now(self) -> int:
        """Sort Downloads now, as one batch so "Undo last sort" reverses all of it."""
        batch_id = uuid.uuid4().hex[:8]
        self._set_sorting(True)
        count = self._run_sort(batch_id)
        self._finish_sort(count, batch_id)
        return count

    def is_sorting(self) -> bool:
        return self._sorting

    def _finish_sort(self, count: int, batch_id: str) -> None:
        self._set_sorting(False)
        self.refresh_data()
        if count <= 0:
            self._banner.show_message("Your Downloads folder is already tidy. Nothing needed sorting.", "info")
        else:
            batch: list[dict[str, Any]] = []
            if hasattr(suite_data, "get_recent_activity"):
                try:
                    recent = self._label_folders(suite_data.get_recent_activity(limit=max(count, 25) + 25))
                    batch = [r for r in recent if r.get("batch_id") == batch_id]
                except Exception:
                    batch = []
            noun = "file" if count == 1 else "files"
            folders = {str(r.get("folder", "")) for r in batch}
            if len(folders) > 1:
                text = f"Sorted {count} {noun} into {len(folders)} folders: {self._folder_breakdown(batch)}"
            elif len(folders) == 1:
                text = f"Sorted {count} {noun} into {next(iter(folders))}."
            else:
                text = f"Sorted {count} {noun}."
            self._banner.show_message(text, "success", "Undo", self.undo_last, timeout_ms=10000)
            dests = {str(r.get("dest", "")) for r in batch}
            rows = [row for row in range(self._activity_table.rowCount())
                    if self._activity_table.item(row, 0) is not None
                    and self._activity_table.item(row, 0).toolTip() in dests]
            ui_motion.flash_rows(self._activity_table, rows)
        self.refresh_waiting()

    def undo_last(self) -> int:
        """Revert the most recent sorting batch."""
        count = 0
        if sort_undo and hasattr(sort_undo, "load_history") and hasattr(sort_undo, "get_active_moves") and hasattr(sort_undo, "undo_moves"):
            try:
                records = sort_undo.load_history()
                active = sort_undo.get_active_moves(records)
                if active:
                    last_batch = active[-1].get("batch_id")
                    targets = [r for r in reversed(active) if r.get("batch_id") == last_batch]
                    count = sort_undo.undo_moves(targets, dry_run=False)
            except Exception:
                pass
        self.refresh_data()
        if count:
            where = "1 file back where it was" if count == 1 else f"{count} files back where they were"
            self._banner.show_message(f"Put {where}.", "info")
        else:
            self._banner.show_message("There was nothing to undo.", "info")
        self.refresh_waiting()
        return count

    def open_downloads(self) -> None:
        """Open downloads directory in Windows File Explorer."""
        dl_dir = ""
        if sorter and hasattr(sorter, "watch_root"):
            try:
                dl_dir = str(sorter.watch_root(self._config))
            except Exception:
                pass
        if not dl_dir:
            dl_dir = str(Path.home() / "Downloads")
        try:
            if Path(dl_dir).exists():
                subprocess.Popen(["explorer.exe", str(dl_dir)])
        except Exception:
            pass

    def toggle_pause(self) -> None:
        """Pause or resume sorting; start the background sorter if it isn't running."""
        state = self._sorter_state()
        if state == "stopped":
            self._starting_until = time.monotonic() + self.START_TIMEOUT_S
            self._start_background_sorter()
        elif sorter and hasattr(sorter, "is_paused") and hasattr(sorter, "set_paused"):
            try:
                now_paused = not sorter.is_paused()
                sorter.set_paused(now_paused)
                if now_paused:
                    self._banner.show_message(
                        "Sorting is paused. New downloads will stay in Downloads until you resume.",
                        "warning",
                    )
                else:
                    self._banner.show_message(
                        "Sorting is on again. New downloads are filed a few seconds after they finish.",
                        "success",
                    )
                    self.refresh_waiting()
            except Exception:
                pass
        self.refresh_status()

    def _start_background_sorter(self) -> None:
        if sys.platform != "win32" or not hasattr(suite_data, "_get_launch_command"):
            return
        try:
            subprocess.Popen(
                suite_data._get_launch_command(["--tray"]),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:
            pass

    def set_persona(self, persona_id: str) -> None:
        """Kept for callers; the profile now lives on the Settings page."""
        self._config["user_persona"] = persona_id

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if hasattr(self, "_refresh_timer") and not self._refresh_timer.isActive():
            self._refresh_timer.start()
        self.refresh_status()
        self.refresh_waiting()
        self.refresh_update_notice()

    def _stop_live_updates(self) -> None:
        # While the window is closing, Qt may already have deleted child widgets.
        try:
            if hasattr(self, "_refresh_timer") and self._refresh_timer.isActive():
                self._refresh_timer.stop()
            ui_motion.breathe(self._status_dot, False)
        except RuntimeError:
            pass

    def closeEvent(self, event) -> None:
        self._stop_live_updates()
        super().closeEvent(event)

    def hideEvent(self, event) -> None:
        self._stop_live_updates()
        super().hideEvent(event)


def parse_type_names(text: str) -> tuple[dict[str, str], list[str]]:
    """Read ".pdf = PDFs, stl = Prints" into {".pdf": "PDFs", ".stl": "Prints"}, and the parts it could not read."""
    names: dict[str, str] = {}
    bad: list[str] = []
    for part in re.split(r"[,;]", text or ""):
        part = part.strip()
        if not part:
            continue
        ext, sep, folder = part.partition("=")
        ext = ext.strip().lower()
        folder = folder.strip()
        if ext and not ext.startswith("."):
            ext = "." + ext
        if not sep or not re.fullmatch(r"\.[a-z0-9]{1,10}", ext) or not folder or re.search(r'[<>:"/\\|?*]', folder):
            bad.append(part)
            continue
        names[ext] = folder
    return names, bad


def format_type_names(names: dict[str, Any]) -> str:
    return ", ".join(f"{ext} = {folder}" for ext, folder in names.items() if str(folder or "").strip())


def watch_folder_problem(folder: Path) -> str:
    """Why ``folder`` should not be the folder to sort, in words for the page; '' when it is fine."""
    try:
        resolved = folder.resolve()
    except OSError:
        return "That folder could not be read."
    if not resolved.is_dir():
        return "That folder was not found."
    if resolved.parent == resolved:
        return "A whole drive can't be sorted. Pick a folder on it."
    risky = [Path(os.environ.get("USERPROFILE") or Path.home())]
    for name in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
        if os.environ.get(name):
            risky.append(Path(os.environ[name]))
    for index, place in enumerate(risky):
        try:
            place = place.resolve()
        except OSError:
            continue
        if resolved == place or (index > 0 and place in resolved.parents):
            return "That folder holds Windows, programs or your whole profile. Pick a folder made for downloads."
    return ""


class SettingsWidget(QWidget):
    """Settings page: profile, Windows integration, zip handling, and About."""

    persona_changed = Signal(str)
    projects_page_toggled = Signal(bool)
    update_ready = Signal(object)
    _update_result = Signal(object, str)

    def __init__(self, config: dict[str, Any] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = copy.deepcopy(config) if config is not None else (
            suite_data.load_sorter_config() if hasattr(suite_data, "load_sorter_config") else {}
        )
        self._checking_updates = False
        self._latest_release: dict[str, Any] | None = None
        self._update_result.connect(self._on_update_checked)
        self._init_ui()
        self.refresh_data()

    def _on_projects_page_toggled(self, checked: bool) -> None:
        self._update_config(lambda cfg: cfg.__setitem__("show_projects_page", bool(checked)))
        self.projects_page_toggled.emit(bool(checked))

    def _section(self, layout: QVBoxLayout, title: str, description: str = "") -> QVBoxLayout:
        frame, body = ui_style.card(spacing=10)
        body.addWidget(ui_style.label(title, "section"))
        if description:
            body.addWidget(ui_style.label(description, "muted", wrap=True))
        layout.addWidget(frame)
        return body

    def _init_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)
        content = QWidget()
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(16)

        header, _ = ui_style.page_header("Settings")
        layout.addWidget(header)

        watched = self._section(
            layout, "Folder to sort",
            "Download Sorter sorts new files saved in this folder. Files in the old folder stay "
            "where they are when you change it.",
        )
        self._watch_folder_lbl = ui_style.label("", wrap=True)
        self._watch_folder_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        watched.addWidget(self._watch_folder_lbl)
        self._watch_folder_note = ui_style.label("", "muted", wrap=True)
        self._watch_folder_note.setVisible(False)
        watched.addWidget(self._watch_folder_note)
        watch_row = QHBoxLayout()
        watch_row.setSpacing(8)
        self._btn_watch_change = ui_style.button("Change folder...")
        self._btn_watch_change.clicked.connect(self._on_change_watch_folder)
        watch_row.addWidget(self._btn_watch_change)
        self._btn_watch_default = ui_style.button("Use my Downloads folder", "ghost")
        self._btn_watch_default.clicked.connect(lambda: self.set_watch_folder(""))
        watch_row.addWidget(self._btn_watch_default)
        watch_row.addStretch()
        watched.addLayout(watch_row)

        profile = self._section(
            layout, "Your folders and starter rules",
            "Setup looks at the folders in your Downloads folder and keeps filing into them. "
            "You can also add a set of ready-made name rules. Your own folders, and rules you "
            "added or that were learned, are kept when you switch.",
        )
        row = QHBoxLayout()
        row.setSpacing(8)
        self._persona_combo = QComboBox()
        for pid, text in PERSONA_LABELS:
            self._persona_combo.addItem(ui_icons.icon(ui_icons.PERSONA_ICONS[pid]), text, pid)
        self._persona_combo.setMinimumWidth(240)
        self._persona_combo.currentIndexChanged.connect(self._on_persona_combo_changed)
        row.addWidget(self._persona_combo)
        row.addStretch()
        self._btn_rerun_tour = ui_style.button("Look at my folders again")
        self._btn_rerun_tour.clicked.connect(self._on_rerun_tour)
        row.addWidget(self._btn_rerun_tour)
        profile.addLayout(row)

        self._chk_projects_page = QCheckBox("Show the Projects page (for code, CAD and other project folders)")
        self._chk_projects_page.toggled.connect(self._on_projects_page_toggled)
        profile.addWidget(self._chk_projects_page)

        windows = self._section(layout, "Windows")
        self._chk_startup = QCheckBox("Start Download Sorter when I sign in")
        self._chk_startup.toggled.connect(self._on_startup_toggled)
        windows.addWidget(self._chk_startup)
        self._chk_context = QCheckBox("Add “Sort with Download Sorter” to the folder right-click menu")
        self._chk_context.toggled.connect(self._on_context_menu_toggled)
        windows.addWidget(self._chk_context)
        # Windows Storage Sense can delete old files in Downloads, sorted ones included.
        self._storage_sense_lbl = ui_style.label("", "muted", wrap=True)
        self._storage_sense_lbl.setStyleSheet(f"color: {COLORS['warning']};")
        self._storage_sense_lbl.setVisible(False)
        windows.addWidget(self._storage_sense_lbl)
        self._btn_storage_sense = ui_style.button("Open Storage Sense settings")
        self._btn_storage_sense.clicked.connect(_open_storage_sense_settings)
        self._btn_storage_sense.setVisible(False)
        windows.addWidget(self._btn_storage_sense)

        downloads = self._section(layout, "Zip files")
        self._chk_unpack = QCheckBox("Unzip downloaded .zip files into the Extracted folder")
        self._chk_unpack.toggled.connect(self._on_unpack_toggled)
        downloads.addWidget(self._chk_unpack)
        downloads.addWidget(ui_style.label("The original zip file is kept in the Archives folder.", "muted", wrap=True))

        self._option_checks: dict[tuple[str, str], QCheckBox] = {}
        folders = self._section(
            layout, "Folders",
            "Both on unless you turn them off. Nothing is ever taken out of a folder you made.",
        )
        for key, text, tip in (
            (("type_folders", "enabled"),
             "Inside each main folder, give every kind of file its own folder (Documents\\PDF, 3D Models\\STL)",
             "Turn this off to keep files directly in the main folder (Documents, 3D Models...)."),
            (("my_folders", "enabled"),
             "Put files named after one of my folders into that folder "
             "(“flanged_mace_v3.f3d” goes to 3D Models\\Flanged Mace)",
             "Make a folder in Downloads, or inside a main folder such as 3D Models, and name it "
             "after your project. New files with that name in theirs land there. Folders with "
             "general names like “New folder” or “Old” are never used for this."),
        ):
            chk = QCheckBox(text)
            chk.setToolTip(tip)
            chk.toggled.connect(lambda checked, k=key: self._on_option_toggled(k, checked))
            folders.addWidget(chk)
            self._option_checks[key] = chk

        folders.addWidget(ui_style.label("Your own names for type folders", "muted"))
        self._type_names_edit = QLineEdit()
        self._type_names_edit.setPlaceholderText(".pdf = PDFs, .stl = Prints")
        self._type_names_edit.setAccessibleName("Your own names for type folders")
        self._type_names_edit.setToolTip(
            "Write the file type, an equals sign and the folder name. Separate several with commas. "
            "Files already sorted are not moved.")
        self._type_names_edit.editingFinished.connect(self._on_type_names_edited)
        folders.addWidget(self._type_names_edit)
        self._type_names_note = ui_style.label("", "muted", wrap=True)
        self._type_names_note.setStyleSheet(f"color: {COLORS['warning']};")
        self._type_names_note.setVisible(False)
        folders.addWidget(self._type_names_note)

        folders.addWidget(ui_style.label("Folders of mine that should not collect files", "muted"))
        self._skip_folders_edit = QLineEdit()
        self._skip_folders_edit.setPlaceholderText("3D Models\\Old, Reference")
        self._skip_folders_edit.setAccessibleName("Folders of mine that should not collect files")
        self._skip_folders_edit.setToolTip(
            "Folder names inside the folder being sorted, separated by commas. Files named after "
            "these folders are sorted the usual way instead.")
        self._skip_folders_edit.editingFinished.connect(self._on_skip_folders_edited)
        folders.addWidget(self._skip_folders_edit)

        printing = self._section(layout, "3D printing", "Both off unless you turn them on.")
        self._chk_by_designer = QCheckBox(
            "Put MakerWorld models in a folder for each designer "
            "(3D Models\\MakerWorld\\Designer\\Model)")
        self._chk_by_designer.toggled.connect(
            lambda checked: self._update_config(lambda cfg: cfg.__setitem__("makerworld_by_designer", bool(checked))))
        printing.addWidget(self._chk_by_designer)
        hold_row = QHBoxLayout()
        hold_row.setSpacing(8)
        chk_hold = QCheckBox("Leave new .3mf files where they were saved for")
        chk_hold.setToolTip(
            "A held .3mf stays where it was saved, so a slicer that has it open keeps working. "
            "It is filed when the days are up, or when you click File now on its ribbon.")
        chk_hold.toggled.connect(lambda checked: self._on_option_toggled(("hold", "enabled"), checked))
        self._option_checks[("hold", "enabled")] = chk_hold
        self._hold_days_spin = self._days_spin("Days to leave a new .3mf file before filing it", 1, 60)
        self._hold_days_spin.valueChanged.connect(
            lambda value: self._update_config(lambda cfg: self._set_option(cfg, "hold", "hold_days", int(value))))
        hold_row.addWidget(chk_hold)
        hold_row.addWidget(self._hold_days_spin)
        hold_row.addWidget(ui_style.label("days before filing them"))
        hold_row.addStretch()
        printing.addLayout(hold_row)

        extras = self._section(
            layout, "Extra options",
            "All off unless you turn them on. Undo puts files back with their old names.",
        )
        for key, text in (
            (("photos", "by_month"), "Put pictures into a folder for each month (Images\\2026-11), "
                                     "by the date a photo was taken"),
            (("rename", "tidy"), "Tidy file names: “My_File (1).pdf” becomes “My File.pdf”"),
            (("rename", "add_date"), "Put the date in front of file names: “2026-11-09 Invoice.pdf”"),
            (("cleanup", "enabled"), "Move installers and zip files older than 30 days to the Recycle Bin, "
                                     "once a day"),
        ):
            chk = QCheckBox(text)
            chk.toggled.connect(lambda checked, k=key: self._on_option_toggled(k, checked))
            extras.addWidget(chk)
            self._option_checks[key] = chk

        experimental = self._section(
            layout, "Experimental",
            "New features you can turn on or off here anytime. Existing installs start with these off.",
        )
        for key, text in (
            (("photos", "split_by_kind"), "Keep camera photos and screenshots in separate folders "
                                          "(Images\\Camera, Images\\Screenshots)"),
            (("pdf_text", "enabled"), "Look for rule words at the top of PDFs, so a "
                                      "“document (3).pdf” that says Invoice still goes to Invoices"),
            (("bambu_recent", "enabled"), "Keep Bambu Studio's Recently Opened list pointing at moved .3mf files"),
        ):
            chk = QCheckBox(text)
            chk.toggled.connect(lambda checked, k=key: self._on_option_toggled(k, checked))
            experimental.addWidget(chk)
            self._option_checks[key] = chk
        # Its own line: one long checkbox made the whole page wider than the window.
        experimental.addWidget(ui_style.label(
            "This writes to BambuStudio.conf while Bambu Studio is closed, and keeps a backup.", "muted", wrap=True))

        sweep_row = QHBoxLayout()
        sweep_row.setSpacing(8)
        chk_sweep = QCheckBox("Move loose files left on the Desktop into the sorted folder after")
        chk_sweep.toggled.connect(lambda checked: self._on_option_toggled(("desktop_sweep", "enabled"), checked))
        self._option_checks[("desktop_sweep", "enabled")] = chk_sweep
        self._sweep_days_spin = self._days_spin("Days a file sits on the Desktop before it is moved", 1, 365)
        self._sweep_days_spin.valueChanged.connect(
            lambda value: self._update_config(lambda cfg: self._set_option(cfg, "desktop_sweep", "days", int(value))))
        sweep_row.addWidget(chk_sweep)
        sweep_row.addWidget(self._sweep_days_spin)
        sweep_row.addWidget(ui_style.label("days"))
        sweep_row.addStretch()
        experimental.addLayout(sweep_row)

        bambu_row = QHBoxLayout()
        bambu_row.setSpacing(8)
        self._btn_restore_bambu = ui_style.button("Restore Bambu Studio backup", "ghost")
        self._btn_restore_bambu.clicked.connect(self._on_restore_bambu)
        self._bambu_status_lbl = ui_style.label("", "muted")
        bambu_row.addWidget(self._btn_restore_bambu)
        bambu_row.addWidget(self._bambu_status_lbl)
        bambu_row.addStretch()
        experimental.addLayout(bambu_row)

        shelf_row = QHBoxLayout()
        shelf_row.setSpacing(8)
        chk_shelf = QCheckBox("Recent files shelf shortcut (takes effect next tray start)")
        chk_shelf.toggled.connect(lambda checked: self._on_option_toggled(("shelf", "enabled"), checked))
        self._option_checks[("shelf", "enabled")] = chk_shelf
        self._shelf_hotkey_combo = QComboBox()
        for spec in ("Ctrl+Shift+Space", "Ctrl+Alt+Space", "Alt+Shift+Space", "Ctrl+Shift+R"):
            self._shelf_hotkey_combo.addItem(spec, spec)
        self._shelf_hotkey_combo.currentIndexChanged.connect(self._on_shelf_hotkey_changed)
        shelf_row.addWidget(chk_shelf)
        shelf_row.addWidget(self._shelf_hotkey_combo)
        shelf_row.addStretch()
        experimental.addLayout(shelf_row)

        learn = self._section(
            layout, "Learning",
            "When you move a sorted file into another folder inside Downloads, Download Sorter "
            "notes the words in its name, its type and the website it came from. After you've "
            "moved 3 similar files into a folder, it asks whether to add a rule for them. It "
            "never moves files on its own guess, and everything stays on this PC.",
        )
        self._chk_learning = QCheckBox("Learn from how I organize files and suggest rules")
        self._chk_learning.toggled.connect(self._on_learning_toggled)
        learn.addWidget(self._chk_learning)
        learn_row = QHBoxLayout()
        learn_row.setSpacing(8)
        self._btn_learning_report = ui_style.button("See what it has learned")
        self._btn_learning_report.clicked.connect(self._show_learning_report)
        learn_row.addWidget(self._btn_learning_report)
        self._btn_learning_forget = ui_style.button("Forget everything", "ghost")
        self._btn_learning_forget.clicked.connect(self._forget_learning)
        learn_row.addWidget(self._btn_learning_forget)
        learn_row.addStretch()
        learn.addLayout(learn_row)

        info = self._section(layout, "About")
        info.addWidget(ui_style.label(f"{about.APP_NAME} {about.__version__}"))
        self._chk_updates = QCheckBox("Check for new versions once a day")
        self._chk_updates.setToolTip(
            "Asks GitHub which version is newest. Nothing about you or your files is sent.")
        self._chk_updates.toggled.connect(self._on_updates_toggled)
        info.addWidget(self._chk_updates)

        update_row = QHBoxLayout()
        update_row.setSpacing(8)
        self._btn_check_updates = ui_style.button("Check for updates")
        self._btn_check_updates.setToolTip("Ask GitHub now whether a newer version is available.")
        self._btn_check_updates.clicked.connect(self.check_for_updates_now)
        update_row.addWidget(self._btn_check_updates)

        self._btn_view_update = ui_style.button("View update on Home", "primary")
        self._btn_view_update.setToolTip("Go to the Home tab to download and install this update.")
        self._btn_view_update.setVisible(False)
        self._btn_view_update.clicked.connect(self._on_view_update_clicked)
        update_row.addWidget(self._btn_view_update)

        self._lbl_update_status = ui_style.label("", "muted")
        update_row.addWidget(self._lbl_update_status)
        update_row.addStretch()
        info.addLayout(update_row)

        info.addWidget(ui_style.label(about.CREDIT, "muted"))
        link = ui_style.label(
            f'<a href="{about.REPO_URL}" style="color: {COLORS["info"]}; text-decoration: none;">'
            f"{about.REPO_URL.replace('https://', '')}</a> · {about.LICENSE_NAME} license",
            "muted",
        )
        link.setOpenExternalLinks(True)
        info.addWidget(link)

        # The bug report opens with the version and Windows version filled in.
        report_href = html.escape(problem_report.issue_url(), quote=True)
        link_style = f'style="color: {COLORS["info"]}; text-decoration: none;"'
        report_link = ui_style.label(
            f'<a href="{report_href}" {link_style}>Report a problem</a> · '
            f'<a href="{about.REPO_URL}/issues/new/choose" {link_style}>Suggest a rule or an idea</a>',
            "muted",
        )
        report_link.setToolTip("Opens GitHub in your browser. Nothing is sent until you post it.")
        report_link.setOpenExternalLinks(True)
        info.addWidget(report_link)

        layout.addStretch()

    def refresh_data(self) -> None:
        if hasattr(suite_data, "load_sorter_config"):
            self._config = suite_data.load_sorter_config()
        for chk, getter in (
            (self._chk_startup, getattr(suite_data, "is_startup_enabled", None)),
            (self._chk_context, getattr(suite_data, "is_context_menu_enabled", None)),
        ):
            if getter is None:
                continue
            try:
                chk.blockSignals(True)
                chk.setChecked(bool(getter()))
            except Exception:
                pass
            finally:
                chk.blockSignals(False)

        self._chk_unpack.blockSignals(True)
        self._chk_unpack.setChecked(_auto_unzip_enabled(self._config))
        self._chk_unpack.blockSignals(False)
        self._chk_learning.blockSignals(True)
        self._chk_learning.setChecked(learning.is_enabled(self._config))
        self._chk_learning.blockSignals(False)
        for (block_name, key), chk in self._option_checks.items():
            block = self._config.get(block_name)
            chk.blockSignals(True)
            on_by_default = block_name in ("type_folders", "my_folders", "shelf")
            chk.setChecked(bool(block.get(key, on_by_default)) if isinstance(block, dict) else on_by_default)
            chk.blockSignals(False)
        self._refresh_watch_folder()
        self._chk_by_designer.blockSignals(True)
        self._chk_projects_page.blockSignals(True)
        self._chk_projects_page.setChecked(projects_page_shown(self._config))
        self._chk_projects_page.blockSignals(False)
        self._chk_by_designer.setChecked(bool(self._config.get("makerworld_by_designer")))
        self._chk_by_designer.blockSignals(False)
        for spin, block_name, name, default in (
            (self._hold_days_spin, "hold", "hold_days", 7),
            (self._sweep_days_spin, "desktop_sweep", "days", 7),
        ):
            block = self._config.get(block_name)
            try:
                value = int(float(block.get(name, default))) if isinstance(block, dict) else default
            except (TypeError, ValueError):
                value = default
            spin.blockSignals(True)
            spin.setValue(max(spin.minimum(), min(spin.maximum(), value)))
            spin.blockSignals(False)
        self._type_names_edit.setText(format_type_names(self._saved_type_names()))
        self._type_names_note.setVisible(False)
        self._skip_folders_edit.setText(", ".join(self._saved_skip_folders()))
        shelf_block = self._config.get("shelf")
        hotkey_spec = str(shelf_block.get("hotkey") or "Ctrl+Shift+Space") if isinstance(shelf_block, dict) else "Ctrl+Shift+Space"
        idx = self._shelf_hotkey_combo.findData(hotkey_spec)
        if idx < 0:
            self._shelf_hotkey_combo.addItem(hotkey_spec, hotkey_spec)
            idx = self._shelf_hotkey_combo.findData(hotkey_spec)
        self._shelf_hotkey_combo.blockSignals(True)
        self._shelf_hotkey_combo.setCurrentIndex(max(0, idx))
        self._shelf_hotkey_combo.blockSignals(False)
        try:
            import bambu_recent
            has_bak = bambu_recent.has_backup()
        except Exception:
            has_bak = False
        self._btn_restore_bambu.setVisible(has_bak)
        self._bambu_status_lbl.setVisible(False)
        days = _storage_sense_days()
        if days:
            self._storage_sense_lbl.setText(
                f"Windows Storage Sense is set to delete files in Downloads that haven't been opened "
                f"for {days} days. That includes the folders Download Sorter files into. To keep "
                "your sorted files, set “Delete files in my Downloads folder” to Never."
            )
        self._storage_sense_lbl.setVisible(bool(days))
        self._btn_storage_sense.setVisible(bool(days))
        block = self._config.get("update_check")
        self._chk_updates.blockSignals(True)
        self._chk_updates.setChecked(bool(block.get("enabled", True)) if isinstance(block, dict) else True)
        self._chk_updates.blockSignals(False)
        self.set_persona(self._config.get("user_persona", "custom"))

    def set_persona(self, persona_id: str) -> None:
        idx = self._persona_combo.findData(persona_id)
        if idx >= 0:
            self._persona_combo.blockSignals(True)
            self._persona_combo.setCurrentIndex(idx)
            self._persona_combo.blockSignals(False)

    def _on_startup_toggled(self, checked: bool) -> None:
        if hasattr(suite_data, "set_startup_enabled"):
            try:
                suite_data.set_startup_enabled(checked)
            except Exception:
                pass

    def _on_context_menu_toggled(self, checked: bool) -> None:
        if hasattr(suite_data, "set_context_menu_enabled"):
            try:
                suite_data.set_context_menu_enabled(checked)
            except Exception:
                pass

    def _update_config(self, change) -> None:
        """Change one setting in the settings as they are now, and save.

        Starting from the file (not this page's earlier copy) keeps changes
        made meanwhile elsewhere, such as a rule added from a learning ribbon.
        ``change`` edits the dict in place or returns a new one.
        """
        current = self._config
        if hasattr(suite_data, "current_sorter_config"):
            current = suite_data.current_sorter_config(self._config)
        current = copy.deepcopy(current)
        self._config = change(current) or current
        if hasattr(suite_data, "save_sorter_config"):
            try:
                suite_data.save_sorter_config(self._config)
            except Exception:
                pass

    @staticmethod
    def _set_option(config: dict[str, Any], block_name: str, name: str, value: Any) -> None:
        block = config.get(block_name)
        if not isinstance(block, dict):
            block = {}
            config[block_name] = block
        block[name] = value

    def _on_option_toggled(self, key: tuple[str, str], checked: bool) -> None:
        block_name, name = key
        self._update_config(lambda cfg: self._set_option(cfg, block_name, name, bool(checked)))

    @staticmethod
    def _days_spin(name: str, low: int, high: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(low, high)
        spin.setAccessibleName(name)
        spin.setFixedWidth(64)
        return spin

    # ---- the folder to sort ----
    def _refresh_watch_folder(self) -> None:
        import sorter
        folder = sorter.watch_root(self._config)
        is_default = sorter.same_folder(folder, sorter.watch_root({}))
        self._watch_folder_lbl.setText(str(folder))
        self._btn_watch_default.setVisible(not is_default)
        missing = not folder.exists()
        self._watch_folder_note.setStyleSheet(f"color: {COLORS['warning']};" if missing else "")
        if missing:
            self._watch_folder_note.setText(
                "This folder was not found. Nothing is sorted until it is back, for example when "
                "its drive is plugged in.")
        self._watch_folder_note.setVisible(missing)

    def _on_change_watch_folder(self) -> None:
        import sorter
        start = sorter.watch_root(self._config)
        picked = QFileDialog.getExistingDirectory(self, "Folder to sort", str(start if start.exists() else Path.home()))
        if picked:
            self.set_watch_folder(picked)

    def set_watch_folder(self, folder: str) -> bool:
        """Sort ``folder`` from now on ('' goes back to the Downloads folder). False when it is refused."""
        import sorter
        folder = str(folder or "").strip()
        if folder:
            problem = watch_folder_problem(Path(folder))
            if problem:
                self._watch_folder_note.setStyleSheet(f"color: {COLORS['warning']};")
                self._watch_folder_note.setText(problem)
                self._watch_folder_note.setVisible(True)
                return False
            folder = str(Path(folder))
            if sorter.same_folder(Path(folder), sorter.watch_root({})):
                folder = ""  # the default follows Windows when Downloads is moved

        def change(cfg: dict[str, Any]) -> None:
            self._set_option(cfg, "watch", "folder", folder)
            cfg.pop("downloads_dir", None)  # the older key; watch.folder is the one read first

        self._update_config(change)
        self._refresh_watch_folder()
        return True

    # ---- folder names and folders left out ----
    def _saved_type_names(self) -> dict[str, Any]:
        block = self._config.get("type_folders")
        names = block.get("names") if isinstance(block, dict) else None
        return names if isinstance(names, dict) else {}

    def _saved_skip_folders(self) -> list[str]:
        block = self._config.get("my_folders")
        skip = block.get("skip") if isinstance(block, dict) else None
        return [str(item) for item in skip] if isinstance(skip, list) else []

    def _on_type_names_edited(self) -> None:
        names, bad = parse_type_names(self._type_names_edit.text())
        if bad:
            self._type_names_note.setText(
                "Not saved. Write each one as “.pdf = PDFs”. Could not read: " + ", ".join(bad))
            self._type_names_note.setVisible(True)
            return
        self._type_names_note.setVisible(False)
        if names != self._saved_type_names():
            self._update_config(lambda cfg: self._set_option(cfg, "type_folders", "names", names))
        self._type_names_edit.setText(format_type_names(names))

    def _on_skip_folders_edited(self) -> None:
        parts = [part.strip().strip("\\/") for part in re.split(r"[,;]", self._skip_folders_edit.text())]
        skip = [part for part in parts if part]
        if skip != self._saved_skip_folders():
            self._update_config(lambda cfg: self._set_option(cfg, "my_folders", "skip", skip))

    def _on_shelf_hotkey_changed(self, index: int) -> None:
        spec = self._shelf_hotkey_combo.itemData(index)
        if spec:
            self._update_config(lambda cfg: self._set_option(cfg, "shelf", "hotkey", str(spec)))

    def _on_restore_bambu(self) -> None:
        import bambu_recent
        ok = bambu_recent.restore_backup()
        self._bambu_status_lbl.setText(
            "Restored BambuStudio.conf from backup." if ok
            else "Close Bambu Studio first to restore its settings."
        )
        self._bambu_status_lbl.setVisible(True)

    def _on_unpack_toggled(self, checked: bool) -> None:
        self._update_config(lambda cfg: _set_auto_unzip(cfg, checked))

    def _on_updates_toggled(self, checked: bool) -> None:
        self._update_config(lambda cfg: self._set_option(cfg, "update_check", "enabled", bool(checked)))

    def check_for_updates_now(self) -> None:
        """Manually query GitHub for a newer version immediately."""
        if self._checking_updates:
            return
        self._checking_updates = True
        self._btn_check_updates.setEnabled(False)
        self._btn_check_updates.setText("Checking...")
        self._btn_view_update.setVisible(False)
        self._lbl_update_status.setText("Checking GitHub for updates...")
        self._lbl_update_status.setStyleSheet(f"color: {COLORS['textMuted']};")
        config = copy.deepcopy(self._config)

        def work() -> None:
            rel, err = None, ""
            try:
                rel = update_check.check(config, force=True)
            except Exception as exc:
                err = str(exc) or "Check failed."
            try:
                self._update_result.emit(rel, err)
            except RuntimeError:
                pass

        threading.Thread(target=work, name="settings-update-check", daemon=True).start()

    def _on_update_checked(self, release: Any, error: str) -> None:
        self._checking_updates = False
        self._btn_check_updates.setEnabled(True)
        self._btn_check_updates.setText("Check for updates")
        self._latest_release = dict(release) if release else None
        if release:
            version = update_check.display_version(release)
            self._lbl_update_status.setText(f"Version {version} is available!")
            self._lbl_update_status.setStyleSheet(f"color: {COLORS['success']};")
            if updater.can_self_update():
                self._btn_view_update.setText("View update on Home")
                self._btn_view_update.setToolTip("Go to the Home tab to download and install this update.")
            else:
                self._btn_view_update.setText("Open download page")
                self._btn_view_update.setToolTip("Open GitHub releases in your web browser.")
            self._btn_view_update.setVisible(True)
            self.update_ready.emit(release)
            parent_dialog = self.window()
            if hasattr(parent_dialog, "_home_widget") and hasattr(parent_dialog._home_widget, "_on_update_ready"):
                parent_dialog._home_widget._on_update_ready(release)
        elif error:
            self._lbl_update_status.setText("Could not reach GitHub. Try again later.")
            self._lbl_update_status.setStyleSheet(f"color: {COLORS['critical']};")
            self._btn_view_update.setVisible(False)
        else:
            self._lbl_update_status.setText(f"You're up to date (version {about.__version__}).")
            self._lbl_update_status.setStyleSheet(f"color: {COLORS['textMuted']};")
            self._btn_view_update.setVisible(False)

    def _on_view_update_clicked(self) -> None:
        if not self._latest_release:
            return
        if updater.can_self_update():
            parent_dialog = self.window()
            if hasattr(parent_dialog, "switch_view"):
                parent_dialog.switch_view("home")
                return
        url = str(self._latest_release.get("url") or update_check.RELEASES_PAGE)
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl(url))

    def _on_learning_toggled(self, checked: bool) -> None:
        def change(cfg: dict[str, Any]) -> None:
            self._set_option(cfg, "learning", "enabled", bool(checked))
            cfg["learning"].setdefault("suggest", True)  # the user has chosen; don't switch it on again
        self._update_config(change)

    def learning_report_text(self) -> str:
        rep = learning.report()
        if not rep["your_moves"]:
            return (
                "Nothing yet. Move a few sorted files into the folders you'd rather keep them in "
                "(inside Downloads), and it will start learning from those moves."
            )
        lines = [f"Files you've moved: {rep['your_moves']}"]
        if rep.get("rules_added"):
            noun = "rule" if rep["rules_added"] == 1 else "rules"
            lines.append(f"Rules you've added from its suggestions: {rep['rules_added']} {noun}")
        if rep["judged"]:
            lines.append(
                f"Its first guess was right {rep['first_guess_right']} of {rep['judged']} times "
                f"({round(100 * rep['first_guess_rate'])}%), and the right folder was in its top three "
                f"{rep['in_top_three']} times ({round(100 * rep['top_three_rate'])}%)."
            )
        else:
            lines.append("It needs a few more of your moves before its guesses can be checked.")
        if rep["folders"]:
            lines.append("")
            lines.append("Folders it has learned about:")
            for f in rep["folders"][:8]:
                noun = "example" if f["examples"] == 1 else "examples"
                lines.append(f"  {f['folder']}: {f['examples']} {noun}")
        if rep["sites"]:
            lines.append("")
            lines.append("Websites it has seen: " + ", ".join(rep["sites"][:6]))
        return "\n".join(lines)

    def _show_learning_report(self) -> None:
        QMessageBox.information(self, "What it has learned", self.learning_report_text())

    def _forget_learning(self) -> None:
        reply = QMessageBox.question(
            self,
            "Forget everything",
            "Delete everything Download Sorter has learned from your moves, and its practice log?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            learning.forget()

    def _on_persona_combo_changed(self, index: int) -> None:
        persona_id = str(self._persona_combo.itemData(index))

        def change(cfg: dict[str, Any]) -> dict[str, Any]:
            if hasattr(suite_data, "apply_persona_profile"):
                try:
                    return suite_data.apply_persona_profile(cfg, persona_id)
                except Exception:
                    pass
            cfg["user_persona"] = persona_id
            return cfg

        self._update_config(change)
        self.persona_changed.emit(persona_id)

    def _on_rerun_tour(self) -> None:
        wizard = WelcomeWizardDialog(config=self._config, parent=self.window())
        if wizard.exec() == QDialog.DialogCode.Accepted:
            self.refresh_data()
            self.persona_changed.emit(self._config.get("user_persona", "everyday_home"))
