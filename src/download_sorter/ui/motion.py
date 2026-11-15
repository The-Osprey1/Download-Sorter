"""Smooth, lightweight Qt UI animations for Download Sorter v3."""

from __future__ import annotations

from collections.abc import Callable
import math
import os
import random
import sys
import time
from typing import Any

from PySide6.QtCore import (
    Property,
    QAbstractAnimation,
    QEasingCurve,
    QParallelAnimationGroup,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    QTimer,
    QVariantAnimation,
    Qt,
    Signal,
)
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QWidget,
)

REDUCED_MOTION_ENV = "DOWNLOAD_SORTER_REDUCED_MOTION"
_SPI_GETCLIENTAREAANIMATION = 0x1042


def animations_enabled() -> bool:
    """Return False when reduced motion is requested via env or Windows accessibility."""
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
    """Keep an animation alive cleanly on ``owner``, stopping any prior one under ``name``."""
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


def is_breathing(widget: QWidget) -> bool:
    anim = getattr(widget, "_motion_breathe", None)
    return anim is not None and anim.state() == QAbstractAnimation.State.Running


def _drop_effect(widget: QWidget, effect: QGraphicsOpacityEffect) -> None:
    try:
        if widget.graphicsEffect() is effect and not is_breathing(widget):
            effect.setOpacity(1.0)
    except RuntimeError:
        pass


def fade_in(widget: QWidget, duration: int = 200) -> None:
    """Smoothly fade ``widget`` from 0.0 to 1.0 opacity."""
    if not widget.isVisible():
        widget.setVisible(True)
    if not animations_enabled() or duration <= 0:
        if isinstance(widget.graphicsEffect(), QGraphicsOpacityEffect):
            widget.graphicsEffect().setOpacity(1.0)
        return

    effect = _opacity_effect(widget)
    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(duration)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    anim.finished.connect(lambda: _drop_effect(widget, effect))
    _keep(widget, "_motion_fade", anim)
    anim.start()


def breathe(widget: QWidget, on: bool, period_ms: int = 1800) -> None:
    """Continuously pulse a status indicator's opacity while ``on`` is True."""
    current = getattr(widget, "_motion_breathe", None)
    if not on or not animations_enabled():
        if current is not None:
            try:
                current.stop()
            except RuntimeError:
                pass
            setattr(widget, "_motion_breathe", None)
        effect = widget.graphicsEffect()
        if isinstance(effect, QGraphicsOpacityEffect):
            effect.setOpacity(1.0)
        return

    if current is not None and current.state() == QAbstractAnimation.State.Running:
        return

    effect = _opacity_effect(widget)
    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(max(600, period_ms))
    anim.setStartValue(1.0)
    anim.setKeyValueAt(0.5, 0.35)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.Type.InOutSine)
    anim.setLoopCount(-1)
    _keep(widget, "_motion_breathe", anim)
    anim.start()


def _clear_flash(table: QTableWidget) -> None:
    for overlay in getattr(table, "_motion_flash_overlays", []) or []:
        try:
            overlay.hide()
            overlay.deleteLater()
        except RuntimeError:
            pass
    table._motion_flash_overlays = []


def flash_rows(
    table: QTableWidget,
    rows: list[int],
    hex_color: str = "#2D6CDF",
    duration: int = 1100,
) -> None:
    """Briefly highlight rows in ``table`` with a smooth alpha fade-out overlay."""
    _clear_flash(table)
    if not rows or not animations_enabled() or duration <= 0:
        return

    base = QColor(hex_color)
    viewport = table.viewport()
    overlays: list[QWidget] = []
    for row in rows:
        if row < 0 or row >= table.rowCount():
            continue
        top = table.rowViewportPosition(row)
        height = table.rowHeight(row)
        if top < -height or height <= 0:
            continue
        overlay = QWidget(viewport)
        overlay.setObjectName("rowFlash")
        overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        overlay.setGeometry(0, max(top, 0), max(viewport.width(), 1), height)
        overlay.show()
        overlays.append(overlay)

    if not overlays:
        return

    def _paint(alpha: float) -> None:
        clamped = max(0.0, min(1.0, alpha))
        tint = f"rgba({base.red()}, {base.green()}, {base.blue()}, {clamped:.3f})"
        for overlay in overlays:
            try:
                overlay.setStyleSheet(f"QWidget#rowFlash {{ background: {tint}; border: none; }}")
            except RuntimeError:
                pass

    anim = QVariantAnimation(table)
    anim.setDuration(duration)
    anim.setStartValue(0.26)
    anim.setEndValue(0.0)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    anim.valueChanged.connect(lambda v: _paint(float(v)))
    anim.finished.connect(lambda: _clear_flash(table))
    table._motion_flash_overlays = overlays
    _keep(table, "_motion_flash", anim)
    _paint(0.26)
    anim.start()


_BANNER_STYLES = {
    "success": ("#13261A", "#30D158", "#30D158", "✓"),
    "info": ("#132033", "#0A84FF", "#64D2FF", "●"),
    "warning": ("#2B2111", "#FF9F0A", "#FFD60A", "!"),
}


