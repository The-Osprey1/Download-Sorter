"""Left-hand 3-step Sorting Flow spine with continuous painted rails and guided inline rule composer."""

from __future__ import annotations

import copy
from pathlib import Path
import re
from typing import Any

from PySide6.QtCore import QMimeData, QPoint, QPointF, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QFont, QFontMetrics, QPainter, QPen, QPixmap
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
from download_sorter.router import apply_rename_template, extract_host
from download_sorter.ui.motion import InteractiveRibbonBtn, make_vector_icon
from download_sorter.ui.settings_widgets import MacToggle
from download_sorter.ui.text_scale import scaled_px

MIME_RULE_ID = "application/x-ds-rule-id"
MIME_BUCKET_ID = "application/x-ds-bucket-id"
NEW_FOLDER_SENTINEL = "__NEW_FOLDER__"

QUICK_FILE_TYPE_CHIPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (".pdf", (".pdf",)),
    (".docx", (".docx",)),
    (".xlsx/.csv", (".xlsx", ".csv")),
    (".png/.jpg", (".png", ".jpg")),
    (".zip", (".zip",)),
    (".3mf/.stl", (".3mf", ".stl")),
    (".exe", (".exe",)),
)
_CHIP_EXT_SET: frozenset[str] = frozenset(
    ext for _lbl, exts in QUICK_FILE_TYPE_CHIPS for ext in exts
)


def normalize_ext_tokens(raw: str | list[str] | tuple[str, ...]) -> list[str]:
    """Normalize file extension tokens (e.g. ``'pdf, *.DOCX, .xlsx/.csv'`` -> ``['.pdf', '.docx', '.xlsx', '.csv']``)."""
    pieces: list[str] = []
    if isinstance(raw, str):
        pieces = [p for p in re.split(r"[,;\s/]+", raw) if p]
    else:
        for item in raw:
            pieces.extend(p for p in re.split(r"[,;\s/]+", str(item or "")) if p)
    out: list[str] = []
    for tok in pieces:
        cleaned = tok.strip().lstrip("*").strip().lower()
        if not cleaned:
            continue
        if not cleaned.startswith("."):
            cleaned = "." + cleaned
        if len(cleaned) < 2 or not re.match(r"^\.[a-z0-9_+-]+$", cleaned):
            continue
        if cleaned not in out:
            out.append(cleaned)
    return out


def normalize_domain_tokens(raw: str | list[str] | tuple[str, ...]) -> list[str]:
    """Normalize website URLs or domains via ``extract_host`` (``'https://stripe.com/pay'`` -> ``'stripe.com'``)."""
    pieces: list[str] = []
    if isinstance(raw, str):
        pieces = [p for p in re.split(r"[,;\s]+", raw) if p]
    else:
        for item in raw:
            pieces.extend(p for p in re.split(r"[,;\s]+", str(item or "")) if p)
    out: list[str] = []
    for tok in pieces:
        cleaned = tok.strip().strip("\"'")
        if not cleaned:
            continue
        host = extract_host(cleaned)
        if not host:
            continue
        if host not in out:
            out.append(host)
    return out


def normalize_word_tokens(raw: str | list[str] | tuple[str, ...]) -> list[str]:
    """Normalize comma-separated filename keyword tokens cleanly."""
    pieces: list[str] = []
    if isinstance(raw, str):
        pieces = raw.split(",")
    else:
        pieces = [str(item or "") for item in raw]
    out: list[str] = []
    for tok in pieces:
        cleaned = tok.strip().strip("\"'")
        if cleaned and cleaned not in out:
            out.append(cleaned)
    return out


def rule_has_any_cue(
    *,
    extensions: list[str] | tuple[str, ...] = (),
    keywords: list[str] | tuple[str, ...] = (),
    domains: list[str] | tuple[str, ...] = (),
    content_keywords: list[str] | tuple[str, ...] = (),
    regex_pattern: str = "",
    min_size_mb: float = 0.0,
    max_size_mb: float = 0.0,
    min_age_days: int = 0,
) -> bool:
    """Return True when at least one positive file-matching cue is configured."""
    return bool(
        extensions
        or keywords
        or domains
        or content_keywords
        or (regex_pattern or "").strip()
        or min_size_mb > 0
        or max_size_mb > 0
        or min_age_days > 0
    )


def suggest_rule_name(
    extensions: list[str],
    keywords: list[str],
    domains: list[str],
    destination: str,
) -> str:
    """Generate a concise human-friendly rule name when the user leaves the title blank."""
    leaf = destination.replace("/", "\\").strip("\\").split("\\")[-1] or "Sorted"
    if domains and keywords:
        return f"{domains[0]} ({keywords[0]})"
    if domains:
        return f"From {domains[0]}"
    if keywords:
        return f"{leaf} ({keywords[0]})"
    if extensions:
        ext_label = ", ".join(extensions[:2])
        return f"{ext_label} → {leaf}"
    return f"{leaf} Rule"


def _join_or(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} or {items[1]}"
    return f"{', '.join(items[:-1])}, or {items[-1]}"


def describe_rule_plain_english(
    *,
    extensions: list[str] | tuple[str, ...] = (),
    keywords: list[str] | tuple[str, ...] = (),
    domains: list[str] | tuple[str, ...] = (),
    destination: str = "Documents",
    match_mode: str = "any",
    exclude_keywords: list[str] | tuple[str, ...] = (),
    content_keywords: list[str] | tuple[str, ...] = (),
    regex_pattern: str = "",
    min_size_mb: float = 0.0,
    max_size_mb: float = 0.0,
    min_age_days: int = 0,
) -> str:
    """Build a live plain-English summary sentence (or completeness prompt when empty)."""
    exts = list(extensions)
    words = list(keywords)
    doms = list(domains)
    ckws = list(content_keywords)
    excl = list(exclude_keywords)
    rx = (regex_pattern or "").strip()

    if not rule_has_any_cue(
        extensions=exts,
        keywords=words,
        domains=doms,
        content_keywords=ckws,
        regex_pattern=rx,
        min_size_mb=min_size_mb,
        max_size_mb=max_size_mb,
        min_age_days=min_age_days,
    ):
        return "Add at least one file type, filename word, or website above"

    if exts:
        shown_exts = exts[:3]
        ext_part = _join_or(shown_exts)
        if len(exts) > 3:
            ext_part = f"{', '.join(shown_exts)} (+{len(exts) - 3})"
        subject = f"{ext_part} files"
    else:
        subject = "files"

    clauses: list[str] = []
    if words:
        quoted = [f"'{w}'" for w in words[:3]]
        w_str = _join_or(quoted)
        if len(words) > 3:
            w_str += f" (+{len(words) - 3})"
        clauses.append(f"containing {w_str}")
    if doms:
        d_str = _join_or(doms[:2])
        if len(doms) > 2:
            d_str += f" (+{len(doms) - 2})"
        clauses.append(f"from {d_str}")
    if ckws:
        clauses.append(f"with PDF text '{ckws[0]}'")
    if rx:
        clauses.append(f"matching /{rx}/")

    joiner = " and " if match_mode == "all" else " or "
    cond_str = f" {joiner.join(clauses)}" if clauses else ""

    bounds: list[str] = []
    if min_size_mb > 0:
        bounds.append(f"≥{min_size_mb:g} MB")
    if max_size_mb > 0:
        bounds.append(f"≤{max_size_mb:g} MB")
    if min_age_days > 0:
        bounds.append(f"≥{min_age_days}d old")
    bound_str = f" ({', '.join(bounds)})" if bounds else ""

    excl_str = ""
    if excl:
        excl_quoted = ", ".join(f"'{x}'" for x in excl[:2])
        excl_str = f" (except {excl_quoted})"

    dest_crumb = destination.replace("/", "\\").strip("\\").replace("\\", " › ") or "Downloads"
    return f"Moves {subject}{cond_str}{bound_str}{excl_str} → {dest_crumb}"


