"""Small, calm animations and inline messages for the main window.

Every effect here is skipped when Windows' "Show animations in Windows" setting
is off (Settings > Accessibility > Visual effects), or when the environment
variable DOWNLOAD_SORTER_REDUCED_MOTION is set to 1. Things then change
instantly instead, so nothing moves for people who turned motion off.
"""

from __future__ import annotations

import os
import sys
from typing import Callable

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QParallelAnimationGroup,
    QPropertyAnimation,
    QTimer,
    QVariantAnimation,
    Qt,
)
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QWidget,
)

import ui_icons
from suite_theme import COLORS

REDUCED_MOTION_ENV = "DOWNLOAD_SORTER_REDUCED_MOTION"
_SPI_GETCLIENTAREAANIMATION = 0x1042


def animations_enabled() -> bool:
    """False when the user asked Windows (or this app) for less motion."""
    if os.environ.get(REDUCED_MOTION_ENV, "").strip().lower() in ("1", "true", "yes", "on"):
        return False
    if sys.platform == "win32":
        try:
            import ctypes

            enabled = ctypes.c_int(1)
            if ctypes.windll.user32.SystemParametersInfoW(
                _SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(enabled), 0
            ):
                return bool(enabled.value)
        except Exception:
            pass
    return True


def _keep(owner: QWidget, name: str, animation: QAbstractAnimation | None) -> None:
    """Hold a reference so the animation isn't garbage-collected mid-run,
    stopping any earlier animation stored under the same name."""
    previous = getattr(owner, name, None)
    if previous is not None and previous is not animation:
        try:
            previous.stop()
        except RuntimeError:
            pass
    setattr(owner, name, animation)


def _opacity_effect(widget: QWidget) -> QGraphicsOpacityEffect:
    effect = widget.graphicsEffect()
    if not isinstance(effect, QGraphicsOpacityEffect):
        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(1.0)
        widget.setGraphicsEffect(effect)
    return effect


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------

def fade_in(widget: QWidget, duration: int = 180) -> None:
    """Show a widget by fading it in."""
    widget.setVisible(True)
    if not animations_enabled():
        if isinstance(widget.graphicsEffect(), QGraphicsOpacityEffect):
            widget.graphicsEffect().setOpacity(1.0)
        return
    effect = _opacity_effect(widget)
    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(duration)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    # Drop the effect afterwards: a permanent opacity effect makes big pages
    # (tables, scroll areas) slower to draw.
    anim.finished.connect(lambda: _drop_effect(widget, effect))
    _keep(widget, "_motion_fade", anim)
    anim.start()


def _drop_effect(widget: QWidget, effect: QGraphicsOpacityEffect) -> None:
    try:
        if widget.graphicsEffect() is effect and not is_breathing(widget):
            widget.setGraphicsEffect(None)
    except RuntimeError:
        pass


def breathe(widget: QWidget, on: bool, period_ms: int = 2000) -> None:
    """Slowly pulse a widget's opacity while ``on`` (the "watching" heartbeat)."""
    current = getattr(widget, "_motion_breathe", None)
    if not on or not animations_enabled():
        if current is not None:
            current.stop()
            setattr(widget, "_motion_breathe", None)
        if isinstance(widget.graphicsEffect(), QGraphicsOpacityEffect):
            widget.graphicsEffect().setOpacity(1.0)
        return
    if current is not None and current.state() == QAbstractAnimation.State.Running:
        return
    effect = _opacity_effect(widget)
    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(period_ms)
    anim.setStartValue(1.0)
    anim.setKeyValueAt(0.5, 0.3)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.Type.InOutSine)
    anim.setLoopCount(-1)
    _keep(widget, "_motion_breathe", anim)
    anim.start()


def is_breathing(widget: QWidget) -> bool:
    anim = getattr(widget, "_motion_breathe", None)
    return anim is not None and anim.state() == QAbstractAnimation.State.Running


