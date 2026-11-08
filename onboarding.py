"""First-run onboarding wizard and consumer Home Dashboard GUI for Download Sorter.

Provides:
- PersonaCard: Clickable selection card for persona profiles.
- WelcomeWizardDialog: 3-step first-run wizard (persona, preview, system setup).
- HomeDashboardWidget: Everyday main view (status hero, recent activity, quick settings).
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import about
from suite_theme import ACCENTS, COLORS, RADII, TYPE

try:
    import config_manager as suite_data
except ImportError:
    import suite_data

try:
    import sorter
except ImportError:
    sorter = None

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
# 1. PersonaCard Widget
# ============================================================================

class PersonaCard(QFrame):
    """Clickable selection card for user workflow personas."""

    clicked = Signal(str)

    def __init__(
        self,
        persona_id: str,
        icon: str,
        name: str,
        description: str,
        badge: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.persona_id = persona_id
        self._selected = False
        self._badge_lbl: QLabel | None = None
        self._rec_badge_lbl: QLabel | None = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._init_ui(icon, name, description, badge)

    def _init_ui(self, icon: str, name: str, description: str, badge: str) -> None:
        self.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(12)

        icon_lbl = QLabel(icon)
        icon_lbl.setFont(QFont(TYPE["ui"], 20))
        icon_lbl.setFixedWidth(38)
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon_lbl)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(3)

        self._title_row = QHBoxLayout()
        self._title_row.setSpacing(8)

        self._title_lbl = QLabel(name)
        self._title_lbl.setFont(QFont(TYPE["ui"], 11, QFont.Weight.Bold))
        self._title_row.addWidget(self._title_lbl)

        if badge:
            self._badge_lbl = QLabel(f" {badge} ")
            self._badge_lbl.setFont(QFont(TYPE["ui"], 8, QFont.Weight.Medium))
            self._badge_lbl.setStyleSheet(f"""
                background: {COLORS['surface']};
                color: {COLORS['textMuted']};
                border: 1px solid {COLORS['border']};
                border-radius: 4px;
            """)
            self._title_row.addWidget(self._badge_lbl)

        self._title_row.addStretch()
        text_layout.addLayout(self._title_row)

        self._desc_lbl = QLabel(description)
        self._desc_lbl.setFont(QFont(TYPE["ui"], 10))
        self._desc_lbl.setStyleSheet(f"color: {COLORS['textMuted']};")
        self._desc_lbl.setWordWrap(True)
        text_layout.addWidget(self._desc_lbl)

        layout.addLayout(text_layout, 1)

        self._indicator = QLabel("○")
        self._indicator.setFont(QFont(TYPE["ui"], 14))
        self._indicator.setStyleSheet(f"color: {COLORS['textMuted']};")
        layout.addWidget(self._indicator)

        self.update_style()

    def set_recommended(self, is_rec: bool, badge_text: str = "✨ Recommended for your PC") -> None:
        """Display or hide the smart recommendation badge on this card."""
        if is_rec:
            if self._rec_badge_lbl is None:
                self._rec_badge_lbl = QLabel(f" {badge_text} ")
                self._rec_badge_lbl.setFont(QFont(TYPE["ui"], 8, QFont.Weight.Bold))
                self._rec_badge_lbl.setStyleSheet(f"""
                    background: {COLORS['successSubtle']};
                    color: {ACCENTS['downloadSorter']};
                    border: 1px solid {COLORS['successBorder']};
                    border-radius: 4px;
                    padding: 1px 5px;
                """)
                # Insert right after _title_lbl (index 1)
                self._title_row.insertWidget(1, self._rec_badge_lbl)
            else:
                self._rec_badge_lbl.setText(f" {badge_text} ")
                self._rec_badge_lbl.setVisible(True)
        else:
            if self._rec_badge_lbl is not None:
                self._rec_badge_lbl.setVisible(False)

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._indicator.setText("●" if selected else "○")
        self._indicator.setStyleSheet(
            f"color: {ACCENTS['downloadSorter']};" if selected else f"color: {COLORS['textMuted']};"
        )
        self.update_style()

    def update_style(self) -> None:
        if self._selected:
            self.setStyleSheet(f"""
                PersonaCard {{
                    background-color: {COLORS['surfaceRaised']};
                    border: 2px solid {ACCENTS['downloadSorter']};
                    border-radius: {RADII['card']}px;
                }}
            """)
            self._title_lbl.setStyleSheet(f"color: {ACCENTS['downloadSorter']};")
        else:
            self.setStyleSheet(f"""
                PersonaCard {{
                    background-color: {COLORS['surface']};
                    border: 1px solid {COLORS['border']};
                    border-radius: {RADII['card']}px;
                }}
                PersonaCard:hover {{
                    background-color: {COLORS['surfaceHover']};
                    border-color: {COLORS['borderStrong']};
                }}
            """)
            self._title_lbl.setStyleSheet(f"color: {COLORS['text']};")

    def mousePressEvent(self, event) -> None:
        self.clicked.emit(self.persona_id)
        super().mousePressEvent(event)


# ============================================================================
# 2. WelcomeWizardDialog (3-Step Guided Setup)
# ============================================================================

class WelcomeWizardDialog(QDialog):
    """3-step first-run wizard guiding persona selection, feature tour, and system setup."""

    def __init__(self, config: dict[str, Any] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Welcome to {about.APP_NAME}")
        self.setMinimumSize(720, 640)
        self.resize(760, 680)

        self._config = copy.deepcopy(config) if config is not None else (
            suite_data.load_sorter_config() if hasattr(suite_data, "load_sorter_config") else {}
        )
        self._current_step = 0
        self._persona_cards: dict[str, PersonaCard] = {}

        # 1. Automatic smart profile recommendation via local file scan
        if hasattr(suite_data, "recommend_persona_profile"):
            try:
                rec = suite_data.recommend_persona_profile()
            except Exception:
                rec = {
                    "recommended_id": "everyday_home",
                    "reason": "Everyday & Home PC is recommended to keep your photos, music, documents, and downloads tidy!",
                    "scores": {},
                    "files_scanned": 0,
                }
        else:
            rec = {
                "recommended_id": "everyday_home",
                "reason": "Everyday & Home PC is recommended to keep your photos, music, documents, and downloads tidy!",
                "scores": {},
                "files_scanned": 0,
            }

        self._recommended_persona_id: str = rec.get("recommended_id", "everyday_home")
        self._recommendation_reason: str = rec.get("reason", "Everyday & Home PC is recommended for your PC.")

        # Default persona selection: pre-select recommended or preserve existing persona in config
        if "user_persona" in self._config:
            self._selected_persona = self._config["user_persona"]
        else:
            self._selected_persona = self._recommended_persona_id

        self._init_ui()

    def _init_ui(self) -> None:
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {COLORS['canvas']};
                color: {COLORS['text']};
            }}
            QLabel {{
                color: {COLORS['text']};
                font-family: '{TYPE['ui']}';
            }}
            QCheckBox {{
                color: {COLORS['text']};
                font-family: '{TYPE['ui']}';
                font-size: 11px;
                spacing: 8px;
            }}
            QCheckBox::indicator {{
                width: 16px;
                height: 16px;
                border-radius: {RADII['small']}px;
                border: 1px solid {COLORS['border']};
                background: {COLORS['surface']};
            }}
            QCheckBox::indicator:checked {{
                background: {ACCENTS['downloadSorter']};
                border-color: {ACCENTS['downloadSorter']};
            }}
            QPushButton {{
                background: {COLORS['surfaceRaised']};
                border: 1px solid {COLORS['border']};
                border-radius: {RADII['small']}px;
                color: {COLORS['text']};
                padding: 7px 16px;
                font-family: '{TYPE['ui']}';
                font-size: 11px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background: {COLORS['surfaceHover']};
                border-color: {ACCENTS['downloadSorter']};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(14)

        # Header Bar with Step Indicators
        header_layout = QHBoxLayout()
        header_title = QLabel(f"✨ {about.APP_NAME}")
        header_title.setFont(QFont(TYPE["ui"], 13, QFont.Weight.Bold))
        header_title.setStyleSheet(f"color: {ACCENTS['downloadSorter']};")
        header_layout.addWidget(header_title)

        header_layout.addStretch()

        self._step_pill_1 = QLabel("1. Profile")
        self._step_pill_2 = QLabel("2. How It Works")
        self._step_pill_3 = QLabel("3. Setup")
        for pill in (self._step_pill_1, self._step_pill_2, self._step_pill_3):
            pill.setFont(QFont(TYPE["ui"], 9, QFont.Weight.DemiBold))
            pill.setStyleSheet(f"color: {COLORS['textMuted']}; padding: 3px 8px; border-radius: 9px;")
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

        mascot_title = QLabel("Sorty · Folder Companion")
        mascot_title.setFont(QFont(TYPE["ui"], 9, QFont.Weight.Bold))
        mascot_title.setStyleSheet(f"color: {ACCENTS['downloadSorter']};")
        bubble_col.addWidget(mascot_title)

        self._mascot_speech_lbl = QLabel(f"Hi! I'm Sorty! {self._recommendation_reason}")
        self._mascot_speech_lbl.setFont(QFont(TYPE["ui"], 10))
        self._mascot_speech_lbl.setStyleSheet(f"color: {COLORS['text']};")
        self._mascot_speech_lbl.setWordWrap(True)
        bubble_col.addWidget(self._mascot_speech_lbl)

        mascot_row.addLayout(bubble_col, 1)
        layout.addWidget(self._mascot_container)

        # Main Stacked Pages
        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._build_step1_persona())
        self._stack.addWidget(self._build_step2_preview())
        self._stack.addWidget(self._build_step3_preferences())
        layout.addWidget(self._stack, 1)

        # Footer Navigation
        footer = QHBoxLayout()
        self._btn_back = QPushButton("← Back")
        self._btn_back.clicked.connect(self._on_back_clicked)
        self._btn_back.setEnabled(False)
        self._btn_back.setVisible(False)
        footer.addWidget(self._btn_back)

        footer.addStretch()

        self._credit_lbl = QLabel(f"{about.APP_NAME} {about.__version__} · {about.CREDIT}")
        self._credit_lbl.setObjectName("creditFooter")
        self._credit_lbl.setStyleSheet(f"color: {COLORS['textFaint']}; font-size: 10px;")
        footer.addWidget(self._credit_lbl)

        footer.addStretch()

        self._btn_finish = QPushButton("Get Started ✓")
        self._btn_finish.setStyleSheet(f"""
            QPushButton {{
                background: {ACCENTS['downloadSorter']};
                color: #0B0D12;
                border: 1px solid {ACCENTS['downloadSorter']};
                font-weight: bold;
                padding: 7px 20px;
            }}
            QPushButton:hover {{
                background: #82ecc0;
            }}
        """)
        self._btn_finish.clicked.connect(self._on_finish_clicked)
        self._btn_finish.setVisible(False)
        footer.addWidget(self._btn_finish)

        self._btn_next = QPushButton("Next →")
        self._btn_next.setStyleSheet(f"""
            QPushButton {{
                background: {ACCENTS['downloadSorter']};
                color: #0B0D12;
                border: 1px solid {ACCENTS['downloadSorter']};
                font-weight: bold;
                padding: 7px 20px;
            }}
            QPushButton:hover {{
                background: #82ecc0;
            }}
        """)
        self._btn_next.clicked.connect(self._on_next_clicked)
        footer.addWidget(self._btn_next)

        layout.addLayout(footer)

        # Initial selection and navigation sync
        self._on_persona_selected(self._selected_persona)
        self._update_navigation()

    def _build_step1_persona(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(10)

        title = QLabel("How do you use your PC?")
        title.setFont(QFont(TYPE["ui"], 14, QFont.Weight.Bold))
        layout.addWidget(title)

        sub = QLabel("Select your workflow profile to automatically configure the best sorting rules. You can change this anytime.")
        sub.setStyleSheet(f"color: {COLORS['textMuted']}; font-size: 11px;")
        sub.setWordWrap(True)
        layout.addWidget(sub)

        profiles: list[tuple[str, str, str, str]] = []
        if hasattr(suite_data, "get_persona_profiles"):
            try:
                for p in suite_data.get_persona_profiles():
                    profiles.append((p.get("id", ""), p.get("icon", "📁"), p.get("name", ""), p.get("description", "")))
            except Exception:
                profiles = []

        if not profiles:
            profiles = [
                ("everyday_home", "🏠", "Everyday & Home PC", "Keep Downloads tidy by automatically filing PDFs, photos, videos, music, installers, and zip archives into clear folders."),
                ("academic_student", "🎓", "Student & Schoolwork", "Organize syllabi, homework assignments, lecture notes, study guides, and research papers alongside everyday downloads."),
                ("office_admin", "💼", "Office, Invoices & Work", "Automatically sort invoices, receipts, tax forms, spreadsheets, resumes, and travel documents."),
                ("maker_3d", "🛠️", "3D Printing, CAD & CNC Maker", "Full engineering suite: MakerWorld 3MF grace-period holds, Bambu/Orca slicer awareness, CAD assemblies, and Haas CNC toolpaths."),
            ]

        for pid, icon, name, desc in profiles:
            card = PersonaCard(pid, icon, name, desc, parent=page)
            if pid == self._recommended_persona_id:
                card.set_recommended(True)
            card.clicked.connect(self._on_persona_selected)
            self._persona_cards[pid] = card
            layout.addWidget(card)

        layout.addStretch()
        return page

    def _build_step2_preview(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(12)

        title = QLabel("How Download Sorter Works")
        title.setFont(QFont(TYPE["ui"], 14, QFont.Weight.Bold))
        layout.addWidget(title)

        sub = QLabel("Silent, automated organization with complete user control and zero cloud telemetry.")
        sub.setStyleSheet(f"color: {COLORS['textMuted']}; font-size: 11px;")
        layout.addWidget(sub)

        # Before & After Comparison Card
        comp_card = QFrame()
        comp_card.setObjectName("compCard")
        comp_card.setStyleSheet(f"QFrame#compCard {{ background: {COLORS['surface']}; border: 1px solid {COLORS['border']}; border-radius: {RADII['card']}px; }}")
        comp_layout = QHBoxLayout(comp_card)
        comp_layout.setContentsMargins(16, 14, 16, 14)
        comp_layout.setSpacing(14)

        # Left (Messy)
        left_box = QVBoxLayout()
        left_title = QLabel("📁 Messy Downloads\\")
        left_title.setFont(QFont(TYPE["ui"], 11, QFont.Weight.Bold))
        left_title.setStyleSheet(f"color: {COLORS['warning']};")
        left_box.addWidget(left_title)
        for item in ("📄 invoice_march.pdf", "📦 setup_v2.exe", "🖼️ vacation_photo.jpg", "📝 homework_draft.docx", "🗜️ model_parts.zip"):
            lbl = QLabel(f"  {item}")
            lbl.setFont(QFont(TYPE["mono"], 10))
            lbl.setStyleSheet(f"color: {COLORS['textMuted']};")
            left_box.addWidget(lbl)
        comp_layout.addLayout(left_box, 1)

        arrow_lbl = QLabel("➔")
        arrow_lbl.setFont(QFont(TYPE["ui"], 20, QFont.Weight.Bold))
        arrow_lbl.setStyleSheet(f"color: {ACCENTS['downloadSorter']};")
        comp_layout.addWidget(arrow_lbl)

        # Right (Organized)
        right_box = QVBoxLayout()
        right_title = QLabel("📁 Organized Folders\\")
        right_title.setFont(QFont(TYPE["ui"], 11, QFont.Weight.Bold))
        right_title.setStyleSheet(f"color: {ACCENTS['downloadSorter']};")
        right_box.addWidget(right_title)
        for item in ("📁 Documents\\ (invoice, homework)", "📁 Installers\\ (setup_v2.exe)", "📁 Images\\ (vacation_photo.jpg)", "📁 Archives\\ (model_parts.zip)"):
            lbl = QLabel(f"  {item}")
            lbl.setFont(QFont(TYPE["mono"], 10))
            lbl.setStyleSheet(f"color: {COLORS['text']};")
            right_box.addWidget(lbl)
        comp_layout.addLayout(right_box, 1)

        layout.addWidget(comp_card)

        # 3 Value Propositions
        props_layout = QHBoxLayout()
        props_layout.setSpacing(10)
        props = [
            ("🔒 100% Offline & Private", "Runs entirely on your PC. No files or metadata ever leave your machine."),
            ("↺ 1-Click Instant Undo", "Every move is safely logged. Click 'Undo Last Sort' anytime to revert."),
            ("🛡️ Zero Overwrites", "Files with duplicate names receive safe numeric suffixes."),
        ]
        for p_title, p_desc in props:
            p_card = QFrame()
            p_card.setObjectName("propCard")
            p_card.setStyleSheet(f"QFrame#propCard {{ background: {COLORS['surface']}; border: 1px solid {COLORS['border']}; border-radius: {RADII['small']}px; }}")
            p_layout = QVBoxLayout(p_card)
            p_layout.setContentsMargins(12, 10, 12, 10)
            t_lbl = QLabel(p_title)
            t_lbl.setFont(QFont(TYPE["ui"], 10, QFont.Weight.Bold))
            p_layout.addWidget(t_lbl)
            d_lbl = QLabel(p_desc)
            d_lbl.setFont(QFont(TYPE["ui"], 9))
            d_lbl.setStyleSheet(f"color: {COLORS['textMuted']};")
            d_lbl.setWordWrap(True)
            p_layout.addWidget(d_lbl)
            props_layout.addWidget(p_card, 1)
        layout.addLayout(props_layout)

        layout.addStretch()
        return page

    def _build_step3_preferences(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(14)

        title = QLabel("Set It & Forget It")
        title.setFont(QFont(TYPE["ui"], 14, QFont.Weight.Bold))
        layout.addWidget(title)

        sub = QLabel("Configure how Download Sorter runs on your PC. All settings can be adjusted later.")
        sub.setStyleSheet(f"color: {COLORS['textMuted']}; font-size: 11px;")
        layout.addWidget(sub)

        pref_card = QFrame()
        pref_card.setObjectName("prefCard")
        pref_card.setStyleSheet(f"QFrame#prefCard {{ background: {COLORS['surface']}; border: 1px solid {COLORS['border']}; border-radius: {RADII['card']}px; }}")
        pref_layout = QVBoxLayout(pref_card)
        pref_layout.setContentsMargins(18, 16, 18, 16)
        pref_layout.setSpacing(10)

        self._chk_startup = QCheckBox("Start automatically when I sign in to Windows (Recommended)")
        self._chk_startup.setChecked(True)
        pref_layout.addWidget(self._chk_startup)
        sub_startup = QLabel("    Keeps your Downloads folder clean in the background without needing to start the app manually.")
        sub_startup.setStyleSheet(f"color: {COLORS['textMuted']}; font-size: 10px;")
        pref_layout.addWidget(sub_startup)

        self._chk_context = QCheckBox("Add 'Sort with DownloadSorter' to Windows Explorer right-click menu")
        self._chk_context.setChecked(True)
        pref_layout.addWidget(self._chk_context)
        sub_context = QLabel("    Allows sorting any folder immediately by right-clicking it in Windows Explorer.")
        sub_context.setStyleSheet(f"color: {COLORS['textMuted']}; font-size: 10px;")
        pref_layout.addWidget(sub_context)

        self._chk_sort_now = QCheckBox("Organize existing files in my Downloads folder right now")
        self._chk_sort_now.setChecked(True)
        pref_layout.addWidget(self._chk_sort_now)
        sub_sort = QLabel("    Runs an initial sort on files currently sitting in your Downloads folder.")
        sub_sort.setStyleSheet(f"color: {COLORS['textMuted']}; font-size: 10px;")
        pref_layout.addWidget(sub_sort)

        layout.addWidget(pref_card)
        layout.addStretch()
        return page

    def _on_persona_selected(self, persona_id: str) -> None:
        self._selected_persona = persona_id
        for pid, card in self._persona_cards.items():
            card.set_selected(pid == persona_id)
        self._btn_next.setEnabled(True)

    def _update_navigation(self) -> None:
        idx = self._current_step
        self._btn_back.setEnabled(idx > 0)
        self._btn_back.setVisible(idx > 0)

        # Update mascot pose and speech text across wizard steps
        if hasattr(self, "_mascot_widget"):
            self._mascot_widget.set_step(idx)

        if hasattr(self, "_mascot_speech_lbl"):
            if idx == 0:
                self._mascot_speech_lbl.setText(f"Hi! I'm Sorty! {self._recommendation_reason}")
            elif idx == 1:
                self._mascot_speech_lbl.setText(
                    "Rest easy! Download Sorter is 100% offline & private. "
                    "Nothing ever leaves your PC, and every sort has 1-Click Instant Undo!"
                )
            elif idx == 2:
                self._mascot_speech_lbl.setText(
                    "Almost done! Choose how you'd like Download Sorter to run, "
                    "then click 'Get Started' to enjoy a clutter-free PC!"
                )

        if idx == 2:
            self._btn_next.setText("Get Started ✓")
            self._btn_next.setVisible(False)
            self._btn_finish.setVisible(True)
        else:
            self._btn_next.setText("Next →")
            self._btn_next.setVisible(True)
            self._btn_finish.setVisible(False)

        pills = [self._step_pill_1, self._step_pill_2, self._step_pill_3]
        for i, pill in enumerate(pills):
            if i == idx:
                pill.setStyleSheet(f"background: {ACCENTS['downloadSorter']}; color: #0B0D12; font-weight: bold; padding: 3px 10px; border-radius: 9px;")
            else:
                pill.setStyleSheet(f"background: {COLORS['surfaceRaised']}; color: {COLORS['textMuted']}; padding: 3px 8px; border-radius: 9px;")

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
        self.accept()

    def accept(self) -> None:
        """Apply persona profile, persist preferences and onboarding completion, then close dialog."""
        try:
            if hasattr(suite_data, "apply_persona_profile"):
                self._config = suite_data.apply_persona_profile(self._config, self._selected_persona)
            else:
                self._config["user_persona"] = self._selected_persona
                self._config["onboarding_completed"] = True

            self._config["onboarding_completed"] = True

            if hasattr(suite_data, "save_sorter_config"):
                suite_data.save_sorter_config(self._config)

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

            if hasattr(self, "_chk_sort_now") and self._chk_sort_now.isChecked():
                if sorter and hasattr(sorter, "sort_existing"):
                    try:
                        sorter.sort_existing(self._config, dry_run=False)
                    except Exception:
                        pass
        except Exception:
            pass

        super().accept()


# ============================================================================
# 3. HomeDashboardWidget (Everyday View)
# ============================================================================

class HomeDashboardWidget(QWidget):
    """Consumer-friendly Home & Activity Dashboard embedded in RulesManagerDialog."""

    persona_changed = Signal(str)

    def __init__(self, config: dict[str, Any] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = copy.deepcopy(config) if config is not None else (
            suite_data.load_sorter_config() if hasattr(suite_data, "load_sorter_config") else {}
        )

        self._init_ui()

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(5000)
        self._refresh_timer.timeout.connect(self.refresh_status)
        self._refresh_timer.start()

        self.refresh_data()

    def _init_ui(self) -> None:
        self.setStyleSheet(f"""
            HomeDashboardWidget {{
                background-color: {COLORS['canvas']};
            }}
            QLabel {{
                color: {COLORS['text']};
                font-family: '{TYPE['ui']}';
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
            QTableWidget {{
                background-color: {COLORS['surface']};
                border: 1px solid {COLORS['border']};
                border-radius: {RADII['small']}px;
                gridline-color: transparent;
                color: {COLORS['text']};
                font-family: '{TYPE['ui']}';
                font-size: 11px;
            }}
            QHeaderView::section {{
                background-color: {COLORS['surfaceRaised']};
                color: {COLORS['textMuted']};
                padding: 6px;
                border: none;
                border-bottom: 1px solid {COLORS['border']};
                font-weight: 600;
            }}
            QCheckBox {{
                color: {COLORS['text']};
                font-family: '{TYPE['ui']}';
                font-size: 11px;
                spacing: 6px;
            }}
            QComboBox {{
                background: {COLORS['canvas']};
                border: 1px solid {COLORS['border']};
                border-radius: {RADII['small']}px;
                color: {COLORS['text']};
                padding: 4px 8px;
                font-size: 11px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(12)

        # ---------------------------------------------------------
        # 1. Hero Card: Status & Quick Actions
        # ---------------------------------------------------------
        self._status_card = QFrame()
        self._status_card.setObjectName("statusCard")
        self._hero_card = self._status_card  # Test compatibility alias
        self._status_card.setStyleSheet(f"""
            QFrame#statusCard {{
                background-color: {COLORS['surface']};
                border: 1px solid {COLORS['border']};
                border-radius: {RADII['card']}px;
            }}
        """)
        hero_layout = QVBoxLayout(self._status_card)
        hero_layout.setContentsMargins(14, 12, 14, 12)
        hero_layout.setSpacing(12)

        # Top Status Line
        status_row = QHBoxLayout()
        self._status_badge = QLabel("● Active — Watching Downloads")
        self._status_badge.setFont(QFont(TYPE["ui"], 12, QFont.Weight.Bold))
        self._status_badge.setStyleSheet(f"color: {COLORS['success']};")
        status_row.addWidget(self._status_badge)

        status_row.addStretch()

        self._files_today_lbl = QLabel("📊 0 files organized today")
        self._metric_lbl = self._files_today_lbl  # Test compatibility alias
        self._files_today_lbl.setFont(QFont(TYPE["ui"], 9, QFont.Weight.DemiBold))
        self._files_today_lbl.setStyleSheet(f"""
            background: {COLORS['surfaceRaised']};
            color: {COLORS['text']};
            border: 1px solid {COLORS['border']};
            border-radius: {RADII['small']}px;
            padding: 4px 10px;
        """)
        status_row.addWidget(self._files_today_lbl)
        hero_layout.addLayout(status_row)

        # Status Description Subtitle
        self._status_desc_lbl = QLabel("Watching Downloads folder in real-time. Incoming files are routed to organized categories.")
        self._status_desc_lbl.setFont(QFont(TYPE["ui"], 10))
        self._status_desc_lbl.setStyleSheet(f"color: {COLORS['textMuted']};")
        hero_layout.addWidget(self._status_desc_lbl)

        # 4 Quick Action Buttons
        actions_row = QHBoxLayout()
        actions_row.setSpacing(10)

        self._btn_sort_now = QPushButton("⚡ Sort Downloads Now")
        self._btn_sort_now.setStyleSheet(f"""
            QPushButton {{
                background: {ACCENTS['downloadSorter']};
                color: #0B0D12;
                border: 1px solid {ACCENTS['downloadSorter']};
                font-weight: bold;
                padding: 7px 16px;
            }}
            QPushButton:hover {{
                background: #82ecc0;
            }}
        """)
        self._btn_sort_now.clicked.connect(self.sort_now)
        actions_row.addWidget(self._btn_sort_now)

        self._btn_undo_last = QPushButton("↺ Undo Last Sort")
        self._btn_undo_last.clicked.connect(self.undo_last)
        actions_row.addWidget(self._btn_undo_last)

        self._btn_open_downloads = QPushButton("📂 Open Downloads Folder")
        self._btn_open_downloads.clicked.connect(self.open_downloads)
        actions_row.addWidget(self._btn_open_downloads)

        self._btn_pause_resume = QPushButton("⏸ Pause / ▶ Resume")
        self._btn_pause_resume.clicked.connect(self.toggle_pause)
        actions_row.addWidget(self._btn_pause_resume)

        actions_row.addStretch()
        hero_layout.addLayout(actions_row)
        layout.addWidget(self._status_card)

        # ---------------------------------------------------------
        # 2. Recent Files Activity Feed
        # ---------------------------------------------------------
        activity_box = QVBoxLayout()
        activity_box.setSpacing(6)

        feed_header = QHBoxLayout()
        feed_title = QLabel("📋 Recent Sorting Activity")
        feed_title.setFont(QFont(TYPE["ui"], 11, QFont.Weight.Bold))
        feed_header.addWidget(feed_title)

        feed_header.addStretch()

        self._refresh_btn = QPushButton("↻ Refresh")
        self._refresh_btn.setMinimumHeight(26)
        self._refresh_btn.clicked.connect(self.refresh_data)
        feed_header.addWidget(self._refresh_btn)
        activity_box.addLayout(feed_header)

        # Container layout for activity table and empty state
        self._activity_layout = QVBoxLayout()

        self._empty_state_lbl = QLabel("No files organized yet. When files land in Downloads, they will appear here.")
        self._empty_widget = self._empty_state_lbl  # Test compatibility alias
        self._empty_state_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_state_lbl.setStyleSheet(f"""
            background: {COLORS['surface']};
            border: 1px dashed {COLORS['border']};
            border-radius: {RADII['small']}px;
            color: {COLORS['textMuted']};
            padding: 30px;
            font-size: 11px;
        """)
        self._activity_layout.addWidget(self._empty_state_lbl)

        self._activity_table = QTableWidget()
        self._activity_table.setColumnCount(4)
        self._activity_table.setHorizontalHeaderLabels(["Filename", "Folder", "Timestamp", "Action"])
        self._activity_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._activity_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self._activity_table.setColumnWidth(1, 150)
        self._activity_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self._activity_table.setColumnWidth(2, 160)
        self._activity_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self._activity_table.setColumnWidth(3, 140)
        self._activity_table.verticalHeader().setVisible(False)
        self._activity_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._activity_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._activity_layout.addWidget(self._activity_table)

        activity_box.addLayout(self._activity_layout, 1)
        layout.addLayout(activity_box, 1)

        # ---------------------------------------------------------
        # 3. Quick Preferences Bar
        # ---------------------------------------------------------
        self._pref_bar = QFrame()
        self._pref_bar.setObjectName("prefBar")
        self._pref_bar.setStyleSheet(f"""
            QFrame#prefBar {{
                background-color: {COLORS['surface']};
                border: 1px solid {COLORS['border']};
                border-radius: {RADII['card']}px;
            }}
        """)
        pref_layout = QHBoxLayout(self._pref_bar)
        pref_layout.setContentsMargins(12, 6, 12, 6)
        pref_layout.setSpacing(14)

        self._chk_startup = QCheckBox("Windows Startup")
        self._chk_startup.toggled.connect(self._on_startup_toggled)
        pref_layout.addWidget(self._chk_startup)

        self._chk_context = QCheckBox("Context Menu")
        self._chk_context.toggled.connect(self._on_context_menu_toggled)
        pref_layout.addWidget(self._chk_context)

        self._chk_unpack = QCheckBox("Auto-Unpack ZIPs")
        self._chk_unpack.toggled.connect(self._on_unpack_toggled)
        pref_layout.addWidget(self._chk_unpack)

        pref_layout.addStretch()

        pref_layout.addWidget(QLabel("Profile:"))
        self._persona_combo = QComboBox()
        self._persona_combo.addItem("🏠 Everyday & Home", "everyday_home")
        self._persona_combo.addItem("🎓 Student & School", "academic_student")
        self._persona_combo.addItem("💼 Office & Finance", "office_admin")
        self._persona_combo.addItem("🛠️ 3D Printing & CNC Maker", "maker_3d")
        self._persona_combo.currentIndexChanged.connect(self._on_persona_combo_changed)
        pref_layout.addWidget(self._persona_combo)

        self._btn_rerun_tour = QPushButton("🧭 Re-run Welcome Tour...")
        self._btn_rerun_tour.clicked.connect(self._on_rerun_tour)
        pref_layout.addWidget(self._btn_rerun_tour)

        layout.addWidget(self._pref_bar)

    def refresh_status(self) -> None:
        """Update active status badge, move count metric, and pause button."""
        is_paused = False
        if sorter and hasattr(sorter, "is_paused"):
            try:
                is_paused = sorter.is_paused()
            except Exception:
                pass

        is_running = True
        if hasattr(suite_data, "is_sorter_running"):
            try:
                is_running = suite_data.is_sorter_running()
            except Exception:
                pass

        if is_paused:
            self._status_badge.setText("⏸ Paused — Monitoring Suspended")
            self._status_badge.setStyleSheet(f"color: {COLORS['warning']};")
            self._btn_pause_resume.setText("▶ Resume Sorter")
        elif is_running:
            self._status_badge.setText("● Active — Watching Downloads")
            self._status_badge.setStyleSheet(f"color: {COLORS['success']};")
            self._btn_pause_resume.setText("⏸ Pause Sorter")
        else:
            self._status_badge.setText("○ Idle — Sorter Stopped")
            self._status_badge.setStyleSheet(f"color: {COLORS['textMuted']};")
            self._btn_pause_resume.setText("▶ Start Sorter")

        # Files organized today metric
        moves_today = 0
        if sorter and hasattr(sorter, "IncrementalHistoryReader") and hasattr(sorter, "HISTORY_FILE"):
            try:
                reader = sorter.IncrementalHistoryReader(sorter.HISTORY_FILE)
                moves_today = reader.moves_today()
            except Exception:
                pass
        self._files_today_lbl.setText(f"📊 {moves_today} files organized today")

    def refresh_data(self) -> None:
        """Reload configuration, preferences, and recent activity records."""
        if hasattr(suite_data, "load_sorter_config"):
            self._config = suite_data.load_sorter_config()

        self.refresh_status()

        # Update checkboxes without triggering signals
        if hasattr(suite_data, "is_startup_enabled"):
            try:
                self._chk_startup.blockSignals(True)
                self._chk_startup.setChecked(bool(suite_data.is_startup_enabled()))
                self._chk_startup.blockSignals(False)
            except Exception:
                pass

        if hasattr(suite_data, "is_context_menu_enabled"):
            try:
                self._chk_context.blockSignals(True)
                self._chk_context.setChecked(bool(suite_data.is_context_menu_enabled()))
                self._chk_context.blockSignals(False)
            except Exception:
                pass

        self._chk_unpack.blockSignals(True)
        self._chk_unpack.setChecked(bool(self._config.get("auto_unpack_zips", False)))
        self._chk_unpack.blockSignals(False)

        current_persona = self._config.get("user_persona", "everyday_home")
        idx = self._persona_combo.findData(current_persona)
        if idx >= 0:
            self._persona_combo.blockSignals(True)
            self._persona_combo.setCurrentIndex(idx)
            self._persona_combo.blockSignals(False)

        # Load Recent Activity records
        records: list[dict[str, Any]] = []
        if hasattr(suite_data, "get_recent_activity"):
            try:
                records = suite_data.get_recent_activity(limit=25)
            except Exception:
                records = []

        if not records:
            self._empty_state_lbl.setVisible(True)
            self._activity_table.setVisible(False)
            self._activity_table.setRowCount(0)
        else:
            self._empty_state_lbl.setVisible(False)
            self._activity_table.setVisible(True)
            self._activity_table.setRowCount(0)
            self._activity_table.setRowCount(len(records))

            for row, rec in enumerate(records):
                filename = str(rec.get("filename", ""))
                folder = str(rec.get("folder", ""))
                ts = str(rec.get("timestamp", ""))
                dest = str(rec.get("dest", ""))

                f_item = QTableWidgetItem(filename)
                f_item.setFont(QFont(TYPE["mono"], 10))
                self._activity_table.setItem(row, 0, f_item)

                folder_item = QTableWidgetItem(f"📁 {folder}")
                folder_item.setForeground(QColor(ACCENTS["downloadSorter"]))
                self._activity_table.setItem(row, 1, folder_item)

                ts_item = QTableWidgetItem(ts)
                ts_item.setForeground(QColor(COLORS["textMuted"]))
                self._activity_table.setItem(row, 2, ts_item)

                show_btn = QPushButton("📂 Show in Folder")
                show_btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {COLORS['surfaceRaised']};
                        border: 1px solid {COLORS['border']};
                        border-radius: 4px;
                        padding: 3px 8px;
                        font-size: 10px;
                    }}
                    QPushButton:hover {{
                        border-color: {ACCENTS['downloadSorter']};
                        color: {ACCENTS['downloadSorter']};
                    }}
                """)
                show_btn.clicked.connect(lambda _c=False, p=dest: self._show_in_folder(p))
                self._activity_table.setCellWidget(row, 3, show_btn)

    def _show_in_folder(self, target_path: str) -> None:
        """Highlight target file in Windows Explorer."""
        if not target_path:
            return
        p = Path(target_path)
        try:
            if p.exists():
                subprocess.Popen(["explorer.exe", f"/select,{p}"])
            elif p.parent.exists():
                subprocess.Popen(["explorer.exe", str(p.parent)])
        except Exception:
            pass

    def sort_now(self) -> int:
        """Trigger an immediate manual sort of downloads."""
        count = 0
        if sorter and hasattr(sorter, "sort_existing"):
            try:
                cfg = suite_data.load_sorter_config() if hasattr(suite_data, "load_sorter_config") else self._config
                count = sorter.sort_existing(cfg, dry_run=False)
            except Exception:
                pass
        self.refresh_data()
        return count

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
        """Toggle sorter background pause state."""
        if sorter and hasattr(sorter, "is_paused") and hasattr(sorter, "set_paused"):
            try:
                new_state = not sorter.is_paused()
                sorter.set_paused(new_state)
            except Exception:
                pass
        self.refresh_status()

    def set_persona(self, persona_id: str) -> None:
        """Update internal persona state and combo selection."""
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

    def _on_unpack_toggled(self, checked: bool) -> None:
        self._config["auto_unpack_zips"] = checked
        if hasattr(suite_data, "save_sorter_config"):
            try:
                suite_data.save_sorter_config(self._config)
            except Exception:
                pass

    def _on_persona_combo_changed(self, index: int) -> None:
        persona_id = str(self._persona_combo.itemData(index))
        if hasattr(suite_data, "apply_persona_profile"):
            try:
                self._config = suite_data.apply_persona_profile(self._config, persona_id)
            except Exception:
                self._config["user_persona"] = persona_id
        else:
            self._config["user_persona"] = persona_id

        if hasattr(suite_data, "save_sorter_config"):
            try:
                suite_data.save_sorter_config(self._config)
            except Exception:
                pass

        self.persona_changed.emit(persona_id)

    def _on_rerun_tour(self) -> None:
        wizard = WelcomeWizardDialog(config=self._config, parent=self.window())
        if wizard.exec() == QDialog.DialogCode.Accepted:
            self.refresh_data()
            pid = self._config.get("user_persona", "everyday_home")
            self.set_persona(pid)
            self.persona_changed.emit(pid)

    def closeEvent(self, event) -> None:
        if hasattr(self, "_refresh_timer") and self._refresh_timer.isActive():
            self._refresh_timer.stop()
        super().closeEvent(event)

    def hideEvent(self, event) -> None:
        if hasattr(self, "_refresh_timer") and self._refresh_timer.isActive():
            self._refresh_timer.stop()
        super().hideEvent(event)
