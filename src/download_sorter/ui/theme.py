"""Shared Apple-inspired macOS Dark Mode Qt stylesheet."""

from __future__ import annotations

PALETTE = {
    "bg_window": "#111113",
    "bg_card": "#1C1C1E",
    "bg_card_top": "#212124",
    "bg_card_hover": "#232326",
    "bg_elevated": "#262629",
    "border": "#2C2C2E",
    "border_hover": "#3D3D41",
    "text_primary": "#F5F5F7",
    "text_secondary": "#98989D",
    "text_muted": "#636366",
    "accent_blue": "#0A84FF",
    "accent_green": "#30D158",
    "accent_cyan": "#5AC8FA",
    "accent_amber": "#FF9F0A",
    "accent_red": "#FF453A",
}

STYLESHEET = """
/* === GLOBAL RESET & CANVAS (macOS Sequoia Dark Mode) === */
QMainWindow, QDialog {
    background-color: #111113;
    color: #F5F5F7;
    font-family: "SF Pro Display", "SF Pro Text", "Segoe UI Variable Display", "Segoe UI", system-ui, sans-serif;
    font-size: 13px;
}
QWidget {
    background-color: transparent;
    color: #F5F5F7;
    font-family: "SF Pro Display", "SF Pro Text", "Segoe UI Variable Display", "Segoe UI", system-ui, sans-serif;
    font-size: 13px;
    selection-background-color: #0A84FF;
    selection-color: #FFFFFF;
}
QDialog#WalkthroughModal {
    background-color: transparent;
}

/* === TYPOGRAPHY & PILLS === */
QLabel#AppTitle {
    font-size: 17px;
    font-weight: 600;
    color: #F5F5F7;
}
QLabel#CardTitle {
    font-size: 14px;
    font-weight: 600;
    color: #F5F5F7;
}
QLabel#SectionEyebrow {
    font-size: 11px;
    font-weight: 600;
    color: #8E8E93;
    text-transform: uppercase;
}
QLabel#MutedText {
    color: #8E8E93;
    font-size: 12px;
}
QLabel#MonoPath {
    font-family: "SF Mono", "Cascadia Mono", "Consolas", monospace;
    font-size: 11.5px;
    color: #98989D;
    background-color: #161618;
    border: 1px solid #2C2C2E;
    border-radius: 7px;
    padding: 3px 9px;
}

/* === UNIFIED TOOLBAR & SURFACE CARDS === */
QFrame#TopStatusBar {
    background: qlineargradient(
        x1: 0, y1: 0, x2: 0, y2: 1,
        stop: 0 #222225,
        stop: 0.08 #1C1C1E,
        stop: 1 #1C1C1E
    );
    border: 1px solid #2C2C2E;
    border-radius: 14px;
}
QFrame#RibbonDivider {
    background-color: #2C2C2E;
    min-width: 1px;
    max-width: 1px;
    margin: 6px 2px;
}
QFrame#Card, QFrame#SurfaceCard, QFrame#heroBanner {
    background: qlineargradient(
        x1: 0, y1: 0, x2: 0, y2: 1,
        stop: 0 #212124,
        stop: 0.06 #1C1C1E,
        stop: 1 #1C1C1E
    );
    border: 1px solid #2C2C2E;
    border-radius: 14px;
}
QFrame#Card:hover, QFrame#SurfaceCard:hover {
    border-color: #3A3A3E;
}
QFrame#CardDivider {
    background-color: #262629;
    min-height: 1px;
    max-height: 1px;
}
QFrame#MascotCard {
    background-color: #161618;
    border: 1px solid #2C2C2E;
    border-radius: 12px;
}
QFrame#stepCard {
    background-color: #161618;
    border: 1px solid #2C2C2E;
    border-radius: 10px;
}
QFrame#stepCard:hover {
    border-color: #0A84FF;
}

/* === APPLE CAPSULE SEGMENTED CONTROL (Home | Custom Rules | Settings) === */
QTabWidget::tab-bar {
    alignment: center;
}
QTabWidget::pane {
    border: none;
    background: transparent;
    margin-top: 8px;
}
QTabBar {
    background-color: #1C1C1E;
    border: 1px solid #2C2C2E;
    border-radius: 10px;
    padding: 3px;
}
QTabBar::tab {
    background: transparent;
    color: #8E8E93;
    border: 1px solid transparent;
    border-radius: 7px;
    padding: 6px 24px;
    font-weight: 600;
    font-size: 12.5px;
    margin: 1px;
}
QTabBar::tab:hover:!selected {
    color: #F5F5F7;
    background-color: rgba(255, 255, 255, 10);
}
QTabBar::tab:selected {
    background-color: #2C2C2E;
    color: #F5F5F7;
    border: 1px solid #3A3A3C;
}

/* === APPLE PILL BADGES === */
QLabel#Badge {
    border-radius: 10px;
    padding: 2px 10px;
    font-size: 11px;
    font-weight: 600;
    min-height: 18px;
    max-height: 18px;
}
QLabel#Badge[variant="success"] {
    background-color: rgba(48, 209, 88, 30);
    color: #30D158;
    border: 1px solid rgba(48, 209, 88, 80);
}
QLabel#Badge[variant="danger"] {
    background-color: rgba(255, 69, 58, 30);
    color: #FF453A;
    border: 1px solid rgba(255, 69, 58, 80);
}
QLabel#Badge[variant="warning"] {
    background-color: rgba(255, 159, 10, 30);
    color: #FF9F0A;
    border: 1px solid rgba(255, 159, 10, 80);
}
QLabel#Badge[variant="primary"] {
    background-color: rgba(10, 132, 255, 34);
    color: #64D2FF;
    border: 1px solid rgba(10, 132, 255, 90);
}
QLabel#Badge[variant="neutral"] {
    background-color: #2C2C2E;
    color: #98989D;
    border: 1px solid #3A3A3C;
}

/* === BUTTONS === */
QPushButton {
    background-color: #2C2C2E;
    color: #F5F5F7;
    border: 1px solid #3A3A3C;
    border-radius: 8px;
    padding: 6px 13px;
    font-weight: 500;
    font-size: 12.5px;
}
QPushButton:hover {
    background-color: #3A3A3C;
    border-color: #48484A;
}
QPushButton:pressed {
    background-color: #242426;
}
QPushButton#primaryBtn, QPushButton#PrimaryButton {
    background-color: #0A84FF;
    border: 1px solid #0A84FF;
    color: #FFFFFF;
    font-weight: 600;
}
QPushButton#primaryBtn:hover, QPushButton#PrimaryButton:hover {
    background-color: #409CFF;
    border-color: #409CFF;
}
QPushButton#successBtn {
    background-color: #30D158;
    border: 1px solid #30D158;
    color: #082411;
    font-weight: 600;
}
QPushButton#successBtn:hover {
    background-color: #4AD96E;
    border-color: #4AD96E;
}
QPushButton#dangerBtn, QPushButton#DangerButton {
    background-color: rgba(255, 69, 58, 30);
    border: 1px solid rgba(255, 69, 58, 90);
    color: #FF453A;
}
QPushButton#dangerBtn:hover, QPushButton#DangerButton:hover {
    background-color: #FF453A;
    border-color: #FF453A;
    color: #FFFFFF;
}
QPushButton#ghostBtn, QPushButton#GhostButton {
    background-color: transparent;
    border: 1px solid transparent;
    color: #98989D;
}
QPushButton#ghostBtn:hover, QPushButton#GhostButton:hover {
    background-color: rgba(255, 255, 255, 14);
    color: #F5F5F7;
}
QPushButton:disabled {
    background-color: #18181A;
    color: #48484A;
    border-color: #242426;
}

/* === INPUTS & CHECKBOXES === */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTextEdit {
    background-color: #141416;
    color: #F5F5F7;
    border: 1px solid #2C2C2E;
    border-radius: 8px;
    padding: 6px 10px;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QTextEdit:focus {
    border: 1px solid #0A84FF;
}
QComboBox QAbstractItemView {
    background-color: #1C1C1E;
    color: #F5F5F7;
    border: 1px solid #38383A;
    selection-background-color: #0A84FF;
}
QCheckBox {
    spacing: 8px;
    color: #F5F5F7;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid #3A3A3C;
    background-color: #141416;
}
QCheckBox::indicator:checked {
    background-color: #0A84FF;
    border-color: #0A84FF;
}

/* === BORDERLESS-INTERIOR TABLES & LISTS === */
QTableWidget, QListWidget {
    background-color: transparent;
    alternate-background-color: rgba(255, 255, 255, 4);
    border: none;
    border-radius: 10px;
    gridline-color: transparent;
    outline: none;
}
QHeaderView::section {
    background-color: transparent;
    color: #8E8E93;
    font-size: 11px;
    font-weight: 600;
    padding: 6px 12px;
    border: none;
    border-bottom: 1px solid #262629;
}
QTableWidget::item {
    padding: 6px 12px;
    border-bottom: 1px solid #222225;
    color: #F5F5F7;
}
QTableWidget::item:hover {
    background-color: rgba(255, 255, 255, 7);
}
QTableWidget::item:selected {
    background-color: rgba(10, 132, 255, 42);
    color: #FFFFFF;
}

/* === GROUP BOXES (FALLBACK) === */
QGroupBox {
    background-color: #1C1C1E;
    border: 1px solid #2C2C2E;
    border-radius: 14px;
    margin-top: 12px;
    padding: 14px 12px 12px 12px;
    font-weight: 600;
    color: #F5F5F7;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 14px;
    padding: 2px 8px;
    background-color: #111113;
    border: 1px solid #2C2C2E;
    border-radius: 6px;
    color: #8E8E93;
}

/* === SCROLLBARS === */
QScrollBar:vertical {
    background: transparent;
    width: 7px;
    margin: 4px 0;
}
QScrollBar::handle:vertical {
    background: #3A3A3C;
    border-radius: 3px;
    min-height: 28px;
}
QScrollBar::handle:vertical:hover {
    background: #48484A;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}

/* === WALKTHROUGH MODAL === */
QFrame#ModalContainer {
    background-color: #1C1C1E;
    border: 1px solid #38383A;
    border-radius: 16px;
}
QFrame#ModalBanner {
    background-color: #141416;
    border: 1px solid #2C2C2E;
    border-radius: 10px;
}
QLabel#StepDot {
    background-color: #3A3A3C;
    border-radius: 4px;
    min-width: 8px;
    max-width: 8px;
    min-height: 8px;
    max-height: 8px;
}
QLabel#StepDot[active="true"] {
    background-color: #0A84FF;
    min-width: 22px;
    max-width: 22px;
}
QLabel#StepDot[done="true"] {
    background-color: #30D158;
}

/* === DYNAMIC PILL BADGES & CYAN RULE BUTTONS === */
QLabel[pill="green"] {
    background-color: rgba(48, 209, 88, 0.13);
    border: 1px solid rgba(48, 209, 88, 0.34);
    color: #30D158;
    border-radius: 10px;
    padding: 2px 9px;
    font-size: 11px;
    font-weight: 600;
}
QLabel[pill="cyan"] {
    background-color: rgba(90, 200, 250, 0.13);
    border: 1px solid rgba(90, 200, 250, 0.30);
    color: #5AC8FA;
    border-radius: 7px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 500;
}
QLabel[pill="blue"] {
    background-color: rgba(10, 132, 255, 0.15);
    border: 1px solid rgba(10, 132, 255, 0.36);
    color: #0A84FF;
    border-radius: 6px;
    padding: 2px 7px;
    font-size: 11px;
    font-weight: 600;
}
QLabel[pill="amber"] {
    background-color: rgba(255, 159, 10, 0.14);
    border: 1px solid rgba(255, 159, 10, 0.34);
    color: #FF9F0A;
    border-radius: 10px;
    padding: 2px 9px;
    font-size: 11px;
    font-weight: 600;
}
QPushButton[variant="cyan-rule"] {
    background-color: rgba(90, 200, 250, 0.08);
    color: #5AC8FA;
    border: 1px solid rgba(90, 200, 250, 0.25);
    border-radius: 6px;
    padding: 4px 10px;
    font-size: 11px;
}
QPushButton[variant="cyan-rule"]:hover {
    background-color: rgba(90, 200, 250, 0.16);
    border-color: #5AC8FA;
    color: #FFFFFF;
}

/* === SETTINGS TAB CARDS, DIVIDERS & ZIP SECURITY BANNER === */
QScrollArea#SettingsScroll, QWidget#SettingsViewport {
    background: transparent;
    border: none;
}
QFrame[divider="true"] {
    background-color: #2C2C2E;
    border: none;
    max-height: 1px;
    min-height: 1px;
}
QFrame[dependentGroup="true"] {
    background-color: rgba(255, 255, 255, 0.015);
    border-left: 2px solid #3D3D41;
    border-top: none;
    border-right: none;
    border-bottom: none;
    border-radius: 0px;
}
QFrame[securityBanner="true"] {
    background-color: rgba(48, 209, 88, 0.06);
    border: 1px solid rgba(48, 209, 88, 0.22);
    border-radius: 8px;
}
QLabel[securitySpec="true"] {
    color: #98989D;
    font-size: 11px;
    background: transparent;
}

/* === TOOLTIPS === */
QToolTip {
    background-color: #1C1C1E;
    color: #F5F5F7;
    border: 1px solid #3D3D41;
    border-radius: 7px;
    padding: 6px 10px;
    font-size: 12px;
}
"""


def set_widget_prop(widget: object, prop: str, value: object) -> None:
    """Update a dynamic QSS property and polish the widget cleanly."""
    widget.setProperty(prop, value)  # type: ignore[attr-defined]
    style = widget.style()  # type: ignore[attr-defined]
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)
    widget.update()  # type: ignore[attr-defined]
