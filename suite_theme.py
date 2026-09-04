"""Generated from personal-ui tokens v1.2.0. Do not edit."""
from __future__ import annotations

TOKENS = {
    "$schema": "./tokens.schema.json",
    "version": "1.2.0",
    "name": "Devyn Personal Suite",
    "color": {
        "canvas": "#0B0D12",
        "surface": "#12151C",
        "surfaceRaised": "#171B24",
        "surfaceHover": "#1D222C",
        "border": "#2A303B",
        "borderStrong": "#3A4352",
        "text": "#EDF1F8",
        "textMuted": "#8B94A7",
        "textFaint": "#657084",
        "success": "#69E6AD",
        "successSubtle": "#0E1B15",
        "successBorder": "#1B4533",
        "info": "#61A5FF",
        "infoSubtle": "#0E1520",
        "infoBorder": "#1A2C4A",
        "warning": "#F5AD62",
        "warningSubtle": "#1A1510",
        "warningBorder": "#4A2E14",
        "critical": "#FA7183",
        "criticalSubtle": "#1A1013",
        "criticalBorder": "#481B22",
        "focus": "#8CB8FF",
        "overlay": "rgba(5, 6, 8, 0.68)",
        "glass": "rgba(18, 21, 28, 0.82)",
        "glassStrong": "rgba(18, 21, 28, 0.92)"
    },
    "accent": {
        "suiteCentral": "#61A5FF",
        "performanceJournal": "#69E6AD",
        "downloadSorter": "#69E6AD",
        "printLibrary": "#E8833A",
        "haasStudio": "#D74A4A",
        "broncoBites": "#10B981",
        "broncoGold": "#FFB500",
        "semesterDeck": "#A67BEF"
    },
    "space": {
        "1": 4,
        "2": 8,
        "3": 12,
        "4": 16,
        "5": 24,
        "6": 32,
        "7": 48
    },
    "radius": {
        "small": 6,
        "control": 8,
        "card": 11,
        "large": 14,
        "pill": 999
    },
    "type": {
        "ui": "Segoe UI",
        "mono": "Cascadia Code",
        "sizeXs": 9,
        "sizeSm": 11,
        "sizeMd": 13,
        "sizeBase": 14,
        "sizeLg": 18,
        "sizeXl": 22,
        "weightRegular": 400,
        "weightMedium": 600,
        "weightBold": 700,
        "lineHeightTight": 1.2,
        "lineHeightNormal": 1.45,
        "lineHeightRelaxed": 1.6,
        "letterSpacingTight": -0.3,
        "letterSpacingNormal": 0,
        "letterSpacingWide": 0.8
    },
    "motion": {
        "instantMs": 80,
        "fastMs": 150,
        "normalMs": 240,
        "emphasizedMs": 280,
        "slowMs": 400,
        "easing": "cubic-bezier(0.2, 0.8, 0.2, 1)",
        "easeEnter": "cubic-bezier(0, 0, 0.2, 1)",
        "easeExit": "cubic-bezier(0.4, 0, 1, 1)"
    },
    "elevation": {
        "flat": "none",
        "low": "0 1px 3px rgba(0, 0, 0, 0.35), 0 1px 2px rgba(0, 0, 0, 0.2)",
        "medium": "0 4px 12px rgba(0, 0, 0, 0.45), 0 2px 4px rgba(0, 0, 0, 0.25)",
        "high": "0 8px 28px rgba(0, 0, 0, 0.55), 0 4px 10px rgba(0, 0, 0, 0.3)"
    },
    "gradient": {
        "surfaceCard": "linear-gradient(180deg, #171B24 0%, #12151C 100%)",
        "emblem": "linear-gradient(135deg, #1C2433 0%, #10141D 100%)"
    },
    "zIndex": {
        "base": 0,
        "raised": 10,
        "dropdown": 100,
        "sticky": 200,
        "overlay": 500,
        "modal": 1000,
        "toast": 2000,
        "tooltip": 3000
    },
    "ribbon": {
        "width": 360,
        "height": 90,
        "offset": 18,
        "gap": 8,
        "maxVisible": 4,
        "timeoutMs": 8000,
        "opacity": 0.88,
        "blurPx": 18
    }
}
COLORS = TOKENS["color"]
ACCENTS = TOKENS["accent"]
SPACING = TOKENS["space"]
RADII = TOKENS["radius"]
TYPE = TOKENS["type"]
MOTION = TOKENS["motion"]
ELEVATION = TOKENS["elevation"]
GRADIENT = TOKENS["gradient"]
Z_INDEX = TOKENS["zIndex"]
RIBBON = TOKENS["ribbon"]

def qt_stylesheet(accent: str) -> str:
    c = COLORS
    r = RADII
    return f"""
        QWidget {{ font-family: '{TYPE['ui']}'; font-size: {TYPE['sizeSm']}px; color: {c['text']}; }}
        QLineEdit, QComboBox {{ background: {c['canvas']}; border: 1px solid {c['border']}; border-radius: {r['small']}px; padding: 6px 9px; }}
        QLineEdit:focus, QComboBox:focus {{ border-color: {accent}; }}
        QPushButton {{ background: {c['surfaceRaised']}; border: 1px solid {c['border']}; border-radius: {r['small']}px; padding: 6px 12px; }}
        QPushButton:hover {{ background: {c['surfaceHover']}; border-color: {accent}; }}
        QPushButton:checked {{ background: {c['surfaceHover']}; border-color: {accent}; color: {accent}; }}
        QPushButton:disabled {{ color: {c['textMuted']}; }}
        QToolTip, QMenu {{ background: {c['surfaceRaised']}; border: 1px solid {c['border']}; color: {c['text']}; }}
        QMenu::item:selected {{ background: {c['surfaceHover']}; }}
        QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
        QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 4px; min-height: 32px; }}
        QScrollBar::handle:vertical:hover {{ background: {c['borderStrong']}; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
        QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 0; }}
        QScrollBar::handle:horizontal {{ background: {c['border']}; border-radius: 4px; min-width: 32px; }}
        QScrollBar::handle:horizontal:hover {{ background: {c['borderStrong']}; }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}
    """