class AnimatedBanner(QFrame):
    """Inline notification bar that smoothly slides open and fades away."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("animatedBanner")
        self._action: Callable[[], None] | None = None

        row = QHBoxLayout(self)
        row.setContentsMargins(14, 8, 10, 8)
        row.setSpacing(10)

        self._icon = QLabel("", self)
        self._icon.setStyleSheet("font-weight: 700; font-size: 11pt; border: none; background: transparent;")
        row.addWidget(self._icon)

        self._text = QLabel("", self)
        self._text.setWordWrap(True)
        self._text.setStyleSheet("border: none; background: transparent;")
        row.addWidget(self._text, 1)

        self._action_btn = QPushButton("", self)
        self._action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._action_btn.clicked.connect(self._run_action)
        self._action_btn.setVisible(False)
        row.addWidget(self._action_btn)

        self._close_btn = QPushButton("×", self)
        self._close_btn.setObjectName("bannerCloseBtn")
        self._close_btn.setFixedSize(26, 26)
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_btn.setToolTip("Dismiss")
        self._close_btn.pressed.connect(self._on_close_clicked)
        self._close_btn.clicked.connect(self._on_close_clicked)
        row.addWidget(self._close_btn)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.dismiss)
        self.setVisible(False)

    def _on_close_clicked(self) -> None:
        self.dismiss(immediate=True)

    def text(self) -> str:
        return self._text.text()

    def show_message(
        self,
        text: str,
        kind: str = "info",
        action_text: str = "",
        action: Callable[[], None] | None = None,
        timeout_ms: int = 6000,
    ) -> None:
        bg, border, accent, symbol = _BANNER_STYLES.get(kind, _BANNER_STYLES["info"])
        self.setStyleSheet(
            f"QFrame#animatedBanner {{ background-color: {bg}; border: 1px solid {border}; border-radius: 8px; }}"
            "QFrame#animatedBanner QLabel { color: #EFF3FA; }"
            f"QFrame#animatedBanner QPushButton {{ background: transparent; color: {accent}; border: 1px solid {border}; "
            "border-radius: 6px; padding: 3px 10px; font-weight: 600; }}"
            "QFrame#animatedBanner QPushButton:hover { background-color: rgba(255,255,255,0.08); }"
            "QFrame#animatedBanner QPushButton#bannerCloseBtn { border: none; color: #B4C0D4; padding: 0; font-size: 13px; font-weight: 700; }"
            "QFrame#animatedBanner QPushButton#bannerCloseBtn:hover { background-color: rgba(255,255,255,0.14); color: #FFFFFF; }"
        )
        self._icon.setText(symbol)
        self._icon.setStyleSheet(f"color: {accent}; font-weight: 700; font-size: 11pt; border: none; background: transparent;")
        self._text.setText(text)
        self._action = action
        self._action_btn.setText(action_text)
        self._action_btn.setVisible(bool(action_text and action))

        if prev := getattr(self, "_motion_fade", None):
            prev.stop()
        _keep(self, "_motion_fade", None)
        self.setMaximumHeight(16777215)
        eff = self.graphicsEffect()
        if isinstance(eff, QGraphicsOpacityEffect):
            eff.setOpacity(1.0)

        was_visible = self.isVisible()
        self.setVisible(True)
        if not was_visible:
            self._slide_open()

        self._hide_timer.stop()
        if timeout_ms > 0:
            self._hide_timer.start(timeout_ms)

    def _slide_open(self) -> None:
        if not animations_enabled():
            return
        target = max(self.sizeHint().height(), 42)
        effect = _opacity_effect(self)
        group = QParallelAnimationGroup(self)

        grow = QPropertyAnimation(self, b"maximumHeight", self)
        grow.setDuration(220)
        grow.setStartValue(0)
        grow.setEndValue(target)
        grow.setEasingCurve(QEasingCurve.Type.OutCubic)

        fade = QPropertyAnimation(effect, b"opacity", self)
        fade.setDuration(220)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)

        group.addAnimation(grow)
        group.addAnimation(fade)
        group.finished.connect(self._finish_open)
        _keep(self, "_motion_fade", group)
        group.start()

    def _finish_open(self) -> None:
        self.setMaximumHeight(16777215)
        effect = self.graphicsEffect()
        if isinstance(effect, QGraphicsOpacityEffect):
            effect.setOpacity(1.0)

    def dismiss(self, immediate: bool = False) -> None:
        self._hide_timer.stop()
        if prev := getattr(self, "_motion_fade", None):
            prev.stop()
        if not self.isVisible():
            return
        if immediate or not animations_enabled():
            self._finish_hide()
            return
        effect = _opacity_effect(self)
        anim = QPropertyAnimation(effect, b"opacity", self)
        anim.setDuration(200)
        anim.setStartValue(effect.opacity())
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.Type.InCubic)
        anim.finished.connect(self._finish_hide)
        _keep(self, "_motion_fade", anim)
        anim.start()
        QTimer.singleShot(240, self._finish_hide)

    def _finish_hide(self) -> None:
        self.setVisible(False)
        self.setMaximumHeight(16777215)
        eff = self.graphicsEffect()
        if isinstance(eff, QGraphicsOpacityEffect):
            eff.setOpacity(1.0)

    def _run_action(self) -> None:
        cb = self._action
        self.dismiss()
        if cb is not None:
            cb()


def slide_toggle(
    widget: QWidget,
    visible: bool,
    duration: int = 220,
    animate: bool | None = None,
) -> None:
    """Smoothly expand or collapse a widget's height and opacity."""
    should_animate = animations_enabled() if animate is None else bool(animate and animations_enabled())
    _keep(widget, "_motion_slide", None)

    if not should_animate or duration <= 0:
        widget.setMaximumHeight(16777215)
        if isinstance(widget.graphicsEffect(), QGraphicsOpacityEffect):
            widget.setGraphicsEffect(None)
        widget.setVisible(visible)
        return

    effect = _opacity_effect(widget)
    if visible:
        widget.setVisible(True)
        widget.setMaximumHeight(16777215)
        target_h = max(widget.sizeHint().height(), widget.height(), 48)
        start_h, end_h = 0, target_h
        start_op, end_op = 0.0, 1.0
    else:
        if not widget.isVisible():
            return
        start_h, end_h = max(widget.height(), widget.sizeHint().height(), 1), 0
        start_op, end_op = 1.0, 0.0

    group = QParallelAnimationGroup(widget)
    grow = QPropertyAnimation(widget, b"maximumHeight", widget)
    grow.setDuration(duration)
    grow.setStartValue(start_h)
    grow.setEndValue(end_h)
    grow.setEasingCurve(QEasingCurve.Type.OutCubic)

    fade = QPropertyAnimation(effect, b"opacity", widget)
    fade.setDuration(duration)
    fade.setStartValue(start_op)
    fade.setEndValue(end_op)
    fade.setEasingCurve(QEasingCurve.Type.OutCubic)

    group.addAnimation(grow)
    group.addAnimation(fade)

    def _done() -> None:
        if not visible:
            widget.setVisible(False)
        widget.setMaximumHeight(16777215)
        if isinstance(widget.graphicsEffect(), QGraphicsOpacityEffect):
            widget.setGraphicsEffect(None)

    group.finished.connect(_done)
    _keep(widget, "_motion_slide", group)
    group.start()


