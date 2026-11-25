"""Transparent QPainter Bezier bridge overlay for Sorting Flow."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEasingCurve, QPointF, QRect, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget


@dataclass(frozen=True)
class BridgeWire:
    """One visual connection from a left-spine port to a right-tree folder row."""

    source: QWidget
    target: QWidget
    color: str = "#0A84FF"
    dashed: bool = False
    opacity: float = 0.90
    animate_pulse: bool = False


class BridgeOverlay(QWidget):
    """Paints smooth anti-aliased Bezier wires across the Sorting Flow gutter."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._wires: list[BridgeWire] = []
        self._clip_rect: QRect | None = None
        self._pulse_progress = 1.0
        self._pulse_anim = QVariantAnimation(self)
        self._pulse_anim.setDuration(420)
        self._pulse_anim.setStartValue(0.0)
        self._pulse_anim.setEndValue(1.0)
        self._pulse_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._pulse_anim.valueChanged.connect(self._on_pulse_tick)

    def _on_pulse_tick(self, value: object) -> None:
        self._pulse_progress = float(value) if value is not None else 1.0
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
        if animate_pulse and any(w.animate_pulse for w in self._wires):
            self._pulse_anim.stop()
            self._pulse_progress = 0.0
            self._pulse_anim.start()
        else:
            self._pulse_progress = 1.0
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
            src_pt = self.mapFromGlobal(wire.source.mapToGlobal(wire.source.rect().center()))
            src_right = self.mapFromGlobal(wire.source.mapToGlobal(wire.source.rect().topRight())).x()
            p1 = QPointF(float(src_right), float(src_pt.y()))

            tgt_y_offset = min(18, max(8, wire.target.height() // 2))
            tgt_pt = self.mapFromGlobal(wire.target.mapToGlobal(wire.target.rect().topLeft()))
            p2 = QPointF(float(tgt_pt.x() - 2), float(tgt_pt.y() + tgt_y_offset))

            gap = p2.x() - p1.x()
            if gap <= 8:
                continue

            dy = abs(p2.y() - p1.y())
            dx = max(36.0, min(96.0, gap * 0.55 + dy * 0.08))

            path = QPainterPath(p1)
            path.cubicTo(
                QPointF(p1.x() + dx, p1.y()),
                QPointF(p2.x() - dx, p2.y()),
                p2,
            )

            base_color = QColor(wire.color)
            if not wire.dashed:
                glow = QColor(base_color)
                glow.setAlphaF(0.14 * wire.opacity)
                painter.setPen(QPen(glow, 6.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                painter.drawPath(path)

            core = QColor(base_color)
            core.setAlphaF(wire.opacity)
            pen_style = Qt.PenStyle.DashLine if wire.dashed else Qt.PenStyle.SolidLine
            pen = QPen(core, 1.8, pen_style, Qt.PenCapStyle.RoundCap)
            if wire.dashed:
                pen.setDashPattern([4.0, 4.0])
            painter.setPen(pen)
            painter.drawPath(path)

            # Endpoints
            painter.setPen(Qt.PenStyle.NoPen)
            dot_col = QColor(base_color)
            dot_col.setAlphaF(wire.opacity)
            painter.setBrush(dot_col)
            painter.drawEllipse(p1, 2.8, 2.8)
            painter.drawEllipse(p2, 3.8, 3.8)

            # One-shot travel dot during route-change pulse
            if wire.animate_pulse and 0.0 < self._pulse_progress < 1.0:
                pulse_pt = path.pointAtPercent(self._pulse_progress)
                halo = QColor(base_color)
                halo.setAlphaF(0.35)
                painter.setBrush(halo)
                painter.drawEllipse(pulse_pt, 6.0, 6.0)
                painter.setBrush(QColor("#FFFFFF"))
                painter.drawEllipse(pulse_pt, 2.6, 2.6)
