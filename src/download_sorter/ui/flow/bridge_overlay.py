"""Transparent QPainter Bezier bridge overlay for Sorting Flow."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEasingCurve, QPoint, QPointF, QRect, Qt, QVariantAnimation
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QAbstractScrollArea, QWidget
from shiboken6 import isValid as _qt_is_valid

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


def _widget_alive(widget: QWidget | None) -> bool:
    """Return True when ``widget`` is a live, non-deleted C++ QWidget."""
    if widget is None:
        return False
    try:
        return bool(_qt_is_valid(widget))
    except RuntimeError:
        return False


def _scroll_viewport_for(widget: QWidget) -> QWidget | None:
    """Walk up parents to locate the enclosing QAbstractScrollArea viewport, if any."""
    if not _widget_alive(widget):
        return None
    try:
        cur = widget.parentWidget()
        while cur is not None and _widget_alive(cur):
            if isinstance(cur, QAbstractScrollArea):
                vp = cur.viewport()
                return vp if _widget_alive(vp) else None
            cur = cur.parentWidget()
    except RuntimeError:
        return None
    return None


def _edge_visibility_alpha(widget: QWidget, overlay: QWidget, y_in_overlay: float) -> float:
    """Fade wire smoothly to 0.0 as an endpoint scrolls near or outside its scroll viewport."""
    try:
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
    except RuntimeError:
        return 0.0


class BridgeOverlay(QWidget):
    """Paints smooth anti-aliased Bezier wires across the Sorting Flow gutter."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self._wires: list[BridgeWire] = []
        self._drag_source: QWidget | None = None
        self._drag_cursor_global: QPoint | None = None
        self._drag_snap_target: QWidget | None = None
        self._drag_color: str = "#0A84FF"
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
        self._wires = [w for w in wires if _widget_alive(w.source) and _widget_alive(w.target)]
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

    def set_drag_wire(
        self,
        source: QWidget | None,
        cursor_global: QPoint | None = None,
        *,
        snap_target: QWidget | None = None,
        color: str = "#0A84FF",
    ) -> None:
        """Render a live interactive Bezier cable while dragging a rule or format row."""
        self._drag_source = source if _widget_alive(source) else None
        self._drag_cursor_global = cursor_global
        self._drag_snap_target = snap_target if _widget_alive(snap_target) else None
        self._drag_color = color
        self.update()

    def clear_drag_wire(self) -> None:
        if self._drag_source is not None or self._drag_snap_target is not None or self._drag_cursor_global is not None:
            self._drag_source = None
            self._drag_cursor_global = None
            self._drag_snap_target = None
            self.update()

    @staticmethod
    def _draw_bezier_connection(
        painter: QPainter,
        p1: QPointF,
        p2: QPointF,
        *,
        color: str,
        dashed: bool,
        opacity: float,
    ) -> None:
        gap = p2.x() - p1.x()
        if abs(gap) <= 6 and abs(p2.y() - p1.y()) <= 6:
            return
        dx = max(28.0, min(96.0, abs(gap) * 0.52 + abs(p2.y() - p1.y()) * 0.06))
        dir_sign = 1.0 if gap >= 0 else -0.45
        path = QPainterPath(p1)
        path.cubicTo(
            QPointF(p1.x() + dx * dir_sign, p1.y()),
            QPointF(p2.x() - dx * dir_sign, p2.y()),
            p2,
        )

        painter.save()
        painter.setOpacity(max(0.0, min(1.0, opacity)))
        base_color = QColor(color)
        lighter = QColor(base_color).lighter(122)

        if not dashed:
            glow = QColor(base_color)
            glow.setAlpha(42)
            painter.setPen(QPen(glow, 6.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawPath(path)
        else:
            glow = QColor(base_color)
            glow.setAlpha(28)
            painter.setPen(QPen(glow, 5.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawPath(path)

        pen_style = Qt.PenStyle.DashLine if dashed else Qt.PenStyle.SolidLine
        grad = QLinearGradient(p1, p2)
        grad.setColorAt(0.0, lighter)
        grad.setColorAt(1.0, base_color)
        pen = QPen(QBrush(grad), 1.9, pen_style, Qt.PenCapStyle.RoundCap)
        if dashed:
            pen.setDashPattern([4.5, 4.0])
        painter.setPen(pen)
        painter.drawPath(path)

        ring_border = QPen(QColor("#16171A"), 1.5)
        painter.setPen(ring_border)
        painter.setBrush(base_color)
        painter.drawEllipse(p1, 3.2, 3.2)
        painter.drawEllipse(p2, 4.0, 4.0)
        painter.restore()

    def paintEvent(self, event) -> None:  # noqa: N802
        live_wires = [w for w in self._wires if _widget_alive(w.source) and _widget_alive(w.target)]
        if len(live_wires) != len(self._wires):
            self._wires = live_wires
        has_drag = _widget_alive(self._drag_source) and (
            _widget_alive(self._drag_snap_target) or self._drag_cursor_global is not None
        )
        if not live_wires and not has_drag:
            return

        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            if self._clip_rect is not None and self._clip_rect.isValid():
                painter.setClipRect(self._clip_rect)

            dim_for_drag = 0.28 if has_drag else 1.0
            for wire in live_wires:
                if not (_widget_alive(wire.source) and _widget_alive(wire.target)):
                    continue
                if not wire.source.isVisible() or not wire.target.isVisible():
                    continue

                src_center = self.mapFromGlobal(wire.source.mapToGlobal(wire.source.rect().center()))
                src_right = self.mapFromGlobal(wire.source.mapToGlobal(wire.source.rect().topRight())).x()
                p1 = QPointF(float(src_right - 1), float(src_center.y()))

                tgt_y_offset = min(18, max(8, wire.target.height() // 2))
                tgt_tl = self.mapFromGlobal(wire.target.mapToGlobal(wire.target.rect().topLeft()))
                p2 = QPointF(float(tgt_tl.x() - 2), float(tgt_tl.y() + tgt_y_offset))

                if p2.x() - p1.x() <= 8:
                    continue

                vis_alpha = min(
                    _edge_visibility_alpha(wire.source, self, p1.y()),
                    _edge_visibility_alpha(wire.target, self, p2.y()),
                )
                eff_opacity = (
                    wire.opacity
                    * vis_alpha
                    * dim_for_drag
                    * (self._fade_alpha if wire.animate_pulse else 1.0)
                )
                if eff_opacity <= 0.02:
                    continue

                self._draw_bezier_connection(
                    painter,
                    p1,
                    p2,
                    color=wire.color,
                    dashed=wire.dashed,
                    opacity=eff_opacity,
                )

            if has_drag and self._drag_source is not None and self._drag_source.isVisible():
                src_center = self.mapFromGlobal(self._drag_source.mapToGlobal(self._drag_source.rect().center()))
                src_right = self.mapFromGlobal(self._drag_source.mapToGlobal(self._drag_source.rect().topRight())).x()
                p1 = QPointF(float(src_right - 1), float(src_center.y()))
                if _widget_alive(self._drag_snap_target) and self._drag_snap_target.isVisible():
                    tgt = self._drag_snap_target
                    tgt_y_offset = min(18, max(8, tgt.height() // 2))
                    tgt_tl = self.mapFromGlobal(tgt.mapToGlobal(tgt.rect().topLeft()))
                    p2 = QPointF(float(tgt_tl.x() - 2), float(tgt_tl.y() + tgt_y_offset))
                    snapped = True
                elif self._drag_cursor_global is not None:
                    local_pt = self.mapFromGlobal(self._drag_cursor_global)
                    p2 = QPointF(float(local_pt.x()), float(local_pt.y()))
                    snapped = False
                else:
                    return
                self._draw_bezier_connection(
                    painter,
                    p1,
                    p2,
                    color=self._drag_color,
                    dashed=not snapped,
                    opacity=0.96 if snapped else 0.82,
                )
        except RuntimeError:
            # Never allow a transient deleted C++ wrapper to unwind while QPainter is active.
            pass
        finally:
            painter.end()
