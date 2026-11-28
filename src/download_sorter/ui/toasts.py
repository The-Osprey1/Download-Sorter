"""Top-right animated Side Ribbon notifications for Download Sorter."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QEasingCurve,
    QElapsedTimer,
    QObject,
    QParallelAnimationGroup,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


CATEGORY_COLORS: dict[str, str] = {
    "cad": "#69E6AD",
    "cnc": "#69E6AD",
    "print": "#69E6AD",
    "pdf": "#FA7183",
    "doc": "#0A84FF",
    "sheet": "#30D158",
    "image": "#BF5AF2",
    "video": "#FF375F",
    "audio": "#5E5CE6",
    "archive": "#64D2FF",
    "installer": "#FF453A",
    "code": "#FF9F0A",
    "default": "#0A84FF",
}

CATEGORY_BADGES: dict[str, str] = {
    "cad": "CAD MODEL",
    "cnc": "CNC",
    "print": "3D PRINT",
    "pdf": "PDF",
    "doc": "DOC",
    "sheet": "SHEET",
    "image": "IMAGE",
    "video": "VIDEO",
    "audio": "AUDIO",
    "archive": "ARCHIVE",
    "installer": "INSTALLER",
    "code": "CODE",
    "default": "SORTED",
}

_EXT_TO_KIND: dict[str, str] = {
    ".step": "cad",
    ".stp": "cad",
    ".iges": "cad",
    ".igs": "cad",
    ".stl": "cad",
    ".obj": "cad",
    ".f3d": "cad",
    ".fcstd": "cad",
    ".dwg": "cad",
    ".dxf": "cad",
    ".3mf": "print",
    ".nc": "cnc",
    ".gcode": "cnc",
    ".ngc": "cnc",
    ".tap": "cnc",
    ".pdf": "pdf",
    ".doc": "doc",
    ".docx": "doc",
    ".txt": "doc",
    ".md": "doc",
    ".rtf": "doc",
    ".odt": "doc",
    ".pages": "doc",
    ".csv": "sheet",
    ".tsv": "sheet",
    ".xls": "sheet",
    ".xlsx": "sheet",
    ".ods": "sheet",
    ".numbers": "sheet",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".gif": "image",
    ".webp": "image",
    ".svg": "image",
    ".bmp": "image",
    ".ico": "image",
    ".heic": "image",
    ".tif": "image",
    ".tiff": "image",
    ".mp4": "video",
    ".mov": "video",
    ".mkv": "video",
    ".avi": "video",
    ".webm": "video",
    ".m4v": "video",
    ".mp3": "audio",
    ".wav": "audio",
    ".flac": "audio",
    ".aac": "audio",
    ".ogg": "audio",
    ".m4a": "audio",
    ".zip": "archive",
    ".7z": "archive",
    ".rar": "archive",
    ".tar": "archive",
    ".gz": "archive",
    ".bz2": "archive",
    ".xz": "archive",
    ".exe": "installer",
    ".msi": "installer",
    ".msix": "installer",
    ".dmg": "installer",
    ".pkg": "installer",
    ".appx": "installer",
    ".py": "code",
    ".js": "code",
    ".ts": "code",
    ".tsx": "code",
    ".jsx": "code",
    ".json": "code",
    ".yaml": "code",
    ".yml": "code",
    ".xml": "code",
    ".html": "code",
    ".css": "code",
    ".sql": "code",
    ".ps1": "code",
    ".sh": "code",
    ".bat": "code",
    ".cpp": "code",
    ".c": "code",
    ".h": "code",
    ".rs": "code",
    ".go": "code",
    ".java": "code",
}


def category_info(dest_path: Path | str | None, folder_disp: str = "") -> tuple[str, str, str]:
    """Return ``(accent_hex, badge_label, kind)`` for a sorted destination file or folder."""
    ext = ""
    if dest_path is not None:
        ext = Path(dest_path).suffix.lower()

    kind = _EXT_TO_KIND.get(ext, "")
    if not kind and folder_disp:
        low = folder_disp.lower()
        if "3d" in low or "print" in low or "bambu" in low or "slicer" in low:
            kind = "print"
        elif "cad" in low or "step" in low or "stl" in low or "model" in low:
            kind = "cad"
        elif "cnc" in low or "gcode" in low:
            kind = "cnc"
        elif "pdf" in low:
            kind = "pdf"
        elif "doc" in low or "text" in low:
            kind = "doc"
        elif "sheet" in low or "csv" in low or "excel" in low:
            kind = "sheet"
        elif "image" in low or "photo" in low or "pic" in low:
            kind = "image"
        elif "video" in low or "movie" in low:
            kind = "video"
        elif "audio" in low or "music" in low:
            kind = "audio"
        elif "archive" in low or "zip" in low:
            kind = "archive"
        elif "install" in low or "setup" in low or "app" in low:
            kind = "installer"
        elif "code" in low or "script" in low:
            kind = "code"

    if not kind:
        kind = "default"
    return (
        CATEGORY_COLORS.get(kind, CATEGORY_COLORS["default"]),
        CATEGORY_BADGES.get(kind, "SORTED"),
        kind,
    )


class CategoryArtWidget(QWidget):
    """46x46 Apple squircle category emblem with OutBack entrance scale & specular sweep."""

    def __init__(self, kind: str = "default", accent_hex: str = "#0A84FF", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(46, 46)
        self._kind = kind
        self._accent = QColor(accent_hex)
        self.art_progress = 1.0

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(420)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.Type.OutBack)
        self._anim.valueChanged.connect(self._on_anim_value)

    def start_entrance(self) -> None:
        self._anim.stop()
        self.art_progress = 0.0
        self._anim.start()
        self.update()

    def _on_anim_value(self, value: object) -> None:
        self.art_progress = float(value)  # type: ignore[arg-type]
        self.update()

    def paintEvent(self, event) -> None:  # noqa: ANN001
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        prog = max(0.0, self.art_progress)
        scale = 0.70 + 0.30 * prog
        p.translate(23.0, 23.0)
        p.scale(scale, scale)
        p.translate(-23.0, -23.0)

        rect = QRectF(2.0, 2.0, 42.0, 42.0)
        fill = QColor(self._accent)
        fill.setAlpha(38)
        border = QColor(self._accent)
        border.setAlpha(98)

        p.setBrush(fill)
        p.setPen(QPen(border, 1.2))
        p.drawRoundedRect(rect, 11.0, 11.0)

        self._paint_glyph(p, self._accent, self._kind)

        # Crisp diagonal specular sheen while animating in
        if 0.04 < prog < 0.99:
            clip = QPainterPath()
            clip.addRoundedRect(rect, 11.0, 11.0)
            p.setClipPath(clip)
            sx = -28.0 + (prog * 84.0)
            grad = QLinearGradient(sx, 0.0, sx + 18.0, 22.0)
            grad.setColorAt(0.0, QColor(255, 255, 255, 0))
            grad.setColorAt(0.5, QColor(255, 255, 255, 58))
            grad.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.fillRect(rect, grad)

    def _paint_glyph(self, p: QPainter, col: QColor, kind: str) -> None:
        pen = QPen(col, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)

        if kind == "cad":
            # Isometric 3D CAD cube
            top = QPolygonF([QPointF(23, 12), QPointF(32, 17), QPointF(23, 22), QPointF(14, 17)])
            p.drawPolygon(top)
            p.drawPolyline([QPointF(14, 17), QPointF(14, 28), QPointF(23, 33), QPointF(32, 28), QPointF(32, 17)])
            p.drawLine(QPointF(23, 22), QPointF(23, 33))
        elif kind == "print":
            # 3D printer hotend nozzle + build plate
            nozzle = QPolygonF([QPointF(18, 13), QPointF(28, 13), QPointF(28, 19), QPointF(24.5, 24), QPointF(21.5, 24), QPointF(18, 19)])
            p.drawPolygon(nozzle)
            p.drawLine(QPointF(23, 25), QPointF(23, 29))
            p.drawLine(QPointF(14, 31.5), QPointF(32, 31.5))
        elif kind == "cnc":
            # Fluted endmill + datum baseline
            p.drawRoundedRect(QRectF(19, 12, 8, 14), 2, 2)
            p.drawLine(QPointF(19, 16), QPointF(27, 20))
            p.drawLine(QPointF(19, 20), QPointF(27, 24))
            p.drawLine(QPointF(21, 26), QPointF(23, 29))
            p.drawLine(QPointF(25, 26), QPointF(23, 29))
            p.drawLine(QPointF(14, 32), QPointF(32, 32))
        elif kind == "pdf":
            # Folded sheet + horizontal accent pill
            p.drawPolygon([QPointF(15, 12), QPointF(27, 12), QPointF(31, 16), QPointF(31, 34), QPointF(15, 34)])
            p.drawPolyline([QPointF(27, 12), QPointF(27, 16), QPointF(31, 16)])
            p.drawLine(QPointF(18.5, 22.5), QPointF(27.5, 22.5))
            p.drawLine(QPointF(18.5, 26.5), QPointF(27.5, 26.5))
            p.drawLine(QPointF(18.5, 30.5), QPointF(24.0, 30.5))
        elif kind == "doc":
            p.drawRoundedRect(QRectF(15, 12, 16, 22), 2.5, 2.5)
            p.drawLine(QPointF(18.5, 18), QPointF(27.5, 18))
            p.drawLine(QPointF(18.5, 22.5), QPointF(27.5, 22.5))
            p.drawLine(QPointF(18.5, 27), QPointF(24.5, 27))
        elif kind == "sheet":
            p.drawRoundedRect(QRectF(14, 14, 18, 18), 2.5, 2.5)
            p.drawLine(QPointF(14, 20), QPointF(32, 20))
            p.drawLine(QPointF(14, 26), QPointF(32, 26))
            p.drawLine(QPointF(22, 14), QPointF(22, 32))
        elif kind == "image":
            p.drawRoundedRect(QRectF(13.5, 14.5, 19, 17), 3.0, 3.0)
            p.drawEllipse(QPointF(18.5, 19.5), 1.8, 1.8)
            p.drawPolyline([QPointF(15.5, 29.0), QPointF(21.0, 23.0), QPointF(24.5, 26.5), QPointF(27.5, 22.0), QPointF(30.5, 29.0)])
        elif kind == "video":
            p.drawRoundedRect(QRectF(13, 15, 20, 16), 3.0, 3.0)
            play = QPolygonF([QPointF(20.5, 19), QPointF(27.5, 23), QPointF(20.5, 27)])
            p.setBrush(col)
            p.drawPolygon(play)
        elif kind == "audio":
            heights = [6, 13, 17, 11, 7]
            for idx, h in enumerate(heights):
                x = 15.0 + idx * 4.0
                p.drawLine(QPointF(x, 23.0 - h / 2.0), QPointF(x, 23.0 + h / 2.0))
        elif kind == "archive":
            p.drawRoundedRect(QRectF(14, 14, 18, 18), 3.0, 3.0)
            p.drawLine(QPointF(23, 14), QPointF(23, 32))
            for y in (18, 22, 26):
                p.drawLine(QPointF(20.5, y), QPointF(25.5, y))
        elif kind == "installer":
            p.drawRoundedRect(QRectF(14, 14, 18, 18), 3.0, 3.0)
            p.drawLine(QPointF(23, 18), QPointF(23, 27.5))
            p.drawPolyline([QPointF(19.5, 24.5), QPointF(23, 28.0), QPointF(26.5, 24.5)])
        elif kind == "code":
            p.drawPolyline([QPointF(18.5, 17.5), QPointF(13.5, 23), QPointF(18.5, 28.5)])
            p.drawPolyline([QPointF(27.5, 17.5), QPointF(32.5, 23), QPointF(27.5, 28.5)])
            p.drawLine(QPointF(24.5, 16.5), QPointF(21.5, 29.5))
        else:
            p.drawPolyline([QPointF(15.5, 23.5), QPointF(20.5, 28.5), QPointF(30.5, 17.5)])


class ToastNotification(QFrame):
    """Floating top-right Side Ribbon with category art, horizontal slide, and hover-pausable timeout."""

    closed = Signal(object)

    WIDTH = 376
    HEIGHT = 94
    DUP_WIDTH = 418
    DUP_HEIGHT = 114

    def __init__(
        self,
        title: str,
        subtitle: str,
        *,
        batch_id: str,
        folder_to_open: Path | None,
        on_undo: Callable[[str], None] | None = None,
        on_open_folder: Callable[[Path], None] | None = None,
        on_replace_old: Callable[[Path, Path | None], None] | None = None,
        on_delete_locate_old: Callable[[Path, Path | None], None] | None = None,
        on_keep_both: Callable[[Path, Path | None], None] | None = None,
        duration_ms: int = 6000,
        kind: str = "default",
        accent_hex: str = "#0A84FF",
        badge_label: str = "SORTED",
        slot: int = 0,
        is_duplicate: bool = False,
        dup_path: Path | None = None,
        existing_path: Path | None = None,
    ) -> None:
        super().__init__(None)
        self.batch_id = batch_id
        self.is_duplicate = bool(is_duplicate)
        self._dup_path = dup_path
        self._existing_path = existing_path
        self._folder_to_open = folder_to_open
        self._on_undo = on_undo
        self._on_open_folder = on_open_folder
        self._on_replace_old = on_replace_old
        self._on_delete_locate_old = on_delete_locate_old
        self._on_keep_both = on_keep_both
        self._duration_ms = max(250, int(duration_ms))
        self._elapsed_ms = 0.0
        self._remaining_fraction = 1.0
        self._hovered = False
        self._closing = False
        self._slot = max(0, int(slot))

        # Auto-enrich category art when generic defaults are passed directly (e.g. unit tests)
        if kind == "default" and badge_label == "SORTED" and accent_hex == "#0A84FF":
            accent_hex, badge_label, kind = category_info(Path(title), subtitle)
        if self.is_duplicate:
            accent_hex = "#FF9F0A"

        self._kind = kind
        self._accent = QColor(accent_hex)
        self._badge_label = badge_label

        self.setObjectName("SideRibbon")
        # NOTE: Omit Qt.WindowType.WindowDoesNotAcceptFocus on Windows because it causes
        # WM_MOUSEACTIVATE=MA_NOACTIVATE to drop clicks on child buttons like close_btn.
        # WA_ShowWithoutActivating + NoFocus focus policy keep the user's app focused
        # while ensuring close/Undo/Jump buttons work reliably on first click.
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        card_w = self.DUP_WIDTH if self.is_duplicate else self.WIDTH
        card_h = self.DUP_HEIGHT if self.is_duplicate else self.HEIGHT
        self.setFixedSize(card_w, card_h)

        root = QHBoxLayout(self)
        root.setContentsMargins(16, 11, 12, 11)
        root.setSpacing(12)

        self.art_widget = CategoryArtWidget(kind=kind, accent_hex=accent_hex, parent=self)
        root.addWidget(self.art_widget, 0, Qt.AlignmentFlag.AlignVCenter)

        right_col = QVBoxLayout()
        right_col.setContentsMargins(0, 0, 0, 0)
        right_col.setSpacing(2)

        eyebrow_row = QHBoxLayout()
        eyebrow_row.setContentsMargins(0, 0, 0, 0)
        eyebrow_row.setSpacing(6)

        eyebrow_prefix = "DUPLICATE" if self.is_duplicate else "SORTED"
        # CAD and 3D print files are usually inserted into another app next: say it is ready.
        self.ready_to_insert = (not self.is_duplicate) and kind in ("cad", "cnc", "print")
        eyebrow = f"● {eyebrow_prefix} · {badge_label.upper()}"
        if self.ready_to_insert:
            eyebrow += " · READY TO INSERT"
        self.eyebrow_lbl = QLabel(eyebrow)
        if self.ready_to_insert:
            self.eyebrow_lbl.setToolTip(
                "Open a file dialog in Fusion, your slicer or any app, and this file is offered next to it."
            )
        self.eyebrow_lbl.setStyleSheet(
            f"color: {accent_hex}; font-size: 10px; font-weight: 700; "
            "letter-spacing: 0.6px; background: transparent;"
        )
        from download_sorter.ui.motion import SortyMascotWidget

        # A tiny Sorty cheers each sort, the same character as in the main window.
        self.sorty = SortyMascotWidget(size=18, parent=self)
        self.sorty.setToolTip("Sorty filed this for you")
        self.sorty.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        if self.is_duplicate:
            self.sorty.set_pose("safe")
        eyebrow_row.addWidget(self.sorty, 0, Qt.AlignmentFlag.AlignVCenter)
        eyebrow_row.addWidget(self.eyebrow_lbl, 1)

        self.close_btn = QPushButton("✕")
        self.close_btn.setFixedSize(24, 24)
        self.close_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.close_btn.setToolTip("Close notification")
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #98989D; border: none; border-radius: 6px; "
            "font-size: 12px; font-weight: 700; padding: 0px; }"
            "QPushButton:hover { background: rgba(255, 255, 255, 28); color: #FFFFFF; }"
        )
        self.close_btn.pressed.connect(self._on_close_clicked)
        self.close_btn.clicked.connect(self._on_close_clicked)
        eyebrow_row.addWidget(self.close_btn, 0)
        right_col.addLayout(eyebrow_row)

        self.title_lbl = QLabel()
        self.title_lbl.setStyleSheet(
            "color: #F5F5F7; font-size: 13px; font-weight: 600; background: transparent;"
        )
        fm = QFontMetrics(QFont("Segoe UI", 13, QFont.Weight.DemiBold))
        self.title_lbl.setText(fm.elidedText(title, Qt.TextElideMode.ElideMiddle, 260))
        self.title_lbl.setToolTip(title)
        right_col.addWidget(self.title_lbl)

        self.subtitle_lbl = QLabel()
        self.subtitle_lbl.setStyleSheet(
            "color: #98989D; font-size: 11px; background: transparent;"
        )
        fm_sub = QFontMetrics(QFont("Segoe UI", 11))
        sub_max_w = 320 if self.is_duplicate else 148
        self.subtitle_lbl.setText(fm_sub.elidedText(subtitle, Qt.TextElideMode.ElideRight, sub_max_w))
        self.subtitle_lbl.setToolTip(subtitle)

        self.open_btn = QPushButton("Show in Folder ›")
        self.open_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_btn.setToolTip("Open destination folder in File Explorer")
        self.open_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {accent_hex}; border: none; "
            "font-size: 11px; font-weight: 600; padding: 2px 4px; }"
            "QPushButton:hover { text-decoration: underline; }"
        )
        if self._folder_to_open is None or self.is_duplicate:
            self.open_btn.setVisible(False)
        else:
            self.open_btn.clicked.connect(self._handle_open)

        self.undo_btn = QPushButton("Undo")
        self.undo_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.undo_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.undo_btn.setToolTip("Undo this sort and move file(s) back to Downloads")
        self.undo_btn.setStyleSheet(
            "QPushButton { background: #2C2C2E; color: #F5F5F7; border: 1px solid #3A3A3C; "
            "border-radius: 6px; padding: 3px 10px; font-size: 11px; font-weight: 600; }"
            "QPushButton:hover { background: #3A3A3C; }"
        )
        self.undo_btn.clicked.connect(self._handle_undo)
        if self.is_duplicate:
            self.undo_btn.setVisible(False)

        self.replace_old_btn = QPushButton("Replace Old", self)
        self.replace_old_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.replace_old_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.replace_old_btn.setStyleSheet(
            "QPushButton { background: #FF9F0A; color: #111111; border: none; "
            "border-radius: 6px; padding: 3px 8px; font-size: 10.5px; font-weight: 700; }"
            "QPushButton:hover { background: #FFB340; }"
            "QPushButton:disabled { background: #3A3A3C; color: #8E8E93; }"
        )
        self.replace_old_btn.clicked.connect(self._handle_replace_old)
        if self._existing_path is None or self._dup_path is None:
            self.replace_old_btn.setEnabled(False)
            self.replace_old_btn.setToolTip("Original file path not matched automatically — open Duplicates Manager to inspect.")
        else:
            self.replace_old_btn.setToolTip(
                "Recycle the older sorted file and replace it with this newly downloaded duplicate."
            )

        self.delete_locate_btn = QPushButton("Delete & Locate Old", self)
        self.delete_locate_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.delete_locate_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.delete_locate_btn.setToolTip(
            "Move the new duplicate to the Recycle Bin and highlight the older file in File Explorer."
        )
        self.delete_locate_btn.setStyleSheet(
            "QPushButton { background: #2C2C2E; color: #F5F5F7; border: 1px solid #48484A; "
            "border-radius: 6px; padding: 3px 8px; font-size: 10.5px; font-weight: 600; }"
            "QPushButton:hover { background: #3A3A3C; }"
        )
        self.delete_locate_btn.clicked.connect(self._handle_delete_locate_old)

        self.keep_both_btn = QPushButton("Keep Both", self)
        self.keep_both_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.keep_both_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.keep_both_btn.setToolTip(
            "Move the new file beside the existing file in its destination folder using numbered naming (e.g. (2))."
        )
        self.keep_both_btn.setStyleSheet(
            "QPushButton { background: #2C2C2E; color: #0A84FF; border: 1px solid #3A3A3C; "
            "border-radius: 6px; padding: 3px 8px; font-size: 10.5px; font-weight: 600; }"
            "QPushButton:hover { background: #3A3A3C; }"
        )
        self.keep_both_btn.clicked.connect(self._handle_keep_both)

        if self.is_duplicate:
            right_col.addWidget(self.subtitle_lbl)
            dup_actions_row = QHBoxLayout()
            dup_actions_row.setContentsMargins(0, 4, 0, 0)
            dup_actions_row.setSpacing(6)
            dup_actions_row.addWidget(self.replace_old_btn)
            dup_actions_row.addWidget(self.delete_locate_btn)
            dup_actions_row.addWidget(self.keep_both_btn)
            dup_actions_row.addStretch(1)
            right_col.addLayout(dup_actions_row)
        else:
            self.replace_old_btn.setVisible(False)
            self.delete_locate_btn.setVisible(False)
            self.keep_both_btn.setVisible(False)
            bottom_row = QHBoxLayout()
            bottom_row.setContentsMargins(0, 2, 0, 0)
            bottom_row.setSpacing(6)
            bottom_row.addWidget(self.subtitle_lbl, 1)
            bottom_row.addWidget(self.open_btn, 0)
            bottom_row.addWidget(self.undo_btn, 0)
            right_col.addLayout(bottom_row)
        root.addLayout(right_col, 1)

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(25)
        self._tick_timer.timeout.connect(self._on_tick)
        self._step_clock = QElapsedTimer()

        self._hard_timeout_timer = QTimer(self)
        self._hard_timeout_timer.setSingleShot(True)
        self._hard_timeout_timer.timeout.connect(self._on_hard_timeout)

        self._slide_group: QParallelAnimationGroup | None = None
        self._slot_anim: QPropertyAnimation | None = None

    def show_animated(self) -> None:
        self._closing = False
        self._elapsed_ms = 0.0
        self._remaining_fraction = 1.0

        target = self._anchor_top_right(self._slot)
        start = QPoint(target.x() + 140, target.y())
        self.move(start)
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()

        self.art_widget.start_entrance()
        if not self.is_duplicate:
            self.sorty.celebrate(1800)

        group = QParallelAnimationGroup(self)
        pos_anim = QPropertyAnimation(self, b"pos", group)
        pos_anim.setDuration(380)
        pos_anim.setStartValue(start)
        pos_anim.setEndValue(target)
        # A soft spring, like the sheets on the Folders screen.
        curve = QEasingCurve(QEasingCurve.Type.OutBack)
        curve.setOvershoot(1.1)
        pos_anim.setEasingCurve(curve)
        group.addAnimation(pos_anim)
        fade = QPropertyAnimation(self, b"windowOpacity", group)
        fade.setDuration(200)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        group.addAnimation(fade)
        self._slide_group = group
        group.start()

        self._step_clock.start()
        self._tick_timer.start()
        self._hard_timeout_timer.start(self._duration_ms + 250)

    def move_to_slot(self, slot: int) -> None:
        self._slot = max(0, int(slot))
        if self._closing:
            return
        target = self._anchor_top_right(self._slot)
        anim = QPropertyAnimation(self, b"pos", self)
        anim.setDuration(220)
        anim.setStartValue(self.pos())
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._slot_anim = anim
        anim.start()

    def _anchor_top_right(self, slot: int | None = None) -> QPoint:
        use_slot = self._slot if slot is None else max(0, int(slot))
        step_h = max(self.HEIGHT, self.height()) + 10
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if screen is None:
            return QPoint(0, 18 + use_slot * step_h)
        area = screen.availableGeometry()
        x = area.right() - self.width() - 18
        y = area.top() + 18 + use_slot * step_h
        pos = QPoint(x, y)
        return pos

    def _anchor_bottom_right(self) -> QPoint:
        pos = self._anchor_top_right()
        self.move(pos)
        return pos

    def _on_tick(self) -> None:
        if self._closing:
            return
        delta = self._step_clock.restart()
        if self._hovered:
            return
        self._elapsed_ms += max(0.0, float(delta))
        frac = max(0.0, 1.0 - (self._elapsed_ms / float(self._duration_ms)))
        self._remaining_fraction = frac
        self.update(0, self.height() - 4, self.width(), 4)
        if self._elapsed_ms >= self._duration_ms:
            self.dismiss(immediate=True)

    def _on_hard_timeout(self) -> None:
        if not self._closing and not self._hovered:
            self.dismiss(immediate=True)

    def mousePressEvent(self, event) -> None:  # noqa: ANN001
        pt = event.position().toPoint()
        if self.close_btn.geometry().adjusted(-6, -6, 6, 6).contains(pt) or event.button() == Qt.MouseButton.RightButton:
            self.dismiss(immediate=True)
            event.accept()
            return
        super().mousePressEvent(event)

    def enterEvent(self, event) -> None:  # noqa: ANN001
        self._hovered = True
        self._hard_timeout_timer.stop()
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: ANN001
        self._hovered = False
        self._step_clock.restart()
        remaining_ms = int(max(100.0, float(self._duration_ms) - self._elapsed_ms)) + 150
        if not self._closing:
            self._hard_timeout_timer.start(remaining_ms)
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # noqa: ANN001
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        body = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        clip = QPainterPath()
        clip.addRoundedRect(body, 13.0, 13.0)
        p.setClipPath(clip)

        # Apple Dark Elevated Surface (#1C1C1E)
        p.setBrush(QColor(28, 28, 30, 246))
        if self._hovered:
            border_col = QColor(self._accent)
            border_col.setAlpha(155)
        else:
            border_col = QColor("#2C2C2E")
        p.setPen(QPen(border_col, 1.0))
        p.drawRoundedRect(body, 13.0, 13.0)

        # 4px Left Category Accent Stripe
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._accent)
        p.drawRoundedRect(QRectF(0.0, 10.0, 4.0, self.height() - 20.0), 2.0, 2.0)

        # Bottom Timeout Track + Depleting Progress Bar
        p.setBrush(QColor(44, 44, 46, 180))
        p.drawRect(QRectF(0.0, self.height() - 3.0, float(self.width()), 3.0))

        bar_col = QColor(self._accent)
        bar_col.setAlpha(215 if not self._hovered else 130)
        p.setBrush(bar_col)
        p.drawRect(
            QRectF(
                0.0,
                self.height() - 3.0,
                max(0.0, float(self.width()) * self._remaining_fraction),
                3.0,
            )
        )

    def _handle_undo(self) -> None:
        self._tick_timer.stop()
        self._hard_timeout_timer.stop()
        if self._on_undo is not None:
            self._on_undo(self.batch_id)
        self.dismiss(immediate=True)

    def _handle_open(self) -> None:
        self._tick_timer.stop()
        self._hard_timeout_timer.stop()
        if self._on_open_folder is not None and self._folder_to_open is not None:
            self._on_open_folder(self._folder_to_open)
        self.dismiss(immediate=True)

    def _handle_replace_old(self) -> None:
        self._tick_timer.stop()
        self._hard_timeout_timer.stop()
        if self._on_replace_old is not None and self._dup_path is not None:
            self._on_replace_old(self._dup_path, self._existing_path)
        self.dismiss(immediate=True)

    def _handle_delete_locate_old(self) -> None:
        self._tick_timer.stop()
        self._hard_timeout_timer.stop()
        if self._on_delete_locate_old is not None and self._dup_path is not None:
            self._on_delete_locate_old(self._dup_path, self._existing_path)
        self.dismiss(immediate=True)

    def _handle_keep_both(self) -> None:
        self._tick_timer.stop()
        self._hard_timeout_timer.stop()
        if self._on_keep_both is not None and self._dup_path is not None:
            self._on_keep_both(self._dup_path, self._existing_path)
        self.dismiss(immediate=True)

    def _on_close_clicked(self) -> None:
        self.dismiss(immediate=True)

    def _stop_runtime(self) -> None:
        self._tick_timer.stop()
        self._hard_timeout_timer.stop()
        if self._slide_group is not None:
            self._slide_group.stop()
            self._slide_group = None
        if self._slot_anim is not None:
            self._slot_anim.stop()
            self._slot_anim = None

    def closeEvent(self, event) -> None:  # noqa: ANN001
        self._stop_runtime()
        self._closing = True
        super().closeEvent(event)

    def dismiss(self, *, immediate: bool = True) -> None:
        self._stop_runtime()
        already_closing = self._closing
        self._closing = True
        self.hide()
        self.close()
        if not already_closing:
            self.closed.emit(self)

    def _finalize_close(self) -> None:
        self._stop_runtime()
        self.hide()
        self.close()


class ToastManager(QObject):
    """Manages stacked top-right Side Ribbon notifications cleanly."""

    MAX_VISIBLE = 4

    def __init__(
        self,
        *,
        on_undo: Callable[[str], None] | None = None,
        on_open_folder: Callable[[Path], None] | None = None,
        on_replace_old: Callable[[Path, Path | None], None] | None = None,
        on_delete_locate_old: Callable[[Path, Path | None], None] | None = None,
        on_keep_both: Callable[[Path, Path | None], None] | None = None,
        default_duration_ms: int = 6000,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_undo = on_undo
        self._on_open_folder = on_open_folder
        self._on_replace_old = on_replace_old
        self._on_delete_locate_old = on_delete_locate_old
        self._on_keep_both = on_keep_both
        self.default_duration_ms = max(500, int(default_duration_ms))
        self._active: list[ToastNotification] = []
        self._current: ToastNotification | None = None

    def close_all(self) -> None:
        """Immediately close all active Side Ribbon notifications."""
        for toast in list(self._active):
            toast.dismiss(immediate=True)
        self._active.clear()
        self._current = None

    def _spawn_toast(self, toast: ToastNotification) -> ToastNotification:
        while len(self._active) >= self.MAX_VISIBLE:
            oldest = self._active.pop(0)
            oldest.dismiss(immediate=True)
            self._reposition_slots()
        toast._slot = len(self._active)
        toast.closed.connect(self._on_toast_closed)
        self._active.append(toast)
        self._current = toast
        toast.show_animated()
        return toast

    def _show_duplicate_move(
        self,
        batch_id: str,
        move: dict[str, Any],
        use_ms: int,
    ) -> ToastNotification:
        dest_raw = move.get("dest_path")
        dup_path = Path(str(dest_raw)) if dest_raw else None
        existing_raw = str(move.get("existing_path") or "").strip()
        existing_path = Path(existing_raw) if existing_raw else None
        existing_disp = str(
            move.get("folder_display")
            or (existing_path.parent.name if existing_path is not None else "Downloads")
        )
        _, badge_label, kind = category_info(existing_path or dup_path, existing_disp)
        title = str(
            move.get("file_name")
            or move.get("filename")
            or (dup_path.name if dup_path is not None else "Duplicate file")
        )
        subtitle = f"Already in {existing_disp}"
        toast = ToastNotification(
            title,
            subtitle,
            batch_id=batch_id,
            folder_to_open=existing_path.parent if existing_path is not None else (dup_path.parent if dup_path else None),
            on_undo=self._on_undo,
            on_open_folder=self._on_open_folder,
            on_replace_old=self._on_replace_old,
            on_delete_locate_old=self._on_delete_locate_old,
            on_keep_both=self._on_keep_both,
            duration_ms=use_ms,
            kind=kind,
            accent_hex="#FF9F0A",
            badge_label=badge_label,
            slot=len(self._active),
            is_duplicate=True,
            dup_path=dup_path,
            existing_path=existing_path,
        )
        return self._spawn_toast(toast)

    def show_moves(
        self,
        batch_id: str,
        moves: list[dict[str, Any]],
        *,
        duration_ms: int | None = None,
    ) -> ToastNotification | None:
        if not moves:
            return None

        use_ms = self.default_duration_ms if duration_ms is None else max(250, int(duration_ms))
        dups = [m for m in moves if m.get("is_duplicate")]
        non_dups = [m for m in moves if not m.get("is_duplicate")]

        if dups and not non_dups and len(dups) == 1:
            return self._show_duplicate_move(batch_id, dups[0], use_ms)

        primary_moves = non_dups if non_dups else moves
        first = primary_moves[0]
        dest_raw = first.get("dest_path")
        dest = Path(str(dest_raw)) if dest_raw else None
        folder_disp = str(first.get("folder_display") or (dest.parent.name if dest else "Sorted"))
        accent_hex, badge_label, kind = category_info(dest, folder_disp)

        if len(primary_moves) == 1:
            title = str(first.get("file_name") or first.get("filename") or (dest.name if dest else "Sorted file"))
            subtitle = f"Saved to {folder_disp}"
            folder_to_open = dest.parent if dest else None
        else:
            title = f"Sorted {len(primary_moves)} files"
            subtitle = f"Into {folder_disp} and more"
            folder_to_open = dest.parent if dest else None

        toast = ToastNotification(
            title,
            subtitle,
            batch_id=batch_id,
            folder_to_open=folder_to_open,
            on_undo=self._on_undo,
            on_open_folder=self._on_open_folder,
            duration_ms=use_ms,
            kind=kind,
            accent_hex=accent_hex,
            badge_label=badge_label,
            slot=len(self._active),
        )
        primary_toast = self._spawn_toast(toast)
        for dup_move in dups[:2]:
            self._show_duplicate_move(batch_id, dup_move, use_ms)
        return primary_toast

    def _on_toast_closed(self, toast: ToastNotification) -> None:
        if toast in self._active:
            self._active.remove(toast)
        if self._current is toast:
            self._current = self._active[-1] if self._active else None
        self._reposition_slots()

    def _reposition_slots(self) -> None:
        for idx, toast in enumerate(self._active):
            toast.move_to_slot(idx)
