"""Cohesive macOS Sequoia Dark Mode QPainter vector emblems and token chips."""

from __future__ import annotations

import math
from typing import Any, Sequence

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QConicalGradient,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from download_sorter.ui.motion import _keep, animations_enabled
from download_sorter.ui.theme import PALETTE


def _with_alpha(hex_color: str, alpha: int) -> QColor:
    c = QColor(hex_color)
    c.setAlpha(max(0, min(255, int(alpha))))
    return c


class FolderScanRadarWidget(QWidget):
    """56x56 QPainter vector radar emblem for 'Scan Existing Folders'."""

    _NODES: tuple[tuple[float, float, str, float], ...] = (
        (0.44, 42.0, PALETTE["accent_blue"], 1.05),
        (0.70, 158.0, PALETTE["accent_green"], 1.15),
        (0.56, 282.0, PALETTE["accent_blue"], 0.95),
        (0.76, 330.0, PALETTE["accent_green"], 0.90),
    )

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        size: int = 56,
        scanning: bool = True,
    ) -> None:
        super().__init__(parent)
        self._preferred_size = max(36, int(size))
        self._scanning = bool(scanning)
        self._sweep_angle = 48.0

        self.setMinimumSize(self._preferred_size, self._preferred_size)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._on_tick)
        self.destroyed.connect(self._stop_timer_safe)
        self._sync_timer_state()

    def sizeHint(self) -> QSize:
        return QSize(self._preferred_size, self._preferred_size)

    def set_scanning(self, active: bool) -> None:
        self._scanning = bool(active)
        if not self._scanning:
            self._sweep_angle = 48.0
        self._sync_timer_state()
        self.update()

    def _sync_timer_state(self) -> None:
        should_run = self._scanning and animations_enabled() and self.isVisible()
        if should_run and not self._timer.isActive():
            self._timer.start()
        elif not should_run and self._timer.isActive():
            self._timer.stop()

    def _stop_timer_safe(self, *_args: Any) -> None:
        if hasattr(self, "_timer") and self._timer is not None and self._timer.isActive():
            self._timer.stop()

    def _on_tick(self) -> None:
        if not animations_enabled() or not self._scanning:
            self._stop_timer_safe()
            self._sweep_angle = 48.0
            self.update()
            return
        self._sweep_angle = (self._sweep_angle + 3.6) % 360.0
        self.update()

    def showEvent(self, event: QEvent) -> None:
        super().showEvent(event)
        self._sync_timer_state()

    def hideEvent(self, event: QEvent) -> None:
        self._stop_timer_safe()
        super().hideEvent(event)

    def closeEvent(self, event: QEvent) -> None:
        self._stop_timer_safe()
        super().closeEvent(event)

    def paintEvent(self, _event: QEvent) -> None:
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            w, h = float(self.width()), float(self.height())
            diameter = min(w, h) - 3.0
            if diameter <= 8.0:
                return
            cx, cy = w * 0.5, h * 0.5
            center = QPointF(cx, cy)
            radius = diameter * 0.5
            badge_rect = QRectF(cx - radius, cy - radius, diameter, diameter)

            bg_grad = QRadialGradient(QPointF(cx - radius * 0.28, cy - radius * 0.32), radius * 1.35)
            bg_grad.setColorAt(0.0, QColor("#252529"))
            bg_grad.setColorAt(0.55, QColor(PALETTE["bg_card"]))
            bg_grad.setColorAt(1.0, QColor("#141416"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(bg_grad))
            painter.drawEllipse(badge_rect)

            painter.setPen(QPen(_with_alpha(PALETTE["border"], 190), 1.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for rf in (0.34, 0.64, 0.88):
                r = radius * rf
                painter.drawEllipse(center, r, r)

            inner_span = radius * 0.88
            painter.setPen(QPen(_with_alpha(PALETTE["border"], 130), 1.0))
            painter.drawLine(QPointF(cx - inner_span, cy), QPointF(cx + inner_span, cy))
            painter.drawLine(QPointF(cx, cy - inner_span), QPointF(cx, cy + inner_span))

            sweep_deg = self._sweep_angle if (self._scanning and animations_enabled()) else 48.0
            if self._scanning:
                beam_r = radius * 0.88
                beam_rect = QRectF(cx - beam_r, cy - beam_r, beam_r * 2.0, beam_r * 2.0)
                conical = QConicalGradient(center, -sweep_deg)
                conical.setColorAt(0.0, _with_alpha(PALETTE["accent_cyan"], 115))
                conical.setColorAt(0.10, _with_alpha(PALETTE["accent_cyan"], 55))
                conical.setColorAt(0.24, _with_alpha(PALETTE["accent_blue"], 18))
                conical.setColorAt(0.34, _with_alpha(PALETTE["accent_blue"], 0))
                conical.setColorAt(1.0, _with_alpha(PALETTE["accent_cyan"], 0))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(conical))
                painter.drawEllipse(beam_rect)

                rad = math.radians(sweep_deg)
                tip = QPointF(cx + math.cos(rad) * beam_r, cy + math.sin(rad) * beam_r)
                needle = QLinearGradient(center, tip)
                needle.setColorAt(0.0, _with_alpha(PALETTE["accent_cyan"], 40))
                needle.setColorAt(1.0, _with_alpha(PALETTE["accent_cyan"], 230))
                painter.setPen(QPen(QBrush(needle), 1.35, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                painter.drawLine(center, tip)

            animate_pulse = self._scanning and animations_enabled()
            for r_ratio, node_deg, color_hex, scale in self._NODES:
                node_rad = math.radians(node_deg)
                pt = QPointF(cx + math.cos(node_rad) * (radius * r_ratio), cy + math.sin(node_rad) * (radius * r_ratio))
                if animate_pulse:
                    delta = (sweep_deg - node_deg) % 360.0
                    pulse = ((1.0 - (delta / 135.0)) ** 1.5) if delta <= 135.0 else 0.15
                else:
                    pulse = 0.55

                halo_r = (3.4 + 2.8 * pulse) * scale
                halo = QRadialGradient(pt, halo_r)
                halo.setColorAt(0.0, _with_alpha(color_hex, int(110 + 115 * pulse)))
                halo.setColorAt(0.6, _with_alpha(color_hex, int(35 + 55 * pulse)))
                halo.setColorAt(1.0, _with_alpha(color_hex, 0))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(halo))
                painter.drawEllipse(pt, halo_r, halo_r)

                core_r = (1.75 + 0.55 * pulse) * scale
                painter.setBrush(QColor(color_hex))
                painter.drawEllipse(pt, core_r, core_r)

            painter.setPen(QPen(_with_alpha(PALETTE["bg_card"], 220), 1.0))
            painter.setBrush(QColor(PALETTE["accent_cyan"]))
            painter.drawEllipse(center, 2.5, 2.5)

            painter.setPen(QPen(QColor(PALETTE["border"]), 1.2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(badge_rect)
        finally:
            painter.end()


class SnapshotEmblemWidget(QWidget):
    """48x48 QPainter stacked time-capsule emblem (#FBBF24 amber + #5AC8FA cyan)."""

    def __init__(self, parent: QWidget | None = None, *, size: int = 48) -> None:
        super().__init__(parent)
        self._preferred_size = max(36, int(size))
        self._hovered = False
        self._shimmer_progress = -1.0

        self.setMinimumSize(self._preferred_size, self._preferred_size)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(680)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._anim.valueChanged.connect(self._on_anim_value)
        self._anim.finished.connect(self._on_anim_finished)
        _keep(self, "_motion_snapshot_shimmer", self._anim)

    def sizeHint(self) -> QSize:
        return QSize(self._preferred_size, self._preferred_size)

    def trigger_shimmer(self) -> None:
        if not animations_enabled() or not self.isVisible():
            self._shimmer_progress = -1.0
            self.update()
            return
        if self._anim.state() == QVariantAnimation.State.Running:
            self._anim.stop()
        self._shimmer_progress = 0.0
        self._anim.start()

    def _on_anim_value(self, value: Any) -> None:
        self._shimmer_progress = float(value)
        self.update()

    def _on_anim_finished(self) -> None:
        self._shimmer_progress = -1.0
        self.update()

    def showEvent(self, event: QEvent) -> None:
        super().showEvent(event)
        if animations_enabled():
            QTimer.singleShot(80, self.trigger_shimmer)

    def enterEvent(self, event: QEvent) -> None:
        self._hovered = True
        self.trigger_shimmer()
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    @staticmethod
    def _isometric_plane(cx: float, cy: float, hw: float, hh: float) -> QPainterPath:
        pts = [QPointF(cx, cy - hh), QPointF(cx + hw, cy), QPointF(cx, cy + hh), QPointF(cx - hw, cy)]
        path = QPainterPath()
        for i in range(4):
            p_prev, p_curr, p_next = pts[(i - 1) % 4], pts[i], pts[(i + 1) % 4]
            t = 0.18
            start = QPointF(p_curr.x() + (p_prev.x() - p_curr.x()) * t, p_curr.y() + (p_prev.y() - p_curr.y()) * t)
            end = QPointF(p_curr.x() + (p_next.x() - p_curr.x()) * t, p_curr.y() + (p_next.y() - p_curr.y()) * t)
            if i == 0:
                path.moveTo(start)
            else:
                path.lineTo(start)
            path.quadTo(p_curr, end)
        path.closeSubpath()
        return path

    def paintEvent(self, _event: QEvent) -> None:
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            w, h = float(self.width()), float(self.height())
            side = min(w, h) - 3.0
            if side <= 8.0:
                return
            cx, cy = w * 0.5, h * 0.5
            card_rect = QRectF(cx - side * 0.5, cy - side * 0.5, side, side)
            card_path = QPainterPath()
            card_path.addRoundedRect(card_rect, side * 0.26, side * 0.26)

            bg_grad = QLinearGradient(card_rect.topLeft(), card_rect.bottomRight())
            bg_grad.setColorAt(0.0, QColor("#242428"))
            bg_grad.setColorAt(0.55, QColor(PALETTE["bg_card"]))
            bg_grad.setColorAt(1.0, QColor("#151517"))
            painter.fillPath(card_path, QBrush(bg_grad))

            amber = "#FBBF24"
            cyan = PALETTE["accent_cyan"]
            hw, hh = side * 0.33, side * 0.175
            lift = 0.8 if self._hovered else 0.0

            bot = self._isometric_plane(cx, cy + side * 0.14 + lift * 0.4, hw * 0.94, hh * 0.94)
            painter.setPen(QPen(_with_alpha(cyan, 175), 1.15))
            painter.setBrush(QBrush(_with_alpha(cyan, 42)))
            painter.drawPath(bot)

            mid = self._isometric_plane(cx, cy + side * 0.02, hw * 0.97, hh * 0.97)
            mid_g = QLinearGradient(QPointF(cx - hw, cy), QPointF(cx + hw, cy))
            mid_g.setColorAt(0.0, _with_alpha(cyan, 75))
            mid_g.setColorAt(1.0, _with_alpha(amber, 65))
            painter.setPen(QPen(_with_alpha(cyan, 210), 1.15))
            painter.setBrush(QBrush(mid_g))
            painter.drawPath(mid)

            top_cy = cy - side * 0.10 - lift
            top = self._isometric_plane(cx, top_cy, hw, hh)
            top_g = QLinearGradient(QPointF(cx - hw, top_cy - hh), QPointF(cx + hw, top_cy + hh))
            top_g.setColorAt(0.0, _with_alpha(amber, 225))
            top_g.setColorAt(1.0, _with_alpha("#D97706", 175))
            painter.setPen(QPen(QColor(amber), 1.25))
            painter.setBrush(QBrush(top_g))
            painter.drawPath(top)

            painter.setPen(QPen(QColor(PALETTE["bg_card"]), 1.35, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(QPointF(cx, top_cy - hh * 0.28), QPointF(cx, top_cy + hh * 0.04))
            painter.drawLine(QPointF(cx, top_cy + hh * 0.04), QPointF(cx + hw * 0.20, top_cy + hh * 0.14))

            if self._shimmer_progress >= 0.0 and animations_enabled():
                painter.save()
                painter.setClipPath(card_path)
                sx = card_rect.left() - side * 0.6 + (side * 2.2) * self._shimmer_progress
                sg = QLinearGradient(QPointF(sx - side * 0.25, card_rect.top()), QPointF(sx + side * 0.25, card_rect.bottom()))
                sg.setColorAt(0.0, QColor(255, 255, 255, 0))
                sg.setColorAt(0.5, QColor(255, 255, 255, 72))
                sg.setColorAt(1.0, QColor(255, 255, 255, 0))
                painter.fillRect(card_rect, QBrush(sg))
                painter.restore()

            border_col = _with_alpha(amber, 135) if self._hovered else QColor(PALETTE["border"])
            painter.setPen(QPen(border_col, 1.2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(card_path)
        finally:
            painter.end()


class DetoxEmblemWidget(QWidget):
    """48x48 QPainter sparkle-shield & clean-sweep broom emblem (#30D158 green + #5AC8FA cyan)."""

    def __init__(self, parent: QWidget | None = None, *, size: int = 48, active: bool = True) -> None:
        super().__init__(parent)
        self._preferred_size = max(36, int(size))
        self._hovered = False
        self._active = bool(active)
        self._phase = 0.0

        self.setMinimumSize(self._preferred_size, self._preferred_size)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._timer = QTimer(self)
        self._timer.setInterval(35)
        self._timer.timeout.connect(self._on_tick)
        self.destroyed.connect(self._stop_timer_safe)
        self._sync_timer_state()

    def sizeHint(self) -> QSize:
        return QSize(self._preferred_size, self._preferred_size)

    def _sync_timer_state(self) -> None:
        should_run = (self._active or self._hovered) and animations_enabled() and self.isVisible()
        if should_run and not self._timer.isActive():
            self._timer.start()
        elif not should_run and self._timer.isActive():
            self._timer.stop()

    def _stop_timer_safe(self, *_args: Any) -> None:
        if hasattr(self, "_timer") and self._timer is not None and self._timer.isActive():
            self._timer.stop()

    def _on_tick(self) -> None:
        if not animations_enabled():
            self._stop_timer_safe()
            self._phase = 0.0
            self.update()
            return
        self._phase = (self._phase + 0.14) % (math.pi * 2.0)
        self.update()

    def showEvent(self, event: QEvent) -> None:
        super().showEvent(event)
        self._sync_timer_state()

    def hideEvent(self, event: QEvent) -> None:
        self._stop_timer_safe()
        super().hideEvent(event)

    def enterEvent(self, event: QEvent) -> None:
        self._hovered = True
        self._sync_timer_state()
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self._hovered = False
        self._sync_timer_state()
        super().leaveEvent(event)

    @staticmethod
    def _draw_sparkle(painter: QPainter, center: QPointF, radius: float, color_hex: str, alpha: int = 235) -> None:
        if radius <= 0.5:
            return
        cx, cy = center.x(), center.y()
        w = radius * 0.24
        path = QPainterPath()
        path.moveTo(cx, cy - radius)
        path.quadTo(cx + w, cy - w, cx + radius, cy)
        path.quadTo(cx + w, cy + w, cx, cy + radius)
        path.quadTo(cx - w, cy + w, cx - radius, cy)
        path.quadTo(cx - w, cy - w, cx, cy - radius)
        path.closeSubpath()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_with_alpha(color_hex, alpha))
        painter.drawPath(path)

    def paintEvent(self, _event: QEvent) -> None:
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            w, h = float(self.width()), float(self.height())
            side = min(w, h) - 3.0
            if side <= 8.0:
                return
            cx, cy = w * 0.5, h * 0.5
            card_rect = QRectF(cx - side * 0.5, cy - side * 0.5, side, side)
            card_path = QPainterPath()
            card_path.addRoundedRect(card_rect, side * 0.26, side * 0.26)

            bg_grad = QLinearGradient(card_rect.topLeft(), card_rect.bottomRight())
            bg_grad.setColorAt(0.0, QColor("#232526"))
            bg_grad.setColorAt(0.55, QColor(PALETTE["bg_card"]))
            bg_grad.setColorAt(1.0, QColor("#141615"))
            painter.fillPath(card_path, QBrush(bg_grad))

            green = PALETTE["accent_green"]
            cyan = PALETTE["accent_cyan"]
            top_y, bot_y, sh_w = cy - side * 0.30, cy + side * 0.32, side * 0.28

            shield = QPainterPath()
            shield.moveTo(cx, top_y)
            shield.quadTo(cx + sh_w * 0.55, top_y + side * 0.03, cx + sh_w, top_y + side * 0.07)
            shield.lineTo(cx + sh_w * 0.94, cy + side * 0.03)
            shield.quadTo(cx + sh_w * 0.72, cy + side * 0.22, cx, bot_y)
            shield.quadTo(cx - sh_w * 0.72, cy + side * 0.22, cx - sh_w * 0.94, cy + side * 0.03)
            shield.lineTo(cx - sh_w, top_y + side * 0.07)
            shield.quadTo(cx - sh_w * 0.55, top_y + side * 0.03, cx, top_y)
            shield.closeSubpath()

            sfill = QLinearGradient(QPointF(cx - sh_w, top_y), QPointF(cx + sh_w, bot_y))
            sfill.setColorAt(0.0, _with_alpha(green, 95))
            sfill.setColorAt(1.0, _with_alpha(cyan, 55))
            sstroke = QLinearGradient(QPointF(cx - sh_w, top_y), QPointF(cx + sh_w, bot_y))
            sstroke.setColorAt(0.0, QColor(green))
            sstroke.setColorAt(1.0, QColor(cyan))
            painter.setPen(QPen(QBrush(sstroke), 1.35))
            painter.setBrush(QBrush(sfill))
            painter.drawPath(shield)

            # Check/swoosh inside shield
            painter.setPen(QPen(QColor(PALETTE["text_primary"]), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.drawLine(QPointF(cx - side * 0.11, cy + side * 0.01), QPointF(cx - side * 0.02, cy + side * 0.10))
            painter.drawLine(QPointF(cx - side * 0.02, cy + side * 0.10), QPointF(cx + side * 0.13, cy - side * 0.07))

            animating = (self._active or self._hovered) and animations_enabled()
            s1 = (0.78 + 0.30 * math.sin(self._phase)) if animating else 1.0
            s2 = (0.78 + 0.30 * math.sin(self._phase + 2.1)) if animating else 0.9
            self._draw_sparkle(painter, QPointF(cx + side * 0.25, cy - side * 0.24), side * 0.10 * s1, cyan, 225)
            self._draw_sparkle(painter, QPointF(cx - side * 0.25, cy - side * 0.16), side * 0.08 * s2, green, 215)

            border_col = _with_alpha(green, 135) if self._hovered else QColor(PALETTE["border"])
            painter.setPen(QPen(border_col, 1.2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(card_path)
        finally:
            painter.end()


class TokenChipBar(QWidget):
    """Interactive token chip bar that inserts tokens at the cursor of a bound QLineEdit."""

    token_inserted = Signal(str)

    DEFAULT_TOKENS: tuple[tuple[str, str], ...] = (
        ("{stem}", "Original filename without extension"),
        ("{stem_slug}", "Lowercase hyphenated filename"),
        ("{year}", "Current 4-digit year"),
        ("{month}", "Current 2-digit month"),
        ("{host}", "Source download domain"),
        ("{ext}", "File extension"),
    )

    def __init__(
        self,
        target_edit: QLineEdit,
        parent: QWidget | None = None,
        tokens: Sequence[tuple[str, str]] = DEFAULT_TOKENS,
    ) -> None:
        super().__init__(parent)
        self._target_edit = target_edit
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(5)

        hint = QLabel("Tokens:")
        hint.setObjectName("MutedText")
        layout.addWidget(hint)

        for token, tooltip in tokens:
            chip = QPushButton(token)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            chip.setToolTip(f"{token} — {tooltip}")
            chip.setStyleSheet(
                "QPushButton { background: #161618; color: #5AC8FA; border: 1px solid #2C2C2E; "
                "border-radius: 5px; padding: 2px 7px; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 11px; }"
                "QPushButton:hover { border-color: #0A84FF; color: #FFFFFF; background: #1B2838; }"
            )
            chip.clicked.connect(lambda _=False, t=token: self._insert_token(t))
            layout.addWidget(chip)
        layout.addStretch(1)

    def _insert_token(self, token: str) -> None:
        text = self._target_edit.text()
        pos = self._target_edit.cursorPosition()
        if pos < 0 or pos > len(text):
            pos = len(text)
        self._target_edit.setText(text[:pos] + token + text[pos:])
        self._target_edit.setFocus()
        self._target_edit.setCursorPosition(pos + len(token))
        self.token_inserted.emit(token)
