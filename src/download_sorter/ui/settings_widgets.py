"""Reusable macOS Dark Mode settings UI primitives for Download Sorter v3."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QRectF,
    QSize,
    Qt,
    QVariantAnimation,
)
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from download_sorter.ui.motion import _keep, animations_enabled
from download_sorter.ui.theme import PALETTE, set_widget_prop


class MacToggle(QCheckBox):
    """Drop-in QCheckBox rendered as a macOS Dark Mode toggle switch."""

    def __init__(self, text: str = "", parent: QWidget | None = None, *, quiet: bool = False) -> None:
        super().__init__(text, parent)
        self._quiet = quiet
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(30, 18) if quiet else self.setFixedSize(38, 22)
        self._thumb_pos: float = 1.0 if self.isChecked() else 0.0
        self.toggled.connect(self._animate_thumb)

    def sizeHint(self) -> QSize:
        return QSize(30, 18) if self._quiet else QSize(38, 22)

    def hitButton(self, pos: QPoint) -> bool:
        return self.rect().contains(pos)

    def _animate_thumb(self, checked: bool) -> None:
        target = 1.0 if checked else 0.0
        if not animations_enabled():
            self._thumb_pos = target
            self.update()
            return

        anim = QVariantAnimation(self)
        anim.setStartValue(self._thumb_pos)
        anim.setEndValue(target)
        anim.setDuration(200)
        anim.setEasingCurve(QEasingCurve.Type.OutBack)
        anim.valueChanged.connect(self._set_thumb_pos)
        _keep(self, "_motion_mac_toggle", anim)
        anim.start()

    def _set_thumb_pos(self, val: float) -> None:
        self._thumb_pos = float(val)
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = QRectF(1, 1, self.width() - 2, self.height() - 2)
        radius = rect.height() / 2.0

        if self._quiet:
            off_c = QColor(44, 44, 46)
            on_c = QColor(92, 92, 98)
        else:
            off_c = QColor("#39393D")
            on_c = QColor(PALETTE["accent_green"])
        t = max(0.0, min(1.0, self._thumb_pos))
        track_color = QColor(
            int(off_c.red() + (on_c.red() - off_c.red()) * t),
            int(off_c.green() + (on_c.green() - off_c.green()) * t),
            int(off_c.blue() + (on_c.blue() - off_c.blue()) * t),
        )
        if not self.isEnabled():
            track_color.setAlphaF(0.4)

        painter.setBrush(track_color)
        border_alpha = 45 if (self._quiet and t > 0.5) else 22
        painter.setPen(QPen(QColor(255, 255, 255, border_alpha), 1.0))
        painter.drawRoundedRect(rect, radius, radius)

        thumb_d = rect.height() - 4.0
        min_x = rect.left() + 2.0
        max_x = rect.right() - thumb_d - 2.0
        thumb_x = min_x + (max_x - min_x) * t
        thumb_rect = QRectF(thumb_x, rect.top() + 2.0, thumb_d, thumb_d)

        painter.setBrush(QColor("#FFFFFF") if self.isEnabled() else QColor("#8E8E93"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(thumb_rect)


def make_divider() -> QFrame:
    """Create a 1px hairline divider."""
    line = QFrame()
    set_widget_prop(line, "divider", True)
    return line


class SettingRow(QWidget):
    """macOS System Settings row: Title + subtitle on left, controls on right."""

    def __init__(
        self,
        title: str,
        subtitle: str = "",
        controls: QWidget | Sequence[QWidget] | None = None,
        badge_text: str = "",
        badge_variant: str = "green",
        compact: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        outer = QHBoxLayout(self)
        v_pad = 7 if compact else 10
        outer.setContentsMargins(14, v_pad, 14, v_pad)
        outer.setSpacing(14)

        left_col = QVBoxLayout()
        left_col.setContentsMargins(0, 0, 0, 0)
        left_col.setSpacing(2)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)

        self.lbl_title = QLabel(title)
        self.lbl_title.setStyleSheet(
            f"font-size: 13px; font-weight: 500; color: {PALETTE['text_primary']}; background: transparent;"
        )
        title_row.addWidget(self.lbl_title)

        if badge_text:
            self.lbl_badge = QLabel(badge_text)
            set_widget_prop(self.lbl_badge, "pill", badge_variant)
            title_row.addWidget(self.lbl_badge)

        title_row.addStretch(1)
        left_col.addLayout(title_row)

        self.lbl_subtitle = QLabel(subtitle, self)
        self.lbl_subtitle.setObjectName("MutedText")
        self.lbl_subtitle.setVisible(False)
        if subtitle:
            self.setToolTip(subtitle)
            self.lbl_title.setToolTip(subtitle)

        outer.addLayout(left_col, stretch=1)

        if controls is not None:
            ctrl_list = [controls] if isinstance(controls, QWidget) else list(controls)
            right_row = QHBoxLayout()
            right_row.setContentsMargins(0, 0, 0, 0)
            right_row.setSpacing(8)
            for ctrl in ctrl_list:
                if subtitle and not ctrl.toolTip():
                    ctrl.setToolTip(subtitle)
                right_row.addWidget(ctrl, 0, Qt.AlignmentFlag.AlignVCenter)
            # Screen readers announce the switch or box by the row's title; a button
            # with its own label ("Browse...") keeps that.
            for ctrl in ctrl_list:
                labelled = isinstance(ctrl, QAbstractButton) and not isinstance(ctrl, QCheckBox) and any(
                    ch.isalnum() for ch in ctrl.text()
                )
                if labelled:
                    continue
                if not ctrl.accessibleName():
                    ctrl.setAccessibleName(title)
                if subtitle and not ctrl.accessibleDescription():
                    ctrl.setAccessibleDescription(subtitle)
                if self.lbl_title.buddy() is None:
                    self.lbl_title.setBuddy(ctrl)
            outer.addLayout(right_row, stretch=0)


class DependentSubGroup(QFrame):
    """Progressive disclosure container bound to a master QCheckBox."""

    def __init__(
        self,
        master_toggle: QCheckBox,
        collapse_when_off: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        set_widget_prop(self, "dependentGroup", True)
        self._collapse_when_off = collapse_when_off

        self.body_layout = QVBoxLayout(self)
        self.body_layout.setContentsMargins(14, 2, 0, 4)
        self.body_layout.setSpacing(2)

        master_toggle.toggled.connect(self._sync_state)
        self._sync_state(master_toggle.isChecked())

    def add_row(self, widget: QWidget) -> None:
        self.body_layout.addWidget(widget)

    def _sync_state(self, enabled: bool) -> None:
        self.setEnabled(enabled)
        if self._collapse_when_off:
            self.setVisible(enabled)


class SettingCard(QFrame):
    """Grouped settings card with section header and automatic hairline dividers."""

    def __init__(
        self,
        title: str,
        subtitle: str = "",
        header_right_widget: QWidget | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)

        self._main_layout = QVBoxLayout(self)
        self._main_layout.setContentsMargins(0, 12, 0, 6)
        self._main_layout.setSpacing(0)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(14, 0, 14, 8)
        header_layout.setSpacing(10)

        header_text_col = QVBoxLayout()
        header_text_col.setContentsMargins(0, 0, 0, 0)
        header_text_col.setSpacing(2)

        lbl_section = QLabel(title.upper())
        lbl_section.setObjectName("SectionEyebrow")
        if subtitle:
            lbl_section.setToolTip(subtitle)
            self.setToolTip(subtitle)
        header_text_col.addWidget(lbl_section)

        self.lbl_sub = QLabel(subtitle, self)
        self.lbl_sub.setObjectName("MutedText")
        self.lbl_sub.setVisible(False)

        header_layout.addLayout(header_text_col, stretch=1)

        if header_right_widget is not None:
            header_layout.addWidget(header_right_widget, 0, Qt.AlignmentFlag.AlignVCenter)

        self._main_layout.addLayout(header_layout)
        self._main_layout.addWidget(make_divider())
        self._row_count = 0

    def add_row(self, row_widget: QWidget, with_divider: bool = True) -> None:
        if self._row_count > 0 and with_divider:
            self._main_layout.addWidget(make_divider())
        self._main_layout.addWidget(row_widget)
        self._row_count += 1