def count_up(label: QLabel, start: int, end: int, fmt: Callable[[int], str], duration: int = 700) -> None:
    """Change a number in a label by counting up (or down) to the new value."""
    label.setProperty("motion_target", end)
    if start == end or not animations_enabled():
        _keep(label, "_motion_count", None)
        label.setText(fmt(end))
        return
    anim = QVariantAnimation(label)
    anim.setDuration(duration)
    anim.setStartValue(int(start))
    anim.setEndValue(int(end))
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    anim.valueChanged.connect(lambda value: label.setText(fmt(int(value))))
    anim.finished.connect(lambda: label.setText(fmt(end)))
    _keep(label, "_motion_count", anim)
    anim.start()


def flash_rows(table: QTableWidget, rows: list[int], color: str | None = None, duration: int = 1800) -> None:
    """Highlight freshly added table rows, then let the highlight fade away.

    The highlight is a see-through layer drawn over each row, because the
    shared stylesheet paints table rows itself and ignores per-item colors.
    """
    _clear_flash(table)
    if not rows or not animations_enabled():
        return
    base = QColor(color or COLORS["success"])
    viewport = table.viewport()
    overlays: list[QWidget] = []
    for row in rows:
        if row < 0 or row >= table.rowCount():
            continue
        top = table.rowViewportPosition(row)
        if top < 0 and row != 0:
            continue
        overlay = QWidget(viewport)
        overlay.setObjectName("rowFlash")
        overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        overlay.setGeometry(0, max(top, 0), viewport.width(), table.rowHeight(row))
        overlay.show()
        overlays.append(overlay)
    if not overlays:
        return

    def paint(alpha: float) -> None:
        tint = f"rgba({base.red()}, {base.green()}, {base.blue()}, {max(0.0, min(1.0, alpha)):.3f})"
        for overlay in overlays:
            try:
                overlay.setStyleSheet(f"QWidget#rowFlash {{ background: {tint}; border: none; }}")
            except RuntimeError:
                pass

    anim = QVariantAnimation(table)
    anim.setDuration(duration)
    anim.setStartValue(0.22)
    anim.setKeyValueAt(0.35, 0.18)
    anim.setEndValue(0.0)
    anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
    anim.valueChanged.connect(lambda value: paint(float(value)))
    anim.finished.connect(lambda: _clear_flash(table))
    table._motion_flash_overlays = overlays
    _keep(table, "_motion_flash", anim)
    paint(0.22)
    anim.start()


def flashing_rows(table: QTableWidget) -> int:
    """How many row highlights are showing (for tests)."""
    return len(getattr(table, "_motion_flash_overlays", []) or [])


def _clear_flash(table: QTableWidget) -> None:
    for overlay in getattr(table, "_motion_flash_overlays", []) or []:
        try:
            overlay.hide()
            overlay.deleteLater()
        except RuntimeError:
            pass
    table._motion_flash_overlays = []


def crossfade_to(stack: QStackedWidget, index: int, duration: int = 160) -> None:
    """Switch a stacked page with a short fade instead of a hard cut."""
    if index == stack.currentIndex():
        return
    stack.setCurrentIndex(index)
    page = stack.currentWidget()
    if page is not None and animations_enabled():
        fade_in(page, duration)


# ---------------------------------------------------------------------------
# Inline message ("banner") that slides in under the status card
# ---------------------------------------------------------------------------

_BANNER_KINDS = {
    "success": ("successSubtle", "successBorder", "success", "check"),
    "info": ("infoSubtle", "infoBorder", "info", "info"),
    "warning": ("warningSubtle", "warningBorder", "warning", "warning"),
    "error": ("criticalSubtle", "criticalBorder", "critical", "warning"),
}