def make_vector_icon(name: str, color_hex: str = "#F3F4F6", size: int = 16) -> QIcon:
    """Render a crisp 2x-HiDPI QPainter vector icon without external SVG assets."""
    px = QPixmap(size * 2, size * 2)
    px.setDevicePixelRatio(2.0)
    px.fill(Qt.GlobalColor.transparent)

    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    if size != 16:
        scale = size / 16.0
        p.scale(scale, scale)

    col = QColor(color_hex)
    pen = QPen(col, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)

    if name == "home":
        path = QPainterPath()
        path.moveTo(2.0, 7.5)
        path.lineTo(8.0, 2.2)
        path.lineTo(14.0, 7.5)
        path.moveTo(4.0, 6.5)
        path.lineTo(4.0, 13.5)
        path.lineTo(12.0, 13.5)
        path.lineTo(12.0, 6.5)
        p.drawPath(path)
    elif name == "layers":
        p.drawPolygon([QPointF(8.0, 2.0), QPointF(14.0, 5.2), QPointF(8.0, 8.4), QPointF(2.0, 5.2)])
        p.drawPolyline([QPointF(2.0, 8.2), QPointF(8.0, 11.4), QPointF(14.0, 8.2)])
        p.drawPolyline([QPointF(2.0, 11.2), QPointF(8.0, 14.4), QPointF(14.0, 11.2)])
    elif name == "rules":
        p.drawLine(QPointF(2, 4), QPointF(14, 4))
        p.drawLine(QPointF(2, 8), QPointF(14, 8))
        p.drawLine(QPointF(2, 12), QPointF(14, 12))
        p.setBrush(QColor("#1C1C1E"))
        p.drawEllipse(QPointF(6.0, 4.0), 1.9, 1.9)
        p.drawEllipse(QPointF(10.5, 8.0), 1.9, 1.9)
        p.drawEllipse(QPointF(5.5, 12.0), 1.9, 1.9)
    elif name == "settings":
        p.drawEllipse(QPointF(8.0, 8.0), 2.8, 2.8)
        for i in range(8):
            angle = i * (math.pi / 4.0)
            p.drawLine(
                QPointF(8.0 + math.cos(angle) * 4.4, 8.0 + math.sin(angle) * 4.4),
                QPointF(8.0 + math.cos(angle) * 6.3, 8.0 + math.sin(angle) * 6.3),
            )
    elif name in ("bolt", "sparkle"):
        path = QPainterPath()
        path.moveTo(9.2, 1.5)
        path.lineTo(3.5, 9.0)
        path.lineTo(8.0, 9.0)
        path.lineTo(6.8, 14.5)
        path.lineTo(12.5, 7.0)
        path.lineTo(8.0, 7.0)
        path.closeSubpath()
        p.setBrush(col)
        p.drawPath(path)
    elif name == "undo":
        path = QPainterPath()
        path.moveTo(3.5, 6.0)
        path.lineTo(9.5, 6.0)
        path.arcTo(QRectF(6.5, 6.0, 6.0, 6.0), 90, -180)
        path.lineTo(5.0, 12.0)
        p.drawPath(path)
        p.drawPolyline([QPointF(6.0, 3.5), QPointF(3.2, 6.0), QPointF(6.0, 8.5)])
    elif name == "folder":
        path = QPainterPath()
        path.moveTo(2.0, 4.5)
        path.lineTo(2.0, 13.0)
        path.lineTo(14.0, 13.0)
        path.lineTo(14.0, 5.5)
        path.lineTo(8.2, 5.5)
        path.lineTo(6.8, 3.5)
        path.lineTo(2.0, 3.5)
        path.closeSubpath()
        p.drawPath(path)
    elif name == "cube":
        p.drawPolygon([QPointF(8, 2), QPointF(13.5, 5), QPointF(8, 8), QPointF(2.5, 5)])
        p.drawLine(QPointF(2.5, 5), QPointF(2.5, 11))
        p.drawLine(QPointF(13.5, 5), QPointF(13.5, 11))
        p.drawLine(QPointF(8, 8), QPointF(8, 14))
        p.drawLine(QPointF(2.5, 11), QPointF(8, 14))
        p.drawLine(QPointF(13.5, 11), QPointF(8, 14))
    elif name == "play":
        p.setBrush(col)
        p.drawPolygon([QPointF(4.5, 3.0), QPointF(13.0, 8.0), QPointF(4.5, 13.0)])
    elif name == "pause":
        p.setBrush(col)
        p.drawRoundedRect(QRectF(4.0, 3.2, 2.8, 9.6), 1.0, 1.0)
        p.drawRoundedRect(QRectF(9.2, 3.2, 2.8, 9.6), 1.0, 1.0)
    elif name == "help":
        p.drawEllipse(QPointF(8.0, 8.0), 6.0, 6.0)
        arc = QPainterPath()
        arc.moveTo(6.2, 6.0)
        arc.cubicTo(6.2, 4.3, 9.8, 4.3, 9.8, 6.2)
        arc.cubicTo(9.8, 7.6, 8.0, 7.8, 8.0, 9.4)
        p.drawPath(arc)
        p.setBrush(col)
        p.drawEllipse(QPointF(8.0, 11.6), 0.7, 0.7)
    elif name == "plus":
        p.drawLine(QPointF(8.0, 3.0), QPointF(8.0, 13.0))
        p.drawLine(QPointF(3.0, 8.0), QPointF(13.0, 8.0))
    elif name == "delete":
        p.drawLine(QPointF(3.5, 4.5), QPointF(12.5, 4.5))
        p.drawLine(QPointF(6.0, 4.5), QPointF(6.5, 2.8))
        p.drawLine(QPointF(6.5, 2.8), QPointF(9.5, 2.8))
        p.drawLine(QPointF(9.5, 2.8), QPointF(10.0, 4.5))
        p.drawRoundedRect(QRectF(4.5, 4.5, 7.0, 8.8), 1.2, 1.2)
    elif name == "edit":
        p.drawPolygon([QPointF(3.0, 13.0), QPointF(3.5, 10.2), QPointF(10.8, 3.0), QPointF(13.0, 5.2), QPointF(5.8, 12.5)])
    elif name == "up":
        p.drawLine(QPointF(8.0, 13.0), QPointF(8.0, 3.5))
        p.drawPolyline([QPointF(4.2, 7.2), QPointF(8.0, 3.2), QPointF(11.8, 7.2)])
    elif name == "down":
        p.drawLine(QPointF(8.0, 3.0), QPointF(8.0, 12.5))
        p.drawPolyline([QPointF(4.2, 8.8), QPointF(8.0, 12.8), QPointF(11.8, 8.8)])
    elif name == "arrow_right":
        p.drawLine(QPointF(2.5, 8.0), QPointF(13.0, 8.0))
        p.drawPolyline([QPointF(9.2, 4.2), QPointF(13.2, 8.0), QPointF(9.2, 11.8)])
    elif name == "arrow_left":
        p.drawLine(QPointF(13.5, 8.0), QPointF(3.0, 8.0))
        p.drawPolyline([QPointF(6.8, 4.2), QPointF(2.8, 8.0), QPointF(6.8, 11.8)])
    elif name == "check":
        p.drawPolyline([QPointF(3.0, 8.5), QPointF(6.5, 12.0), QPointF(13.2, 4.2)])
    elif name == "refresh":
        path = QPainterPath()
        path.arcMoveTo(QRectF(3.0, 3.0, 10.0, 10.0), 40)
        path.arcTo(QRectF(3.0, 3.0, 10.0, 10.0), 40, 290)
        p.drawPath(path)
        p.drawPolyline([QPointF(10.5, 3.0), QPointF(13.0, 5.2), QPointF(9.8, 6.2)])

    p.end()
    return QIcon(px)


