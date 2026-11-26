"""Transparent QPainter Bezier bridge overlay for Sorting Flow."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEasingCurve, QPointF, QRect, Qt, QVariantAnimation
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QAbstractScrollArea, QWidget

from download_sorter.ui.motion import animations_enabled


@dataclass(frozen=True)
class BridgeWire:
    """One visual connection from a left-spine port to a right-tree folder row."""

    source: QWidget
    target: QWidget
    color: str = "#3B82F6"
    dashed: bool = False
    opacity: float = 0.92
    animate_pulse: bool = False


def _scroll_viewport_for(widget: QWidget) -> QWidget | None:
    """Walk up parents to locate the enclosing QAbstractScrollArea viewport, if any."""
    cur = widget.parentWidget()
    while cur is not None:
        if isinstance(cur, QAbstractScrollArea):
            return cur.viewport()
        cur = cur.parentWidget()
    return None


def _edge_visibility_alpha(widget: QWidget, overlay: QWidget, y_in_overlay: float) -> float:
    """Fade wire smoothly to 0.0 as an endpoint scrolls near or outside its scroll viewport."""
    vp = _scroll_viewport_for(widget)
    if vp is None or not vp.isVisible():
        return 1.0
    vp_top = float(overlay.mapFromGlobal(vp.mapToGlobal(vp.rect().topLeft())).y())
    vp_bot = float(overlay.mapFromGlobal(vp.mapToGlobal(vp.rect().bottomLeft())).y())
    if y_in_overlay < vp_top + 2.0 or y_in_overlay > vp_bot - 2.0:
        return 0.0
    fade_band = 24.0
    top_alpha = min(1.0, (y_in_overlay - vp_top) / fade_band)
    bot_alpha = min(1.0, (vp_bot - y_in_overlay) / fade_band)
    return max(0.0, min(top_alpha, bot_alpha))


class BridgeOverlay(QWidget):
    """Paints smooth anti-aliased Bezier wires across the Sorting Flow gutter."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._wires: list[BridgeWire] = []
        self._clip_rect: QRect | None = None
        self._fade_alpha = 1.0
        self._fade_anim = QVariantAnimation(self)
        self._fade_anim.setDuration(180)
        self._fade_anim.setStartValue(0.35)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade_anim.valueChanged.connect(self._on_fade_tick)

    def _on_fade_tick(self, value: object) -> None:
        self._fade_alpha = float(value) if value is not None else 1.0
        self.update()

    def set_wires(
        self,
        wires: list[BridgeWire],
        *,
        clip_rect: QRect | None = None,
        animate_pulse: bool = False,
    ) -> None:
        self._wires = list(wires)
        self._clip_rect = clip_rect
        if animate_pulse and animations_enabled() and any(w.animate_pulse for w in self._wires):
            self._fade_anim.stop()
            self._fade_alpha = 0.35
            self._fade_anim.start()
        else:
            self._fade_alpha = 1.0
            self.update()

    def clear_wires(self) -> None:
        if self._wires:
            self._wires = []
            self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        if not self._wires:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self._clip_rect is not None and self._clip_rect.isValid():
            painter.setClipRect(self._clip_rect)

        for wire in self._wires:
            if not wire.source.isVisible() or not wire.target.isVisible():
                continue

            src_center = self.mapFromGlobal(wire.source.mapToGlobal(wire.source.rect().center()))
            src_right = self.mapFromGlobal(wire.source.mapToGlobal(wire.source.rect().topRight())).x()
            p1 = QPointF(float(src_right - 1), float(src_center.y()))

            tgt_y_offset = min(18, max(8, wire.target.height() // 2))
            tgt_tl = self.mapFromGlobal(wire.target.mapToGlobal(wire.target.rect().topLeft()))
            p2 = QPointF(float(tgt_tl.x() - 2), float(tgt_tl.y() + tgt_y_offset))

            gap = p2.x() - p1.x()
            if gap <= 8:
                continue

            vis_alpha = min(
                _edge_visibility_alpha(wire.source, self, p1.y()),
                _edge_visibility_alpha(wire.target, self, p2.y()),
            )
            eff_opacity = wire.opacity * vis_alpha * (self._fade_alpha if wire.animate_pulse else 1.0)
            if eff_opacity <= 0.02:
                continue

            # Horizontal tangents guarantee crisp S-curve entry/exit with zero kinks
            dx = max(28.0, min(92.0, gap * 0.52 + abs(p2.y() - p1.y()) * 0.06))
            path = QPainterPath(p1)
            path.cubicTo(
                QPointF(p1.x() + dx, p1.y()),
                QPointF(p2.x() - dx, p2.y()),
                p2,
            )

            painter.save()
            painter.setOpacity(eff_opacity)
            base_color = QColor(wire.color)
            lighter = QColor(base_color).lighter(122)

            # Pass 1: Soft ambient glow
            if not wire.dashed:
                glow = QColor(base_color)
                glow.setAlpha(42)
                painter.setPen(QPen(glow, 6.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                painter.drawPath(path)

            # Pass 2: Crisp gradient core stroke
            pen_style = Qt.PenStyle.DashLine if wire.dashed else Qt.PenStyle.SolidLine
            grad = QLinearGradient(p1, p2)
            grad.setColorAt(0.0, lighter)
            grad.setColorAt(1.0, base_color)
            pen = QPen(QBrush(grad), 1.8, pen_style, Qt.PenCapStyle.RoundCap)
            if wire.dashed:
                pen.setDashPattern([4.5, 4.0])
            painter.setPen(pen)
            painter.drawPath(path)

            # Pass 3: Clean vector port rings with canvas cutout border
            ring_border = QPen(QColor("#16171A"), 1.5)
            painter.setPen(ring_border)
            painter.setBrush(base_color)
            painter.drawEllipse(p1, 3.2, 3.2)
            painter.drawEllipse(p2, 3.8, 3.8)
            painter.restore()