class Banner(QFrame):
    """A one-line message with an optional action, e.g. "Sorted 8 files. [Undo]".

    It slides open, stays for a few seconds (or until closed), then fades away.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("banner")
        self._kind = "info"
        self._action: Callable[[], None] | None = None

        row = QHBoxLayout(self)
        row.setContentsMargins(14, 10, 10, 10)
        row.setSpacing(10)
        self._icon = QLabel(self)
        self._icon.setFixedSize(16, 16)
        row.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignVCenter)
        self._text = QLabel(self)
        self._text.setWordWrap(True)
        self._text.setTextFormat(Qt.TextFormat.PlainText)
        row.addWidget(self._text, 1)
        self._action_btn = QPushButton(self)
        self._action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._action_btn.clicked.connect(self._run_action)
        self._action_btn.setVisible(False)
        row.addWidget(self._action_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self._close_btn = QPushButton(self)
        self._close_btn.setFlat(True)
        self._close_btn.setToolTip("Dismiss")
        self._close_btn.setFixedSize(24, 24)
        self._close_btn.setIcon(ui_icons.icon("close"))
        self._close_btn.clicked.connect(self.dismiss)
        row.addWidget(self._close_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.dismiss)
        self.setVisible(False)

    # -- public ------------------------------------------------------------

    def text(self) -> str:
        return self._text.text()

    def kind(self) -> str:
        return self._kind

    def action_text(self) -> str:
        return self._action_btn.text() if self._action_btn.isVisible() else ""

    def show_message(
        self,
        text: str,
        kind: str = "info",
        action_text: str = "",
        action: Callable[[], None] | None = None,
        timeout_ms: int = 7000,
    ) -> None:
        """Show ``text``. ``timeout_ms=0`` keeps it until the user closes it."""
        self._kind = kind if kind in _BANNER_KINDS else "info"
        bg, border, fg, icon_name = _BANNER_KINDS[self._kind]
        self.setStyleSheet(
            f"QFrame#banner {{ background: {COLORS[bg]}; border: 1px solid {COLORS[border]};"
            f" border-radius: 8px; }}"
            f" QFrame#banner QLabel {{ color: {COLORS['text']}; background: transparent; border: none; }}"
            f" QFrame#banner QPushButton {{ color: {COLORS[fg]}; background: transparent;"
            f" border: 1px solid {COLORS[border]}; border-radius: 6px; padding: 4px 12px; }}"
            f" QFrame#banner QPushButton:hover {{ background: {COLORS['surfaceHover']}; }}"
            f" QFrame#banner QPushButton:flat {{ border: none; padding: 0; }}"
        )
        try:
            self._icon.setPixmap(ui_icons.pixmap(icon_name, COLORS[fg], 16))
        except Exception:
            self._icon.clear()
        self._text.setText(text)
        self._action = action
        self._action_btn.setText(action_text.replace("&", "&&"))
        self._action_btn.setVisible(bool(action_text and action))

        was_visible = self.isVisible()
        self.setVisible(True)
        if not was_visible:
            self._slide_open()
        self._hide_timer.stop()
        if timeout_ms > 0:
            self._hide_timer.start(timeout_ms)

    def dismiss(self) -> None:
        self._hide_timer.stop()
        if not self.isVisible():
            return
        if not animations_enabled():
            self.setVisible(False)
            return
        effect = _opacity_effect(self)
        anim = QPropertyAnimation(effect, b"opacity", self)
        anim.setDuration(220)
        anim.setStartValue(effect.opacity())
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.Type.InCubic)
        anim.finished.connect(self._finish_hide)
        _keep(self, "_motion_fade", anim)
        anim.start()

    # -- internals ---------------------------------------------------------

    def _finish_hide(self) -> None:
        self.setVisible(False)
        effect = self.graphicsEffect()
        if isinstance(effect, QGraphicsOpacityEffect):
            effect.setOpacity(1.0)

    def _run_action(self) -> None:
        action = self._action
        self.dismiss()
        if action is not None:
            action()

    def _slide_open(self) -> None:
        if not animations_enabled():
            return
        target = max(self.sizeHint().height(), 44)
        effect = _opacity_effect(self)
        group = QParallelAnimationGroup(self)
        grow = QPropertyAnimation(self, b"maximumHeight", self)
        grow.setDuration(200)
        grow.setStartValue(0)
        grow.setEndValue(target)
        grow.setEasingCurve(QEasingCurve.Type.OutCubic)
        fade = QPropertyAnimation(effect, b"opacity", self)
        fade.setDuration(240)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        group.addAnimation(grow)
        group.addAnimation(fade)
        group.finished.connect(lambda: self.setMaximumHeight(16777215))
        _keep(self, "_motion_fade", group)
        group.start()