class AnimatedRibbon(QFrame):
    """Top command ribbon container with smooth slide/fade entrance support."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TopStatusBar")
        self._reveal_started = False

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._reveal_started and animations_enabled() and not os.environ.get("PYTEST_CURRENT_TEST"):
            self._reveal_started = True
            fade_in(self, duration=240)


class InteractiveRibbonBtn(QPushButton):
    """QPushButton with smooth hover sheen animation for the command ribbon."""

    def __init__(
        self,
        text: str = "",
        icon: QIcon | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        if icon is not None:
            self.setIcon(icon)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hover_prog = 0.0
        self._anim = QPropertyAnimation(self, b"hoverProg", self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def get_hover(self) -> float:
        return self._hover_prog

    def set_hover(self, val: float) -> None:
        self._hover_prog = float(val)
        self.update()

    hoverProg = Property(float, get_hover, set_hover)

    def enterEvent(self, event) -> None:
        if animations_enabled() and self.isEnabled():
            self._anim.stop()
            self._anim.setStartValue(self._hover_prog)
            self._anim.setEndValue(1.0)
            self._anim.start()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if animations_enabled():
            self._anim.stop()
            self._anim.setStartValue(self._hover_prog)
            self._anim.setEndValue(0.0)
            self._anim.start()
        else:
            self._hover_prog = 0.0
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self._hover_prog > 0.01 and self.isEnabled():
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, int(20 * self._hover_prog)))
            p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 8, 8)
            p.end()


class ElidedLabel(QLabel):
    """A one-line label that shortens its text with "..." instead of widening its row.

    The full text is kept (``full_text``) and shown as the tooltip while shortened.
    """

    def __init__(
        self,
        text: str = "",
        parent: QWidget | None = None,
        *,
        mode: Qt.TextElideMode = Qt.TextElideMode.ElideRight,
        tooltip: str = "",
    ) -> None:
        super().__init__(parent)
        self._full = ""
        self._mode = mode
        self._base_tooltip = tooltip
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(40)
        self.setText(text)

    def full_text(self) -> str:
        return self._full

    def text(self) -> str:  # noqa: D102 (the whole text, not the shortened one on screen)
        return self._full

    def setText(self, text: str) -> None:  # noqa: N802 (Qt name)
        self._full = str(text)
        self._elide()

    def resizeEvent(self, event) -> None:  # noqa: ANN001
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        shown = self.fontMetrics().elidedText(self._full, self._mode, max(0, self.width()))
        super().setText(shown)
        self.setToolTip(self._full if shown != self._full else self._base_tooltip)


class CollapsibleRibbonBtn(InteractiveRibbonBtn):
    """A ribbon button that can show only its icon, for a narrow window.

    Its owner calls ``set_collapsed`` as the window width crosses a threshold; the
    words stay in the tooltip. Its minimum width is always the icon-only width, so
    the row never forces the window wider than that.
    """

    ICON_ONLY_WIDTH = 40

    def __init__(self, text: str = "", icon: QIcon | None = None, parent: QWidget | None = None) -> None:
        super().__init__(text, icon, parent)
        self._full_text = text
        self._collapsed = False
        self.setMinimumWidth(self.ICON_ONLY_WIDTH)
        self._own_tooltip = not self.toolTip()
        if self._own_tooltip:
            self.setToolTip(text)

    def full_text(self) -> str:
        return getattr(self, "_full_text", self.text())

    def is_collapsed(self) -> bool:
        return bool(getattr(self, "_collapsed", False))

    def setText(self, text: str) -> None:  # noqa: N802 (Qt name)
        self._full_text = text
        super().setText("" if self.is_collapsed() else text)
        if getattr(self, "_own_tooltip", False):
            super().setToolTip(text)

    def setToolTip(self, text: str) -> None:  # noqa: N802 (Qt name)
        self._own_tooltip = False
        super().setToolTip(text)

    def set_collapsed(self, collapsed: bool) -> None:
        collapsed = bool(collapsed)
        if collapsed == self.is_collapsed():
            return
        self._collapsed = collapsed
        QPushButton.setText(self, "" if collapsed else self._full_text)
        self.updateGeometry()


class SortyMascotWidget(QWidget):
    """Calm Apple-refined folder companion mascot ('Sorty') — click for tips & cute animation."""

    clicked = Signal(str)
    easter_egg_triggered = Signal()

    POSES = ("smart", "waving", "safe", "protective", "celebrating", "happy", "sleeping", "party")
    SORTY_TIPS: tuple[str, ...] = (
        "Open the Folder Architecture tab to add layers (tiers), pick premade folders, or design custom rule folders.",
        "Clicked 'Apply & Shift Files'? Don't worry — Undo always puts shifted files and folders right back.",
        "Upgrading from Download Sorter v2.8? Use 'Carry Over v2.8 / Setup...' to bring all rules, type folders, and holds over.",
        "Double-click any folder card on the Folder Architecture canvas to edit its extensions, keywords, or websites.",
        "Prefer simple folders? Switch Blueprint to 'Minimalist 1-Tier' or remove extra tiers anytime.",
        "New .3MF print files stay safe in Downloads for your slicer until their hold period ends — or click Move Held .3MF.",
        "Top-right Side Ribbons pause their countdown timer whenever you hover over them so you never miss Undo.",
        "Want custom priority? Tier 3 specific rules automatically match before Tier 2 subfolders and Tier 1 categories.",
        "Export your Folder Architecture & Custom Rules to JSON in Settings to share or back up your setup.",
        "Only loose top-level files and managed category folders shift — your private subfolders stay 100% untouched.",
        "Psst... rapid-click me 5 times (or type 'sorty' in the rule tester) for a secret surprise!",
    )

    def __init__(self, size: int = 64, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pose = "smart"
        self._resting_pose = "smart"
        self._tick = 0
        self._blinking = False
        self._pose_prog = 1.0
        self._cute_prog = 0.0
        self._party_mode = False
        self._party_phase = 0.0
        self._click_timestamps: list[float] = []
        self._tip_index = -1
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._pose_anim = QVariantAnimation(self)
        self._pose_anim.setDuration(350)
        self._pose_anim.setStartValue(0.0)
        self._pose_anim.setEndValue(1.0)
        self._pose_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._pose_anim.valueChanged.connect(self._on_pose_anim)

        self._cute_anim = QVariantAnimation(self)
        self._cute_anim.setDuration(650)
        self._cute_anim.setStartValue(0.0)
        self._cute_anim.setEndValue(1.0)
        self._cute_anim.setEasingCurve(QEasingCurve.Type.OutBack)
        self._cute_anim.valueChanged.connect(self._on_cute_anim)
        self._cute_anim.finished.connect(self._on_cute_done)

        self._party_anim = QVariantAnimation(self)
        self._party_anim.setDuration(850)
        self._party_anim.setStartValue(0.0)
        self._party_anim.setEndValue(1.0)
        self._party_anim.setLoopCount(-1)
        self._party_anim.valueChanged.connect(self._on_party_anim)

        # Calm natural blink every 4.5s (no continuous 30fps bouncing loop)
        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(4500)
        self._blink_timer.timeout.connect(self._trigger_blink)

        self._blink_end_timer = QTimer(self)
        self._blink_end_timer.setSingleShot(True)
        self._blink_end_timer.setInterval(120)
        self._blink_end_timer.timeout.connect(self._end_blink)

        self._timer = self._blink_timer  # alias for lifecycle compatibility

        self._celebrate_timer = QTimer(self)
        self._celebrate_timer.setSingleShot(True)
        self._celebrate_timer.timeout.connect(self._end_celebration)

        self._party_timer = QTimer(self)
        self._party_timer.setSingleShot(True)
        self._party_timer.timeout.connect(self._end_party_mode)

    @property
    def pose(self) -> str:
        return self._pose

    @property
    def state(self) -> str:
        return self._pose

    def set_pose(self, pose: str) -> None:
        norm = str(pose).lower()
        if norm in self.POSES:
            changed = norm != self._pose
            self._pose = norm
            if norm not in ("celebrating", "happy", "party"):
                self._resting_pose = norm
            if changed and animations_enabled() and not os.environ.get("PYTEST_CURRENT_TEST"):
                self._pose_anim.stop()
                self._pose_prog = 0.0
                self._pose_anim.start()
            else:
                self._pose_prog = 1.0
                self.update()

    def set_state(self, state: str, expression: str | None = None) -> None:
        self.set_pose(state)

    def set_step(self, step_index: int) -> None:
        """Sync mascot pose to walkthrough step index (0=waving, 1=safe, 2=celebrating)."""
        if step_index == 0:
            self.set_pose("waving")
        elif step_index == 1:
            self.set_pose("safe")
        else:
            self.set_pose("celebrating")

    def celebrate(self, duration_ms: int = 2400) -> None:
        """Temporarily switch Sorty to celebrating pose before restoring resting pose."""
        if self._pose != "sleeping" and not self._party_mode:
            self._pose = "celebrating"
            self._pose_prog = 1.0
            self.update()
            if animations_enabled() and not os.environ.get("PYTEST_CURRENT_TEST"):
                self._celebrate_timer.start(max(400, duration_ms))

    def start_party_mode(self, duration_ms: int = 5000) -> None:
        """Secret Sorty Party Mode Easter Egg with crown, shades, and dance bounce."""
        self._party_mode = True
        self._pose = "party"
        self._cute_prog = 0.45
        if animations_enabled() and not os.environ.get("PYTEST_CURRENT_TEST"):
            self._party_anim.stop()
            self._party_anim.start()
            self._party_timer.start(max(800, int(duration_ms)))
        else:
            self._party_phase = 0.35
            self.update()
        self.easter_egg_triggered.emit()

    def _on_party_anim(self, value: object) -> None:
        self._party_phase = float(value)  # type: ignore[arg-type]
        self.update()

    def _end_party_mode(self) -> None:
        self._party_mode = False
        self._party_anim.stop()
        self._party_phase = 0.0
        self._cute_prog = 0.0
        self._pose = self._resting_pose
        self.update()

    def _on_pose_anim(self, value: object) -> None:
        self._pose_prog = float(value)  # type: ignore[arg-type]
        self.update()

    def _on_cute_anim(self, value: object) -> None:
        self._cute_prog = float(value)  # type: ignore[arg-type]
        self.update()

    def _on_cute_done(self) -> None:
        if not self._party_mode:
            self._cute_prog = 0.0
            if self._pose == "happy":
                self._pose = self._resting_pose
            self.update()

    def trigger_cute_tip(self) -> str:
        """Play a cute hop/sparkle animation and cycle through helpful Sorty tips."""
        now = time.monotonic()
        self._click_timestamps = [t for t in self._click_timestamps if (now - t) <= 4.0]
        self._click_timestamps.append(now)

        if len(self._click_timestamps) >= 5:
            self._click_timestamps.clear()
            secret = "SECRET UNLOCKED: Sorty Party Mode! Deal with it!"
            self.start_party_mode()
            self.clicked.emit(secret)
            return secret

        self._tip_index = (self._tip_index + 1) % len(self.SORTY_TIPS)
        tip = self.SORTY_TIPS[self._tip_index]
        if self._pose != "sleeping" and not self._party_mode:
            self._pose = "happy"
        if animations_enabled() and not os.environ.get("PYTEST_CURRENT_TEST"):
            self._cute_anim.stop()
            self._cute_prog = 0.01
            self._cute_anim.start()
        else:
            self._cute_prog = 0.45
            self.update()
        self.clicked.emit(tip)
        return tip

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.trigger_cute_tip()
        super().mousePressEvent(event)

    def _step(self) -> None:
        self._tick = (self._tick + 1) % 3000
        self.update()

    def _trigger_blink(self) -> None:
        if self._pose != "sleeping" and not self._party_mode:
            self._blinking = True
            self.update()
            self._blink_end_timer.start()

    def _end_blink(self) -> None:
        self._blinking = False
        self.update()

    def _end_celebration(self) -> None:
        if not self._party_mode:
            self._pose = self._resting_pose
            self.update()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if animations_enabled() and not self._blink_timer.isActive():
            self._blink_timer.start()

    def hideEvent(self, event) -> None:
        if self._blink_timer.isActive():
            self._blink_timer.stop()
        if self._blink_end_timer.isActive():
            self._blink_end_timer.stop()
        self._pose_anim.stop()
        self._cute_anim.stop()
        self._party_anim.stop()
        super().hideEvent(event)

    def to_pixmap(self, width: int = 64, height: int = 64) -> QPixmap:
        px = QPixmap(width, height)
        px.fill(Qt.GlobalColor.transparent)
        painter = QPainter(px)
        try:
            self._draw(painter, width, height)
        finally:
            painter.end()
        return px

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        try:
            self._draw(painter, self.width(), self.height())
        finally:
            painter.end()

    def _draw(self, p: QPainter, width: int, height: int) -> None:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.save()
        p.scale(width / 64.0, height / 64.0)

        # Static calm Y at rest; gentle 2px settle on pose change; playful hop+tilt on click/party
        settle = math.sin(self._pose_prog * math.pi) * -2.0 if self._pose_prog < 1.0 else 0.0
        if self._party_mode:
            dance_y = math.sin(self._party_phase * math.pi * 2.0) * -4.2
            dance_rot = math.sin(self._party_phase * math.pi * 2.0) * 8.5
            p.translate(32.0, 32.0 + dance_y)
            p.rotate(dance_rot)
            p.translate(-32.0, -32.0)
        elif 0.0 < self._cute_prog < 1.0:
            hop = math.sin(max(0.0, min(1.0, self._cute_prog)) * math.pi) * -6.0
            tilt = math.sin(max(0.0, min(1.0, self._cute_prog)) * math.pi * 2.0) * 6.5
            p.translate(32.0, 32.0 + hop)
            p.rotate(tilt)
            p.translate(-32.0, -32.0)
        else:
            p.translate(0.0, settle)

        # Back folder tab & body (Apple Finder Blue)
        p.setPen(QPen(QColor("#0055B3"), 1.5))
        p.setBrush(QColor("#0071E3"))
        back = QPainterPath()
        back.moveTo(10, 22)
        back.lineTo(10, 17)
        back.quadTo(10, 14, 14, 14)
        back.lineTo(26, 14)
        back.quadTo(29, 14, 31, 18)
        back.lineTo(52, 18)
        back.quadTo(55, 18, 55, 22)
        back.lineTo(55, 50)
        back.lineTo(10, 50)
        back.closeSubpath()
        p.drawPath(back)

        # Peeking crisp white doc sheet
        p.setPen(QPen(QColor("#D1D1D6"), 1.0))
        p.setBrush(QColor("#F5F5F7"))
        p.drawRoundedRect(QRectF(20, 11, 24, 16), 3, 3)

        # Front folder face body
        p.setPen(QPen(QColor("#64D2FF"), 1.6))
        p.setBrush(QColor("#0A84FF"))
        p.drawRoundedRect(QRectF(8, 25, 48, 29), 8, 8)

        # Subtle highlight blush
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 45))
        p.drawEllipse(QRectF(13, 41, 6, 4))
        p.drawEllipse(QRectF(45, 41, 6, 4))

        # Eyes with quiet natural blink (~every 4.5s)
        if self._pose == "sleeping" or self._blinking:
            p.setPen(QPen(QColor("#FFFFFF"), 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawArc(QRectF(19, 36, 8, 5), 180 * 16, 180 * 16)
            p.drawArc(QRectF(37, 36, 8, 5), 180 * 16, 180 * 16)
        elif self._pose in ("celebrating", "happy"):
            p.setPen(QPen(QColor("#FFFFFF"), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.setBrush(Qt.BrushStyle.NoBrush)
            for cx in (23, 41):
                arc = QPainterPath()
                arc.moveTo(cx - 4, 40)
                arc.quadTo(cx, 34, cx + 4, 40)
                p.drawPath(arc)
        else:
            p.setBrush(QColor("#FFFFFF"))
            p.drawEllipse(QPointF(23.5, 38.0), 2.8, 3.2)
            p.drawEllipse(QPointF(40.5, 38.0), 2.8, 3.2)
            p.setBrush(QColor("#002B5C"))
            p.drawEllipse(QPointF(24.2, 38.3), 1.2, 1.4)
            p.drawEllipse(QPointF(41.2, 38.3), 1.2, 1.4)

        # Smile mouth
        p.setPen(QPen(QColor("#FFFFFF"), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.setBrush(Qt.BrushStyle.NoBrush)
        if self._pose != "sleeping":
            mouth = QPainterPath()
            mouth.moveTo(28, 45)
            mouth.quadTo(32, 50 if self._pose in ("celebrating", "happy") else 48.5, 36, 45)
            p.drawPath(mouth)
        else:
            p.drawEllipse(QPointF(32, 46), 1.8, 1.4)

        # Pose overlays
        if self._pose == "smart":
            p.setPen(QPen(QColor("#E5F1FF"), 1.6))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(17, 33, 13, 10), 3, 3)
            p.drawRoundedRect(QRectF(34, 33, 13, 10), 3, 3)
            p.drawLine(QPointF(30, 37.5), QPointF(34, 37.5))
        elif self._pose == "waving":
            angle = 10.0 + (math.sin(self._pose_prog * math.pi) * 14.0 if self._pose_prog < 1.0 else 0.0)
            p.save()
            p.translate(54, 39)
            p.rotate(angle)
            p.setPen(QPen(QColor("#64D2FF"), 3.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(0, 0), QPointF(6.5, -9.0))
            p.restore()
        elif self._pose in ("safe", "protective"):
            shield = QPainterPath()
            shield.moveTo(47, 35)
            shield.lineTo(58, 38)
            shield.lineTo(58, 46)
            shield.quadTo(52.5, 55, 47, 57)
            shield.quadTo(41.5, 55, 36, 46)
            shield.lineTo(36, 38)
            shield.closeSubpath()
            p.setPen(QPen(QColor("#0A6E31"), 1.4))
            p.setBrush(QColor("#30D158"))
            p.drawPath(shield)
            p.setPen(QPen(QColor("#FFFFFF"), 1.9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.drawPolyline([QPointF(42.5, 46.5), QPointF(45.5, 49.5), QPointF(51.5, 42.5)])
        elif self._pose in ("celebrating", "happy"):
            r = 3.8 * (0.75 + 0.25 * self._pose_prog)
            p.setPen(QPen(QColor("#FFD60A"), 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            for cx, cy in ((8.5, 13.0), (54.0, 14.0), (58.0, 34.0)):
                p.drawLine(QPointF(cx - r, cy), QPointF(cx + r, cy))
                p.drawLine(QPointF(cx, cy - r), QPointF(cx, cy + r))
        elif self._pose == "sleeping":
            p.setOpacity(0.75)
            p.setPen(QPen(QColor("#8E8E93"), 1.5))
            p.drawText(QRectF(44, 15, 16, 16), Qt.AlignmentFlag.AlignCenter, "z")

        if self._party_mode:
            # Golden 3-point Party Crown on Sorty's head
            crown = QPainterPath()
            crown.moveTo(19, 13)
            crown.lineTo(17, 4)
            crown.lineTo(25, 8.5)
            crown.lineTo(32, 2.5)
            crown.lineTo(39, 8.5)
            crown.lineTo(47, 4)
            crown.lineTo(45, 13)
            crown.closeSubpath()
            p.setPen(QPen(QColor("#B45309"), 1.3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.setBrush(QColor("#FFD60A"))
            p.drawPath(crown)
            for jx, jy, jcol in ((22.5, 10.5, "#FF375F"), (32.0, 9.5, "#64D2FF"), (41.5, 10.5, "#30D158")):
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(jcol))
                p.drawEllipse(QPointF(jx, jy), 1.5, 1.5)

            # Cool "Deal With It" pixel-art dark sunglasses across Sorty's eyes
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#111113"))
            p.drawRoundedRect(QRectF(14.5, 34.0, 15.0, 7.5), 1.5, 1.5)
            p.drawRoundedRect(QRectF(34.5, 34.0, 15.0, 7.5), 1.5, 1.5)
            p.drawRect(QRectF(29.0, 35.0, 6.0, 2.5))
            p.drawRect(QRectF(10.0, 35.0, 5.0, 2.0))
            p.drawRect(QRectF(49.0, 35.0, 5.0, 2.0))
            # Crisp diagonal pixel glint across each lens
            p.setPen(QPen(QColor("#FFFFFF"), 1.3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(17.0, 36.0), QPointF(21.0, 39.5))
            p.drawLine(QPointF(21.5, 35.8), QPointF(23.8, 37.9))
            p.drawLine(QPointF(37.0, 36.0), QPointF(41.0, 39.5))
            p.drawLine(QPointF(41.5, 35.8), QPointF(43.8, 37.9))

            # Floating colorful party confetti around Sorty
            confetti_pts = (
                (8.0, 11.0, "#FF375F"),
                (56.0, 12.0, "#30D158"),
                (6.0, 31.0, "#FFD60A"),
                (58.0, 29.0, "#64D2FF"),
                (12.0, 53.0, "#BF5AF2"),
                (52.0, 54.0, "#FF9F0A"),
            )
            for idx, (cx, cy, hx) in enumerate(confetti_pts):
                dy_off = math.sin((self._party_phase * math.pi * 2.0) + idx) * 2.4
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(hx))
                p.drawEllipse(QPointF(cx, cy + dy_off), 2.1, 2.1)

        if 0.0 < self._cute_prog < 1.0 and not self._party_mode:
            t_cut = max(0.0, min(1.0, self._cute_prog))
            hx, hy = 52.0, 18.0 - t_cut * 10.0
            heart = QPainterPath()
            heart.moveTo(hx, hy + 4.5)
            heart.cubicTo(hx - 6.0, hy + 0.5, hx - 4.0, hy - 4.5, hx, hy - 1.2)
            heart.cubicTo(hx + 4.0, hy - 4.5, hx + 6.0, hy + 0.5, hx, hy + 4.5)
            p.setOpacity(max(0.0, 1.0 - max(0.0, (t_cut - 0.55) / 0.45)))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#FF375F"))
            p.drawPath(heart)

        p.restore()


SorterMascotWidget = SortyMascotWidget


class ConfettiOverlayWidget(QWidget):
    """Click-through translucent confetti celebration burst overlay for Easter Eggs."""

    COLORS = ("#0A84FF", "#30D158", "#FFD60A", "#FF375F", "#BF5AF2", "#64D2FF", "#FF9F0A")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._pieces: list[dict[str, Any]] = []
        self._elapsed_ms = 0
        self._duration_ms = 2600
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._tick)
        self.hide()

    def burst(self, duration_ms: int = 2600) -> None:
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(parent.rect())
        w = max(480, self.width())
        self._pieces = []
        rng = random.Random(42)
        for i in range(44):
            self._pieces.append(
                {
                    "x": rng.uniform(24.0, float(w - 24)),
                    "y": rng.uniform(8.0, 110.0) if not animations_enabled() else rng.uniform(-30.0, 26.0),
                    "vx": rng.uniform(-1.9, 1.9),
                    "vy": rng.uniform(2.8, 6.2),
                    "w": rng.uniform(6.0, 11.0),
                    "h": rng.uniform(4.0, 7.0),
                    "rot": rng.uniform(0.0, 360.0),
                    "vrot": rng.uniform(-10.0, 10.0),
                    "color": self.COLORS[i % len(self.COLORS)],
                }
            )
        self._elapsed_ms = 0
        self._duration_ms = max(600, int(duration_ms))
        self.show()
        self.raise_()
        if animations_enabled() and not os.environ.get("PYTEST_CURRENT_TEST"):
            self._timer.start()
        self.update()

    def _tick(self) -> None:
        self._elapsed_ms += 30
        h = max(320, self.height())
        for piece in self._pieces:
            piece["x"] += piece["vx"]
            piece["y"] += piece["vy"]
            piece["vy"] = min(8.5, piece["vy"] + 0.08)
            piece["rot"] = (piece["rot"] + piece["vrot"]) % 360.0
            if piece["y"] > h + 20:
                piece["y"] = h + 20
        self.update()
        if self._elapsed_ms >= self._duration_ms:
            self._timer.stop()
            self.hide()

    def paintEvent(self, event) -> None:  # noqa: ANN001
        if not self._pieces:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        fade = max(0.0, 1.0 - max(0.0, (self._elapsed_ms - self._duration_ms * 0.65) / max(1.0, self._duration_ms * 0.35)))
        p.setOpacity(fade)
        p.setPen(Qt.PenStyle.NoPen)
        for piece in self._pieces:
            p.save()
            p.translate(piece["x"], piece["y"])
            p.rotate(piece["rot"])
            p.setBrush(QColor(piece["color"]))
            p.drawRoundedRect(
                QRectF(-piece["w"] / 2.0, -piece["h"] / 2.0, piece["w"], piece["h"]),
                2.0,
                2.0,
            )
            p.restore()
        p.end()


def _sub_progress(prog: float, start: float, end: float) -> float:
    return max(0.0, min(1.0, (prog - start) / max(0.001, end - start)))


class WalkthroughDiagramCanvas(QWidget):
    """Calm Apple-HIG 3-node diagram with finite 450ms OutCubic entrance choreography."""

    STEPS: list[tuple[tuple[str, str, str], tuple[str, str, str], tuple[str, str, str]]] = [
        # Step 0: Top-Level Only
        (
            ("Downloads/", "Watched Root", "#8E8E93"),
            ("Top-Level Files", "Sorted Automatically", "#30D158"),
            ("Subfolders", "Left Untouched", "#0A84FF"),
        ),
        # Step 1: Safe .3MF Hold
        (
            ("model.3mf", "New 3D Print", "#8E8E93"),
            ("Safe Slicer Hold", "Ready for Bambu", "#FF9F0A"),
            ("3D Models/", "Auto-Filed After", "#30D158"),
        ),
        # Step 2: Categorized Routing + Side Ribbon
        (
            ("invoice.pdf", "Matches Rule", "#8E8E93"),
            ("/Documents/PDF", "Auto-Routed", "#0A84FF"),
            ("Top-Right Ribbon", "1-Click Undo", "#69E6AD"),
        ),
    ]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(460, 148)
        self.setFixedHeight(148)
        self._step = 0
        self._anim_progress = 1.0
        self._phase = 1.0

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(450)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim_tick)
        self._anim.finished.connect(self._on_anim_done)

    @property
    def step(self) -> int:
        return self._step

    def set_step(self, step: int) -> None:
        self._step = max(0, min(2, int(step)))
        if animations_enabled() and not os.environ.get("PYTEST_CURRENT_TEST"):
            self._anim.stop()
            self._anim_progress = 0.0
            self._phase = 0.0
            self._anim.start()
        else:
            self._anim_progress = 1.0
            self._phase = 1.0
        self.update()

    def _on_anim_tick(self, value: object) -> None:
        val = float(value)  # type: ignore[arg-type]
        self._anim_progress = val
        self._phase = val
        self.update()

    def _on_anim_done(self) -> None:
        self._anim_progress = 1.0
        self._phase = 1.0
        self.update()

    def _tick(self) -> None:
        self._anim_progress = min(1.0, self._anim_progress + 0.1)
        self._phase = self._anim_progress
        self.update()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if animations_enabled() and not os.environ.get("PYTEST_CURRENT_TEST"):
            self._anim.stop()
            self._anim_progress = 0.0
            self._phase = 0.0
            self._anim.start()

    def hideEvent(self, event) -> None:
        self._anim.stop()
        super().hideEvent(event)

    def _draw_node(
        self,
        p: QPainter,
        cx: float,
        cy: float,
        title: str,
        subtitle: str,
        accent_hex: str,
        local_p: float,
        *,
        mini_ribbon: bool = False,
    ) -> None:
        if local_p <= 0.01:
            return
        p.save()
        p.setOpacity(local_p)
        y_shift = (1.0 - local_p) * 6.0
        node_w = 138.0
        node_h = 62.0
        rect = QRectF(cx - node_w / 2.0, cy - node_h / 2.0 + y_shift, node_w, node_h)

        # Elevated Apple dark card node (#1C1C1E with #2C2C2E 1px hairline)
        p.setPen(QPen(QColor("#2C2C2E"), 1.0))
        p.setBrush(QColor("#1C1C1E"))
        p.drawRoundedRect(rect, 10.0, 10.0)

        if mini_ribbon:
            # Left category stripe + bottom timeout bar representing the Top-Right Side Ribbon
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(accent_hex))
            p.drawRoundedRect(QRectF(rect.left() + 1.0, rect.top() + 8.0, 3.0, node_h - 16.0), 1.5, 1.5)
            bar_col = QColor(accent_hex)
            bar_col.setAlpha(200)
            p.setBrush(bar_col)
            p.drawRoundedRect(QRectF(rect.left() + 6.0, rect.bottom() - 4.0, (node_w - 12.0) * 0.72, 2.5), 1.0, 1.0)
        else:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(accent_hex))
            p.drawEllipse(QPointF(rect.left() + 14.0, rect.top() + 20.0), 3.5, 3.5)

        f_main = p.font()
        f_main.setPointSize(9)
        f_main.setBold(True)
        p.setFont(f_main)
        p.setPen(QColor("#F5F5F7"))
        text_left = rect.left() + (12.0 if mini_ribbon else 23.0)
        p.drawText(
            QRectF(text_left, rect.top() + 9.0, rect.right() - text_left - 8.0, 22.0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            title,
        )

        f_sub = p.font()
        f_sub.setPointSize(8)
        f_sub.setBold(False)
        p.setFont(f_sub)
        p.setPen(QColor("#8E8E93"))
        p.drawText(
            QRectF(rect.left() + 12.0, rect.top() + 33.0, node_w - 22.0, 20.0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            subtitle,
        )
        p.restore()

    def _draw_connector(
        self,
        p: QPainter,
        x1: float,
        x2: float,
        y: float,
        color_hex: str,
        local_p: float,
    ) -> None:
        if local_p <= 0.01:
            return
        p.save()
        cur_x2 = x1 + (x2 - x1) * local_p
        pen = QPen(QColor(color_hex), 1.75, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.drawLine(QPointF(x1, y), QPointF(cur_x2, y))

        if local_p > 0.78:
            p.setOpacity((local_p - 0.78) / 0.22)
            p.drawPolyline(
                [
                    QPointF(cur_x2 - 5.0, y - 4.0),
                    QPointF(cur_x2, y),
                    QPointF(cur_x2 - 5.0, y + 4.0),
                ]
            )
        p.restore()

    def paintEvent(self, event) -> None:  # noqa: ANN001
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w = float(max(self.width(), 460))
        h = float(self.height())
        # Clean borderless recessed Apple well (#141416)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#141416"))
        p.drawRoundedRect(QRectF(0.0, 0.0, w, h), 12.0, 12.0)

        n1, n2, n3 = self.STEPS[self._step]
        cy = h / 2.0
        cx1 = w * 0.175
        cx2 = w * 0.500
        cx3 = w * 0.825

        half_card = 69.0
        gap = 6.0
        self._draw_node(p, cx1, cy, n1[0], n1[1], n1[2], _sub_progress(self._anim_progress, 0.0, 0.45))
        self._draw_connector(
            p,
            cx1 + half_card + gap,
            cx2 - half_card - gap,
            cy,
            n2[2],
            _sub_progress(self._anim_progress, 0.20, 0.55),
        )
        self._draw_node(p, cx2, cy, n2[0], n2[1], n2[2], _sub_progress(self._anim_progress, 0.25, 0.70))
        self._draw_connector(
            p,
            cx2 + half_card + gap,
            cx3 - half_card - gap,
            cy,
            n3[2],
            _sub_progress(self._anim_progress, 0.50, 0.85),
        )
        self._draw_node(
            p,
            cx3,
            cy,
            n3[0],
            n3[1],
            n3[2],
            _sub_progress(self._anim_progress, 0.55, 1.00),
            mini_ribbon=(self._step == 2),
        )


WalkthroughCanvas = WalkthroughDiagramCanvas