def make_flow_drag_pixmap(title: str, subtitle: str = "", accent: str = "#0A84FF") -> QPixmap:
    """Render a crisp macOS Sequoia dark-mode drag ghost pill for Sorting Flow."""
    w, h = 236, (46 if subtitle else 32)
    pix = QPixmap(w, h)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    bg = QColor("#1C1C1E")
    bg.setAlpha(238)
    bdr = QColor(accent)
    bdr.setAlpha(210)
    p.setBrush(bg)
    p.setPen(QPen(bdr, 1.4))
    p.drawRoundedRect(QRectF(1.0, 1.0, w - 2.0, h - 2.0), 9.0, 9.0)

    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(accent))
    p.drawEllipse(QPointF(12.0, 15.0 if subtitle else h / 2.0), 3.2, 3.2)

    f_title = QFont("Segoe UI", 9)
    f_title.setBold(True)
    p.setFont(f_title)
    p.setPen(QColor("#F5F5F7"))
    fm_t = QFontMetrics(f_title)
    p.drawText(
        QRectF(21.0, 5.0 if subtitle else 4.0, w - 28.0, 18.0 if subtitle else h - 8.0),
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        fm_t.elidedText(title, Qt.TextElideMode.ElideRight, w - 30),
    )
    if subtitle:
        f_sub = QFont("Segoe UI", 8)
        p.setFont(f_sub)
        p.setPen(QColor("#98989D"))
        fm_s = QFontMetrics(f_sub)
        p.drawText(
            QRectF(21.0, 23.0, w - 28.0, 17.0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            fm_s.elidedText(subtitle, Qt.TextElideMode.ElideRight, w - 30),
        )
    p.end()
    return pix


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


class _GuidedRuleComposerWidget(QFrame):
    """Structured 3-part Guided Rule Composer shared by New Rule drafts and expanded rule rows."""

    fields_committed = Signal(dict)  # emitted when editing an existing rule & cues are valid
    draft_updated = Signal(dict)  # emitted live in draft mode (without rebuilding parent)
    draft_saved = Signal(object)  # emits completed Rule
    draft_cancelled = Signal()
    new_folder_requested = Signal(str)  # emits current destination / parent hint
    open_full_editor = Signal()
    delete_requested = Signal()
    collapse_requested = Signal()

    def __init__(
        self,
        rule: Rule,
        *,
        is_draft: bool = False,
        folder_choices: list[str] | None = None,
        sample_filename: str = "March_Invoice_2026.pdf",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._rule = copy.deepcopy(rule)
        self._is_draft = is_draft
        self._sample_filename = sample_filename
        self._suppress = False

        init_exts = normalize_ext_tokens(rule.extensions)
        self._chip_exts: set[str] = {e for e in init_exts if e in _CHIP_EXT_SET}
        extra_exts = [e for e in init_exts if e not in _CHIP_EXT_SET]

        if is_draft:
            self.setObjectName("GuidedRuleDraftCard")
            self.setStyleSheet(
                "QFrame#GuidedRuleDraftCard { background-color: #161619; "
                "border: 1px solid rgba(10, 132, 255, 0.55); border-radius: 11px; }"
            )
        else:
            self.setObjectName("GuidedRuleInlineBody")
            self.setStyleSheet("QFrame#GuidedRuleInlineBody { border-top: 1px solid #262629; }")

        cl = QVBoxLayout(self)
        cl.setContentsMargins(10 if is_draft else 0, 9 if is_draft else 8, 10 if is_draft else 0, 9 if is_draft else 0)
        cl.setSpacing(8)

        # Draft Header: Rule title + live destination port badge
        self.port_badge: QFrame | None = None
        self.lbl_port_dest: QLabel | None = None
        self.edit_name: QLineEdit | None = None
        if is_draft:
            hdr = QHBoxLayout()
            hdr.setSpacing(8)
            name_col = QVBoxLayout()
            name_col.setSpacing(2)
            lbl_kicker = QLabel("NEW SORTING RULE")
            lbl_kicker.setStyleSheet(
                "color: #60A5FA; font-size: 10px; font-weight: 700; letter-spacing: 0.6px; border: none;"
            )
            self.edit_name = QLineEdit(rule.name)
            self.edit_name.setPlaceholderText("Rule name (optional — auto-named from your cues)")
            self.edit_name.setToolTip("Optional name for this rule (leave blank to name it automatically)")
            self.edit_name.setStyleSheet(
                "background-color: #111113; border: 1px solid #2C2C2E; border-radius: 7px; "
                "padding: 4px 8px; color: #F5F5F7; font-weight: 600; font-size: 12px;"
            )
            self.edit_name.textChanged.connect(self._on_live_inputs_changed)
            name_col.addWidget(lbl_kicker)
            name_col.addWidget(self.edit_name)
            hdr.addLayout(name_col, 1)

            leaf = (rule.destination.replace("/", "\\").strip("\\").split("\\")[-1]) or "Documents"
            self.port_badge = QFrame()
            self.port_badge.setStyleSheet(
                "QFrame { background-color: #111113; border: 1px solid #0A84FF; border-radius: 7px; }"
            )
            pb = QHBoxLayout(self.port_badge)
            pb.setContentsMargins(8, 3, 7, 3)
            pb.setSpacing(5)
            self.lbl_port_dest = QLabel(f"→ {leaf}")
            self.lbl_port_dest.setStyleSheet("color: #F5F5F7; font-size: 11.5px; font-weight: 600; border: none;")
            pb.addWidget(self.lbl_port_dest)
            pb.addWidget(_PortDotWidget("#3B82F6"))
            hdr.addWidget(self.port_badge, 0, Qt.AlignmentFlag.AlignTop)
            cl.addLayout(hdr)

        # ------------------------------------------------------------------
        # 1. What files should this rule catch?
        # ------------------------------------------------------------------
        lbl_s1 = QLabel("1. What files should this rule catch?")
        lbl_s1.setStyleSheet("color: #EAEAEF; font-size: 11.5px; font-weight: 600; border: none;")
        cl.addWidget(lbl_s1)

        self._chip_buttons: dict[str, QPushButton] = {}
        chips_row1 = QHBoxLayout()
        chips_row1.setSpacing(5)
        chips_row2 = QHBoxLayout()
        chips_row2.setSpacing(5)
        for idx, (chip_label, chip_exts) in enumerate(QUICK_FILE_TYPE_CHIPS):
            btn = QPushButton(chip_label)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(f"Match {chip_label} files")
            btn.setAccessibleName(f"File type {chip_label}")
            is_on = all(e in self._chip_exts for e in chip_exts)
            btn.setChecked(is_on)
            self._style_chip_btn(btn, is_on)
            btn.clicked.connect(lambda checked=False, exts=chip_exts, b=btn: self._on_chip_toggled(b, exts, checked))
            self._chip_buttons[chip_label] = btn
            if idx < 4:
                chips_row1.addWidget(btn)
            else:
                chips_row2.addWidget(btn)

        self.edit_custom_ext = QLineEdit(", ".join(extra_exts))
        self.edit_custom_ext.setPlaceholderText("+ .ext")
        self.edit_custom_ext.setToolTip("Optional extra file extensions (e.g. .step, .flac — dot added automatically)")
        self.edit_custom_ext.setMaximumWidth(scaled_px(96))
        self.edit_custom_ext.setStyleSheet(
            "background-color: #111113; border: 1px solid #2C2C2E; border-radius: 6px; "
            "padding: 2px 7px; font-size: 11px; color: #F5F5F7;"
        )
        self.edit_custom_ext.textChanged.connect(self._on_live_inputs_changed)
        self.edit_custom_ext.editingFinished.connect(self._on_custom_ext_finished)
        chips_row2.addWidget(self.edit_custom_ext)

        chips_row1.addStretch(1)
        chips_row2.addStretch(1)
        cl.addLayout(chips_row1)
        cl.addLayout(chips_row2)

        inputs_row = QHBoxLayout()
        inputs_row.setSpacing(8)

        words_col = QVBoxLayout()
        words_col.setSpacing(2)
        lbl_words = QLabel("Filename words")
        lbl_words.setStyleSheet("color: #8E8E93; font-size: 11px; border: none;")
        self.edit_words = QLineEdit(", ".join(rule.keywords))
        self.edit_words.setPlaceholderText("e.g. invoice, receipt")
        self.edit_words.setToolTip("Match files whose name contains any of these words (comma-separated)")
        self.edit_words.textChanged.connect(self._on_live_inputs_changed)
        self.edit_words.editingFinished.connect(self._on_words_finished)
        words_col.addWidget(lbl_words)
        words_col.addWidget(self.edit_words)
        inputs_row.addLayout(words_col, 1)

        dom_col = QVBoxLayout()
        dom_col.setSpacing(2)
        lbl_dom = QLabel("Website domain")
        lbl_dom.setStyleSheet("color: #8E8E93; font-size: 11px; border: none;")
        self.edit_domains = QLineEdit(", ".join(rule.domains))
        self.edit_domains.setPlaceholderText("e.g. stripe.com or URL")
        self.edit_domains.setToolTip("Match files downloaded from this website (paste a full URL or domain)")
        self.edit_domains.textChanged.connect(self._on_live_inputs_changed)
        self.edit_domains.editingFinished.connect(self._on_domains_finished)
        dom_col.addWidget(lbl_dom)
        dom_col.addWidget(self.edit_domains)
        inputs_row.addLayout(dom_col, 1)
        cl.addLayout(inputs_row)

        # Backwards-compatible alias for any caller inspecting edit_match
        self.edit_match = self.edit_words

        # ------------------------------------------------------------------
        # 2. Where should they go?
        # ------------------------------------------------------------------
        lbl_s2 = QLabel("2. Where should they go?  (pick below or click any folder →)")
        lbl_s2.setStyleSheet("color: #EAEAEF; font-size: 11.5px; font-weight: 600; border: none;")
        cl.addWidget(lbl_s2)

        dest_row = QHBoxLayout()
        dest_row.setSpacing(6)
        self.cmb_dest = QComboBox()
        self.cmb_dest.setToolTip("Choose which folder inside Downloads receives matching files")
        self.cmb_dest.setAccessibleName("Destination folder")
        self._last_valid_dest = rule.destination.replace("/", "\\").strip("\\") or "Documents"
        self._populate_dest_combo(folder_choices or [], self._last_valid_dest)
        self.cmb_dest.currentIndexChanged.connect(self._on_dest_combo_changed)
        dest_row.addWidget(self.cmb_dest, 1)

        self.btn_dest_new_folder = QPushButton("+ Folder...")
        self.btn_dest_new_folder.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_dest_new_folder.setToolTip("Create a new custom folder with guided setup")
        self.btn_dest_new_folder.setAccessibleName("Create new destination folder")
        self.btn_dest_new_folder.setStyleSheet(
            "QPushButton { padding: 4px 9px; font-size: 11.5px; border-radius: 7px; "
            "background-color: #1C1C1E; border: 1px solid #3A3A3E; color: #64D2FF; font-weight: 600; }"
            "QPushButton:hover { background-color: #0A84FF; border-color: #0A84FF; color: #FFFFFF; }"
        )
        self.btn_dest_new_folder.clicked.connect(
            lambda: self.new_folder_requested.emit(self._current_parent_hint())
        )
        dest_row.addWidget(self.btn_dest_new_folder)
        cl.addLayout(dest_row)

        # Optional progressive power fields (shown when populated or toggled on)
        self._show_excl = bool(rule.exclude_keywords)
        self._show_pdf = bool(rule.content_keywords)
        self._show_rename = bool(rule.rename_template)

        self.excl_wrap = QWidget()
        ex_row = QVBoxLayout(self.excl_wrap)
        ex_row.setContentsMargins(0, 0, 0, 0)
        ex_row.setSpacing(2)
        lbl_ex = QLabel("Skip if filename contains")
        lbl_ex.setStyleSheet("color: #8E8E93; font-size: 11px;")
        self.edit_excl = QLineEdit(", ".join(rule.exclude_keywords))
        self.edit_excl.setPlaceholderText("e.g. draft, sample")
        self.edit_excl.textChanged.connect(self._on_live_inputs_changed)
        self.edit_excl.editingFinished.connect(self._commit_if_edit_mode)
        ex_row.addWidget(lbl_ex)
        ex_row.addWidget(self.edit_excl)
        self.excl_wrap.setVisible(self._show_excl)
        cl.addWidget(self.excl_wrap)

        self.pdf_wrap = QWidget()
        pdf_row = QVBoxLayout(self.pdf_wrap)
        pdf_row.setContentsMargins(0, 0, 0, 0)
        pdf_row.setSpacing(2)
        lbl_pdf = QLabel("Inside PDF text contains")
        lbl_pdf.setStyleSheet("color: #8E8E93; font-size: 11px;")
        self.edit_pdf = QLineEdit(", ".join(rule.content_keywords))
        self.edit_pdf.setPlaceholderText("e.g. total due, packing slip")
        self.edit_pdf.textChanged.connect(self._on_live_inputs_changed)
        self.edit_pdf.editingFinished.connect(self._commit_if_edit_mode)
        pdf_row.addWidget(lbl_pdf)
        pdf_row.addWidget(self.edit_pdf)
        self.pdf_wrap.setVisible(self._show_pdf)
        cl.addWidget(self.pdf_wrap)

        self.ren_box = QFrame()
        self.ren_box.setStyleSheet("QFrame { background-color: #111113; border: 1px solid #2C2C2E; border-radius: 8px; }")
        rl = QVBoxLayout(self.ren_box)
        rl.setContentsMargins(8, 6, 8, 6)
        rl.setSpacing(4)
        ren_hdr = QHBoxLayout()
        lbl_ren = QLabel("Rename template")
        lbl_ren.setStyleSheet("color: #8E8E93; font-size: 11px; border: none;")
        ren_hdr.addWidget(lbl_ren)
        ren_hdr.addStretch(1)
        self.edit_rename = QLineEdit(rule.rename_template)
        self.edit_rename.setPlaceholderText("{year}-{month}_{stem}{ext}")
        for tok in ("{year}", "{month}", "{stem}", "{host}"):
            tb = QPushButton(tok)
            tb.setStyleSheet(
                "padding: 1px 6px; font-size: 10.5px; border-radius: 5px; "
                "background-color: #1C1C1E; border: 1px solid #2C2C2E; color: #AEAEB2;"
            )
            tb.clicked.connect(lambda _=False, t=tok: self._append_rename_token(t))
            ren_hdr.addWidget(tb)
        rl.addLayout(ren_hdr)
        self.edit_rename.textChanged.connect(self._on_rename_changed)
        self.edit_rename.editingFinished.connect(self._commit_if_edit_mode)
        rl.addWidget(self.edit_rename)
        self.lbl_rename_prev = QLabel("")
        self.lbl_rename_prev.setStyleSheet("color: #8E8E93; font-size: 11px; border: none;")
        rl.addWidget(self.lbl_rename_prev)
        self.ren_box.setVisible(self._show_rename)
        cl.addWidget(self.ren_box)

        # ------------------------------------------------------------------
        # 3. Plain-English Live Preview & Completeness Guard
        # ------------------------------------------------------------------
        self.preview_card = QFrame()
        self.preview_card.setObjectName("RuleLivePreviewCard")
        pv_l = QHBoxLayout(self.preview_card)
        pv_l.setContentsMargins(10, 7, 10, 7)
        pv_l.setSpacing(7)
        self.lbl_preview_dot = QLabel("●")
        self.lbl_preview_dot.setStyleSheet("font-size: 10px; border: none; background: transparent;")
        self.lbl_preview_sentence = QLabel("")
        self.lbl_preview_sentence.setWordWrap(True)
        self.lbl_preview_sentence.setMinimumWidth(0)
        self.lbl_preview_sentence.setStyleSheet("font-size: 11.5px; border: none; background: transparent;")
        pv_l.addWidget(self.lbl_preview_dot, 0, Qt.AlignmentFlag.AlignTop)
        pv_l.addWidget(self.lbl_preview_sentence, 1)
        cl.addWidget(self.preview_card)

        # Progressive options row (kept separate from CTA row so narrow 900x560 windows never clip buttons)
        opt_bar = QHBoxLayout()
        opt_bar.setSpacing(8)
        self.btn_add_excl = self._text_action_btn("+ Exclude words")
        self.btn_add_excl.setToolTip("Skip files containing certain words (e.g. draft)")
        self.btn_add_excl.setVisible(not self._show_excl)
        self.btn_add_excl.clicked.connect(self._reveal_excl)
        opt_bar.addWidget(self.btn_add_excl)

        self.btn_add_pdf = self._text_action_btn("+ PDF text")
        self.btn_add_pdf.setToolTip("Match words inside a PDF document's text")
        self.btn_add_pdf.setVisible(not self._show_pdf)
        self.btn_add_pdf.clicked.connect(self._reveal_pdf)
        opt_bar.addWidget(self.btn_add_pdf)

        self.btn_add_rename = self._text_action_btn("+ Rename")
        self.btn_add_rename.setToolTip("Automatically rename matched files using date/host tokens")
        self.btn_add_rename.setVisible(not self._show_rename)
        self.btn_add_rename.clicked.connect(self._reveal_rename)
        opt_bar.addWidget(self.btn_add_rename)

        self._auto_unzip = bool(rule.auto_unzip)
        self.btn_unzip = self._text_action_btn(
            "✓ Auto-unzip" if self._auto_unzip else "+ Auto-unzip",
            active=self._auto_unzip,
        )
        self.btn_unzip.setToolTip("Automatically unpack .zip archives matched by this rule")
        self.btn_unzip.clicked.connect(self._toggle_unzip)
        opt_bar.addWidget(self.btn_unzip)

        if not is_draft:
            btn_full = self._text_action_btn("More options...")
            btn_full.setToolTip("Open full rule editor (regex, file size/age limits, ALL/ANY mode)")
            btn_full.clicked.connect(self.open_full_editor.emit)
            opt_bar.addWidget(btn_full)

        opt_bar.addStretch(1)
        cl.addLayout(opt_bar)

        # Primary CTA / Footer row
        act_bar = QHBoxLayout()
        act_bar.setSpacing(8)
        act_bar.addStretch(1)

        if is_draft:
            self.btn_cancel_draft = QPushButton("Cancel")
            self.btn_cancel_draft.setToolTip("Discard this draft rule (Esc)")
            self.btn_cancel_draft.setStyleSheet("padding: 4px 10px; font-size: 11.5px;")
            self.btn_cancel_draft.clicked.connect(self.draft_cancelled.emit)
            self.btn_save_draft = QPushButton("Pick a file type, word, or website")
            self.btn_save_draft.setObjectName("PrimaryButton")
            self.btn_save_draft.setAccessibleName("Save Rule")
            self.btn_save_draft.setToolTip("Save this sorting rule once at least one file type, word, or website is chosen")
            self.btn_save_draft.setStyleSheet("padding: 4px 12px; font-size: 11.5px;")
            self.btn_save_draft.clicked.connect(self._on_save_draft_clicked)
            act_bar.addWidget(self.btn_cancel_draft)
            act_bar.addWidget(self.btn_save_draft)
        else:
            btn_del = QPushButton("Delete")
            btn_del.setToolTip("Delete this sorting rule")
            btn_del.setStyleSheet("border: none; background: transparent; color: #FF453A; font-size: 11.5px; padding: 2px 6px;")
            btn_del.clicked.connect(self.delete_requested.emit)
            btn_done = QPushButton("Done")
            btn_done.setToolTip("Collapse rule editor")
            btn_done.setStyleSheet("padding: 3px 10px; font-size: 11.5px;")
            btn_done.clicked.connect(self.collapse_requested.emit)
            act_bar.addWidget(btn_del)
            act_bar.addWidget(btn_done)

        cl.addLayout(act_bar)
        self._refresh_rename_preview()
        self._sync_live_preview(emit_draft=False)

    @staticmethod
    def _style_chip_btn(btn: QPushButton, active: bool) -> None:
        if active:
            btn.setStyleSheet(
                "QPushButton { padding: 2px 8px; font-size: 11px; border-radius: 6px; font-weight: 600; "
                "background-color: rgba(10, 132, 255, 0.22); border: 1px solid #0A84FF; color: #FFFFFF; }"
                "QPushButton:hover { background-color: rgba(10, 132, 255, 0.32); }"
            )
        else:
            btn.setStyleSheet(
                "QPushButton { padding: 2px 8px; font-size: 11px; border-radius: 6px; font-weight: 500; "
                "background-color: #111113; border: 1px solid #2C2C2E; color: #AEAEB2; }"
                "QPushButton:hover { border-color: #48484A; color: #F5F5F7; }"
            )

    @staticmethod
    def _text_action_btn(text: str, *, active: bool = False) -> QPushButton:
        btn = QPushButton(text)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        col = "#0A84FF" if active else "#8E8E93"
        btn.setStyleSheet(
            f"QPushButton {{ border: none; background: transparent; color: {col}; font-size: 11px; padding: 2px 3px; }}"
            "QPushButton:hover { color: #F5F5F7; }"
        )
        return btn

    def _populate_dest_combo(self, folder_choices: list[str], selected_dest: str) -> None:
        self._suppress = True
        try:
            self.cmb_dest.clear()
            choices: list[str] = []
            for ch in folder_choices:
                norm = ch.replace("/", "\\").strip("\\")
                if norm and norm not in choices:
                    choices.append(norm)
            norm_sel = selected_dest.replace("/", "\\").strip("\\") or "Documents"
            if norm_sel not in choices:
                choices.insert(0, norm_sel)
            for ch in choices:
                self.cmb_dest.addItem(ch.replace("\\", " › "), ch)
            self.cmb_dest.addItem("+ New Folder...", NEW_FOLDER_SENTINEL)
            idx = self.cmb_dest.findData(norm_sel)
            if idx >= 0:
                self.cmb_dest.setCurrentIndex(idx)
            self._last_valid_dest = norm_sel
        finally:
            self._suppress = False

    def set_destination(self, folder_path: str, folder_choices: list[str] | None = None) -> None:
        norm = folder_path.replace("/", "\\").strip("\\")
        if not norm:
            return
        if folder_choices is not None:
            self._populate_dest_combo(folder_choices, norm)
        else:
            self._suppress = True
            try:
                idx = self.cmb_dest.findData(norm)
                if idx < 0:
                    insert_at = max(0, self.cmb_dest.count() - 1)
                    self.cmb_dest.insertItem(insert_at, norm.replace("\\", " › "), norm)
                    idx = insert_at
                self.cmb_dest.setCurrentIndex(idx)
                self._last_valid_dest = norm
            finally:
                self._suppress = False
        self._update_port_badge()
        self._sync_live_preview(emit_draft=True)
        self._commit_if_edit_mode()

    def _current_parent_hint(self) -> str:
        dest = self._last_valid_dest
        return dest.split("\\")[0] if dest else ""

    def _update_port_badge(self) -> None:
        if self.lbl_port_dest is not None:
            leaf = self._last_valid_dest.split("\\")[-1] or "Downloads"
            self.lbl_port_dest.setText(f"→ {leaf}")

    def current_fields(self) -> dict[str, Any]:
        custom_exts = normalize_ext_tokens(self.edit_custom_ext.text())
        ordered_exts: list[str] = []
        for _lbl, exts in QUICK_FILE_TYPE_CHIPS:
            for e in exts:
                if e in self._chip_exts and e not in ordered_exts:
                    ordered_exts.append(e)
        for e in custom_exts:
            if e not in ordered_exts:
                ordered_exts.append(e)

        words = normalize_word_tokens(self.edit_words.text())
        domains = normalize_domain_tokens(self.edit_domains.text())
        excl = normalize_word_tokens(self.edit_excl.text()) if self.excl_wrap.isVisible() else []
        pdf_kws = normalize_word_tokens(self.edit_pdf.text()) if self.pdf_wrap.isVisible() else []
        ren = self.edit_rename.text().strip() if self.ren_box.isVisible() else ""
        dest = self._last_valid_dest or "Documents"

        typed_name = self.edit_name.text().strip() if self.edit_name is not None else self._rule.name
        return {
            "name": typed_name,
            "destination": dest,
            "extensions": ordered_exts,
            "keywords": words,
            "domains": domains,
            "exclude_keywords": excl,
            "content_keywords": pdf_kws,
            "rename_template": ren,
            "auto_unzip": self._auto_unzip,
        }

    def has_valid_cues(self) -> bool:
        f = self.current_fields()
        return rule_has_any_cue(
            extensions=f["extensions"],
            keywords=f["keywords"],
            domains=f["domains"],
            content_keywords=f["content_keywords"],
            regex_pattern=self._rule.regex_pattern,
            min_size_mb=self._rule.min_size_mb,
            max_size_mb=self._rule.max_size_mb,
            min_age_days=self._rule.min_age_days,
        )

    def build_rule(self) -> Rule:
        f = self.current_fields()
        final_name = f["name"] or suggest_rule_name(
            f["extensions"],
            f["keywords"],
            f["domains"],
            f["destination"],
        )
        new_id = self._rule.id or f"rule_{abs(hash((final_name, f['destination'], tuple(f['keywords']), tuple(f['domains']), tuple(f['extensions'])))) % 10_000_000}"
        return Rule(
            id=new_id,
            name=final_name,
            destination=f["destination"],
            keywords=f["keywords"],
            domains=f["domains"],
            extensions=f["extensions"],
            content_keywords=f["content_keywords"],
            exclude_keywords=f["exclude_keywords"],
            regex_pattern=self._rule.regex_pattern,
            match_mode=self._rule.match_mode,
            min_size_mb=self._rule.min_size_mb,
            max_size_mb=self._rule.max_size_mb,
            min_age_days=self._rule.min_age_days,
            rename_template=f["rename_template"],
            auto_unzip=f["auto_unzip"],
            enabled=self._rule.enabled,
        )

    def _sync_live_preview(self, *, emit_draft: bool = True) -> None:
        f = self.current_fields()
        has_cue = self.has_valid_cues()
        sentence = describe_rule_plain_english(
            extensions=f["extensions"],
            keywords=f["keywords"],
            domains=f["domains"],
            destination=f["destination"],
            match_mode=self._rule.match_mode,
            exclude_keywords=f["exclude_keywords"],
            content_keywords=f["content_keywords"],
            regex_pattern=self._rule.regex_pattern,
            min_size_mb=self._rule.min_size_mb,
            max_size_mb=self._rule.max_size_mb,
            min_age_days=self._rule.min_age_days,
        )
        self.lbl_preview_sentence.setText(sentence)
        if has_cue:
            self.preview_card.setStyleSheet(
                "QFrame#RuleLivePreviewCard { background-color: rgba(10, 132, 255, 0.10); "
                "border: 1px solid rgba(10, 132, 255, 0.34); border-radius: 8px; }"
            )
            self.lbl_preview_dot.setStyleSheet("color: #3B82F6; font-size: 10px; border: none; background: transparent;")
            self.lbl_preview_sentence.setStyleSheet("color: #F5F5F7; font-size: 11.5px; border: none; background: transparent;")
        else:
            self.preview_card.setStyleSheet(
                "QFrame#RuleLivePreviewCard { background-color: rgba(255, 159, 10, 0.09); "
                "border: 1px solid rgba(255, 159, 10, 0.32); border-radius: 8px; }"
            )
            self.lbl_preview_dot.setStyleSheet("color: #FF9F0A; font-size: 10px; border: none; background: transparent;")
            self.lbl_preview_sentence.setStyleSheet("color: #FFD60A; font-size: 11.5px; border: none; background: transparent;")

        if self._is_draft and hasattr(self, "btn_save_draft"):
            self.btn_save_draft.setEnabled(has_cue)
            if has_cue:
                leaf = f["destination"].replace("/", "\\").strip("\\").split("\\")[-1] or "Downloads"
                short_leaf = (leaf[:20] + "…") if len(leaf) > 21 else leaf
                self.btn_save_draft.setText(f"Save Rule → {short_leaf}")
            else:
                self.btn_save_draft.setText("Pick a file type, word, or website")
            if self.edit_name is not None and not self.edit_name.text().strip() and has_cue:
                auto_title = suggest_rule_name(f["extensions"], f["keywords"], f["domains"], f["destination"])
                self.edit_name.setPlaceholderText(f"Auto-named: {auto_title}")
            elif self.edit_name is not None and not has_cue:
                self.edit_name.setPlaceholderText("Rule name (optional — auto-named from your cues)")

        if emit_draft and self._is_draft:
            self.draft_updated.emit(f)

    def _on_chip_toggled(self, btn: QPushButton, chip_exts: tuple[str, ...], checked: bool) -> None:
        if checked:
            for e in chip_exts:
                self._chip_exts.add(e)
        else:
            for e in chip_exts:
                self._chip_exts.discard(e)
        self._style_chip_btn(btn, checked)
        self._sync_live_preview(emit_draft=True)
        self._commit_if_edit_mode()

    def _on_custom_ext_finished(self) -> None:
        norm = normalize_ext_tokens(self.edit_custom_ext.text())
        extra = [e for e in norm if e not in _CHIP_EXT_SET]
        for chip_label, chip_exts in QUICK_FILE_TYPE_CHIPS:
            if any(e in norm for e in chip_exts):
                for e in chip_exts:
                    self._chip_exts.add(e)
                btn = self._chip_buttons.get(chip_label)
                if btn is not None:
                    btn.setChecked(True)
                    self._style_chip_btn(btn, True)
        self._suppress = True
        try:
            self.edit_custom_ext.setText(", ".join(extra))
        finally:
            self._suppress = False
        self._sync_live_preview(emit_draft=True)
        self._commit_if_edit_mode()

    def _on_words_finished(self) -> None:
        words = normalize_word_tokens(self.edit_words.text())
        self._suppress = True
        try:
            self.edit_words.setText(", ".join(words))
        finally:
            self._suppress = False
        self._sync_live_preview(emit_draft=True)
        self._commit_if_edit_mode()

    def _on_domains_finished(self) -> None:
        doms = normalize_domain_tokens(self.edit_domains.text())
        self._suppress = True
        try:
            self.edit_domains.setText(", ".join(doms))
        finally:
            self._suppress = False
        self._sync_live_preview(emit_draft=True)
        self._commit_if_edit_mode()

    def _on_dest_combo_changed(self) -> None:
        if self._suppress:
            return
        data = str(self.cmb_dest.currentData() or "")
        if data == NEW_FOLDER_SENTINEL:
            self._suppress = True
            try:
                prev_idx = self.cmb_dest.findData(self._last_valid_dest)
                if prev_idx >= 0:
                    self.cmb_dest.setCurrentIndex(prev_idx)
            finally:
                self._suppress = False
            self.new_folder_requested.emit(self._current_parent_hint())
            return
        if data:
            self._last_valid_dest = data
            self._update_port_badge()
            self._sync_live_preview(emit_draft=True)
            self._commit_if_edit_mode()

    def _on_live_inputs_changed(self) -> None:
        if self._suppress:
            return
        self._sync_live_preview(emit_draft=True)

    def _reveal_excl(self) -> None:
        self.excl_wrap.setVisible(True)
        self.btn_add_excl.setVisible(False)
        self.edit_excl.setFocus()

    def _reveal_pdf(self) -> None:
        self.pdf_wrap.setVisible(True)
        self.btn_add_pdf.setVisible(False)
        self.edit_pdf.setFocus()

    def _reveal_rename(self) -> None:
        self.ren_box.setVisible(True)
        self.btn_add_rename.setVisible(False)
        if not self.edit_rename.text().strip():
            self.edit_rename.setText("{year}-{month}_{stem}{ext}")
        self._refresh_rename_preview()
        self.edit_rename.setFocus()
        self._commit_if_edit_mode()

    def _append_rename_token(self, tok: str) -> None:
        cur = self.edit_rename.text().strip()
        new_tpl = f"{cur}_{tok}" if cur else f"{tok}_{{stem}}{{ext}}"
        self.edit_rename.setText(new_tpl)
        self._refresh_rename_preview()
        self._commit_if_edit_mode()

    def _on_rename_changed(self) -> None:
        self._refresh_rename_preview()
        self._on_live_inputs_changed()

    def _refresh_rename_preview(self) -> None:
        tpl = self.edit_rename.text().strip()
        if tpl:
            preview_name = apply_rename_template(tpl, self._sample_filename)
            self.lbl_rename_prev.setText(f"Example: {self._sample_filename} → {preview_name}")
        else:
            self.lbl_rename_prev.setText("")

    def _toggle_unzip(self) -> None:
        self._auto_unzip = not self._auto_unzip
        self.btn_unzip.setText("✓ Auto-unzip" if self._auto_unzip else "+ Auto-unzip")
        col = "#0A84FF" if self._auto_unzip else "#8E8E93"
        self.btn_unzip.setStyleSheet(
            f"QPushButton {{ border: none; background: transparent; color: {col}; font-size: 11px; padding: 2px 3px; }}"
            "QPushButton:hover { color: #F5F5F7; }"
        )
        self._sync_live_preview(emit_draft=True)
        self._commit_if_edit_mode()

    def flush_and_collect_updates(self) -> dict[str, Any] | None:
        """Normalize all text inputs in-place and return rule field updates if cues are valid."""
        norm_ext = normalize_ext_tokens(self.edit_custom_ext.text())
        extra_ext = [e for e in norm_ext if e not in _CHIP_EXT_SET]
        for chip_label, chip_exts in QUICK_FILE_TYPE_CHIPS:
            if any(e in norm_ext for e in chip_exts):
                for e in chip_exts:
                    self._chip_exts.add(e)
                btn = self._chip_buttons.get(chip_label)
                if btn is not None:
                    btn.setChecked(True)
                    self._style_chip_btn(btn, True)
        words = normalize_word_tokens(self.edit_words.text())
        doms = normalize_domain_tokens(self.edit_domains.text())
        self._suppress = True
        try:
            self.edit_custom_ext.setText(", ".join(extra_ext))
            self.edit_words.setText(", ".join(words))
            self.edit_domains.setText(", ".join(doms))
        finally:
            self._suppress = False
        self._sync_live_preview(emit_draft=self._is_draft)
        if not self.has_valid_cues():
            return None
        f = self.current_fields()
        return {
            "destination": f["destination"],
            "extensions": f["extensions"],
            "keywords": f["keywords"],
            "domains": f["domains"],
            "exclude_keywords": f["exclude_keywords"],
            "content_keywords": f["content_keywords"],
            "rename_template": f["rename_template"],
            "auto_unzip": f["auto_unzip"],
        }

    def _commit_if_edit_mode(self) -> None:
        if self._is_draft or self._suppress:
            return
        if not self.has_valid_cues():
            return
        f = self.current_fields()
        updates = {
            "destination": f["destination"],
            "extensions": f["extensions"],
            "keywords": f["keywords"],
            "domains": f["domains"],
            "exclude_keywords": f["exclude_keywords"],
            "content_keywords": f["content_keywords"],
            "rename_template": f["rename_template"],
            "auto_unzip": f["auto_unzip"],
        }
        self.fields_committed.emit(updates)

    def _on_save_draft_clicked(self) -> None:
        if not self.has_valid_cues():
            return
        self.draft_saved.emit(self.build_rule())


class _RuleRowWidget(QFrame):
    """Collapsible, drag-reorderable Step 2 rule row with flush-right port badge."""

    toggled = Signal(str, bool)
    expand_requested = Signal(str)
    hover_entered = Signal(str, str)  # (rule_id, destination)
    hover_left = Signal()
    reordered = Signal(str, str, bool)  # (dragged_rule_id, target_rule_id, insert_after)
    flow_drag_started = Signal(object, str)  # (port_widget, color)
    flow_drag_finished = Signal()
    rule_updated = Signal(str, dict)
    rule_inline_edited = Signal(str, dict)
    collapse_with_updates = Signal(str, dict)
    rule_deleted = Signal(str)
    open_full_editor = Signal(str)
    new_folder_requested = Signal(str)

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
        self.composer: _GuidedRuleComposerWidget | None = None
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        if is_expanded:
            self._base_qss = (
                "_RuleRowWidget { background-color: #161618; border: 1px solid #3D3D41; border-radius: 11px; }"
            )
        elif is_winner or is_focus_feeder:
            self._base_qss = (
                "_RuleRowWidget { background-color: rgba(10, 132, 255, 0.11); "
                "border: 1px solid rgba(10, 132, 255, 0.45); border-radius: 11px; }"
            )
        else:
            self._base_qss = (
                "_RuleRowWidget { background-color: transparent; border: 1px solid transparent; border-radius: 11px; }"
                "_RuleRowWidget:hover { background-color: #161618; }"
            )
        self.setStyleSheet(self._base_qss)

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
            self.edit_title.setPlaceholderText("Rule name")
            self.edit_title.setToolTip("Edit sorting rule name")
            self.edit_title.setStyleSheet(
                "background: transparent; border: none; border-bottom: 1px solid #3A3A3E; "
                "color: #F5F5F7; font-weight: 600; font-size: 12.5px; padding: 1px 0;"
            )
            self.edit_title.editingFinished.connect(
                lambda: self.rule_inline_edited.emit(rule.id, {"name": self.edit_title.text().strip() or rule.name})
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
        mid.addLayout(sub_row)
        top.addLayout(mid, 1)

        # Flush-right Destination Port Badge (draggable onto any folder on the right)
        dest_leaf = rule.destination.replace("/", "\\").split("\\")[-1] or "Downloads"
        self.port_badge = QFrame()
        self.port_badge.setCursor(Qt.CursorShape.OpenHandCursor)
        self.port_badge.setToolTip(
            f"Routes to {rule.destination} — drag onto any folder on the right to re-route, or drag up/down to reorder"
        )
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

        # Expanded inline Guided Rule Composer
        if is_expanded:
            self.composer = _GuidedRuleComposerWidget(
                rule,
                is_draft=False,
                folder_choices=folder_choices,
                sample_filename=sample_filename,
                parent=self,
            )
            self.edit_match = self.composer.edit_words
            self.cmb_dest = self.composer.cmb_dest
            self.composer.fields_committed.connect(lambda updates: self.rule_inline_edited.emit(rule.id, updates))
            self.composer.new_folder_requested.connect(self.new_folder_requested.emit)
            self.composer.open_full_editor.connect(lambda: self.open_full_editor.emit(rule.id))
            self.composer.delete_requested.connect(lambda: self.rule_deleted.emit(rule.id))
            self.composer.collapse_requested.connect(self._flush_and_collapse)
            root.addWidget(self.composer)

    def collect_pending_updates(self) -> dict[str, Any]:
        updates: dict[str, Any] = {}
        if self.composer is not None:
            comp_updates = self.composer.flush_and_collect_updates()
            if comp_updates is not None:
                updates.update(comp_updates)
        if hasattr(self, "edit_title"):
            clean_title = self.edit_title.text().strip()
            if clean_title:
                updates["name"] = clean_title
        return updates

    def _flush_and_collapse(self) -> None:
        self.collapse_with_updates.emit(self.rule.id, self.collect_pending_updates())

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

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if (
            self._drag_start is not None
            and (event.buttons() & Qt.MouseButton.LeftButton)
            and (event.position().toPoint() - self._drag_start).manhattanLength() >= 10
        ):
            self._drag_start = None
            drag = QDrag(self.window() or self)
            mime = QMimeData()
            mime.setData(MIME_RULE_ID, self.rule.id.encode("utf-8"))
            drag.setMimeData(mime)
            ghost = make_flow_drag_pixmap(
                f"Rule: {self.rule.name}",
                "Drop on rule to reorder · Drop on folder to route",
                "#0A84FF",
            )
            drag.setPixmap(ghost)
            drag.setHotSpot(QPoint(18, ghost.height() // 2))
            self.flow_drag_started.emit(self.port_badge, "#0A84FF")
            try:
                drag.exec(Qt.DropAction.MoveAction | Qt.DropAction.CopyAction)
            finally:
                self.flow_drag_finished.emit()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._drag_start is not None:
            if (event.position().toPoint() - self._drag_start).manhattanLength() < 10:
                if self._is_expanded:
                    self._flush_and_collapse()
                else:
                    self.expand_requested.emit(self.rule.id)
        self._drag_start = None
        super().mouseReleaseEvent(event)

    def enterEvent(self, event) -> None:  # noqa: N802
        self.hover_entered.emit(self.rule.id, self.rule.destination)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.hover_left.emit()
        super().leaveEvent(event)

    def _update_drop_indicator(self, pos_y: float) -> bool:
        insert_after = pos_y > (self.height() / 2.0)
        edge = "border-bottom: 2px solid #0A84FF;" if insert_after else "border-top: 2px solid #0A84FF;"
        self.setStyleSheet(
            f"_RuleRowWidget {{ background-color: rgba(10, 132, 255, 0.10); {edge} border-radius: 9px; }}"
        )
        return insert_after

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasFormat(MIME_RULE_ID):
            dragged_id = bytes(event.mimeData().data(MIME_RULE_ID)).decode("utf-8", "ignore")
            if dragged_id and dragged_id != self.rule.id:
                self._update_drop_indicator(event.position().y())
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasFormat(MIME_RULE_ID):
            dragged_id = bytes(event.mimeData().data(MIME_RULE_ID)).decode("utf-8", "ignore")
            if dragged_id and dragged_id != self.rule.id:
                self._update_drop_indicator(event.position().y())
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self.setStyleSheet(self._base_qss)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        self.setStyleSheet(self._base_qss)
        if event.mimeData().hasFormat(MIME_RULE_ID):
            dragged_id = bytes(event.mimeData().data(MIME_RULE_ID)).decode("utf-8", "ignore")
            insert_after = event.position().y() > (self.height() / 2.0)
            target_id = self.rule.id
            event.acceptProposedAction()
            if dragged_id and dragged_id != target_id:
                QTimer.singleShot(0, lambda d=dragged_id, t=target_id, a=insert_after: self.reordered.emit(d, t, a))
            return
        super().dropEvent(event)


class _TypeRowWidget(QFrame):
    """Single-column Step 3 file-type row with draggable destination port badge."""

    hover_entered = Signal(str, str)  # (bucket_id, target_folder)
    hover_left = Signal()
    unzip_toggled = Signal()
    flow_drag_started = Signal(object, str)  # (port_widget, color)
    flow_drag_finished = Signal()
    route_to_folder_requested = Signal(str, str)  # (bucket_id, folder_path)
    reset_route_requested = Signal(str)  # bucket_id

    def __init__(
        self,
        bucket: TypeBucket,
        target_folder: str,
        *,
        is_winner: bool = False,
        is_focus_feeder: bool = False,
        dimmed: bool = False,
        auto_unzip_all: bool = False,
        folder_choices: list[str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.bucket = bucket
        self.target_folder = target_folder
        self._folder_choices = list(folder_choices or [])
        self._drag_start = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self.setToolTip(
            f"Drag {bucket.name} onto any folder on the right to change where its files go (or right-click)"
        )

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
        row.setContentsMargins(6, 4, 8, 4)
        row.setSpacing(7)

        grip = _GripDotsWidget()
        grip.setToolTip(f"Drag {bucket.name} onto any folder on the right to change destination")
        row.addWidget(grip)

        lbl_name = QLabel(bucket.name)
        lbl_name.setStyleSheet(f"color: {'#636366' if dimmed else '#F5F5F7'}; font-size: 12.5px; font-weight: 600;")
        row.addWidget(lbl_name)

        lbl_exts = QLabel(" ".join(bucket.extensions[:4]))
        lbl_exts.setStyleSheet("color: #8E8E93; font-size: 11px; font-family: 'Cascadia Mono', Consolas, monospace;")
        row.addWidget(lbl_exts, 1)

        if bucket.unzippable:
            btn_uz = QPushButton("✓ Auto-extract" if auto_unzip_all else "Auto-extract: off")
            btn_uz.setToolTip("Automatically unpack .zip archives into a subfolder")
            btn_uz.setStyleSheet(
                f"padding: 2px 7px; font-size: 11px; border-radius: 6px; "
                f"color: {'#0A84FF' if auto_unzip_all else '#8E8E93'}; border: 1px solid #2C2C2E;"
            )
            btn_uz.clicked.connect(self.unzip_toggled.emit)
            row.addWidget(btn_uz)

        leaf = target_folder.replace("/", "\\").split("\\")[-1] or bucket.default_folder
        self.port_badge = QFrame()
        self.port_badge.setCursor(Qt.CursorShape.OpenHandCursor)
        self.port_badge.setToolTip(f"Drag onto any folder in Downloads to route {bucket.name} there")
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

    def _show_context_menu(self, pos) -> None:
        menu = QMenu(self)
        sub = menu.addMenu(f"Route {self.bucket.name} to Folder")
        for ch in self._folder_choices[:24]:
            act = sub.addAction(ch.replace("\\", " › "))
            act.triggered.connect(lambda _=False, dest=ch: self.route_to_folder_requested.emit(self.bucket.id, dest))
        menu.addSeparator()
        act_reset = menu.addAction(f"Reset to Default ({self.bucket.default_folder})")
        act_reset.triggered.connect(lambda: self.reset_route_requested.emit(self.bucket.id))
        menu.exec(self.mapToGlobal(pos))

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if (
            self._drag_start is not None
            and (event.buttons() & Qt.MouseButton.LeftButton)
            and (event.position().toPoint() - self._drag_start).manhattanLength() >= 10
        ):
            self._drag_start = None
            drag = QDrag(self.window() or self)
            mime = QMimeData()
            mime.setData(MIME_BUCKET_ID, self.bucket.id.encode("utf-8"))
            drag.setMimeData(mime)
            ghost = make_flow_drag_pixmap(
                f"{self.bucket.name}",
                "Drop onto any folder on the right to route",
                "#3B82F6",
            )
            drag.setPixmap(ghost)
            drag.setHotSpot(QPoint(18, ghost.height() // 2))
            self.flow_drag_started.emit(self.port_badge, "#3B82F6")
            try:
                drag.exec(Qt.DropAction.MoveAction | Qt.DropAction.CopyAction)
            finally:
                self.flow_drag_finished.emit()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_start = None
        super().mouseReleaseEvent(event)

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
    config_mutated_inline = Signal(object)  # persists Config without destroying active expanded composer row
    rule_port_hovered = Signal(object, str)  # (port_widget, destination_folder)
    port_hover_left = Signal()
    flow_drag_started = Signal(object, str)  # (port_widget, color)
    flow_drag_finished = Signal()
    bucket_routed = Signal(str, str)  # (bucket_id, folder_path)
    bucket_reset = Signal(str)  # bucket_id
    teach_committed = Signal(object, bool, object)  # (Rule, also_move_now, source_path)
    teach_cancelled = Signal()
    draft_target_changed = Signal(str)  # live destination change inside draft composer
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
        self._rule_draft: dict | None = None
        self._draft_composer: _GuidedRuleComposerWidget | None = None
        self._last_folder_choices: list[str] = []
        self._rule_ports: dict[str, QWidget] = {}
        self._type_ports: dict[str, QWidget] = {}
        self._hold_port: QWidget | None = None
        self._ai_port: QWidget | None = None
        self._teach_port: QWidget | None = None
        self._rule_draft_port: QWidget | None = None

        self.body = _SpineBodyWidget()
        self.body.setMinimumWidth(0)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 8, 8)
        self.body_layout.setSpacing(10)

        # Build the 3 step containers (Step 1 is strictly opt-in and hidden by default)
        self.step1_row, self.badge_1, self.lbl_state_1, self.card_1 = self._make_step_shell(
            "1",
            "Hold & group first",
            "Optional: hold specific formats or bundle multi-file downloads",
        )
        self.step2_row, self.badge_2, self.lbl_state_2, self.card_2 = self._make_step_shell(
            "1",
            "Your rules",
            "Matched first · Drag to reorder or drop onto a folder →",
        )
        self.step3_row, self.badge_3, self.lbl_state_3, self.card_3 = self._make_step_shell(
            "2",
            "Sort the rest by file type",
            "Drag any format group onto a folder on the right to re-route",
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
        lbl_sec = QLabel("In-progress downloads wait · Exact copies go to Duplicates · Every move has Undo")
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

        badge_wrap = QWidget()
        bw_l = QVBoxLayout(badge_wrap)
        bw_l.setContentsMargins(0, 8, 0, 0)
        bw_l.setSpacing(0)
        badge = QLabel(num)
        badge.setFixedSize(scaled_px(26), scaled_px(26))
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(
            "background-color: #24272E; border: 1px solid rgba(255, 255, 255, 0.14); border-radius: 13px; "
            "color: #F5F5F7; font-size: 12px; font-weight: 700;"
        )
        bw_l.addWidget(badge)
        row.addWidget(badge_wrap, 0, Qt.AlignmentFlag.AlignTop)

        card = QFrame()
        card.setObjectName("SurfaceCard")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(12, 10, 12, 10)
        cl.setSpacing(6)

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
        self.spin_hold_days.setToolTip("Number of days to hold .3mf files at the top of Downloads")
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
        btn_detect.setToolTip("Scan Downloads for multi-file project bursts")
        btn_detect.clicked.connect(lambda: self.action_requested.emit("detect_projects", ""))
        pl.addWidget(btn_detect)
        cl.addWidget(self.proj_row_frame)

    def _populate_step2_static(self) -> None:
        hdr: QHBoxLayout = self.card_2.property("_hdr_layout")
        cl: QVBoxLayout = self.card_2.layout()  # type: ignore[assignment]

        # + New rule ▾ split controls + ⋯ overflow
        self.btn_new_rule = InteractiveRibbonBtn("New rule", make_vector_icon("plus", "#F5F5F7", 13))
        self.btn_new_rule.setToolTip("Open the guided rule composer to create a new sorting rule")
        self.btn_new_rule.setStyleSheet("padding: 4px 9px;")
        self.btn_new_rule.clicked.connect(self._on_add_blank_rule)

        self.btn_rule_packs = InteractiveRibbonBtn("Packs ▾")
        self.btn_rule_packs.setObjectName("GhostButton")
        self.btn_rule_packs.setToolTip("Add ready-made starter rule packs (School, Finance, 3D Printing, and more)")
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

        self.btn_step2_more = InteractiveRibbonBtn("•••")
        self.btn_step2_more.setObjectName("GhostButton")
        self.btn_step2_more.setAccessibleName("More rule options")
        self.btn_step2_more.setToolTip("More rule options: Smart Order, Carry Over v2.8 / Setup...")
        self.btn_step2_more.setStyleSheet("padding: 4px 7px; font-size: 10px; min-width: 0px;")
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
        btn_accept.setToolTip("Review and add this suggested rule")
        btn_accept.setStyleSheet("padding: 3px 10px; font-size: 11.5px;")
        btn_accept.clicked.connect(lambda: self.action_requested.emit("accept_suggestion", ""))
        btn_dismiss = QPushButton("Dismiss")
        btn_dismiss.setToolTip("Dismiss this rule suggestion")
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

        # Guided New Rule Draft Host (0px when inactive; never saves incomplete rules to config)
        self.rule_draft_host = QWidget()
        self.rule_draft_host.setVisible(False)
        self.rule_draft_layout = QVBoxLayout(self.rule_draft_host)
        self.rule_draft_layout.setContentsMargins(0, 0, 0, 0)
        self.rule_draft_layout.setSpacing(0)
        cl.addWidget(self.rule_draft_host)

        self.rules_host = QWidget()
        self.rules_layout = QVBoxLayout(self.rules_host)
        self.rules_layout.setContentsMargins(0, 0, 0, 0)
        self.rules_layout.setSpacing(3)
        cl.addWidget(self.rules_host)

        self.btn_empty_folder_cta = QPushButton("")
        self.btn_empty_folder_cta.setVisible(False)
        self.btn_empty_folder_cta.setToolTip("Create a custom sorting rule that routes matching files into this folder")
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
        self.btn_ai_insights.setToolTip("Inspect local on-device AI predictions and learned patterns")
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
        self.cmb_fallback.setToolTip("Choose how unrecognized file extensions are handled")
        self.cmb_fallback.setAccessibleName("Unrecognized file handling")
        self.cmb_fallback.addItem("Move to Downloads › Other", "other")
        self.cmb_fallback.addItem("Leave in Downloads", "leave")
        self.cmb_fallback.currentIndexChanged.connect(self._on_step3_toggled)
        fb_row.addWidget(lbl_fb, 1)
        fb_row.addWidget(self.cmb_fallback)
        fl.addLayout(fb_row)

        cl.addWidget(footer)

    # ------------------------------------------------------------------
    # Rule Draft & Active Editor Helpers
    # ------------------------------------------------------------------
    def expanded_rule_id(self) -> str | None:
        return self._expanded_rule_id

    def has_rule_draft(self) -> bool:
        return self._rule_draft is not None

    def start_rule_draft(
        self,
        destination: str = "Documents",
        *,
        name: str = "",
        extensions: list[str] | None = None,
        keywords: list[str] | None = None,
        domains: list[str] | None = None,
    ) -> None:
        """Open a clean inline Guided Rule Composer draft without mutating ``config.rules``."""
        self.clear_teach_draft()
        self._expanded_rule_id = None
        norm_dest = destination.replace("/", "\\").strip("\\") or "Documents"
        self._rule_draft = {
            "name": name,
            "destination": norm_dest,
            "extensions": list(extensions or []),
            "keywords": list(keywords or []),
            "domains": list(domains or []),
            "exclude_keywords": [],
            "content_keywords": [],
            "rename_template": "",
            "auto_unzip": False,
        }
        self._mount_rule_draft_widget()

    def clear_rule_draft(self) -> None:
        self._rule_draft = None
        self._draft_composer = None
        self._rule_draft_port = None
        while self.rule_draft_layout.count():
            item = self.rule_draft_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.rule_draft_host.setVisible(False)

    def draft_target_folder(self) -> str | None:
        if self._teach_draft is not None:
            return str(self._teach_draft.get("target_folder") or "")
        if self._rule_draft is not None:
            return str(self._rule_draft.get("destination") or "")
        return None

    def set_active_rule_destination(self, folder_path: str, folder_choices: list[str] | None = None) -> bool:
        """Set destination on the open New Rule draft or expanded rule row."""
        norm = folder_path.replace("/", "\\").strip("\\")
        if not norm:
            return False
        if folder_choices is not None:
            self._last_folder_choices = list(folder_choices)
        if self._rule_draft is not None and self._draft_composer is not None:
            self._rule_draft["destination"] = norm
            self._draft_composer.set_destination(norm, self._last_folder_choices or None)
            self.draft_target_changed.emit(norm)
            return True
        if self._expanded_rule_id and self._config is not None:
            self._update_rule_fields(self._expanded_rule_id, {"destination": norm})
            return True
        return False

    def set_expanded_rule_dest(self, folder_path: str) -> bool:
        return self.set_active_rule_destination(folder_path)

    def _mount_rule_draft_widget(self) -> None:
        while self.rule_draft_layout.count():
            item = self.rule_draft_layout.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self._draft_composer = None
        self._rule_draft_port = None
        if self._rule_draft is None:
            self.rule_draft_host.setVisible(False)
            return

        draft_rule = Rule(
            id="",
            name=str(self._rule_draft.get("name") or ""),
            destination=str(self._rule_draft.get("destination") or "Documents"),
            extensions=list(self._rule_draft.get("extensions") or []),
            keywords=list(self._rule_draft.get("keywords") or []),
            domains=list(self._rule_draft.get("domains") or []),
            exclude_keywords=list(self._rule_draft.get("exclude_keywords") or []),
            content_keywords=list(self._rule_draft.get("content_keywords") or []),
            rename_template=str(self._rule_draft.get("rename_template") or ""),
            auto_unzip=bool(self._rule_draft.get("auto_unzip")),
            enabled=True,
        )
        comp = _GuidedRuleComposerWidget(
            draft_rule,
            is_draft=True,
            folder_choices=self._last_folder_choices,
            parent=self.rule_draft_host,
        )
        comp.draft_updated.connect(self._on_draft_composer_updated)
        comp.draft_saved.connect(self._on_draft_composer_saved)
        comp.draft_cancelled.connect(self._on_draft_composer_cancelled)
        comp.new_folder_requested.connect(lambda hint: self.action_requested.emit("new_folder_for_rule", hint))
        self._draft_composer = comp
        self._rule_draft_port = comp.port_badge
        self.rule_draft_layout.addWidget(comp)
        self.rule_draft_host.setVisible(True)

    def _on_draft_composer_updated(self, fields: dict) -> None:
        if self._rule_draft is None:
            return
        prev_dest = self._rule_draft.get("destination")
        self._rule_draft.update(fields)
        new_dest = fields.get("destination")
        if new_dest and new_dest != prev_dest:
            self.draft_target_changed.emit(str(new_dest))

    def _on_draft_composer_saved(self, new_rule: Rule) -> None:
        if self._config is None:
            return
        cfg = copy.deepcopy(self._config)
        cfg.rules.insert(0, new_rule)
        self.clear_rule_draft()
        self.config_mutated.emit(cfg)

    def _on_draft_composer_cancelled(self) -> None:
        self.clear_rule_draft()
        self.action_requested.emit("refresh_flow", "")

    def start_teach_draft(self, path: Path, target_folder: str, source_url: str) -> None:
        self.clear_rule_draft()
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
        if self._rule_draft is not None and self._rule_draft_port is not None:
            return self._rule_draft_port, "#0A84FF"
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
        disk_state: dict | None = None,
    ) -> None:
        self._config = config
        self._last_folder_choices = list(folder_choices or [])
        self._rule_ports.clear()
        self._type_ports.clear()

        if self._rule_draft is not None:
            if self._draft_composer is None:
                self._mount_rule_draft_widget()
            else:
                self._draft_composer._populate_dest_combo(
                    self._last_folder_choices,
                    str(self._rule_draft.get("destination") or "Documents"),
                )
        else:
            self.rule_draft_host.setVisible(False)

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

        has_any_draft = self._teach_draft is not None or self._rule_draft is not None

        # Opt-in Hold & Group step visibility + dynamic 2-step vs 3-step numbering
        show_3mf = bool(config.hold_3mf_enabled or trace.outcome == "hold")
        show_group = bool(config.project_grouping_enabled)
        show_step1 = show_3mf or show_group
        self.step1_row.setVisible(show_3mf or show_group)
        self.hold_3mf_frame.setVisible(show_3mf)
        self.proj_row_frame.setVisible(show_group)
        self.optin_row.setVisible(not show_step1)
        if show_step1:
            self.badge_1.setText("1")
            self.badge_2.setText("2")
            self.badge_3.setText("3")
            disp_active = 2 if has_any_draft else trace.winning_step
        else:
            self.badge_2.setText("1")
            self.badge_3.setText("2")
            raw_step = 2 if has_any_draft else trace.winning_step
            disp_active = max(1, raw_step - 1) if raw_step > 0 else 0

        # 3-State Spine Grammar
        active_step = 2 if has_any_draft else trace.winning_step
        self.body.active_step = disp_active
        self.body.focused = bool(focused_folder)
        self._apply_step_styles(active_step, trace.outcome, focused=bool(focused_folder))

        is_hold_hit = (not focused_folder and not has_any_draft and trace.outcome == "hold")
        self.hold_port_badge.setStyleSheet(
            f"QFrame {{ background-color: #161618; border: 1px solid {'#818CF8' if is_hold_hit else '#2C2C2E'}; border-radius: 7px; }}"
        )
        self.lbl_hold_dot.set_color("#818CF8" if is_hold_hit else "#636366")

        # Teach-by-Example Draft Card
        self._rebuild_teach_card()

        # Step 2 Rules
        while self.rules_layout.count():
            item = self.rules_layout.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()

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
                is_winner=(not focused_folder and not has_any_draft and trace.matched_rule_id == rule.id),
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
            row.flow_drag_started.connect(self.flow_drag_started.emit)
            row.flow_drag_finished.connect(self.flow_drag_finished.emit)
            row.rule_updated.connect(self._update_rule_fields)
            row.rule_inline_edited.connect(self._update_rule_fields_inline)
            row.collapse_with_updates.connect(self._on_rule_collapse_with_updates)
            row.rule_deleted.connect(self._on_rule_deleted)
            row.open_full_editor.connect(lambda rid: self.action_requested.emit("open_full_rule_editor", rid))
            row.new_folder_requested.connect(lambda hint: self.action_requested.emit("new_folder_for_rule", hint))
            self._rule_ports[rule.id] = row.port_badge
            self.rules_layout.addWidget(row)

        if not config.rules and not has_any_draft:
            empty_card = QFrame()
            empty_card.setObjectName("EmptyRulesCard")
            empty_card.setStyleSheet(
                "QFrame#EmptyRulesCard { background-color: rgba(255, 255, 255, 0.03); "
                "border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 10px; }"
            )
            el = QVBoxLayout(empty_card)
            el.setContentsMargins(12, 10, 12, 10)
            el.setSpacing(8)
            lbl_es = QLabel(
                "No custom rules yet — files sort automatically by file type below. "
                "Create a rule for specific websites or filenames, or drop any file onto a folder on the right."
            )
            lbl_es.setWordWrap(True)
            lbl_es.setMinimumWidth(0)
            lbl_es.setStyleSheet("color: #98989D; font-size: 12px; border: none; background: transparent;")
            el.addWidget(lbl_es)

            cta_row = QHBoxLayout()
            cta_row.setSpacing(8)
            btn_empty_create = InteractiveRibbonBtn("+ Create a Rule")
            btn_empty_create.setObjectName("PrimaryButton")
            btn_empty_create.setToolTip("Open the guided rule composer to build your first rule")
            btn_empty_create.setStyleSheet("padding: 4px 10px; font-size: 11.5px;")
            btn_empty_create.clicked.connect(self._on_add_blank_rule)

            btn_empty_packs = InteractiveRibbonBtn("Starter Rule Packs ▾")
            btn_empty_packs.setObjectName("GhostButton")
            btn_empty_packs.setToolTip("Add ready-made rules for Receipts, School, Career, or 3D Printing")
            btn_empty_packs.setStyleSheet(
                "QPushButton { padding: 4px 10px; font-size: 11.5px; border: 1px solid #2C2C2E; "
                "border-radius: 7px; color: #D1D1D6; }"
                "QPushButton:hover { border-color: #48484A; color: #F5F5F7; }"
            )
            btn_empty_packs.clicked.connect(
                lambda b=btn_empty_packs: self.menu_packs.exec(b.mapToGlobal(b.rect().bottomLeft()))
            )
            cta_row.addWidget(btn_empty_create)
            cta_row.addWidget(btn_empty_packs)
            cta_row.addStretch(1)
            el.addLayout(cta_row)
            self.rules_layout.addWidget(empty_card)

        # Step 3 File Types
        while self.types_layout.count():
            item = self.types_layout.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()

        existing_folders: dict[str, int] = dict((disk_state or {}).get("existing_folders") or {})
        has_3d_activity = (
            config.show_advanced_tools
            or config.hold_3mf_enabled
            or bool(config.architecture)
            or ("3D Models" in existing_folders)
            or ("CAD" in existing_folders)
            or trace.matched_bucket_id == "t-3d"
            or trace.dest_folder.startswith("3D Models")
            or any("3d" in r.destination.lower() or ".3mf" in r.extensions or ".stl" in r.extensions for r in config.rules)
        )

        type_feed_count = 0
        for bucket in TYPE_BUCKETS:
            if bucket.id == "t-3d" and not has_3d_activity:
                continue
            targets = bucket_target_folders(bucket, config)
            is_winner = (not focused_folder and not has_any_draft and trace.matched_bucket_id == bucket.id)
            is_feeder = bool(norm_focus and norm_focus in targets)
            if is_feeder:
                type_feed_count += 1
            if is_winner and trace.dest_folder:
                dest_display = trace.dest_folder
            else:
                primary_ext = bucket.extensions[0] if bucket.extensions else ""
                mapped = str(config.category_map.get(primary_ext, "") or "").replace("/", "\\").strip("\\")
                dest_display = mapped or bucket.default_folder
            t_row = _TypeRowWidget(
                bucket,
                dest_display,
                is_winner=is_winner,
                is_focus_feeder=is_feeder,
                dimmed=bool(norm_focus and not is_feeder),
                auto_unzip_all=config.auto_unzip_all,
                folder_choices=folder_choices,
            )
            t_row.hover_entered.connect(lambda bid, dest: self.rule_port_hovered.emit(self._type_ports.get(bid), dest))
            t_row.hover_left.connect(self.port_hover_left.emit)
            t_row.unzip_toggled.connect(self._on_archive_unzip_toggled)
            t_row.flow_drag_started.connect(self.flow_drag_started.emit)
            t_row.flow_drag_finished.connect(self.flow_drag_finished.emit)
            t_row.route_to_folder_requested.connect(self.bucket_routed.emit)
            t_row.reset_route_requested.connect(self.bucket_reset.emit)
            self._type_ports[bucket.id] = t_row.port_badge
            self.types_layout.addWidget(t_row)

        if norm_focus and feed_count == 0 and type_feed_count == 0 and self._rule_draft is None:
            self.btn_empty_folder_cta.setText(f"+ Create a rule routing into {norm_focus.replace(chr(92), ' › ')}")
            self.btn_empty_folder_cta.setVisible(True)
        else:
            self.btn_empty_folder_cta.setVisible(False)

        self.body.update()

    def _rebuild_teach_card(self) -> None:
        while self.teach_layout.count():
            item = self.teach_layout.takeAt(0)
            if item is not None and item.widget():
                w = item.widget()
                w.hide()
                w.setParent(None)
                w.deleteLater()
            elif item is not None and item.layout():
                while item.layout().count():
                    sub = item.layout().takeAt(0)
                    if sub is not None and sub.widget():
                        sw = sub.widget()
                        sw.hide()
                        sw.setParent(None)
                        sw.deleteLater()

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

        any_checked = any(checked for _k, _v, checked in self._teach_cue_checks)
        btns = QHBoxLayout()
        btns.setSpacing(8)
        if not any_checked:
            lbl_guard = QLabel("Pick at least one cue above")
            lbl_guard.setStyleSheet("color: #FFD60A; font-size: 11px; border: none; background: transparent;")
            btns.addWidget(lbl_guard)
        btns.addStretch(1)
        b_cancel = QPushButton("Cancel")
        b_cancel.clicked.connect(self._cancel_teach)
        b_save_move = QPushButton("Save & Move File")
        b_save_move.setEnabled(any_checked)
        b_save_move.clicked.connect(lambda: self._commit_teach(True))
        b_save = QPushButton("Save Rule")
        b_save.setObjectName("PrimaryButton")
        b_save.setEnabled(any_checked)
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
        if not (words or exts or sites):
            return
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
        has_any_draft = self._teach_draft is not None or self._rule_draft is not None

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
                lbl_s.setText("· Drafting rule" if has_any_draft else ("· Held" if outcome == "hold" else "· Matched"))
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
        if self._rule_draft is not None and self._draft_composer is not None:
            self._draft_composer.edit_words.setFocus()
            return
        default_dest = "Documents"
        if self._last_folder_choices:
            default_dest = self._last_folder_choices[0]
        self.start_rule_draft(destination=default_dest)
        self.action_requested.emit("refresh_flow", "")

    def _on_rule_toggled(self, rule_id: str, enabled: bool) -> None:
        self._update_rule_fields(rule_id, {"enabled": enabled})

    def _on_rule_expand_requested(self, rule_id: str) -> None:
        if self._rule_draft is not None:
            self.clear_rule_draft()
        if self._expanded_rule_id and self._expanded_rule_id != rule_id:
            self._flush_expanded_row_into_config()
        self._expanded_rule_id = None if self._expanded_rule_id == rule_id else rule_id
        self.action_requested.emit("refresh_flow", "")

    def _flush_expanded_row_into_config(self) -> bool:
        if not self._expanded_rule_id or self._config is None:
            return False
        for i in range(self.rules_layout.count()):
            w = self.rules_layout.itemAt(i).widget()
            if isinstance(w, _RuleRowWidget) and w.rule.id == self._expanded_rule_id:
                updates = w.collect_pending_updates()
                if updates:
                    for r in self._config.rules:
                        if r.id == self._expanded_rule_id:
                            for k, v in updates.items():
                                setattr(r, k, v)
                            return True
        return False

    def _on_rule_collapse_with_updates(self, rule_id: str, updates: dict) -> None:
        self._expanded_rule_id = None
        if self._config is not None and updates:
            cfg = copy.deepcopy(self._config)
            for r in cfg.rules:
                if r.id == rule_id:
                    for k, v in updates.items():
                        setattr(r, k, v)
                    break
            self.config_mutated.emit(cfg)
            return
        self.action_requested.emit("refresh_flow", "")

    def _update_rule_fields_inline(self, rule_id: str, updates: dict) -> None:
        """Persist inline field edits while keeping the expanded row alive for smooth focus/Done clicks."""
        if self._config is None or not updates:
            return
        cfg = copy.deepcopy(self._config)
        for r in cfg.rules:
            if r.id == rule_id:
                for k, v in updates.items():
                    setattr(r, k, v)
                break
        self._config = cfg
        self.config_mutated_inline.emit(cfg)

    def _on_rule_reordered(self, dragged_id: str, target_id: str, insert_after: bool = False) -> None:
        if self._config is None:
            return
        cfg = copy.deepcopy(self._config)
        src_idx = next((i for i, r in enumerate(cfg.rules) if r.id == dragged_id), -1)
        dst_idx = next((i for i, r in enumerate(cfg.rules) if r.id == target_id), -1)
        if src_idx < 0 or dst_idx < 0 or src_idx == dst_idx:
            return
        moved = cfg.rules.pop(src_idx)
        new_dst_idx = next((i for i, r in enumerate(cfg.rules) if r.id == target_id), dst_idx)
        insert_pos = new_dst_idx + (1 if insert_after else 0)
        cfg.rules.insert(max(0, min(len(cfg.rules), insert_pos)), moved)
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
