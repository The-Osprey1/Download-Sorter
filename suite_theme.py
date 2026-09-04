"""Generated from personal-ui tokens v1.1.0. Do not edit."""
from __future__ import annotations

TOKENS = {
    "$schema": "./tokens.schema.json",
    "version": "1.1.0",
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
        "info": "#61A5FF",
        "warning": "#F5AD62",
        "critical": "#FA7183",
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
        "6": 32
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
        "sizeLg": 18,
        "weightRegular": 400,
        "weightMedium": 600,
        "weightBold": 700
    },
    "motion": {
        "fastMs": 150,
        "normalMs": 240,
        "emphasizedMs": 280,
        "easing": "cubic-bezier(0.2, 0.8, 0.2, 1)"
    },
    "ribbon": {
        "width": 336,
        "height": 84,
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
    """
