"""Left-hand 3-step Sorting Flow spine with continuous painted rails and inline editors."""

from __future__ import annotations

import copy
from pathlib import Path

from PySide6.QtCore import QMimeData, QPointF, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from download_sorter.config import Config, Rule, STARTER_PACK_LABELS
from download_sorter.pipeline import (
    TYPE_BUCKETS,
    PipelineTrace,
    TypeBucket,
    bucket_target_folders,
    build_rule_from_cues,
    infer_teach_cues,
)
from download_sorter.router import apply_rename_template
from download_sorter.ui.motion import InteractiveRibbonBtn, make_vector_icon
from download_sorter.ui.settings_widgets import MacToggle
from download_sorter.ui.text_scale import scaled_px


class _GripDotsWidget(QWidget):
    """Crisp antialiased 2x3 drag-handle dots (replaces raw Unicode glyph)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(12, 16)
        self.setToolTip("Drag to reorder rule priority")

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 76))
        for cx in (3.5, 8.5):
            for cy in (3.5, 8.0, 12.5):
                painter.drawEllipse(QPointF(cx, cy), 1.2, 1.2)


class _PortDotWidget(QWidget):
    """Crisp antialiased 6px status/port indicator dot."""

    def __init__(self, color: str = "#636366", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = color
        self.setFixedSize(8, 8)

    def set_color(self, color: str) -> None:
        if self._color != color:
            self._color = color
            self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self._color))
        painter.drawEllipse(QPointF(4.0, 4.0), 3.0, 3.0)


class _RuleRowWidget(QFrame):
    """Collapsible, drag-reorderable Step 2 rule row with flush-right port badge."""

    toggled = Signal(str, bool)
    expand_requested = Signal(str)
    hover_entered = Signal(str, str)  # (rule_id, destination)
    hover_left = Signal()
    reordered = Signal(str, str)  # (dragged_rule_id, target_rule_id)
    rule_updated = Signal(str, dict)
    rule_deleted = Signal(str)
    open_full_editor = Signal(str)

    def __init__(
        self,
        rule: Rule,
        *,
        is_expanded: bool = False,
        is_winner: bool = False,
        is_shadowed: bool = False,
        blocked_word: str = "",
        is_focus_feeder: bool = False,
        dimmed: bool = False,
        folder_choices: list[str] | None = None,
        sample_filename: str = "March_Invoice_2026.pdf",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.rule = rule
        self._is_expanded = is_expanded
        self._drag_start = None
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        if is_expanded:
            self.setStyleSheet(
                "_RuleRowWidget { background-color: #161618; border: 1px solid #3D3D41; border-radius: 11px; }"
            )
        elif is_winner or is_focus_feeder:
            self.setStyleSheet(
                "_RuleRowWidget { background-color: rgba(10, 132, 255, 0.11); "
                "border: 1px solid rgba(10, 132, 255, 0.45); border-radius: 11px; }"
            )
        else:
            self.setStyleSheet(
                "_RuleRowWidget { background-color: transparent; border: 1px solid transparent; border-radius: 11px; }"
                "_RuleRowWidget:hover { background-color: #161618; }"
            )

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 7, 8, 7)
        root.setSpacing(8)

        # Collapsed summary line
        top = QHBoxLayout()
        top.setSpacing(8)

        lbl_grip = _GripDotsWidget()
        top.addWidget(lbl_grip)

        self.sw_enabled = MacToggle(quiet=True)
        self.sw_enabled.setAccessibleName(f"Enable rule {rule.name}")
        self.sw_enabled.setChecked(rule.enabled)
        self.sw_enabled.toggled.connect(lambda checked: self.toggled.emit(rule.id, checked))
        top.addWidget(self.sw_enabled)

        mid = QVBoxLayout()
        mid.setSpacing(2)

        if is_expanded:
            self.edit_title = QLineEdit(rule.name)
            self.edit_title.setStyleSheet(
                "background: transparent; border: none; border-bottom: 1px solid #3A3A3E; "
                "color: #F5F5F7; font-weight: 600; font-size: 12.5px; padding: 1px 0;"
            )
            self.edit_title.editingFinished.connect(
                lambda: self.rule_updated.emit(rule.id, {"name": self.edit_title.text().strip() or rule.name})
            )
            mid.addWidget(self.edit_title)
        else:
            lbl_title = QLabel(rule.name)
            title_col = "#636366" if (dimmed or not rule.enabled) else "#F5F5F7"
            strike = "text-decoration: line-through;" if not rule.enabled else ""
            lbl_title.setStyleSheet(f"color: {title_col}; font-weight: 600; font-size: 12.5px; {strike}")
            mid.addWidget(lbl_title)

        tokens = list(rule.domains) + list(rule.keywords) + list(rule.extensions)
        summary_text = ", ".join(tokens[:5]) if tokens else (rule.regex_pattern or "Any matching file")
        if len(tokens) > 5:
            summary_text += f" +{len(tokens) - 5}"

        sub_row = QHBoxLayout()
        sub_row.setSpacing(5)
        lbl_sub = QLabel(summary_text)
        lbl_sub.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        lbl_sub.setMinimumWidth(0)
        lbl_sub.setStyleSheet("color: #8E8E93; font-size: 11px; font-family: 'Cascadia Mono', Consolas, monospace;")
        sub_row.addWidget(lbl_sub, 1)

        if rule.exclude_keywords:
            sub_row.addWidget(self._micro_chip(f"-{', -'.join(rule.exclude_keywords[:2])}", warn=bool(blocked_word)))
        if rule.content_keywords:
            sub_row.addWidget(self._micro_chip(f'PDF:"{rule.content_keywords[0]}"'))
        if rule.rename_template:
            sub_row.addWidget(self._micro_chip("Aa Rename"))
        if rule.auto_unzip:
            sub_row.addWidget(self._micro_chip("Unzip"))
        if is_shadowed:
            lbl_sh = QLabel("Also matches · Caught earlier")
            lbl_sh.setStyleSheet("color: #8E8E93; font-size: 11px; font-style: italic;")
            sub_row.addWidget(lbl_sh)
        if blocked_word:
            lbl_bl = QLabel(f'Skipped ("{blocked_word}" excluded)')
            lbl_bl.setStyleSheet("color: #FF9F0A; font-size: 11px;")
            sub_row.addWidget(lbl_bl)
        sub_row.addStretch(1)
        mid.addLayout(sub_row)
        top.addLayout(mid, 1)

        # Flush-right Destination Port Badge
        dest_leaf = rule.destination.replace("/", "\\").split("\\")[-1] or "Downloads"
        self.port_badge = QFrame()
        b_col = "#0A84FF" if (is_winner or is_focus_feeder) else "#2C2C2E"
        t_col = "#F5F5F7" if (is_winner or is_focus_feeder) else "#98989D"
        self.port_badge.setStyleSheet(
            f"QFrame {{ background-color: #161618; border: 1px solid {b_col}; border-radius: 7px; }}"
        )
        pb = QHBoxLayout(self.port_badge)
        pb.setContentsMargins(8, 3, 7, 3)
        pb.setSpacing(5)
        lbl_dest = QLabel(f"→ {dest_leaf}")
        lbl_dest.setStyleSheet(f"color: {t_col}; font-size: 11.5px; font-weight: 500; border: none;")
        dot_col = "#3B82F6" if (is_winner or is_focus_feeder) else "#636366"
        lbl_dot = _PortDotWidget(dot_col)
        pb.addWidget(lbl_dest)
        pb.addWidget(lbl_dot)
        top.addWidget(self.port_badge)

        root.addLayout(top)

        # Expanded inline WHEN / THEN composer
        if is_expanded:
            comp = QFrame()
            comp.setStyleSheet("QFrame { border-top: 1px solid #262629; }")
            cl = QVBoxLayout(comp)
            cl.setContentsMargins(0, 8, 0, 0)
            cl.setSpacing(8)

            grid_row = QHBoxLayout()
            grid_row.setSpacing(8)

            when_col = QVBoxLayout()
            when_col.setSpacing(3)
            lbl_w = QLabel("WHEN filename or site matches")
            lbl_w.setStyleSheet("color: #8E8E93; font-size: 11px;")
            raw_match = ", ".join(list(rule.domains) + list(rule.keywords))
            self.edit_match = QLineEdit(raw_match)
            self.edit_match.setPlaceholderText("invoice, receipt, stripe.com")
            self.edit_match.editingFinished.connect(self._commit_match_tokens)
            when_col.addWidget(lbl_w)
            when_col.addWidget(self.edit_match)
            grid_row.addLayout(when_col, 1)

            then_col = QVBoxLayout()
            then_col.setSpacing(3)
            lbl_t = QLabel("THEN move to (or click folder →)")
            lbl_t.setStyleSheet("color: #8E8E93; font-size: 11px;")
            self.cmb_dest = QComboBox()
            choices = list(folder_choices or [])
            norm_dest = rule.destination.replace("/", "\\").strip("\\")
            if norm_dest and norm_dest not in choices:
                choices.insert(0, norm_dest)
            for ch in choices:
                self.cmb_dest.addItem(ch.replace("\\", " › "), ch)
            idx = self.cmb_dest.findData(norm_dest)
            if idx >= 0:
                self.cmb_dest.setCurrentIndex(idx)
            self.cmb_dest.currentIndexChanged.connect(
                lambda: self.rule_updated.emit(rule.id, {"destination": self.cmb_dest.currentData()})
            )
            then_col.addWidget(lbl_t)
            then_col.addWidget(self.cmb_dest)
            grid_row.addLayout(then_col, 1)
            cl.addLayout(grid_row)

            if rule.exclude_keywords:
                ex_row = QVBoxLayout()
                ex_row.setSpacing(3)
                lbl_ex = QLabel("Exclude words (skip if filename contains)")
                lbl_ex.setStyleSheet("color: #8E8E93; font-size: 11px;")
                self.edit_excl = QLineEdit(", ".join(rule.exclude_keywords))
                self.edit_excl.editingFinished.connect(
                    lambda: self.rule_updated.emit(
                        rule.id,
                        {"exclude_keywords": [s.strip() for s in self.edit_excl.text().split(",") if s.strip()]},
                    )
                )
                ex_row.addWidget(lbl_ex)
                ex_row.addWidget(self.edit_excl)
                cl.addLayout(ex_row)

            if rule.content_keywords:
                pdf_row = QVBoxLayout()
                pdf_row.setSpacing(3)
                lbl_pdf = QLabel("Inside PDF text contains")
                lbl_pdf.setStyleSheet("color: #8E8E93; font-size: 11px;")
                self.edit_pdf = QLineEdit(", ".join(rule.content_keywords))
                self.edit_pdf.editingFinished.connect(
                    lambda: self.rule_updated.emit(
                        rule.id,
                        {"content_keywords": [s.strip() for s in self.edit_pdf.text().split(",") if s.strip()]},
                    )
                )
                pdf_row.addWidget(lbl_pdf)
                pdf_row.addWidget(self.edit_pdf)
                cl.addLayout(pdf_row)

            if rule.rename_template:
                ren_box = QFrame()
                ren_box.setStyleSheet("QFrame { background-color: #111113; border: 1px solid #2C2C2E; border-radius: 8px; }")
                rl = QVBoxLayout(ren_box)
                rl.setContentsMargins(8, 6, 8, 6)
                rl.setSpacing(4)
                ren_hdr = QHBoxLayout()
                lbl_ren = QLabel("Rename template")
                lbl_ren.setStyleSheet("color: #8E8E93; font-size: 11px; border: none;")
                ren_hdr.addWidget(lbl_ren)
                ren_hdr.addStretch(1)
                for tok in ("{year}", "{month}", "{stem}", "{host}"):
                    tb = QPushButton(tok)
                    tb.setStyleSheet(
                        "padding: 1px 6px; font-size: 10.5px; border-radius: 5px; "
                        "background-color: #1C1C1E; border: 1px solid #2C2C2E; color: #AEAEB2;"
                    )
                    tb.clicked.connect(
                        lambda _=False, t=tok: self.rule_updated.emit(
                            rule.id,
                            {"rename_template": f"{rule.rename_template}_{t}" if rule.rename_template else f"{t}_{{stem}}{{ext}}"},
                        )
                    )
                    ren_hdr.addWidget(tb)
                rl.addLayout(ren_hdr)
                self.edit_rename = QLineEdit(rule.rename_template)
                self.edit_rename.editingFinished.connect(
                    lambda: self.rule_updated.emit(rule.id, {"rename_template": self.edit_rename.text().strip()})
                )
                rl.addWidget(self.edit_rename)
                preview_name = apply_rename_template(rule.rename_template, sample_filename)
                lbl_prev = QLabel(f"Example: {sample_filename} → {preview_name}")
                lbl_prev.setStyleSheet("color: #8E8E93; font-size: 11px; border: none;")
                rl.addWidget(lbl_prev)
                cl.addWidget(ren_box)

            # Progressive action bar
            act_bar = QHBoxLayout()
            act_bar.setSpacing(8)
            if not rule.exclude_keywords:
                b = self._text_action_btn("+ Exclude words")
                b.clicked.connect(lambda: self.rule_updated.emit(rule.id, {"exclude_keywords": ["draft"]}))
                act_bar.addWidget(b)
            if not rule.content_keywords:
                b = self._text_action_btn("+ PDF text")
                b.clicked.connect(lambda: self.rule_updated.emit(rule.id, {"content_keywords": ["total due"]}))
                act_bar.addWidget(b)
            if not rule.rename_template:
                b = self._text_action_btn("+ Rename")
                b.clicked.connect(lambda: self.rule_updated.emit(rule.id, {"rename_template": "{year}-{month}_{stem}{ext}"}))
                act_bar.addWidget(b)

            btn_unzip = self._text_action_btn("✓ Auto-unzip .zip" if rule.auto_unzip else "+ Auto-unzip", active=rule.auto_unzip)
            btn_unzip.clicked.connect(lambda: self.rule_updated.emit(rule.id, {"auto_unzip": not rule.auto_unzip}))
            act_bar.addWidget(btn_unzip)

            btn_full = self._text_action_btn("More options...")
            btn_full.clicked.connect(lambda: self.open_full_editor.emit(rule.id))
            act_bar.addWidget(btn_full)

            act_bar.addStretch(1)
            btn_del = QPushButton("Delete")
            btn_del.setStyleSheet("border: none; background: transparent; color: #FF453A; font-size: 11.5px;")
            btn_del.clicked.connect(lambda: self.rule_deleted.emit(rule.id))
            btn_done = QPushButton("Done")
            btn_done.setStyleSheet("padding: 3px 10px; font-size: 11.5px;")
            btn_done.clicked.connect(lambda: self.expand_requested.emit(rule.id))
            act_bar.addWidget(btn_del)
            act_bar.addWidget(btn_done)
            cl.addLayout(act_bar)
            root.addWidget(comp)

    @staticmethod
    def _micro_chip(text: str, *, warn: bool = False) -> QLabel:
        lbl = QLabel(text)
        col = "#FF9F0A" if warn else "#8E8E93"
        bdr = "#FF9F0A" if warn else "#2C2C2E"
        lbl.setStyleSheet(
            f"color: {col}; background-color: #161618; border: 1px solid {bdr}; "
            "border-radius: 5px; padding: 0px 5px; font-size: 10.5px;"
        )
        return lbl

    @staticmethod
    def _text_action_btn(text: str, *, active: bool = False) -> QPushButton:
        btn = QPushButton(text)
        col = "#0A84FF" if active else "#8E8E93"
        btn.setStyleSheet(
            f"QPushButton {{ border: none; background: transparent; color: {col}; font-size: 11.5px; padding: 2px 4px; }}"
            "QPushButton:hover { color: #F5F5F7; }"
        )
        return btn

    def _commit_match_tokens(self) -> None:
        parts = [s.strip().lower() for s in self.edit_match.text().split(",") if s.strip()]
        domains = [p for p in parts if "." in p and not p.startswith(".")]
        exts = [p for p in parts if p.startswith(".")]
        words = [p for p in parts if p not in domains and p not in exts]
        self.rule_updated.emit(self.rule.id, {"domains": domains, "keywords": words, "extensions": exts})

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if (
            not self._is_expanded
            and self._drag_start is not None
            and (event.buttons() & Qt.MouseButton.LeftButton)
            and (event.position().toPoint() - self._drag_start).manhattanLength() >= 10
        ):
            drag = QDrag(self)
            mime = QMimeData()
            mime.setData("application/x-ds-rule-id", self.rule.id.encode("utf-8"))
            drag.setMimeData(mime)
            drag.exec(Qt.DropAction.MoveAction)
            self._drag_start = None
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._drag_start is not None:
            if (event.position().toPoint() - self._drag_start).manhattanLength() < 10:
                self.expand_requested.emit(self.rule.id)
        self._drag_start = None
        super().mouseReleaseEvent(event)

    def enterEvent(self, event) -> None:  # noqa: N802
        self.hover_entered.emit(self.rule.id, self.rule.destination)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.hover_left.emit()
        super().leaveEvent(event)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasFormat("application/x-ds-rule-id"):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasFormat("application/x-ds-rule-id"):
            dragged_id = bytes(event.mimeData().data("application/x-ds-rule-id")).decode("utf-8", "ignore")
            if dragged_id and dragged_id != self.rule.id:
                self.reordered.emit(dragged_id, self.rule.id)
            event.acceptProposedAction()
            return
        super().dropEvent(event)


class _TypeRowWidget(QFrame):
    """Single-column Step 3 file-type row with right-aligned port badge."""

    hover_entered = Signal(str, str)  # (bucket_id, target_folder)
    hover_left = Signal()
    unzip_toggled = Signal()

    def __init__(
        self,
        bucket: TypeBucket,
        target_folder: str,
        *,
        is_winner: bool = False,
        is_focus_feeder: bool = False,
        dimmed: bool = False,
        auto_unzip_all: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.bucket = bucket
        self.target_folder = target_folder

        if is_winner or is_focus_feeder:
            self.setStyleSheet(
                "_TypeRowWidget { background-color: rgba(10, 132, 255, 0.11); "
                "border: 1px solid rgba(10, 132, 255, 0.45); border-radius: 10px; }"
            )
        else:
            self.setStyleSheet(
                "_TypeRowWidget { background-color: transparent; border: 1px solid transparent; border-radius: 10px; }"
                "_TypeRowWidget:hover { background-color: #161618; }"
            )

        row = QHBoxLayout(self)
        row.setContentsMargins(8, 5, 8, 5)
        row.setSpacing(8)

        lbl_name = QLabel(bucket.name)
        lbl_name.setStyleSheet(f"color: {'#636366' if dimmed else '#F5F5F7'}; font-size: 12.5px; font-weight: 600;")
        row.addWidget(lbl_name)

        lbl_exts = QLabel(" ".join(bucket.extensions[:4]))
        lbl_exts.setStyleSheet("color: #8E8E93; font-size: 11px; font-family: 'Cascadia Mono', Consolas, monospace;")
        row.addWidget(lbl_exts, 1)

        if bucket.unzippable:
            btn_uz = QPushButton("✓ Auto-extract" if auto_unzip_all else "Auto-extract: off")
            btn_uz.setStyleSheet(
                f"padding: 2px 7px; font-size: 11px; border-radius: 6px; "
                f"color: {'#0A84FF' if auto_unzip_all else '#8E8E93'}; border: 1px solid #2C2C2E;"
            )
            btn_uz.clicked.connect(self.unzip_toggled.emit)
            row.addWidget(btn_uz)

        leaf = target_folder.replace("/", "\\").split("\\")[-1] or bucket.default_folder
        self.port_badge = QFrame()
        b_col = "#0A84FF" if (is_winner or is_focus_feeder) else "#2C2C2E"
        t_col = "#F5F5F7" if (is_winner or is_focus_feeder) else "#98989D"
        self.port_badge.setStyleSheet(
            f"QFrame {{ background-color: #161618; border: 1px solid {b_col}; border-radius: 7px; }}"
        )
        pb = QHBoxLayout(self.port_badge)
        pb.setContentsMargins(8, 2, 7, 2)
        pb.setSpacing(5)
        lbl_d = QLabel(f"→ {leaf}")
        lbl_d.setStyleSheet(f"color: {t_col}; font-size: 11.5px; border: none;")
        lbl_dot = _PortDotWidget("#3B82F6" if (is_winner or is_focus_feeder) else "#636366")
        pb.addWidget(lbl_d)
        pb.addWidget(lbl_dot)
        row.addWidget(self.port_badge)

    def enterEvent(self, event) -> None:  # noqa: N802
        self.hover_entered.emit(self.bucket.id, self.target_folder)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.hover_left.emit()
        super().leaveEvent(event)


class _SpineBodyWidget(QWidget):
    """Inner scroll widget that paints exact badge-to-badge vertical spine rails."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.badges: list[QLabel] = []
        self.active_step: int = 0
        self.focused: bool = False

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        visible = [b for b in self.badges if b.isVisible()]
        if len(visible) < 2:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        for idx in range(len(visible) - 1):
            b1 = visible[idx]
            b2 = visible[idx + 1]
            c1 = b1.mapTo(self, b1.rect().center())
            c2 = b2.mapTo(self, b2.rect().center())
            y1 = float(c1.y() + b1.height() / 2 + 4)
            y2 = float(c2.y() - b2.height() / 2 - 4)
            if y2 <= y1:
                continue
            step_num = int(b1.text()) if b1.text().isdigit() else (idx + 1)
            dashed = (not self.focused) and self.active_step > 0 and step_num >= self.active_step
            pen = QPen(
                QColor(255, 255, 255, 36 if not dashed else 22),
                1.5,
                Qt.PenStyle.DashLine if dashed else Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
            )
            painter.setPen(pen)
            painter.drawLine(QPointF(float(c1.x()), y1), QPointF(float(c2.x()), y2))


