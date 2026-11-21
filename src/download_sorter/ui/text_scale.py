"""Follow Windows' "Text size" setting (Settings > Accessibility > Text size).

Every font size in the app's styles is written in pixels, so Windows' text size
slider had no effect. ``install_text_scaling`` runs every style the app sets
through ``scale_font_sizes``, so ``font-size: 12px`` becomes ``18px`` at 150%.
It is installed once at startup, before any window is built; at 100% nothing
changes.

The factor is capped at ``MAX_TEXT_SCALE``: above it, fixed-size parts of the
layout would cut text off.
"""

from __future__ import annotations

from collections.abc import Callable
import re
import sys
from typing import Any

from PySide6.QtWidgets import QWidget

MAX_TEXT_SCALE = 1.5
_FONT_SIZE_PX_RE = re.compile(r"font-size:\s*([0-9]+(?:\.[0-9]+)?)px")

_state: dict[str, Any] = {"factor": 1.0, "original": None}


def scale_font_sizes(css: str, factor: float) -> str:
    """Multiply every ``font-size: Npx`` in ``css`` by ``factor`` (whole pixels)."""
    if not css or abs(factor - 1.0) < 1e-6:
        return css
    return _FONT_SIZE_PX_RE.sub(lambda m: f"font-size: {scaled_px(float(m.group(1)), factor)}px", css)


def scaled_px(value: float, factor: float | None = None) -> int:
    """A pixel size scaled for the text size in use (or ``factor``)."""
    use = current_text_scale() if factor is None else factor
    return int(round(value * use))


def _read_registry_value() -> object:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Accessibility") as key:
            value, _kind = winreg.QueryValueEx(key, "TextScaleFactor")
            return value
    except OSError:
        return None


def read_windows_text_scale(reader: Callable[[], object] | None = None) -> float:
    """Windows' text size as a factor: 1.0 at 100%, 1.5 at 150% (capped)."""
    if reader is None:
        if sys.platform != "win32":
            return 1.0
        reader = _read_registry_value
    try:
        percent = int(reader())  # type: ignore[arg-type]
    except (TypeError, ValueError, OSError):
        return 1.0
    if percent <= 100:
        return 1.0
    return min(percent / 100.0, MAX_TEXT_SCALE)


def current_text_scale() -> float:
    return float(_state["factor"])


def install_text_scaling(factor: float) -> None:
    """Scale the font sizes of every style set from now on. Safe to call again."""
    factor = max(1.0, min(float(factor), MAX_TEXT_SCALE))
    if _state["original"] is None:
        original = QWidget.setStyleSheet
        _state["original"] = original

        def _set_scaled_style_sheet(widget: QWidget, css: str) -> None:
            original(widget, scale_font_sizes(css, current_text_scale()))

        QWidget.setStyleSheet = _set_scaled_style_sheet  # type: ignore[method-assign]
    _state["factor"] = factor


def uninstall_text_scaling() -> None:
    if _state["original"] is not None:
        QWidget.setStyleSheet = _state["original"]  # type: ignore[method-assign]
        _state["original"] = None
    _state["factor"] = 1.0