class SpinePanel(QScrollArea):
    """Left-hand 3-step Sorting Flow spine (Hold & Group -> Rules -> File Types & AI)."""

    config_mutated = Signal(object)  # emits new Config
    rule_port_hovered = Signal(object, str)  # (port_widget, destination_folder)
    port_hover_left = Signal()
    teach_committed = Signal(object, bool, object)  # (Rule, also_move_now, source_path)
    teach_cancelled = Signal()
    action_requested = Signal(str, str)  # (action, arg)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet(
            "QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }"
            "QScrollBar:vertical { background: transparent; width: 5px; margin: 4px 0; }"
            "QScrollBar::handle:vertical { background: rgba(255, 255, 255, 0.12); border-radius: 2px; min-height: 24px; }"
            "QScrollBar::handle:vertical:hover { background: rgba(255, 255, 255, 0.28); }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical, QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; height: 0px; }"
        )

        self._config: Config | None = None
        self._expanded_rule_id: str | None = None
        self._teach_draft: dict | None = None
        self._teach_cue_checks: list[tuple[str, str, bool]] = []
        self._rule_ports: dict[str, QWidget] = {}
        self._type_ports: dict[str, QWidget] = {}
        self._hold_port: QWidget | None = None
        self._ai_port: QWidget | None = None
        self._teach_port: QWidget | None = None

        self.body = _SpineBodyWidget()
        self.body.setMinimumWidth(0)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 8, 8)
        self.body_layout.setSpacing(14)

        # Build the 3 step containers (Step 1 is strictly opt-in and hidden by default)
        self.step1_row, self.badge_1, self.lbl_state_1, self.card_1 = self._make_step_shell(
            "1",
            "Hold & group first",
            "Optional: hold specific formats or bundle multi-file downloads",
        )
        self.step2_row, self.badge_2, self.lbl_state_2, self.card_2 = self._make_step_shell(
            "1",
            "Your rules",
            "Matched first, top to bottom · Click any rule to edit",
        )
        self.step3_row, self.badge_3, self.lbl_state_3, self.card_3 = self._make_step_shell(
            "2",
            "Sort the rest by file type",
            "Everything else sorts automatically by format",
        )
        self.body.badges = [self.badge_1, self.badge_2, self.badge_3]

        self._populate_step1_static()
        self._populate_step2_static()
        self._populate_step3_static()

        # Quiet opt-in discovery link (only shown when Holds & Grouping are off)
        self.optin_row = QWidget()
        or_l = QHBoxLayout(self.optin_row)
        or_l.setContentsMargins(scaled_px(38), 0, 0, 0)
        or_l.setSpacing(4)
        self.btn_optin_holds = QPushButton("Need to hold .3mf files or group project downloads? Enable in Preferences →")
        self.btn_optin_holds.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_optin_holds.setStyleSheet(
            "QPushButton { border: none; background: transparent; color: #8E8E93; "
            "font-size: 11.5px; text-align: left; padding: 2px 0; min-width: 0px; }"
            "QPushButton:hover { color: #60A5FA; }"
        )
        self.btn_optin_holds.clicked.connect(lambda: self.action_requested.emit("open_prefs_section", "sorting"))
        or_l.addWidget(self.btn_optin_holds)
        or_l.addStretch(1)
        self.body_layout.addWidget(self.optin_row)

        # Quiet safety reassurance footer
        safety_row = QHBoxLayout()
        safety_row.setContentsMargins(scaled_px(38), 0, 0, 0)
        safety_row.setSpacing(6)
        lbl_sec = QLabel("In-progress downloads wait · Exact copies go to _Duplicates · Every move has Undo")
        lbl_sec.setObjectName("MutedText")
        lbl_sec.setWordWrap(True)
        lbl_sec.setMinimumWidth(0)
        safety_row.addWidget(lbl_sec, 1)
        self.body_layout.addLayout(safety_row)
        self.body_layout.addStretch(1)

        self.setWidget(self.body)

    def _make_step_shell(self, num: str, title: str, subtitle: str) -> tuple[QWidget, QLabel, QLabel, QFrame]:
        shell = QWidget()
        row = QHBoxLayout(shell)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        row.setAlignment(Qt.AlignmentFlag.AlignTop)

        badge = QLabel(num)
        badge.setFixedSize(scaled_px(26), scaled_px(26))
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(
            "background-color: #24272E; border: 1px solid rgba(255, 255, 255, 0.14); border-radius: 13px; "
            "color: #F5F5F7; font-size: 12px; font-weight: 700;"
        )
        row.addWidget(badge)

        card = QFrame()
        card.setObjectName("SurfaceCard")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 12, 14, 12)
        cl.setSpacing(8)

        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        t_col = QVBoxLayout()
        t_col.setSpacing(1)
        t_top = QHBoxLayout()
        t_top.setSpacing(6)
        lbl_t = QLabel(title)
        lbl_t.setObjectName("CardTitle")
        lbl_state = QLabel("")
        lbl_state.setStyleSheet("color: #8E8E93; font-size: 11.5px;")
        t_top.addWidget(lbl_t)
        t_top.addWidget(lbl_state)
        t_top.addStretch(1)
        lbl_sub = QLabel(subtitle)
        lbl_sub.setObjectName("MutedText")
        lbl_sub.setWordWrap(True)
        lbl_sub.setMinimumWidth(0)
        t_col.addLayout(t_top)
        t_col.addWidget(lbl_sub)
        hdr.addLayout(t_col, 1)
        card.setProperty("_hdr_layout", hdr)
        cl.addLayout(hdr)

        row.addWidget(card, 1)
        self.body_layout.addWidget(shell)
        return shell, badge, lbl_state, card

    def _populate_step1_static(self) -> None:
        cl: QVBoxLayout = self.card_1.layout()  # type: ignore[assignment]
        self.hold_3mf_frame = QFrame()
        self.hold_3mf_frame.setStyleSheet("QFrame { border-top: 1px solid #262629; }")
        hrow = QHBoxLayout(self.hold_3mf_frame)
        hrow.setContentsMargins(4, 8, 4, 4)
        hrow.setSpacing(10)

        self.sw_hold_3mf = MacToggle(quiet=True)
        self.sw_hold_3mf.setAccessibleName("Hold 3D printer project files (.3mf)")
        self.sw_hold_3mf.toggled.connect(self._on_hold_3mf_changed)
        hrow.addWidget(self.sw_hold_3mf)

        info = QVBoxLayout()
        info.setSpacing(1)
        lbl_ht = QLabel("3D printer project files (.3mf)")
        lbl_ht.setStyleSheet("color: #F5F5F7; font-size: 12.5px; font-weight: 600; border: none;")
        lbl_hs = QLabel("Kept at the top of Downloads so Bambu Studio / OrcaSlicer finds them")
        lbl_hs.setWordWrap(True)
        lbl_hs.setMinimumWidth(0)
        lbl_hs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding)
        lbl_hs.setStyleSheet("color: #8E8E93; font-size: 11.5px; border: none;")
        info.addWidget(lbl_ht)
        info.addWidget(lbl_hs)
        hrow.addLayout(info, 1)

        self.spin_hold_days = QSpinBox()
        self.spin_hold_days.setRange(1, 30)
        self.spin_hold_days.setSuffix("d")
        self.spin_hold_days.setFixedWidth(scaled_px(76))
        self.spin_hold_days.valueChanged.connect(self._on_hold_3mf_changed)
        hrow.addWidget(self.spin_hold_days)

        self.hold_port_badge = QFrame()
        self.hold_port_badge.setStyleSheet(
            "QFrame { background-color: #161618; border: 1px solid #2C2C2E; border-radius: 7px; }"
        )
        hp = QHBoxLayout(self.hold_port_badge)
        hp.setContentsMargins(8, 2, 7, 2)
        hp.setSpacing(5)
        lbl_hp = QLabel("Hold")
        lbl_hp.setStyleSheet("color: #98989D; font-size: 11.5px; border: none;")
        self.lbl_hold_dot = _PortDotWidget("#636366")
        hp.addWidget(lbl_hp)
        hp.addWidget(self.lbl_hold_dot)
        hrow.addWidget(self.hold_port_badge)
        self._hold_port = self.hold_port_badge
        cl.addWidget(self.hold_3mf_frame)

        # Detect Projects row (opt-in)
        self.proj_row_frame = QFrame()
        self.proj_row_frame.setStyleSheet("QFrame { border-top: 1px solid #262629; }")
        pl = QHBoxLayout(self.proj_row_frame)
        pl.setContentsMargins(4, 8, 4, 2)
        pl.setSpacing(10)
        pinfo = QVBoxLayout()
        pinfo.setSpacing(1)
        lbl_pt = QLabel("Group multi-file project bursts")
        lbl_pt.setStyleSheet("color: #F5F5F7; font-size: 12.5px; font-weight: 600; border: none;")
        lbl_ps = QLabel("Cluster related CAD, code, or course downloads into project subfolders")
        lbl_ps.setWordWrap(True)
        lbl_ps.setMinimumWidth(0)
        lbl_ps.setStyleSheet("color: #8E8E93; font-size: 11.5px; border: none;")
        pinfo.addWidget(lbl_pt)
        pinfo.addWidget(lbl_ps)
        pl.addLayout(pinfo, 1)
        btn_detect = InteractiveRibbonBtn("Detect Projects", make_vector_icon("search", "#F5F5F7", 13))
        btn_detect.setObjectName("GhostButton")
        btn_detect.clicked.connect(lambda: self.action_requested.emit("detect_projects", ""))
        pl.addWidget(btn_detect)
        cl.addWidget(self.proj_row_frame)

    def _populate_step2_static(self) -> None:
        hdr: QHBoxLayout = self.card_2.property("_hdr_layout")
        cl: QVBoxLayout = self.card_2.layout()  # type: ignore[assignment]

        # + New rule ▾ split controls + ⋯ overflow
        self.btn_new_rule = InteractiveRibbonBtn("New rule", make_vector_icon("plus", "#F5F5F7", 13))
        self.btn_new_rule.setStyleSheet("padding: 4px 9px;")
        self.btn_new_rule.clicked.connect(self._on_add_blank_rule)

        self.btn_rule_packs = InteractiveRibbonBtn("Packs ▾")
        self.btn_rule_packs.setObjectName("GhostButton")
        self.btn_rule_packs.setStyleSheet("padding: 4px 8px;")
        self.menu_packs = QMenu(self.btn_rule_packs)
        for key, (label, desc) in STARTER_PACK_LABELS.items():
            act = self.menu_packs.addAction(f"{label} — {desc}".replace("&", "&&"))
            act.triggered.connect(lambda _=False, k=key: self.action_requested.emit("apply_pack", k))
        self.btn_rule_packs.clicked.connect(
            lambda: self.menu_packs.exec(
                self.btn_rule_packs.mapToGlobal(self.btn_rule_packs.rect().bottomLeft())
            )
        )

        self.btn_step2_more = InteractiveRibbonBtn("⋯")
        self.btn_step2_more.setObjectName("GhostButton")
        self.btn_step2_more.setStyleSheet("padding: 4px 6px; min-width: 0px;")
        self.btn_step2_more.setFixedWidth(28)
        self.menu_step2 = QMenu(self.btn_step2_more)
        self.menu_step2.addAction("Smart Order (Most Specific First)", lambda: self.action_requested.emit("smart_order", ""))
        self.menu_step2.addAction("Carry Over v2.8 / Setup...", lambda: self.action_requested.emit("carry_over", ""))
        self.btn_step2_more.clicked.connect(
            lambda: self.menu_step2.exec(
                self.btn_step2_more.mapToGlobal(self.btn_step2_more.rect().bottomRight())
            )
        )

        hdr.addWidget(self.btn_new_rule)
        hdr.addWidget(self.btn_rule_packs)
        hdr.addWidget(self.btn_step2_more)

        # Contextual Learned Suggestion Strip (0px when empty)
        self.sugg_banner = QFrame()
        self.sugg_banner.setVisible(False)
        self.sugg_banner.setStyleSheet(
            "QFrame { background-color: rgba(10, 132, 255, 0.10); border: 1px solid rgba(10, 132, 255, 0.35); border-radius: 9px; }"
        )
        sb = QHBoxLayout(self.sugg_banner)
        sb.setContentsMargins(10, 6, 8, 6)
        sb.setSpacing(8)
        self.lbl_sugg = QLabel("")
        self.lbl_sugg.setStyleSheet("color: #F5F5F7; font-size: 12px; border: none; background: transparent;")
        self.lbl_sugg.setWordWrap(True)
        btn_accept = QPushButton("Review & Add")
        btn_accept.setObjectName("PrimaryButton")
        btn_accept.setStyleSheet("padding: 3px 10px; font-size: 11.5px;")
        btn_accept.clicked.connect(lambda: self.action_requested.emit("accept_suggestion", ""))
        btn_dismiss = QPushButton("Dismiss")
        btn_dismiss.setStyleSheet("padding: 3px 8px; font-size: 11.5px;")
        btn_dismiss.clicked.connect(lambda: self.action_requested.emit("dismiss_suggestion", ""))
        sb.addWidget(self.lbl_sugg, 1)
        sb.addWidget(btn_accept)
        sb.addWidget(btn_dismiss)
        cl.addWidget(self.sugg_banner)

        # Teach-by-Example Draft Card (0px when inactive)
        self.teach_card = QFrame()
        self.teach_card.setVisible(False)
        self.teach_card.setStyleSheet(
            "QFrame { background-color: rgba(10, 132, 255, 0.11); border: 1px solid rgba(10, 132, 255, 0.45); border-radius: 10px; }"
        )
        self.teach_layout = QVBoxLayout(self.teach_card)
        self.teach_layout.setContentsMargins(10, 9, 10, 9)
        self.teach_layout.setSpacing(7)
        cl.addWidget(self.teach_card)

        self.rules_host = QWidget()
        self.rules_layout = QVBoxLayout(self.rules_host)
        self.rules_layout.setContentsMargins(0, 0, 0, 0)
        self.rules_layout.setSpacing(3)
        cl.addWidget(self.rules_host)

        self.btn_empty_folder_cta = QPushButton("")
        self.btn_empty_folder_cta.setVisible(False)
        self.btn_empty_folder_cta.setStyleSheet(
            "padding: 7px 12px; border: 1px dashed #3A3A3E; border-radius: 9px; "
            "color: #0A84FF; background: transparent; font-weight: 600;"
        )
        self.btn_empty_folder_cta.clicked.connect(lambda: self.action_requested.emit("create_rule_for_focus", ""))
        cl.addWidget(self.btn_empty_folder_cta)

    def _populate_step3_static(self) -> None:
        cl: QVBoxLayout = self.card_3.layout()  # type: ignore[assignment]
        self.types_host = QWidget()
        self.types_layout = QVBoxLayout(self.types_host)
        self.types_layout.setContentsMargins(0, 0, 0, 0)
        self.types_layout.setSpacing(3)
        cl.addWidget(self.types_host)

        footer = QFrame()
        footer.setStyleSheet("QFrame { border-top: 1px solid #262629; }")
        fl = QVBoxLayout(footer)
        fl.setContentsMargins(0, 8, 0, 0)
        fl.setSpacing(7)

        sub_row = QHBoxLayout()
        lbl_sub = QLabel("Group into type subfolders (e.g. Documents › PDF)")
        lbl_sub.setStyleSheet("color: #AEAEB2; font-size: 12px; border: none;")
        self.sw_subfolders = MacToggle(quiet=True)
        self.sw_subfolders.setAccessibleName("Group into type subfolders")
        self.sw_subfolders.toggled.connect(self._on_step3_toggled)
        sub_row.addWidget(lbl_sub, 1)
        sub_row.addWidget(self.sw_subfolders)
        fl.addLayout(sub_row)

        self.ai_row_frame = QFrame()
        al = QHBoxLayout(self.ai_row_frame)
        al.setContentsMargins(4, 3, 4, 3)
        al.setSpacing(8)
        lbl_ai = QLabel("Smart fallback (100% offline AI) · classifies unknown files")
        lbl_ai.setStyleSheet("color: #F5F5F7; font-size: 12px; border: none;")
        self.btn_ai_insights = QPushButton("Insights...")
        self.btn_ai_insights.setStyleSheet(
            "border: none; background: transparent; color: #0A84FF; font-size: 12px; font-weight: 600;"
        )
        self.btn_ai_insights.clicked.connect(lambda: self.action_requested.emit("ai_insights", ""))
        self.sw_ai_fallback = MacToggle(quiet=True)
        self.sw_ai_fallback.setAccessibleName("Smart fallback offline AI")
        self.sw_ai_fallback.toggled.connect(self._on_step3_toggled)
        al.addWidget(lbl_ai, 1)
        al.addWidget(self.btn_ai_insights)
        al.addWidget(self.sw_ai_fallback)
        self._ai_port = self.ai_row_frame
        fl.addWidget(self.ai_row_frame)

        fb_row = QHBoxLayout()
        lbl_fb = QLabel("If still unrecognized")
        lbl_fb.setStyleSheet("color: #8E8E93; font-size: 12px; border: none;")
        self.cmb_fallback = QComboBox()
        self.cmb_fallback.addItem("Move to Downloads › Other", "other")
        self.cmb_fallback.addItem("Leave in Downloads", "leave")
        self.cmb_fallback.currentIndexChanged.connect(self._on_step3_toggled)
        fb_row.addWidget(lbl_fb, 1)
        fb_row.addWidget(self.cmb_fallback)
        fl.addLayout(fb_row)

        cl.addWidget(footer)

    def expanded_rule_id(self) -> str | None:
        return self._expanded_rule_id

    def set_expanded_rule_dest(self, folder_path: str) -> bool:
        if not self._expanded_rule_id or self._config is None:
            return False
        self._update_rule_fields(self._expanded_rule_id, {"destination": folder_path})
        return True

    def start_teach_draft(self, path: Path, target_folder: str, source_url: str) -> None:
        self._expanded_rule_id = None
        cues = infer_teach_cues(path.name, source_url)
        items: list[tuple[str, str, bool]] = []
        for wc in cues["word_cues"]:
            items.append(("word", wc["value"], bool(wc["checked"])))
        if cues["ext"]:
            items.append(("ext", cues["ext"], True))
        if cues["host"]:
            items.append(("site", cues["host"], True))
        best = cues["best_word"] or cues["ext"] or "files"
        leaf = target_folder.replace("/", "\\").split("\\")[-1]
        self._teach_draft = {
            "path": path,
            "filename": path.name,
            "source_url": source_url,
            "target_folder": target_folder,
            "title": f"{leaf} ({best})",
        }
        self._teach_cue_checks = items

    def clear_teach_draft(self) -> None:
        self._teach_draft = None
        self._teach_cue_checks = []
        self._teach_port = None
        self.teach_card.setVisible(False)

    def has_teach_draft(self) -> bool:
        return self._teach_draft is not None

    def set_suggestion(self, suggestion: dict | None) -> None:
        if suggestion:
            self.lbl_sugg.setText(f"{suggestion['summary']}. Make that a rule?")
            self.sugg_banner.setVisible(True)
        else:
            self.sugg_banner.setVisible(False)

    def port_for_trace(self, trace: PipelineTrace) -> tuple[QWidget | None, str]:
        if self._teach_draft is not None and self._teach_port is not None:
            return self._teach_port, "#0A84FF"
        if trace.hold_id and self._hold_port is not None:
            return self._hold_port, "#818CF8"
        if trace.matched_rule_id and trace.matched_rule_id in self._rule_ports:
            return self._rule_ports[trace.matched_rule_id], "#0A84FF"
        if trace.matched_bucket_id and trace.matched_bucket_id in self._type_ports:
            return self._type_ports[trace.matched_bucket_id], "#0A84FF"
        if trace.ai_matched and self._ai_port is not None:
            return self._ai_port, "#0A84FF"
        return None, "#0A84FF"

    def ports_for_folder(self, folder_path: str, config: Config) -> list[QWidget]:
        norm = folder_path.replace("/", "\\").strip("\\")
        out: list[QWidget] = []
        for r in config.rules:
            if r.enabled and r.destination.replace("/", "\\").strip("\\") == norm:
                if r.id in self._rule_ports:
                    out.append(self._rule_ports[r.id])
        for bucket in TYPE_BUCKETS:
            if norm in bucket_target_folders(bucket, config):
                if bucket.id in self._type_ports:
                    out.append(self._type_ports[bucket.id])
        return out

    def rebuild(
        self,
        config: Config,
        trace: PipelineTrace,
        *,
        focused_folder: str | None = None,
        folder_choices: list[str] | None = None,
    ) -> None:
        self._config = config
        self._rule_ports.clear()
        self._type_ports.clear()

        # Sync static controls without triggering save loops
        for w in (self.sw_hold_3mf, self.spin_hold_days, self.sw_subfolders, self.sw_ai_fallback, self.cmb_fallback):
            w.blockSignals(True)
        self.sw_hold_3mf.setChecked(config.hold_3mf_enabled)
        self.spin_hold_days.setValue(max(1, config.hold_3mf_days))
        self.spin_hold_days.setEnabled(config.hold_3mf_enabled)
        self.sw_subfolders.setChecked(config.use_type_subfolders)
        self.sw_ai_fallback.setChecked(config.ai_auto_route_fallback or config.ai_smart_hints_enabled)
        self.btn_ai_insights.setVisible(config.ai_auto_route_fallback or config.ai_smart_hints_enabled)
        fb_idx = self.cmb_fallback.findData(config.fallback_mode)
        if fb_idx >= 0:
            self.cmb_fallback.setCurrentIndex(fb_idx)
        for w in (self.sw_hold_3mf, self.spin_hold_days, self.sw_subfolders, self.sw_ai_fallback, self.cmb_fallback):
            w.blockSignals(False)

        # Opt-in Hold & Group step visibility + dynamic 2-step vs 3-step numbering
        show_3mf = bool(config.hold_3mf_enabled or trace.outcome == "hold")
        show_group = bool(config.project_grouping_enabled)
        show_step1 = show_3mf or show_group
        self.step1_row.setVisible(show_step1)
        self.hold_3mf_frame.setVisible(show_3mf)
        self.proj_row_frame.setVisible(show_group)
        self.optin_row.setVisible(not show_step1)
        if show_step1:
            self.badge_1.setText("1")
            self.badge_2.setText("2")
            self.badge_3.setText("3")
            disp_active = 2 if self._teach_draft is not None else trace.winning_step
        else:
            self.badge_2.setText("1")
            self.badge_3.setText("2")
            raw_step = 2 if self._teach_draft is not None else trace.winning_step
            disp_active = max(1, raw_step - 1) if raw_step > 0 else 0

        # 3-State Spine Grammar
        active_step = 2 if self._teach_draft is not None else trace.winning_step
        self.body.active_step = disp_active
        self.body.focused = bool(focused_folder)
        self._apply_step_styles(active_step, trace.outcome, focused=bool(focused_folder))

        is_hold_hit = (not focused_folder and self._teach_draft is None and trace.outcome == "hold")
        self.hold_port_badge.setStyleSheet(
            f"QFrame {{ background-color: #161618; border: 1px solid {'#818CF8' if is_hold_hit else '#2C2C2E'}; border-radius: 7px; }}"
        )
        self.lbl_hold_dot.set_color("#818CF8" if is_hold_hit else "#636366")

        # Teach-by-Example Draft Card
        self._rebuild_teach_card()

        # Step 2 Rules
        while self.rules_layout.count():
            item = self.rules_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        norm_focus = focused_folder.replace("/", "\\").strip("\\") if focused_folder else None
        feed_count = 0
        for rule in config.rules:
            dest_norm = rule.destination.replace("/", "\\").strip("\\")
            is_feeder = bool(norm_focus and rule.enabled and dest_norm == norm_focus)
            if is_feeder:
                feed_count += 1
            row = _RuleRowWidget(
                rule,
                is_expanded=(self._expanded_rule_id == rule.id),
                is_winner=(not focused_folder and self._teach_draft is None and trace.matched_rule_id == rule.id),
                is_shadowed=(rule.id in trace.shadowed_rule_ids),
                blocked_word=trace.blocked_rules.get(rule.id, ""),
                is_focus_feeder=is_feeder,
                dimmed=bool(norm_focus and not is_feeder),
                folder_choices=folder_choices,
                sample_filename=trace.filename or "March_Invoice_2026.pdf",
            )
            row.toggled.connect(self._on_rule_toggled)
            row.expand_requested.connect(self._on_rule_expand_requested)
            row.hover_entered.connect(lambda rid, dest: self.rule_port_hovered.emit(self._rule_ports.get(rid), dest))
            row.hover_left.connect(self.port_hover_left.emit)
            row.reordered.connect(self._on_rule_reordered)
            row.rule_updated.connect(self._update_rule_fields)
            row.rule_deleted.connect(self._on_rule_deleted)
            row.open_full_editor.connect(lambda rid: self.action_requested.emit("open_full_rule_editor", rid))
            self._rule_ports[rule.id] = row.port_badge
            self.rules_layout.addWidget(row)

        if not config.rules and self._teach_draft is None:
            empty_card = QFrame()
            empty_card.setStyleSheet(
                "QFrame { background-color: rgba(255, 255, 255, 0.03); "
                "border: 1px solid rgba(255, 255, 255, 0.07); border-radius: 9px; }"
            )
            el = QHBoxLayout(empty_card)
            el.setContentsMargins(12, 9, 12, 9)
            el.setSpacing(8)
            lbl_es = QLabel(
                "No custom rules yet — files skip straight to automatic file-type sorting below. "
                "Drop any file onto a folder on the right to teach a rule."
            )
            lbl_es.setWordWrap(True)
            lbl_es.setMinimumWidth(0)
            lbl_es.setStyleSheet("color: #98989D; font-size: 12px; border: none; background: transparent;")
            el.addWidget(lbl_es, 1)
            self.rules_layout.addWidget(empty_card)

        # Step 3 File Types
        while self.types_layout.count():
            item = self.types_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        type_feed_count = 0
        for bucket in TYPE_BUCKETS:
            targets = bucket_target_folders(bucket, config)
            is_winner = (not focused_folder and self._teach_draft is None and trace.matched_bucket_id == bucket.id)
            is_feeder = bool(norm_focus and norm_focus in targets)
            if is_feeder:
                type_feed_count += 1
            dest_display = trace.dest_folder if (is_winner and trace.dest_folder) else bucket.default_folder
            t_row = _TypeRowWidget(
                bucket,
                dest_display,
                is_winner=is_winner,
                is_focus_feeder=is_feeder,
                dimmed=bool(norm_focus and not is_feeder),
                auto_unzip_all=config.auto_unzip_all,
            )
            t_row.hover_entered.connect(lambda bid, dest: self.rule_port_hovered.emit(self._type_ports.get(bid), dest))
            t_row.hover_left.connect(self.port_hover_left.emit)
            t_row.unzip_toggled.connect(self._on_archive_unzip_toggled)
            self._type_ports[bucket.id] = t_row.port_badge
            self.types_layout.addWidget(t_row)

        if norm_focus and feed_count == 0 and type_feed_count == 0:
            self.btn_empty_folder_cta.setText(f"+ Create a rule routing into {norm_focus.replace(chr(92), ' › ')}")
            self.btn_empty_folder_cta.setVisible(True)
        else:
            self.btn_empty_folder_cta.setVisible(False)

        self.body.update()

    def _rebuild_teach_card(self) -> None:
        while self.teach_layout.count():
            item = self.teach_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                while item.layout().count():
                    sub = item.layout().takeAt(0)
                    if sub.widget():
                        sub.widget().deleteLater()

        if self._teach_draft is None:
            self._teach_port = None
            self.teach_card.setVisible(False)
            return

        self.teach_card.setVisible(True)
        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        info = QVBoxLayout()
        info.setSpacing(1)
        lbl_t = QLabel(f"Draft rule from {self._teach_draft['filename']}")
        lbl_t.setStyleSheet("color: #F5F5F7; font-weight: 600; font-size: 12.5px; border: none; background: transparent;")
        lbl_s = QLabel(f"Routes into {self._teach_draft['target_folder'].replace(chr(92), ' › ')} — pick cues to match:")
        lbl_s.setStyleSheet("color: #8E8E93; font-size: 11.5px; border: none; background: transparent;")
        info.addWidget(lbl_t)
        info.addWidget(lbl_s)
        hdr.addLayout(info, 1)

        leaf = self._teach_draft["target_folder"].replace("/", "\\").split("\\")[-1]
        port = QFrame()
        port.setStyleSheet("QFrame { background-color: #161618; border: 1px solid #0A84FF; border-radius: 7px; }")
        pl = QHBoxLayout(port)
        pl.setContentsMargins(8, 3, 7, 3)
        pl.setSpacing(5)
        lbl_p = QLabel(f"→ {leaf}")
        lbl_p.setStyleSheet("color: #F5F5F7; font-size: 11.5px; border: none;")
        lbl_d = _PortDotWidget("#3B82F6")
        pl.addWidget(lbl_p)
        pl.addWidget(lbl_d)
        self._teach_port = port
        hdr.addWidget(port)
        self.teach_layout.addLayout(hdr)

        cues_row = QHBoxLayout()
        cues_row.setSpacing(6)
        for idx, (kind, val, checked) in enumerate(self._teach_cue_checks):
            label = f"from {val}" if kind == "site" else (f'"{val}"' if kind == "word" else val)
            btn = QPushButton(f"✓ {label}" if checked else label)
            if checked:
                btn.setStyleSheet(
                    "padding: 3px 8px; font-size: 11.5px; border-radius: 7px; "
                    "background-color: rgba(10, 132, 255, 0.16); border: 1px solid #0A84FF; color: #F5F5F7;"
                )
            else:
                btn.setStyleSheet(
                    "padding: 3px 8px; font-size: 11.5px; border-radius: 7px; "
                    "background-color: #161618; border: 1px solid #2C2C2E; color: #8E8E93;"
                )
            btn.clicked.connect(lambda _=False, i=idx: self._toggle_teach_cue(i))
            cues_row.addWidget(btn)
        cues_row.addStretch(1)
        self.teach_layout.addLayout(cues_row)

        btns = QHBoxLayout()
        btns.setSpacing(8)
        btns.addStretch(1)
        b_cancel = QPushButton("Cancel")
        b_cancel.clicked.connect(self._cancel_teach)
        b_save_move = QPushButton("Save & Move File")
        b_save_move.clicked.connect(lambda: self._commit_teach(True))
        b_save = QPushButton("Save Rule")
        b_save.setObjectName("PrimaryButton")
        b_save.clicked.connect(lambda: self._commit_teach(False))
        btns.addWidget(b_cancel)
        btns.addWidget(b_save_move)
        btns.addWidget(b_save)
        self.teach_layout.addLayout(btns)

    def _toggle_teach_cue(self, idx: int) -> None:
        kind, val, checked = self._teach_cue_checks[idx]
        self._teach_cue_checks[idx] = (kind, val, not checked)
        self._rebuild_teach_card()

    def _cancel_teach(self) -> None:
        self.clear_teach_draft()
        self.teach_cancelled.emit()

    def _commit_teach(self, also_move: bool) -> None:
        if self._teach_draft is None:
            return
        words = [v for k, v, c in self._teach_cue_checks if c and k == "word"]
        exts = [v for k, v, c in self._teach_cue_checks if c and k == "ext"]
        sites = [v for k, v, c in self._teach_cue_checks if c and k == "site"]
        rule = build_rule_from_cues(
            title=self._teach_draft["title"],
            destination=self._teach_draft["target_folder"],
            words=words,
            extensions=exts,
            domains=sites,
        )
        src_path = self._teach_draft["path"]
        self.clear_teach_draft()
        self.teach_committed.emit(rule, also_move, src_path)

    def _apply_step_styles(self, active_step: int, outcome: str, *, focused: bool) -> None:
        badges = (self.badge_1, self.badge_2, self.badge_3)
        states = (self.lbl_state_1, self.lbl_state_2, self.lbl_state_3)
        cards = (self.card_1, self.card_2, self.card_3)

        for idx, (badge, lbl_s, card) in enumerate(zip(badges, states, cards), start=1):
            if focused or active_step == 0:
                badge.setStyleSheet(
                    "background-color: #24272E; border: 1px solid rgba(255, 255, 255, 0.14); border-radius: 13px; "
                    "color: #F5F5F7; font-size: 12px; font-weight: 700;"
                )
                lbl_s.setText("")
                card.setGraphicsEffect(None)
                continue

            if idx == active_step:
                col = "#818CF8" if outcome in ("hold", "ignore") else "#3B82F6"
                badge.setStyleSheet(
                    f"background-color: {col}; border: 1px solid {col}; border-radius: 13px; "
                    "color: #FFFFFF; font-size: 12px; font-weight: 700;"
                )
                lbl_s.setText("· Drafting rule" if self._teach_draft else ("· Held" if outcome == "hold" else "· Matched"))
                lbl_s.setStyleSheet(f"color: {col}; font-size: 11.5px; font-weight: 600;")
            elif idx < active_step:
                badge.setStyleSheet(
                    "background-color: #24272E; border: 1px solid #3A3A3E; border-radius: 13px; "
                    "color: #AEAEB2; font-size: 12px; font-weight: 600;"
                )
                lbl_s.setText("· ✓ Checked")
                lbl_s.setStyleSheet("color: #8E8E93; font-size: 11.5px;")
            else:
                badge.setStyleSheet(
                    "background-color: #161618; border: 1px solid #262629; border-radius: 13px; "
                    "color: #636366; font-size: 12px; font-weight: 600;"
                )
                lbl_s.setText("· Skipped")
                lbl_s.setStyleSheet("color: #636366; font-size: 11.5px;")

    def _on_hold_3mf_changed(self) -> None:
        if self._config is None:
            return
        cfg = copy.deepcopy(self._config)
        cfg.hold_3mf_enabled = self.sw_hold_3mf.isChecked()
        cfg.hold_3mf_days = int(self.spin_hold_days.value())
        self.config_mutated.emit(cfg)

    def _on_step3_toggled(self) -> None:
        if self._config is None:
            return
        cfg = copy.deepcopy(self._config)
        cfg.use_type_subfolders = self.sw_subfolders.isChecked()
        ai_on = self.sw_ai_fallback.isChecked()
        cfg.ai_smart_hints_enabled = ai_on
        cfg.ai_auto_route_fallback = ai_on
        cfg.fallback_mode = str(self.cmb_fallback.currentData() or "other")
        self.config_mutated.emit(cfg)

    def _on_archive_unzip_toggled(self) -> None:
        if self._config is None:
            return
        cfg = copy.deepcopy(self._config)
        cfg.auto_unzip_all = not cfg.auto_unzip_all
        self.config_mutated.emit(cfg)

    def _on_add_blank_rule(self) -> None:
        if self._config is None:
            return
        cfg = copy.deepcopy(self._config)
        new_id = f"rule_{len(cfg.rules) + 1}_{abs(hash(len(cfg.rules))) % 10000}"
        new_rule = Rule(
            id=new_id,
            name="New Rule",
            destination="Documents",
            keywords=["example"],
            enabled=True,
        )
        cfg.rules.insert(0, new_rule)
        self._expanded_rule_id = new_id
        self.config_mutated.emit(cfg)

    def _on_rule_toggled(self, rule_id: str, enabled: bool) -> None:
        self._update_rule_fields(rule_id, {"enabled": enabled})

    def _on_rule_expand_requested(self, rule_id: str) -> None:
        self._expanded_rule_id = None if self._expanded_rule_id == rule_id else rule_id
        self.action_requested.emit("refresh_flow", "")

    def _on_rule_reordered(self, dragged_id: str, target_id: str) -> None:
        if self._config is None:
            return
        cfg = copy.deepcopy(self._config)
        src_idx = next((i for i, r in enumerate(cfg.rules) if r.id == dragged_id), -1)
        dst_idx = next((i for i, r in enumerate(cfg.rules) if r.id == target_id), -1)
        if src_idx < 0 or dst_idx < 0 or src_idx == dst_idx:
            return
        moved = cfg.rules.pop(src_idx)
        cfg.rules.insert(dst_idx, moved)
        self.config_mutated.emit(cfg)

    def _on_rule_deleted(self, rule_id: str) -> None:
        if self._config is None:
            return
        cfg = copy.deepcopy(self._config)
        cfg.rules = [r for r in cfg.rules if r.id != rule_id]
        if self._expanded_rule_id == rule_id:
            self._expanded_rule_id = None
        self.config_mutated.emit(cfg)

    def _update_rule_fields(self, rule_id: str, updates: dict) -> None:
        if self._config is None:
            return
        cfg = copy.deepcopy(self._config)
        for r in cfg.rules:
            if r.id == rule_id:
                for k, v in updates.items():
                    setattr(r, k, v)
                break
        self.config_mutated.emit(cfg)
