"""Guided 3-step Folder Setup Studio for setting up and mistake-proofing Downloads folders."""

from __future__ import annotations

import copy
import os
from pathlib import Path
import re
from typing import Any

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QLayoutItem,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from download_sorter.architecture import (
    ArchNode,
    FolderArchitecture,
    _clean_exts,
    _clean_strs,
    apply_architecture_to_config,
    architecture_from_config,
    get_tier2_suggestions,
    scan_folder_architecture,
)
from download_sorter.config import Config, Rule
from download_sorter.router import (
    FileCandidate,
    _normalize_tokens,
    evaluate,
    extract_host,
    read_zone_url,
)
from download_sorter.service import SorterService
from download_sorter.ui.accessibility import name_controls_for_screen_readers
from download_sorter.ui.emblems import FolderScanRadarWidget
from download_sorter.ui.motion import InteractiveRibbonBtn, make_vector_icon, refresh_style
from download_sorter.ui.theme import STYLESHEET

_MANUAL_MARKER = "__manual__"

BLUEPRINT_CHOICES: tuple[tuple[str, str, str], ...] = (
    ("standard", "Standard Everyday", "Balanced 2-level folders for everyday documents, media, and 3D files"),
    ("basic_starter", "Simple Starter", "Clean starter categories with essential subfolders"),
    ("maker_pro", "3D Printing & CAD", "Workshop layout for 3MF, STL, G-Code, MakerWorld, STEP & Fusion 360"),
    ("student_pro", "Student & School", "Coursework folders for syllabi, homework, labs, exams & resumes"),
    ("creator_pro", "Work & Finance", "Studio & business layout for receipts, tax forms, contracts & design"),
    ("flat", "Flat 1-Level", "Single-level top categories with zero nested subfolders"),
)

FILE_TYPE_CHIP_SPECS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    (".pdf", (".pdf",), "PDF documents (.pdf)"),
    (".docx", (".docx",), "Word documents (.docx)"),
    (".xlsx/.csv", (".xlsx", ".csv"), "Excel spreadsheets & CSV tables (.xlsx, .csv)"),
    (".pptx", (".pptx",), "Slide presentations (.pptx)"),
    (".png/.jpg", (".png", ".jpg"), "PNG & JPEG images (.png, .jpg)"),
    (".zip", (".zip",), "Compressed ZIP archives (.zip)"),
    (".3mf/.stl", (".3mf", ".stl"), "3D printable models (.3mf, .stl)"),
    (".step/.f3d", (".step", ".f3d"), "CAD assemblies & Fusion 360 files (.step, .f3d)"),
    (".mp4", (".mp4",), "MP4 videos (.mp4)"),
    (".mp3", (".mp3",), "MP3 audio files (.mp3)"),
    (".exe/.msi", (".exe", ".msi"), "Windows installers (.exe, .msi)"),
    (".py/.js", (".py", ".js"), "Python & JavaScript code files (.py, .js)"),
)

_PROTECTED_DIR_NAMES: frozenset[str] = frozenset(
    {
        "_duplicates",
        "_extracted",
        "extracted",
        "_archive",
        "games",
        "portable apps",
        "__pycache__",
        "__macosx",
        "node_modules",
        ".git",
        ".venv",
    }
)

_SAMPLE_STOPWORDS: frozenset[str] = frozenset(
    {
        "copy",
        "final",
        "new",
        "download",
        "downloads",
        "file",
        "files",
        "folder",
        "folders",
        "untitled",
        "draft",
        "version",
        "sample",
        "test",
        "the",
        "and",
        "for",
        "from",
        "with",
    }
)

_NAME_INFERENCE_CATALOG: tuple[tuple[tuple[str, ...], dict[str, tuple[str, ...]]], ...] = (
    (
        ("tax", "taxes", "irs", "1099", "w2"),
        {
            "extensions": (".pdf", ".csv", ".xlsx"),
            "keywords": ("tax", "1099", "w2", "irs", "return"),
            "domains": ("irs.gov", "turbotax.com"),
        },
    ),
    (
        ("invoice", "invoices", "receipt", "receipts", "bill", "bills", "finance", "bank", "banking", "accounting"),
        {
            "extensions": (".pdf", ".csv", ".xlsx"),
            "keywords": ("invoice", "receipt", "statement", "billing", "payment"),
            "domains": ("stripe.com", "paypal.com", "chase.com"),
        },
    ),
    (
        ("school", "course", "courses", "coursework", "class", "classes", "homework", "syllabus", "syllabi", "exam", "study", "lab"),
        {
            "extensions": (".pdf", ".docx", ".pptx"),
            "keywords": ("syllabus", "homework", "lecture", "assignment", "study_guide", "exam"),
            "domains": ("instructure.com", "gradescope.com", "blackboard.com", "classroom.google.com"),
        },
    ),
    (
        ("resume", "resumes", "career", "job", "jobs", "hiring", "application", "applications"),
        {
            "extensions": (".pdf", ".docx"),
            "keywords": ("resume", "cv", "cover_letter", "offer_letter"),
            "domains": ("linkedin.com", "greenhouse.io", "lever.co"),
        },
    ),
    (
        ("contract", "contracts", "legal", "nda", "lease", "agreements"),
        {
            "extensions": (".pdf", ".docx"),
            "keywords": ("contract", "agreement", "nda", "lease", "signed"),
            "domains": ("docusign.com", "hellosign.com"),
        },
    ),
    (
        ("makerworld", "bambu", "print", "prints", "3d", "stl", "3mf", "filament", "slicer", "gridfinity", "miniature", "miniatures"),
        {
            "extensions": (".3mf", ".stl"),
            "keywords": ("bambu", "plate", "bracket", "mount", "gridfinity"),
            "domains": ("makerworld.com", "printables.com", "thingiverse.com"),
        },
    ),
    (
        ("cad", "step", "fusion", "solidworks", "enclosure", "enclosures", "mechanical", "drawing", "drawings"),
        {
            "extensions": (".step", ".f3d"),
            "keywords": ("bracket", "enclosure", "assembly", "housing", "schematic"),
            "domains": (),
        },
    ),
    (
        ("screenshot", "screenshots", "capture", "captures", "snip", "snips"),
        {
            "extensions": (".png", ".jpg"),
            "keywords": ("screenshot", "snip", "capture", "cleanshot"),
            "domains": (),
        },
    ),
    (
        ("photo", "photos", "camera", "wallpaper", "wallpapers", "logo", "logos", "design", "art"),
        {
            "extensions": (".png", ".jpg"),
            "keywords": ("photo", "logo", "wallpaper", "mockup"),
            "domains": ("figma.com", "canva.com"),
        },
    ),
    (
        ("travel", "trip", "trips", "flight", "flights", "hotel", "vacation"),
        {
            "extensions": (".pdf",),
            "keywords": ("itinerary", "boarding", "reservation", "ticket"),
            "domains": ("booking.com", "airbnb.com", "expedia.com"),
        },
    ),
    (
        ("manual", "manuals", "datasheet", "datasheets", "guide", "guides", "ebook", "ebooks"),
        {
            "extensions": (".pdf",),
            "keywords": ("manual", "datasheet", "guide", "handbook"),
            "domains": (),
        },
    ),
    (
        ("code", "dev", "python", "script", "scripts", "repo", "github"),
        {
            "extensions": (".py", ".js", ".zip"),
            "keywords": ("script", "source", "config", "release"),
            "domains": ("github.com", "gitlab.com"),
        },
    ),
)

_STUDIO_QSS = """
QDialog#FolderSetupStudio {
    background-color: #111113;
    color: #F5F5F7;
}
QFrame#StudioHeader {
    background-color: #18181B;
    border: 1px solid #2C2C2E;
    border-radius: 12px;
}
QPushButton#StepPillBtn {
    background-color: transparent;
    color: #8E8E93;
    border: 1px solid transparent;
    border-radius: 8px;
    padding: 6px 12px;
    font-size: 12px;
    font-weight: 600;
}
QPushButton#StepPillBtn:hover {
    background-color: rgba(255, 255, 255, 0.06);
    color: #F5F5F7;
}
QPushButton#StepPillBtn[active="true"] {
    background-color: rgba(10, 132, 255, 0.18);
    border: 1px solid #0A84FF;
    color: #FFFFFF;
}
QPushButton#StepPillBtn[done="true"] {
    color: #30D158;
}
QFrame#ChoiceCard {
    background-color: #1C1C1E;
    border: 1.5px solid #2C2C2E;
    border-radius: 12px;
}
QFrame#ChoiceCard:hover {
    background-color: #202024;
    border-color: #3D3D42;
}
QFrame#ChoiceCard[selected="true"] {
    background-color: rgba(10, 132, 255, 0.09);
    border: 1.5px solid #0A84FF;
}
QPushButton#StudioChip {
    background-color: #161618;
    color: #AEAEB2;
    border: 1px solid #2C2C2E;
    border-radius: 7px;
    padding: 4px 10px;
    font-size: 11.5px;
    font-weight: 600;
}
QPushButton#StudioChip:hover {
    background-color: #232327;
    border-color: #48484E;
    color: #F5F5F7;
}
QPushButton#StudioChip:checked {
    background-color: rgba(10, 132, 255, 0.20);
    border: 1px solid #0A84FF;
    color: #FFFFFF;
}
QPushButton#RemovableTokenChip {
    background-color: rgba(10, 132, 255, 0.15);
    color: #64D2FF;
    border: 1px solid rgba(10, 132, 255, 0.42);
    border-radius: 7px;
    padding: 3px 9px;
    font-size: 11.5px;
    font-weight: 600;
}
QPushButton#RemovableTokenChip:hover {
    background-color: rgba(255, 69, 58, 0.18);
    border-color: rgba(255, 69, 58, 0.65);
    color: #FF6961;
}
QPushButton#GhostSuggestionChip {
    background-color: rgba(255, 255, 255, 0.03);
    color: #98989D;
    border: 1px dashed #3A3A3E;
    border-radius: 7px;
    padding: 3px 9px;
    font-size: 11px;
    font-weight: 500;
}
QPushButton#GhostSuggestionChip:hover {
    background-color: rgba(48, 209, 88, 0.14);
    border: 1px solid #30D158;
    color: #30D158;
}
QPushButton#IntentSegmentBtn {
    background-color: #161618;
    color: #98989D;
    border: 1px solid #2C2C2E;
    border-radius: 7px;
    padding: 5px 12px;
    font-size: 12px;
    font-weight: 600;
}
QPushButton#IntentSegmentBtn:hover {
    color: #F5F5F7;
    border-color: #3E3E44;
}
QPushButton#IntentSegmentBtn:checked {
    background-color: rgba(10, 132, 255, 0.22);
    border: 1px solid #0A84FF;
    color: #FFFFFF;
}
QFrame#RuleSentenceCard {
    background-color: #151922;
    border: 1px solid rgba(10, 132, 255, 0.35);
    border-radius: 10px;
}
QFrame#GuidanceCallout {
    background-color: #261D10;
    border: 1px solid rgba(255, 159, 10, 0.55);
    border-radius: 9px;
}
QFrame#ReassureCard {
    background-color: #122318;
    border: 1px solid rgba(48, 209, 88, 0.48);
    border-radius: 10px;
}
"""


class _FlowLayout(QLayout):
    """Height-for-width wrapping flow layout so chip buttons never clip or force horizontal overflow."""

    def __init__(self, parent: QWidget | None = None, *, margin: int = 0, h_spacing: int = 6, v_spacing: int = 6) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._h_space = h_spacing
        self._v_space = v_spacing
        self.setContentsMargins(margin, margin, margin, margin)

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._do_layout(QRect(0, 0, max(1, width), 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        max_w = 0
        max_h = 0
        for item in self._items:
            sz = item.minimumSize()
            max_w = max(max_w, sz.width())
            max_h = max(max_h, sz.height())
        m = self.contentsMargins()
        return QSize(min(max_w, 220) + m.left() + m.right(), max_h + m.top() + m.bottom())

    def _do_layout(self, rect: QRect, *, test_only: bool) -> int:
        m = self.contentsMargins()
        effective = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x = effective.x()
        y = effective.y()
        line_height = 0

        for item in self._items:
            wid = item.widget()
            if wid is not None and wid.isHidden():
                continue
            hint = item.sizeHint()
            item_w = min(hint.width(), max(effective.width(), hint.width()))
            next_x = x + item_w + self._h_space
            if next_x - self._h_space > effective.right() and line_height > 0:
                x = effective.x()
                y = y + line_height + self._v_space
                next_x = x + item_w + self._h_space
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), QSize(hint.width(), hint.height())))
            x = next_x
            line_height = max(line_height, hint.height())

        return y + line_height - rect.y() + m.bottom()


def _clear_layout(layout: QLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item is None:
            continue
        w = item.widget()
        if w is not None:
            w.hide()
            w.deleteLater()


_ILLEGAL_WIN_CHARS: frozenset[str] = frozenset('<>:"/\\|?*')
_DOS_RESERVED_NAMES: frozenset[str] = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)
_UNEXPANDED_VAR_RE = re.compile(r"^(\$\{[^}]*\}|\$[A-Za-z_][A-Za-z0-9_]*|%[^%]*%)$")


def validate_windows_folder_name(raw_name: str) -> tuple[str, str]:
    """Validate and tidy a single Windows folder name.

    Returns ``(cleaned_name, error_message)``. When valid, ``error_message`` is ``""``
    and ``cleaned_name`` has leading/trailing spaces and trailing dots stripped.
    """
    raw = str(raw_name if raw_name is not None else "")
    if any((ch in _ILLEGAL_WIN_CHARS or ord(ch) < 32) for ch in raw):
        return ("", 'Folder names cannot contain Windows reserved characters (< > : " / \\ | ? *).')

    stripped = raw.strip()
    if not stripped or stripped in (".", "..") or set(stripped) <= {"."}:
        return ("", "Enter a folder name (folder names cannot be blank or only dots).")

    cleaned = stripped.rstrip(". ").strip()
    if not cleaned:
        return ("", "Enter a folder name (folder names cannot be blank or only dots).")

    stem_upper = cleaned.split(".", 1)[0].strip().upper()
    if stem_upper in _DOS_RESERVED_NAMES:
        return ("", f"'{cleaned}' is a reserved Windows system name — please choose a different folder name.")

    if _UNEXPANDED_VAR_RE.match(cleaned):
        return ("", "Folder names cannot use environment variable syntax like %NAME% or ${NAME}.")

    return (cleaned, "")


def _normalize_single_ext(raw: str) -> str:
    cleaned = "".join(ch for ch in str(raw or "").strip().lower() if ch.isalnum() or ch in (".", "_", "-"))
    cleaned = cleaned.lstrip(".")
    if not cleaned:
        return ""
    return f".{cleaned}"


def _esc_amp(text: str) -> str:
    """Escape literal ampersands for Qt button labels so '&' is never eaten as a mnemonic."""
    return str(text).replace("&", "&&")


def _infer_folder_suggestions(folder_name: str, parent_name: str = "") -> dict[str, list[str]]:
    """Infer helpful extension, filename word, and website suggestions from a folder's name."""
    combined = f"{parent_name} {folder_name}".strip()
    tokens = _normalize_tokens(combined).split()
    name_tokens = _normalize_tokens(folder_name).split()

    exts: list[str] = []
    kws: list[str] = []
    doms: list[str] = []

    for triggers, payload in _NAME_INFERENCE_CATALOG:
        if any(tok in triggers for tok in name_tokens) or any(tok in triggers for tok in tokens):
            for e in payload.get("extensions", ()):
                if e not in exts:
                    exts.append(e)
            for k in payload.get("keywords", ()):
                if k not in kws:
                    kws.append(k)
            for d in payload.get("domains", ()):
                if d not in doms:
                    doms.append(d)

    # Also offer clean tokens directly from the user's custom folder name (e.g. "Client Acme" -> "acme")
    for tok in name_tokens:
        if len(tok) >= 3 and not tok.isdigit() and tok not in _SAMPLE_STOPWORDS:
            singular = tok[:-1] if (len(tok) >= 5 and tok.endswith("s") and not tok.endswith("ss")) else tok
            for cand in (singular, tok):
                if cand and len(cand) >= 3 and cand not in kws:
                    kws.append(cand)

    return {
        "extensions": exts[:6],
        "keywords": kws[:7],
        "domains": doms[:4],
    }


def _scan_watch_root_safely(watch_root: Path) -> dict[str, Any]:
    """Scan existing Downloads folders safely while separating protected untouched bundles."""
    protected: list[str] = []
    has_user_subdirs = False

    if watch_root.is_dir():
        try:
            with os.scandir(watch_root) as it:
                entries = sorted(list(it), key=lambda e: e.name.lower())
            for entry in entries:
                if not entry.is_dir(follow_symlinks=False):
                    continue
                nm = entry.name
                low = nm.lower()
                if nm.startswith("."):
                    continue
                ep = Path(entry.path)
                if low in _PROTECTED_DIR_NAMES or nm.startswith("_") or SorterService._is_project_or_bundle_dir(ep):
                    if nm not in protected:
                        protected.append(nm)
                    continue
                has_user_subdirs = True
                # Check depth-1 children for protected bundles too (e.g. Extracted or git repos)
                try:
                    with os.scandir(ep) as sub_it:
                        for sub in sorted(list(sub_it), key=lambda e: e.name.lower()):
                            if not sub.is_dir(follow_symlinks=False) or sub.name.startswith("."):
                                continue
                            slow = sub.name.lower()
                            sp = Path(sub.path)
                            if (
                                slow in _PROTECTED_DIR_NAMES
                                or sub.name.startswith("_")
                                or SorterService._is_project_or_bundle_dir(sp)
                            ):
                                rel_prot = f"{nm}\\{sub.name}"
                                if rel_prot not in protected:
                                    protected.append(rel_prot)
                except OSError:
                    pass
        except OSError:
            pass

    try:
        raw_scan = scan_folder_architecture(watch_root)
    except OSError:
        raw_scan = {"architecture": FolderArchitecture(tier_count=2, nodes=[]), "folder_count": 0, "file_count": 0}

    arch: FolderArchitecture = raw_scan["architecture"]
    protected_top_lower = {p.split("\\")[0].lower() for p in protected}
    if protected_top_lower:
        for node in list(arch.nodes_in_tier(1)):
            if node.name.lower() in protected_top_lower:
                arch.remove_node(node.id)

    return {
        "architecture": arch,
        "folder_count": len(arch.nodes),
        "file_count": int(raw_scan.get("file_count", 0)),
        "protected_folders": protected,
        "has_user_subdirs": has_user_subdirs and len(arch.nodes) > 0,
    }


def format_plain_english_folder_rule(
    folder_display_path: str,
    *,
    extensions: list[str],
    keywords: list[str],
    domains: list[str],
    is_manual: bool,
    has_active_children: bool = False,
) -> str:
    """Build an accurate plain-English sorting explanation reflecting router AND/OR semantics."""
    dest = folder_display_path or "this folder"
    if is_manual:
        return f"Manual folder — files only go into '{dest}' when you drag or save them there yourself."

    exts = _clean_exts(extensions)
    kws = _clean_strs(keywords)
    doms = [d.lower() for d in _clean_strs(domains)]

    if not exts and not kws and not doms:
        if has_active_children:
            return f"Group folder — files sort into the subfolders inside '{dest}' based on each subfolder's rules."
        return f"Needs sorting rules — pick a file type, filename word, or website above so Download Sorter knows what belongs in '{dest}'."

    if exts:
        shown_exts = exts[:4]
        if len(shown_exts) == 1:
            ext_phrase = f"{shown_exts[0]} files"
        elif len(exts) <= 4:
            ext_phrase = f"{', '.join(shown_exts[:-1])} or {shown_exts[-1]} files"
        else:
            ext_phrase = f"{', '.join(shown_exts)} (+{len(exts) - 4} more) files"
    else:
        ext_phrase = "files of any type"

    clauses: list[str] = []
    if kws:
        quoted = [f"'{k}'" for k in kws[:4]]
        if len(quoted) == 1:
            kw_text = quoted[0]
        elif len(kws) <= 4:
            kw_text = f"{', '.join(quoted[:-1])} or {quoted[-1]}"
        else:
            kw_text = f"{', '.join(quoted)} (+{len(kws) - 4} more)"
        clauses.append(f"contain {kw_text}")

    if doms:
        shown_doms = doms[:3]
        if len(shown_doms) == 1:
            dom_text = shown_doms[0]
        elif len(doms) <= 3:
            dom_text = f"{', '.join(shown_doms[:-1])} or {shown_doms[-1]}"
        else:
            dom_text = f"{', '.join(shown_doms)} (+{len(doms) - 3} more)"
        clauses.append(f"come from {dom_text}")

    if not clauses:
        return f"Moves {ext_phrase} into {dest}."

    filter_phrase = " or ".join(clauses)
    return f"Moves {ext_phrase} that {filter_phrase} into {dest}."


_ROLE_NODE_ID = int(Qt.ItemDataRole.UserRole)
_ROLE_DEPTH = int(Qt.ItemDataRole.UserRole) + 1
_ROLE_ACCENT = int(Qt.ItemDataRole.UserRole) + 2
_ROLE_BADGE_TEXT = int(Qt.ItemDataRole.UserRole) + 3
_ROLE_BADGE_KIND = int(Qt.ItemDataRole.UserRole) + 4
_ROLE_FOLDER_NAME = int(Qt.ItemDataRole.UserRole) + 5


class _ChecklistItemDelegate(QStyledItemDelegate):
    """Paints hierarchical folder rows with accent folder dots and crisp completeness badges."""

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:  # noqa: ANN001, N802
        return QSize(max(200, option.rect.width()), 36)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:  # noqa: ANN001
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = QRectF(option.rect).adjusted(3.0, 2.0, -3.0, -2.0)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)

        if selected:
            painter.setBrush(QColor(10, 132, 255, 42))
            painter.setPen(QPen(QColor("#0A84FF"), 1.2))
            painter.drawRoundedRect(rect, 8.0, 8.0)
        elif hovered:
            painter.setBrush(QColor(255, 255, 255, 10))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(rect, 8.0, 8.0)

        depth = int(index.data(_ROLE_DEPTH) or 0)
        accent_hex = str(index.data(_ROLE_ACCENT) or "#0A84FF")
        folder_name = str(index.data(_ROLE_FOLDER_NAME) or "")
        badge_text = str(index.data(_ROLE_BADGE_TEXT) or "✓ Ready")
        badge_kind = str(index.data(_ROLE_BADGE_KIND) or "ready")

        display_badge = "Ready" if badge_kind == "ready" else badge_text
        badge_font = QFont(option.font)
        badge_font.setPointSizeF(8.5)
        badge_font.setWeight(QFont.Weight.DemiBold)
        bfm = QFontMetrics(badge_font)
        check_extra = 12 if badge_kind == "ready" else 0
        bw = bfm.horizontalAdvance(display_badge) + 16 + check_extra
        bh = 20.0
        bx = rect.right() - bw - 8.0
        by = rect.center().y() - bh * 0.5
        badge_rect = QRectF(bx, by, float(bw), bh)

        if badge_kind == "needs_rules":
            b_bg, b_fg, b_bd = QColor(255, 159, 10, 38), QColor("#FF9F0A"), QColor(255, 159, 10, 115)
        elif badge_kind == "manual":
            b_bg, b_fg, b_bd = QColor(152, 152, 157, 34), QColor("#D1D1D6"), QColor(152, 152, 157, 95)
        else:
            b_bg, b_fg, b_bd = QColor(48, 209, 88, 32), QColor("#30D158"), QColor(48, 209, 88, 95)

        painter.setBrush(b_bg)
        painter.setPen(QPen(b_bd, 1.0))
        painter.drawRoundedRect(badge_rect, 9.5, 9.5)
        painter.setFont(badge_font)
        painter.setPen(b_fg)
        if badge_kind == "ready":
            ck_x = badge_rect.left() + 8.5
            ck_y = badge_rect.center().y()
            ck_pen = QPen(b_fg, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            painter.setPen(ck_pen)
            painter.drawPolyline([QPointF(ck_x, ck_y + 0.3), QPointF(ck_x + 2.4, ck_y + 2.7), QPointF(ck_x + 6.6, ck_y - 2.3)])
            painter.setPen(b_fg)
            txt_rect = QRectF(badge_rect.left() + 18.0, badge_rect.top(), badge_rect.width() - 22.0, badge_rect.height())
            painter.drawText(txt_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, display_badge)
        else:
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, display_badge)

        indent_x = rect.left() + 10.0 + depth * 16.0
        cy = rect.center().y()
        if depth > 0:
            painter.setPen(QPen(QColor("#52525B"), 1.2))
            painter.drawLine(QPointF(indent_x - 9.0, cy - 4.0), QPointF(indent_x - 9.0, cy))
            painter.drawLine(QPointF(indent_x - 9.0, cy), QPointF(indent_x - 4.0, cy))

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(accent_hex))
        painter.drawEllipse(QPointF(indent_x + 4.0, cy), 4.0, 4.0)

        name_font = QFont(option.font)
        name_font.setPointSizeF(9.5)
        name_font.setWeight(QFont.Weight.DemiBold if (depth == 0 or selected) else QFont.Weight.Normal)
        painter.setFont(name_font)
        painter.setPen(QColor("#FFFFFF" if selected else ("#F5F5F7" if depth == 0 else "#D1D1D6")))

        text_left = indent_x + 13.0
        text_width = max(36.0, bx - text_left - 8.0)
        nfm = QFontMetrics(name_font)
        elided = nfm.elidedText(folder_name, Qt.TextElideMode.ElideRight, int(text_width))
        text_rect = QRectF(text_left, rect.top(), text_width, rect.height())
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided)

        painter.restore()


class _ChoiceCard(QFrame):
    """Clickable starting-point card in Step 1."""

    selected = Signal(str)

    def __init__(
        self,
        mode_key: str,
        title: str,
        subtitle: str,
        *,
        badge_text: str = "",
        emblem_widget: QWidget | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.mode_key = mode_key
        self.setObjectName("ChoiceCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setProperty("selected", "false")

        self.outer_layout = QVBoxLayout(self)
        self.outer_layout.setContentsMargins(14, 12, 14, 12)
        self.outer_layout.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(10)

        if emblem_widget is not None:
            top.addWidget(emblem_widget, 0, Qt.AlignmentFlag.AlignTop)

        text_col = QVBoxLayout()
        text_col.setSpacing(3)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        self.lbl_radio = QLabel("○")
        self.lbl_radio.setStyleSheet("color: #8E8E93; font-size: 14px; font-weight: 700; background: transparent; border: none;")
        title_row.addWidget(self.lbl_radio)

        self.btn_select = QPushButton(title)
        self.btn_select.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_select.setAccessibleName(title)
        self.btn_select.setToolTip(subtitle)
        self.btn_select.setStyleSheet(
            "QPushButton { background: transparent; border: none; color: #F5F5F7; "
            "font-size: 13.5px; font-weight: 700; padding: 0px; text-align: left; }"
            "QPushButton:hover { color: #64D2FF; }"
        )
        self.btn_select.clicked.connect(lambda: self.selected.emit(self.mode_key))
        title_row.addWidget(self.btn_select)

        self.lbl_badge = QLabel(badge_text)
        self.lbl_badge.setObjectName("Badge")
        self.lbl_badge.setProperty("variant", "success")
        self.lbl_badge.setVisible(bool(badge_text))
        title_row.addWidget(self.lbl_badge)
        title_row.addStretch(1)
        text_col.addLayout(title_row)

        self.lbl_sub = QLabel(subtitle)
        self.lbl_sub.setWordWrap(True)
        self.lbl_sub.setStyleSheet("color: #98989D; font-size: 12px; background: transparent; border: none;")
        text_col.addWidget(self.lbl_sub)

        self.lbl_meta = QLabel("")
        self.lbl_meta.setWordWrap(True)
        self.lbl_meta.setStyleSheet("color: #64D2FF; font-size: 11.5px; font-weight: 600; background: transparent; border: none;")
        self.lbl_meta.setVisible(False)
        text_col.addWidget(self.lbl_meta)

        top.addLayout(text_col, 1)
        self.outer_layout.addLayout(top)

    def set_selected(self, active: bool) -> None:
        self.setProperty("selected", "true" if active else "false")
        self.lbl_radio.setText("●" if active else "○")
        self.lbl_radio.setStyleSheet(
            f"color: {'#0A84FF' if active else '#8E8E93'}; font-size: 14px; font-weight: 700; background: transparent; border: none;"
        )
        refresh_style(self)

    def set_meta_summary(self, text: str) -> None:
        self.lbl_meta.setText(text)
        self.lbl_meta.setVisible(bool(text))

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.selected.emit(self.mode_key)
            event.accept()
            return
        super().mousePressEvent(event)


class _EditorDropSurface(QFrame):
    """Right-hand Step 2 surface card that accepts sample file drops to auto-fill cues."""

    sample_file_dropped = Signal(Path)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SurfaceCard")
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        mime = event.mimeData()
        if mime.hasUrls() and any(u.isLocalFile() and Path(u.toLocalFile()).is_file() for u in mime.urls()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        mime = event.mimeData()
        if mime.hasUrls() and any(u.isLocalFile() and Path(u.toLocalFile()).is_file() for u in mime.urls()):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        mime = event.mimeData()
        if mime.hasUrls():
            for u in mime.urls():
                if u.isLocalFile():
                    p = Path(u.toLocalFile())
                    if p.is_file():
                        event.acceptProposedAction()
                        self.sample_file_dropped.emit(p)
                        return
        super().dropEvent(event)


class FolderSetupStudioDialog(QDialog):
    """Guided 3-step Folder Setup Studio that walks the user through complete folder & rule setup."""

    def __init__(
        self,
        service: SorterService,
        *,
        initial_mode: str = "auto",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.setObjectName("FolderSetupStudio")
        self.setWindowTitle("Folder Setup Studio — Set Up Downloads Folders")
        self.setMinimumSize(860, 540)
        self.resize(980, 660)
        self.setStyleSheet(STYLESHEET + "\n" + _STUDIO_QSS)

        self._scan_info = _scan_watch_root_safely(self.service.watch_root)
        self._current_arch_initial = architecture_from_config(self.service.config)

        req_mode = str(initial_mode or "auto").strip().lower()
        if req_mode not in ("auto", "scan", "preset", "customize"):
            req_mode = "auto"

        if req_mode == "auto":
            if self._scan_info["has_user_subdirs"]:
                self._selected_mode = "scan"
            elif self.service.config.architecture or self.service.config.rules:
                self._selected_mode = "customize"
            else:
                self._selected_mode = "preset"
        else:
            self._selected_mode = req_mode

        self._selected_preset_key = "standard"
        self._loaded_source_sig: tuple[str, str] | None = None
        self._arch: FolderArchitecture = FolderArchitecture(tier_count=2, nodes=[])
        self._manual_node_ids: set[str] = set()
        self._stashed_cues: dict[str, tuple[list[str], list[str], list[str]]] = {}
        self._edited_paths: set[str] = set()
        self._removed_paths: set[str] = set()
        self._name_drafts: dict[str, str] = {}
        self._name_errors: dict[str, str] = {}
        self._edited_name_ids: set[str] = set()
        self._selected_node_id: str | None = None
        self._syncing_editor = False
        self._last_preview_plan: dict[str, Any] = {"moves_planned": [], "folders_to_create": [], "architecture_folders": []}

        self._build_ui()
        self._select_starting_mode(self._selected_mode, preset_key=self._selected_preset_key, force_reload=True)
        self._go_to_step(0)
        name_controls_for_screen_readers(self)

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        # Top Header with Clickable 3-Step Pills
        hdr = QFrame()
        hdr.setObjectName("StudioHeader")
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(14, 10, 14, 10)
        hl.setSpacing(12)

        title_col = QVBoxLayout()
        title_col.setSpacing(1)
        lbl_title = QLabel("Folder Setup Studio")
        lbl_title.setStyleSheet("color: #F5F5F7; font-size: 15px; font-weight: 700; background: transparent; border: none;")
        title_col.addWidget(lbl_title)
        lbl_sub = QLabel("Build a clean, complete folder structure for Downloads — 100% reversible")
        lbl_sub.setStyleSheet("color: #8E8E93; font-size: 11.5px; background: transparent; border: none;")
        title_col.addWidget(lbl_sub)
        hl.addLayout(title_col, 1)

        steps_row = QHBoxLayout()
        steps_row.setSpacing(6)
        self.btn_step_1 = QPushButton("1. Starting Point")
        self.btn_step_2 = QPushButton("2. Customize Folders")
        self.btn_step_3 = QPushButton(_esc_amp("3. Review & Apply"))
        self._step_buttons = (self.btn_step_1, self.btn_step_2, self.btn_step_3)
        step_tips = (
            "Step 1: Choose whether to scan existing Downloads folders, pick a ready-made layout, or customize your current setup",
            "Step 2: Customize every folder and verify its file types, filename words, or website sources",
            "Step 3: Review planned folders and safely apply your folder setup",
        )
        for idx, (btn, tip) in enumerate(zip(self._step_buttons, step_tips, strict=True)):
            btn.setObjectName("StepPillBtn")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(tip)
            btn.setAccessibleName(btn.text().replace("&&", "&"))
            btn.clicked.connect(lambda _=False, s=idx: self._on_step_pill_clicked(s))
            steps_row.addWidget(btn)
        hl.addLayout(steps_row)
        root.addWidget(hdr)

        # Stacked 3-Step Body
        self.stack = QStackedWidget()
        self._page_step1 = self._build_step1_page()
        self._page_step2 = self._build_step2_page()
        self._page_step3 = self._build_step3_page()
        self.stack.addWidget(self._page_step1)
        self.stack.addWidget(self._page_step2)
        self.stack.addWidget(self._page_step3)
        root.addWidget(self.stack, 1)

        # Footer Navigation
        foot = QHBoxLayout()
        foot.setSpacing(10)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setObjectName("GhostButton")
        self.btn_cancel.setToolTip("Close Folder Setup Studio without changing your folders")
        self.btn_cancel.clicked.connect(self.reject)
        foot.addWidget(self.btn_cancel)

        self.lbl_footer_hint = QLabel("")
        self.lbl_footer_hint.setStyleSheet("color: #8E8E93; font-size: 12px;")
        foot.addWidget(self.lbl_footer_hint, 1)

        self.btn_back = QPushButton("← Back")
        self.btn_back.setToolTip("Go back to the previous setup step")
        self.btn_back.clicked.connect(self._on_back_clicked)
        foot.addWidget(self.btn_back)

        self.btn_next = InteractiveRibbonBtn("Next: Customize Folders →")
        self.btn_next.setObjectName("PrimaryButton")
        self.btn_next.setToolTip("Continue to the next step")
        self.btn_next.clicked.connect(self._on_next_clicked)
        foot.addWidget(self.btn_next)

        self.btn_apply = InteractiveRibbonBtn("Apply Folder Setup", make_vector_icon("check", "#FFFFFF", 14))
        self.btn_apply.setObjectName("PrimaryButton")
        self.btn_apply.setToolTip("Save this folder setup and apply your choices")
        self.btn_apply.clicked.connect(self._on_apply_clicked)
        foot.addWidget(self.btn_apply)

        root.addLayout(foot)

    def _build_step1_page(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(12)

        # Left column: 3 Interactive Choice Cards
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        left_host = QWidget()
        left_col = QVBoxLayout(left_host)
        left_col.setContentsMargins(0, 0, 4, 0)
        left_col.setSpacing(10)

        lbl_step1_intro = QLabel("Step 1 · Choose How to Set Up Your Folders")
        lbl_step1_intro.setStyleSheet("color: #F5F5F7; font-size: 13.5px; font-weight: 700;")
        left_col.addWidget(lbl_step1_intro)

        radar = FolderScanRadarWidget(size=42, scanning=False)
        self.card_scan = _ChoiceCard(
            "scan",
            "Match My Existing Downloads Folders",
            "Safely scan your Downloads folders to mirror the structure you already use and infer sorting rules.",
            badge_text="Recommended" if self._scan_info["has_user_subdirs"] else "",
            emblem_widget=radar,
        )
        self.card_scan.selected.connect(self._on_mode_card_clicked)
        scan_cnt = self._scan_info["folder_count"]
        prot = self._scan_info["protected_folders"]
        prot_preview = ", ".join(prot[:4]) if prot else "Games, Extracted, _Duplicates"
        if scan_cnt > 0:
            self.card_scan.set_meta_summary(
                f"Found {scan_cnt} folder{'s' if scan_cnt != 1 else ''} ({self._scan_info['file_count']} files sampled) "
                f"· Protected & untouched: {prot_preview}"
            )
        else:
            self.card_scan.set_meta_summary(
                f"No existing sorting folders detected yet · Protected & untouched: {prot_preview}"
            )
        left_col.addWidget(self.card_scan)

        self.card_preset = _ChoiceCard(
            "preset",
            "Pick a Ready-Made Folder Layout",
            "Start with a proven folder blueprint tailored to how you use your computer, then tweak anything in Step 2.",
        )
        self.card_preset.selected.connect(self._on_mode_card_clicked)

        bp_wrap = QWidget()
        bp_flow = _FlowLayout(bp_wrap, margin=0, h_spacing=6, v_spacing=6)
        self.blueprint_buttons: dict[str, QPushButton] = {}
        for key, label, desc in BLUEPRINT_CHOICES:
            btn = QPushButton(_esc_amp(label))
            btn.setObjectName("StudioChip")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(desc)
            btn.setAccessibleName(f"Blueprint: {label}")
            btn.clicked.connect(lambda _=False, k=key: self._on_blueprint_clicked(k))
            bp_flow.addWidget(btn)
            self.blueprint_buttons[key] = btn
        self.card_preset.outer_layout.addWidget(bp_wrap)
        left_col.addWidget(self.card_preset)

        cur_count = len(self._current_arch_initial.nodes)
        self.card_customize = _ChoiceCard(
            "customize",
            "Customize My Current Setup",
            "Load your active Download Sorter folders and custom rules directly into the guided checklist editor.",
        )
        self.card_customize.selected.connect(self._on_mode_card_clicked)
        self.card_customize.set_meta_summary(
            f"Currently active: {cur_count} folder{'s' if cur_count != 1 else ''}, "
            f"{len(self.service.config.rules)} custom rule{'s' if len(self.service.config.rules) != 1 else ''}"
        )
        left_col.addWidget(self.card_customize)
        left_col.addStretch(1)
        left_scroll.setWidget(left_host)
        layout.addWidget(left_scroll, 11)

        # Right column: Live Starting-Point Preview Tree + Protected Folders
        right_card = QFrame()
        right_card.setObjectName("SurfaceCard")
        rc = QVBoxLayout(right_card)
        rc.setContentsMargins(14, 12, 14, 12)
        rc.setSpacing(8)

        ph = QHBoxLayout()
        self.lbl_step1_preview_title = QLabel("Live Layout Preview")
        self.lbl_step1_preview_title.setStyleSheet("color: #F5F5F7; font-size: 13.5px; font-weight: 700;")
        ph.addWidget(self.lbl_step1_preview_title, 1)
        self.lbl_step1_preview_badge = QLabel("")
        self.lbl_step1_preview_badge.setObjectName("Badge")
        self.lbl_step1_preview_badge.setProperty("variant", "primary")
        ph.addWidget(self.lbl_step1_preview_badge)
        rc.addLayout(ph)

        self.lbl_step1_preview_desc = QLabel("")
        self.lbl_step1_preview_desc.setWordWrap(True)
        self.lbl_step1_preview_desc.setStyleSheet("color: #98989D; font-size: 11.5px;")
        rc.addWidget(self.lbl_step1_preview_desc)

        self.step1_tree_scroll = QScrollArea()
        self.step1_tree_scroll.setWidgetResizable(True)
        self.step1_tree_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.step1_tree_host = QWidget()
        self.step1_tree_layout = QVBoxLayout(self.step1_tree_host)
        self.step1_tree_layout.setContentsMargins(2, 2, 4, 2)
        self.step1_tree_layout.setSpacing(4)
        self.step1_tree_scroll.setWidget(self.step1_tree_host)
        rc.addWidget(self.step1_tree_scroll, 1)

        prot_frame = QFrame()
        prot_frame.setStyleSheet(
            "QFrame { background-color: #151B18; border: 1px solid rgba(48, 209, 88, 0.32); border-radius: 9px; }"
        )
        pf = QVBoxLayout(prot_frame)
        pf.setContentsMargins(10, 8, 10, 8)
        pf.setSpacing(2)
        lbl_prot_hdr = QLabel("Protected & Left Untouched Automatically")
        lbl_prot_hdr.setStyleSheet("color: #30D158; font-size: 11.5px; font-weight: 700; border: none; background: transparent;")
        pf.addWidget(lbl_prot_hdr)
        self.lbl_step1_protected = QLabel("")
        self.lbl_step1_protected.setWordWrap(True)
        self.lbl_step1_protected.setStyleSheet("color: #AEAEB2; font-size: 11px; border: none; background: transparent;")
        pf.addWidget(self.lbl_step1_protected)
        rc.addWidget(prot_frame)

        layout.addWidget(right_card, 9)
        return page

    def _build_step2_page(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(12)

        # Left Pane: Hierarchical Folder Checklist
        left_card = QFrame()
        left_card.setObjectName("SurfaceCard")
        lc = QVBoxLayout(left_card)
        lc.setContentsMargins(12, 12, 12, 12)
        lc.setSpacing(8)

        lh = QHBoxLayout()
        lh.setSpacing(6)
        lbl_chk_title = QLabel("Folder Checklist")
        lbl_chk_title.setStyleSheet("color: #F5F5F7; font-size: 13.5px; font-weight: 700;")
        lh.addWidget(lbl_chk_title, 1)
        self.lbl_checklist_status = QLabel("Ready")
        self.lbl_checklist_status.setObjectName("Badge")
        self.lbl_checklist_status.setProperty("variant", "success")
        lh.addWidget(self.lbl_checklist_status)
        lc.addLayout(lh)

        act_row = QHBoxLayout()
        act_row.setSpacing(6)
        self.btn_add_folder = QPushButton("+ Add Folder")
        self.btn_add_folder.setToolTip("Add a new top-level folder inside Downloads")
        self.btn_add_folder.clicked.connect(self._on_add_folder_clicked)
        act_row.addWidget(self.btn_add_folder)

        self.btn_add_subfolder = QPushButton("+ Add Subfolder")
        self.btn_add_subfolder.setToolTip("Add a subfolder inside the selected folder")
        self.btn_add_subfolder.clicked.connect(self._on_add_subfolder_clicked)
        act_row.addWidget(self.btn_add_subfolder)

        self.btn_remove_folder = QPushButton("Remove")
        self.btn_remove_folder.setObjectName("DangerButton")
        self.btn_remove_folder.setToolTip("Remove the selected folder from this setup")
        self.btn_remove_folder.clicked.connect(self._on_remove_folder_clicked)
        act_row.addWidget(self.btn_remove_folder)
        act_row.addStretch(1)
        lc.addLayout(act_row)

        self.checklist = QListWidget()
        self.checklist.setAccessibleName("Configured folders checklist")
        self.checklist.setToolTip("Select any folder to edit its name, location, and sorting cues")
        self.checklist.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.checklist.setMouseTracking(True)
        self.checklist.setSpacing(2)
        self.checklist.setItemDelegate(_ChecklistItemDelegate(self.checklist))
        self.checklist.currentRowChanged.connect(self._on_checklist_row_changed)
        lc.addWidget(self.checklist, 1)

        layout.addWidget(left_card, 4)

        # Right Pane: Mistake-Proof Guided Folder Editor (with Sample File Drop Support)
        self.editor_surface = _EditorDropSurface()
        self.editor_surface.sample_file_dropped.connect(self.learn_from_sample_file)
        es_outer = QVBoxLayout(self.editor_surface)
        es_outer.setContentsMargins(10, 8, 10, 10)
        es_outer.setSpacing(8)

        self.editor_scroll = QScrollArea()
        self.editor_scroll.setWidgetResizable(True)
        self.editor_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.editor_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        editor_host = QWidget()
        ec = QVBoxLayout(editor_host)
        ec.setContentsMargins(4, 2, 8, 4)
        ec.setSpacing(8)

        # Inline guidance banner (shown when a folder needs cues or learned from a sample drop)
        self.guidance_frame = QFrame()
        self.guidance_frame.setObjectName("GuidanceCallout")
        gf = QHBoxLayout(self.guidance_frame)
        gf.setContentsMargins(10, 6, 10, 6)
        gf.setSpacing(8)
        self.lbl_guidance_icon = QLabel("!")
        self.lbl_guidance_icon.setStyleSheet("color: #FF9F0A; font-weight: 800; font-size: 13px; border: none; background: transparent;")
        gf.addWidget(self.lbl_guidance_icon)
        self.lbl_guidance_text = QLabel("")
        self.lbl_guidance_text.setWordWrap(True)
        self.lbl_guidance_text.setStyleSheet("color: #F5F5F7; font-size: 11.5px; border: none; background: transparent;")
        gf.addWidget(self.lbl_guidance_text, 1)
        self.guidance_frame.setVisible(False)
        ec.addWidget(self.guidance_frame)

        # Editor Header + Intent Switch
        ed_hdr = QHBoxLayout()
        ed_hdr.setSpacing(6)
        hdr_titles = QVBoxLayout()
        hdr_titles.setSpacing(1)
        self.lbl_editor_heading = QLabel("Guided Folder Editor")
        self.lbl_editor_heading.setWordWrap(True)
        self.lbl_editor_heading.setStyleSheet("color: #F5F5F7; font-size: 13.5px; font-weight: 700;")
        hdr_titles.addWidget(self.lbl_editor_heading)
        lbl_drop_hint = QLabel("Drop any sample file onto this panel to auto-fill its type, words & website")
        lbl_drop_hint.setWordWrap(True)
        lbl_drop_hint.setStyleSheet("color: #8E8E93; font-size: 11px;")
        hdr_titles.addWidget(lbl_drop_hint)
        ed_hdr.addLayout(hdr_titles, 1)

        self.btn_intent_auto = QPushButton("Sort automatically")
        self.btn_intent_auto.setObjectName("IntentSegmentBtn")
        self.btn_intent_auto.setCheckable(True)
        self.btn_intent_auto.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_intent_auto.setToolTip("Route matching downloads into this folder automatically")
        self.btn_intent_auto.clicked.connect(lambda: self._set_selected_manual(False))
        ed_hdr.addWidget(self.btn_intent_auto, 0, Qt.AlignmentFlag.AlignTop)

        self.btn_intent_manual = QPushButton("Manual folder")
        self.btn_intent_manual.setObjectName("IntentSegmentBtn")
        self.btn_intent_manual.setCheckable(True)
        self.btn_intent_manual.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_intent_manual.setToolTip("Keep this folder in Downloads for manual drag-and-drop only (no automatic rules)")
        self.btn_intent_manual.clicked.connect(lambda: self._set_selected_manual(True))
        ed_hdr.addWidget(self.btn_intent_manual, 0, Qt.AlignmentFlag.AlignTop)
        ec.addLayout(ed_hdr)

        # Folder Name + Parent Location Pills
        name_loc_row = QHBoxLayout()
        name_loc_row.setSpacing(10)
        lbl_fn = QLabel("Folder Name:")
        lbl_fn.setStyleSheet("color: #AEAEB2; font-size: 12px; font-weight: 600;")
        name_loc_row.addWidget(lbl_fn)

        self.edit_folder_name = QLineEdit()
        self.edit_folder_name.setPlaceholderText("e.g. Receipts, 3D Prints, Coursework")
        self.edit_folder_name.setToolTip("Name of this folder inside Downloads")
        self.edit_folder_name.textChanged.connect(self._on_folder_name_edited)
        self.edit_folder_name.editingFinished.connect(self._tidy_folder_name_field)
        name_loc_row.addWidget(self.edit_folder_name, 1)
        ec.addLayout(name_loc_row)

        lbl_parent = QLabel("Where this folder lives:")
        lbl_parent.setStyleSheet("color: #AEAEB2; font-size: 11.5px; font-weight: 600;")
        ec.addWidget(lbl_parent)

        self.parent_pills_host = QWidget()
        self.parent_pills_layout = _FlowLayout(self.parent_pills_host, margin=0, h_spacing=6, v_spacing=5)
        ec.addWidget(self.parent_pills_host)

        # 1-Click Suggested Subfolder Chips
        self.sub_sug_box = QWidget()
        ss_layout = QVBoxLayout(self.sub_sug_box)
        ss_layout.setContentsMargins(0, 0, 0, 0)
        ss_layout.setSpacing(4)
        self.lbl_sub_sug_title = QLabel("1-click suggested subfolders:")
        self.lbl_sub_sug_title.setWordWrap(True)
        self.lbl_sub_sug_title.setStyleSheet("color: #8E8E93; font-size: 11px; font-weight: 600;")
        ss_layout.addWidget(self.lbl_sub_sug_title)
        self.sub_sug_chips_host = QWidget()
        self.sub_sug_chips_layout = _FlowLayout(self.sub_sug_chips_host, margin=0, h_spacing=6, v_spacing=5)
        ss_layout.addWidget(self.sub_sug_chips_host)
        ec.addWidget(self.sub_sug_box)

        div1 = QFrame()
        div1.setObjectName("CardDivider")
        ec.addWidget(div1)

        # Automatic Cues Container (disabled cleanly when Manual folder is active)
        self.cues_container = QWidget()
        cc = QVBoxLayout(self.cues_container)
        cc.setContentsMargins(0, 0, 0, 0)
        cc.setSpacing(9)

        # 1. File Types
        lbl_ft = QLabel("File Types (click to toggle — acts as an AND filter when combined with words or websites):")
        lbl_ft.setWordWrap(True)
        lbl_ft.setStyleSheet("color: #EAEAEF; font-size: 12px; font-weight: 600;")
        cc.addWidget(lbl_ft)

        ft_chips_host = QWidget()
        ft_chips_flow = _FlowLayout(ft_chips_host, margin=0, h_spacing=6, v_spacing=6)
        self.ext_chip_buttons: dict[str, tuple[QPushButton, tuple[str, ...]]] = {}
        for label, exts, tip in FILE_TYPE_CHIP_SPECS:
            btn = QPushButton(label)
            btn.setObjectName("StudioChip")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(tip)
            btn.setAccessibleName(f"File type {label}")
            btn.clicked.connect(lambda checked, grp=exts: self._on_ext_chip_toggled(grp, checked))
            ft_chips_flow.addWidget(btn)
            self.ext_chip_buttons[label] = (btn, exts)
        cc.addWidget(ft_chips_host)

        ext_custom_row = QHBoxLayout()
        ext_custom_row.setSpacing(6)
        self.edit_custom_ext = QLineEdit()
        self.edit_custom_ext.setPlaceholderText("Custom file type (e.g. svg or .gcode — dot added automatically)")
        self.edit_custom_ext.setToolTip("Type any file extension like pdf or .svg and press Enter")
        self.edit_custom_ext.returnPressed.connect(self._on_add_custom_ext)
        ext_custom_row.addWidget(self.edit_custom_ext, 1)

        self.btn_add_ext = QPushButton("+ Add Type")
        self.btn_add_ext.setToolTip("Add custom file extension to this folder")
        self.btn_add_ext.clicked.connect(self._on_add_custom_ext)
        ext_custom_row.addWidget(self.btn_add_ext)
        cc.addLayout(ext_custom_row)

        self.extra_exts_host = QWidget()
        self.extra_exts_layout = _FlowLayout(self.extra_exts_host, margin=0, h_spacing=6, v_spacing=5)
        cc.addWidget(self.extra_exts_host)

        # 2. Filename Words
        lbl_kw = QLabel("Filename Words (matches if filename contains ANY of these words):")
        lbl_kw.setWordWrap(True)
        lbl_kw.setStyleSheet("color: #EAEAEF; font-size: 12px; font-weight: 600;")
        cc.addWidget(lbl_kw)

        kw_input_row = QHBoxLayout()
        kw_input_row.setSpacing(6)
        self.edit_keyword = QLineEdit()
        self.edit_keyword.setPlaceholderText("Add filename word (e.g. invoice, syllabus, bracket) and press Enter")
        self.edit_keyword.setToolTip("Add a filename keyword that routes files into this folder")
        self.edit_keyword.returnPressed.connect(self._on_add_keyword)
        kw_input_row.addWidget(self.edit_keyword, 1)

        self.btn_add_keyword = QPushButton("+ Add Word")
        self.btn_add_keyword.setToolTip("Add filename word chip")
        self.btn_add_keyword.clicked.connect(self._on_add_keyword)
        kw_input_row.addWidget(self.btn_add_keyword)
        cc.addLayout(kw_input_row)

        self.kw_chips_host = QWidget()
        self.kw_chips_layout = _FlowLayout(self.kw_chips_host, margin=0, h_spacing=6, v_spacing=5)
        cc.addWidget(self.kw_chips_host)

        # 3. Website Sources
        lbl_dom = QLabel("Website Sources (paste any domain or full URL — normalized automatically):")
        lbl_dom.setWordWrap(True)
        lbl_dom.setStyleSheet("color: #EAEAEF; font-size: 12px; font-weight: 600;")
        cc.addWidget(lbl_dom)

        dom_input_row = QHBoxLayout()
        dom_input_row.setSpacing(6)
        self.edit_domain = QLineEdit()
        self.edit_domain.setPlaceholderText("e.g. https://makerworld.com/en/models/... or stripe.com")
        self.edit_domain.setToolTip("Paste a website URL or domain; full URLs are automatically shortened to their domain")
        self.edit_domain.returnPressed.connect(self._on_add_domain)
        dom_input_row.addWidget(self.edit_domain, 1)

        self.btn_add_domain = QPushButton("+ Add Site")
        self.btn_add_domain.setToolTip("Add normalized website domain chip")
        self.btn_add_domain.clicked.connect(self._on_add_domain)
        dom_input_row.addWidget(self.btn_add_domain)
        cc.addLayout(dom_input_row)

        self.dom_chips_host = QWidget()
        self.dom_chips_layout = _FlowLayout(self.dom_chips_host, margin=0, h_spacing=6, v_spacing=5)
        cc.addWidget(self.dom_chips_host)

        ec.addWidget(self.cues_container)

        # Explicit Manual Checkbox (synced with top Intent Switch)
        self.chk_manual = QCheckBox("Manual folder only (drag files yourself)")
        self.chk_manual.setToolTip("Keep this folder without automatic sorting rules so only files you drag yourself go here")
        self.chk_manual.toggled.connect(self._set_selected_manual)
        ec.addWidget(self.chk_manual)
        ec.addStretch(1)

        self.editor_scroll.setWidget(editor_host)
        es_outer.addWidget(self.editor_scroll, 1)

        # Pinned Live Plain-English Rule Sentence + 1-Line Filename Tester (always visible!)
        rule_card = QFrame()
        rule_card.setObjectName("RuleSentenceCard")
        rc_lay = QVBoxLayout(rule_card)
        rc_lay.setContentsMargins(12, 9, 12, 9)
        rc_lay.setSpacing(5)

        lbl_rule_hdr = QLabel("LIVE PLAIN-ENGLISH RULE")
        lbl_rule_hdr.setStyleSheet("color: #64D2FF; font-size: 10.5px; font-weight: 700; border: none; background: transparent;")
        rc_lay.addWidget(lbl_rule_hdr)

        self.lbl_rule_sentence = QLabel("")
        self.lbl_rule_sentence.setWordWrap(True)
        self.lbl_rule_sentence.setStyleSheet("color: #F5F5F7; font-size: 12px; font-weight: 600; border: none; background: transparent;")
        rc_lay.addWidget(self.lbl_rule_sentence)

        tester_row = QHBoxLayout()
        tester_row.setSpacing(8)
        self.edit_test_filename = QLineEdit()
        self.edit_test_filename.setPlaceholderText("Test a filename (e.g. March_Invoice.pdf or plate.3mf from makerworld.com)")
        self.edit_test_filename.setToolTip("Type a sample filename to check immediately where this setup will sort it")
        self.edit_test_filename.textChanged.connect(self._refresh_live_test_result)
        tester_row.addWidget(self.edit_test_filename, 1)

        self.lbl_test_result = QLabel("Type a filename to test")
        self.lbl_test_result.setStyleSheet("color: #8E8E93; font-size: 11.5px; font-weight: 600; border: none; background: transparent;")
        tester_row.addWidget(self.lbl_test_result)
        rc_lay.addLayout(tester_row)

        es_outer.addWidget(rule_card)
        layout.addWidget(self.editor_surface, 6)
        return page

    def _build_step3_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(10)

        lbl_hdr = QLabel("Step 3 · Review & Apply Safely")
        lbl_hdr.setStyleSheet("color: #F5F5F7; font-size: 14px; font-weight: 700;")
        layout.addWidget(lbl_hdr)

        # 3 Summary Stat Cards
        stats_row = QHBoxLayout()
        stats_row.setSpacing(10)

        self.stat_folders_card = QFrame()
        self.stat_folders_card.setObjectName("SurfaceCard")
        sf1 = QVBoxLayout(self.stat_folders_card)
        sf1.setContentsMargins(14, 10, 14, 10)
        sf1.setSpacing(3)
        lbl_sf1 = QLabel("CONFIGURED FOLDERS")
        lbl_sf1.setObjectName("SectionEyebrow")
        sf1.addWidget(lbl_sf1)
        self.lbl_step3_folder_count = QLabel("0 folders")
        self.lbl_step3_folder_count.setStyleSheet("color: #F5F5F7; font-size: 16px; font-weight: 700;")
        sf1.addWidget(self.lbl_step3_folder_count)
        self.lbl_step3_folder_sub = QLabel("")
        self.lbl_step3_folder_sub.setWordWrap(True)
        self.lbl_step3_folder_sub.setStyleSheet("color: #98989D; font-size: 11.5px;")
        sf1.addWidget(self.lbl_step3_folder_sub)
        stats_row.addWidget(self.stat_folders_card, 1)

        self.stat_prot_card = QFrame()
        self.stat_prot_card.setObjectName("SurfaceCard")
        sf2 = QVBoxLayout(self.stat_prot_card)
        sf2.setContentsMargins(14, 10, 14, 10)
        sf2.setSpacing(3)
        lbl_sf2 = QLabel("PROTECTED & UNTOUCHED")
        lbl_sf2.setObjectName("SectionEyebrow")
        sf2.addWidget(lbl_sf2)
        self.lbl_step3_prot_headline = QLabel("100% Untouched")
        self.lbl_step3_prot_headline.setStyleSheet("color: #30D158; font-size: 16px; font-weight: 700;")
        sf2.addWidget(self.lbl_step3_prot_headline)
        self.lbl_step3_prot_sub = QLabel("")
        self.lbl_step3_prot_sub.setWordWrap(True)
        self.lbl_step3_prot_sub.setStyleSheet("color: #98989D; font-size: 11.5px;")
        sf2.addWidget(self.lbl_step3_prot_sub)
        stats_row.addWidget(self.stat_prot_card, 1)

        self.stat_moves_card = QFrame()
        self.stat_moves_card.setObjectName("SurfaceCard")
        sf3 = QVBoxLayout(self.stat_moves_card)
        sf3.setContentsMargins(14, 10, 14, 10)
        sf3.setSpacing(3)
        lbl_sf3 = QLabel("EXISTING DOWNLOADS FILES")
        lbl_sf3.setObjectName("SectionEyebrow")
        sf3.addWidget(lbl_sf3)
        self.lbl_step3_moves_headline = QLabel("0 files")
        self.lbl_step3_moves_headline.setStyleSheet("color: #64D2FF; font-size: 16px; font-weight: 700;")
        sf3.addWidget(self.lbl_step3_moves_headline)
        self.lbl_step3_moves_sub = QLabel("")
        self.lbl_step3_moves_sub.setWordWrap(True)
        self.lbl_step3_moves_sub.setStyleSheet("color: #98989D; font-size: 11.5px;")
        sf3.addWidget(self.lbl_step3_moves_sub)
        stats_row.addWidget(self.stat_moves_card, 1)

        layout.addLayout(stats_row)

        # Optional collision / overlap notice
        self.step3_collision_frame = QFrame()
        self.step3_collision_frame.setObjectName("GuidanceCallout")
        cf = QHBoxLayout(self.step3_collision_frame)
        cf.setContentsMargins(12, 8, 12, 8)
        self.lbl_step3_collision = QLabel("")
        self.lbl_step3_collision.setWordWrap(True)
        self.lbl_step3_collision.setStyleSheet("color: #FFD60A; font-size: 11.5px; border: none; background: transparent;")
        cf.addWidget(self.lbl_step3_collision, 1)
        self.step3_collision_frame.setVisible(False)
        layout.addWidget(self.step3_collision_frame)

        # Reassuring 0-move summary card
        self.step3_zero_moves_card = QFrame()
        self.step3_zero_moves_card.setObjectName("ReassureCard")
        zm = QHBoxLayout(self.step3_zero_moves_card)
        zm.setContentsMargins(14, 12, 14, 12)
        zm.setSpacing(10)
        lbl_zm_check = QLabel()
        lbl_zm_check.setPixmap(make_vector_icon("check", "#30D158", 16).pixmap(16, 16))
        lbl_zm_check.setStyleSheet("border: none; background: transparent;")
        zm.addWidget(lbl_zm_check)
        self.lbl_zero_moves_text = QLabel(
            "All existing files stay right where they are — your folder rules are ready for new downloads."
        )
        self.lbl_zero_moves_text.setWordWrap(True)
        self.lbl_zero_moves_text.setStyleSheet("color: #EAEAEF; font-size: 12.5px; font-weight: 600; border: none; background: transparent;")
        zm.addWidget(self.lbl_zero_moves_text, 1)
        layout.addWidget(self.step3_zero_moves_card)

        # Shift Files Opt-In Card + Sample Preview Table
        self.step3_shift_card = QFrame()
        self.step3_shift_card.setObjectName("SurfaceCard")
        sc = QVBoxLayout(self.step3_shift_card)
        sc.setContentsMargins(14, 12, 14, 12)
        sc.setSpacing(8)

        self.chk_shift_files = QCheckBox(
            "Also organize 0 matching existing file(s) into these folders right now (100% Undoable)"
        )
        self.chk_shift_files.setChecked(False)
        self.chk_shift_files.setToolTip(
            "When checked, loose top-level files and managed category files that match your new layout are moved in one undoable batch"
        )
        self.chk_shift_files.toggled.connect(lambda *_: self._sync_footer_summary())
        sc.addWidget(self.chk_shift_files)

        self.table_preview = QTableWidget(0, 3)
        self.table_preview.setAccessibleName("Preview of existing files that match this folder setup")
        self.table_preview.setHorizontalHeaderLabels(["File", "Current Location", "Target Folder"])
        self.table_preview.verticalHeader().setVisible(False)
        self.table_preview.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table_preview.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        hdr_view = self.table_preview.horizontalHeader()
        hdr_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr_view.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr_view.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        sc.addWidget(self.table_preview, 1)

        layout.addWidget(self.step3_shift_card, 1)
        return page

    # ------------------------------------------------------------------
    # Step 1 Logic (Starting Point Selection & Live Preview)
    # ------------------------------------------------------------------
    def _on_mode_card_clicked(self, mode_key: str) -> None:
        self._select_starting_mode(mode_key, preset_key=self._selected_preset_key, force_reload=False)

    def _on_blueprint_clicked(self, preset_key: str) -> None:
        self._select_starting_mode("preset", preset_key=preset_key, force_reload=False)

    def _select_starting_mode(self, mode_key: str, *, preset_key: str = "standard", force_reload: bool = False) -> None:
        if mode_key not in ("scan", "preset", "customize"):
            mode_key = "preset"
        if preset_key not in dict((k, label) for k, label, _ in BLUEPRINT_CHOICES):
            preset_key = "standard"

        self._selected_mode = mode_key
        self._selected_preset_key = preset_key

        self.card_scan.set_selected(mode_key == "scan")
        self.card_preset.set_selected(mode_key == "preset")
        self.card_customize.set_selected(mode_key == "customize")

        for k, btn in self.blueprint_buttons.items():
            btn.setChecked(mode_key == "preset" and k == preset_key)

        sig = (mode_key, preset_key if mode_key == "preset" else "")
        if force_reload or self._loaded_source_sig != sig:
            self._load_architecture_from_choice(mode_key, preset_key)
            self._loaded_source_sig = sig

        self._render_step1_preview()

    def _load_architecture_from_choice(self, mode_key: str, preset_key: str) -> None:
        self._manual_node_ids.clear()
        self._stashed_cues.clear()
        self._edited_paths.clear()
        self._removed_paths.clear()
        self._name_drafts.clear()
        self._name_errors.clear()
        self._edited_name_ids.clear()

        if mode_key == "scan":
            scanned_arch: FolderArchitecture = self._scan_info["architecture"].clone()
            if not scanned_arch.nodes:
                scanned_arch = FolderArchitecture.from_preset("basic_starter")
            self._arch = scanned_arch
        elif mode_key == "customize":
            self._arch = architecture_from_config(self.service.config).clone()
        else:
            self._arch = FolderArchitecture.from_preset(preset_key)

        for n in self._arch.nodes:
            if n.premade_key == _MANUAL_MARKER:
                self._manual_node_ids.add(n.id)

        first = self._ordered_nodes_with_depth()
        self._selected_node_id = first[0][0].id if first else None

    def _render_step1_preview(self) -> None:
        _clear_layout(self.step1_tree_layout)
        ordered = self._ordered_nodes_with_depth()
        top_cnt = sum(1 for n, d in ordered if d == 0)
        sub_cnt = len(ordered) - top_cnt

        if self._selected_mode == "scan":
            if self._scan_info["folder_count"] > 0:
                self.lbl_step1_preview_title.setText("Discovered Downloads Structure")
                self.lbl_step1_preview_desc.setText(
                    "Categories and custom folders scanned safely from your Downloads directory. "
                    "You can refine every folder's sorting rules in Step 2."
                )
            else:
                self.lbl_step1_preview_title.setText("Starter Layout (No Existing Folders Yet)")
                self.lbl_step1_preview_desc.setText(
                    "Your Downloads folder doesn't have custom category folders yet, so we prepared a clean starter layout."
                )
        elif self._selected_mode == "customize":
            self.lbl_step1_preview_title.setText("Your Current Active Setup")
            self.lbl_step1_preview_desc.setText(
                "Loaded directly from your saved Download Sorter configuration and custom rules."
            )
        else:
            bp_label = next((lbl for k, lbl, _ in BLUEPRINT_CHOICES if k == self._selected_preset_key), "Standard Everyday")
            bp_desc = next((d for k, _, d in BLUEPRINT_CHOICES if k == self._selected_preset_key), "")
            self.lbl_step1_preview_title.setText(f"Blueprint · {bp_label}")
            self.lbl_step1_preview_desc.setText(bp_desc)

        self.lbl_step1_preview_badge.setText(f"{top_cnt} top · {sub_cnt} sub")

        for node, depth in ordered[:28]:
            row = QFrame()
            row.setStyleSheet(
                "QFrame { background-color: rgba(255, 255, 255, 0.03); border: 1px solid rgba(255, 255, 255, 0.05); border-radius: 7px; }"
            )
            rl = QHBoxLayout(row)
            rl.setContentsMargins(8 + depth * 16, 4, 8, 4)
            rl.setSpacing(6)

            dot = QLabel("●")
            dot.setStyleSheet(f"color: {node.accent_hex}; font-size: 10px; border: none; background: transparent;")
            rl.addWidget(dot)

            nm = QLabel(node.name)
            nm.setStyleSheet(
                f"color: {'#F5F5F7' if depth == 0 else '#D1D1D6'}; "
                f"font-weight: {'600' if depth == 0 else '400'}; font-size: 12px; border: none; background: transparent;"
            )
            rl.addWidget(nm, 1)

            cues_preview: list[str] = []
            if node.id in self._manual_node_ids:
                cues_preview.append("Manual")
            else:
                if node.extensions:
                    cues_preview.append(", ".join(node.extensions[:3]) + ("…" if len(node.extensions) > 3 else ""))
                if node.keywords:
                    cues_preview.append(f"{len(node.keywords)} word{'s' if len(node.keywords) != 1 else ''}")
                if node.domains:
                    cues_preview.append(", ".join(node.domains[:2]))
            cue_lbl = QLabel(" · ".join(cues_preview) if cues_preview else "Needs rules in Step 2")
            cue_col = "#FF9F0A" if not cues_preview and not self._has_configured_descendant(node.id) else "#8E8E93"
            cue_lbl.setStyleSheet(f"color: {cue_col}; font-size: 11px; border: none; background: transparent;")
            rl.addWidget(cue_lbl)

            self.step1_tree_layout.addWidget(row)

        if len(ordered) > 28:
            more = QLabel(f"+ {len(ordered) - 28} more folders shown in Step 2…")
            more.setStyleSheet("color: #8E8E93; font-size: 11px; padding-left: 6px;")
            self.step1_tree_layout.addWidget(more)
        self.step1_tree_layout.addStretch(1)

        prot = self._scan_info["protected_folders"]
        if prot:
            prot_low = {p.lower() for p in prot}
            extra_examples = [ex for ex in ("Games", "Extracted", "_Duplicates", "Portable Apps") if ex.lower() not in prot_low]
            extra_tail = f" (plus {', '.join(extra_examples)} and git/app bundles)" if extra_examples else " (plus git/app bundles)"
            self.lbl_step1_protected.setText(
                f"Detected on disk & kept 100% untouched: {', '.join(prot)}{extra_tail}."
            )
        else:
            self.lbl_step1_protected.setText(
                "Folders like Games, Extracted, _Duplicates, Portable Apps, and git/app bundles are never scanned or altered."
            )

    # ------------------------------------------------------------------
    # Step 2 Logic (Checklist Completeness + Guided Folder Editor)
    # ------------------------------------------------------------------
    def _ordered_nodes_with_depth(self) -> list[tuple[ArchNode, int]]:
        result: list[tuple[ArchNode, int]] = []
        visited: set[str] = set()

        def _visit(node: ArchNode, depth: int) -> None:
            if node.id in visited or not node.enabled:
                return
            visited.add(node.id)
            result.append((node, depth))
            for child in self._arch.children_of(node.id):
                _visit(child, depth + 1)

        for top in self._arch.nodes_in_tier(1):
            _visit(top, 0)
        for orphan in self._arch.nodes:
            if orphan.id not in visited and orphan.enabled:
                _visit(orphan, max(0, orphan.tier - 1))
        return result

    def _node_has_own_cues(self, node: ArchNode) -> bool:
        return bool(node.extensions or node.keywords or node.domains)

    def _has_configured_descendant(self, node_id: str) -> bool:
        for desc in self._arch.descendants_of(node_id):
            if not desc.enabled:
                continue
            if desc.id in self._manual_node_ids or self._node_has_own_cues(desc):
                return True
        return False

    def _folder_name_error(self, node: ArchNode) -> str:
        """Return a validation error string if ``node`` has an invalid Windows name or duplicate sibling."""
        draft_err = self._name_errors.get(node.id, "")
        if draft_err:
            return draft_err

        cleaned, node_err = validate_windows_folder_name(node.name)
        if node_err:
            return node_err

        siblings = [s for s in self._arch.children_of(node.parent_id) if s.enabled and s.id not in self._name_errors]
        same_name = [
            s for s in siblings if s.name.strip().rstrip(". ").strip().lower() == cleaned.lower()
        ]
        if len(same_name) > 1:
            edited_matches = [s for s in same_name if s.id in self._edited_name_ids]
            offender_ids = {s.id for s in edited_matches} if (0 < len(edited_matches) < len(same_name)) else {s.id for s in same_name[1:]}
            if node.id in offender_ids:
                other = next((s for s in same_name if s.id != node.id), same_name[0])
                parent_label = "Downloads" if node.parent_id is None else self._display_path(node.parent_id)
                return f"A folder named '{other.name}' already exists in {parent_label} — please choose a unique name."
        return ""

    def _node_status(self, node: ArchNode) -> tuple[str, str]:
        """Return ``(badge_kind, badge_label)`` for ``node``."""
        if self._folder_name_error(node):
            return ("needs_rules", "! Fix name")
        if node.id in self._manual_node_ids:
            return ("manual", "Manual")
        if self._node_has_own_cues(node) or self._has_configured_descendant(node.id):
            return ("ready", "✓ Ready")
        return ("needs_rules", "! Needs rules")

    def _incomplete_nodes(self) -> list[ArchNode]:
        return [
            node
            for node, _depth in self._ordered_nodes_with_depth()
            if self._node_status(node)[0] == "needs_rules"
        ]

    def _display_path(self, node_id: str) -> str:
        rel = self._arch.node_relative_path(node_id)
        return rel.replace("\\", " › ") if rel else "Downloads"

    def _rebuild_checklist(self, *, keep_node_id: str | None = None) -> None:
        target_id = keep_node_id or self._selected_node_id
        ordered = self._ordered_nodes_with_depth()

        self.checklist.blockSignals(True)
        self.checklist.clear()
        select_row = 0
        needs_cnt = 0

        for row_idx, (node, depth) in enumerate(ordered):
            kind, badge_label = self._node_status(node)
            if kind == "needs_rules":
                needs_cnt += 1
            item = QListWidgetItem(f"{node.name} ({badge_label})")
            item.setData(_ROLE_NODE_ID, node.id)
            item.setData(_ROLE_DEPTH, depth)
            item.setData(_ROLE_ACCENT, node.accent_hex)
            item.setData(_ROLE_BADGE_TEXT, badge_label)
            item.setData(_ROLE_BADGE_KIND, kind)
            item.setData(_ROLE_FOLDER_NAME, node.name)
            item.setToolTip(f"{self._display_path(node.id)} — {badge_label}")
            self.checklist.addItem(item)
            if node.id == target_id:
                select_row = row_idx

        if ordered:
            self.checklist.setCurrentRow(select_row)
            self._selected_node_id = ordered[select_row][0].id
        else:
            self._selected_node_id = None
        self.checklist.blockSignals(False)

        self._update_checklist_header_badge(len(ordered), needs_cnt)
        self._load_selected_node_into_editor()

    def _refresh_current_checklist_item_in_place(self) -> None:
        ordered = self._ordered_nodes_with_depth()
        needs_cnt = 0
        for idx in range(self.checklist.count()):
            it = self.checklist.item(idx)
            if it is None:
                continue
            nid = str(it.data(_ROLE_NODE_ID) or "")
            node = self._arch.get_node(nid)
            if node is None:
                continue
            kind, badge_label = self._node_status(node)
            if kind == "needs_rules":
                needs_cnt += 1
            it.setText(f"{node.name} ({badge_label})")
            it.setData(_ROLE_ACCENT, node.accent_hex)
            it.setData(_ROLE_BADGE_TEXT, badge_label)
            it.setData(_ROLE_BADGE_KIND, kind)
            it.setData(_ROLE_FOLDER_NAME, node.name)
            it.setToolTip(f"{self._display_path(node.id)} — {badge_label}")
        self.checklist.viewport().update()
        self._update_checklist_header_badge(len(ordered), needs_cnt)
        self._sync_footer_summary()

    def _update_checklist_header_badge(self, total: int, needs_cnt: int) -> None:
        if needs_cnt > 0:
            self.lbl_checklist_status.setText(f"{needs_cnt} need{'s' if needs_cnt == 1 else ''} rules")
            self.lbl_checklist_status.setProperty("variant", "warning")
        else:
            self.lbl_checklist_status.setText(f"{total} ready")
            self.lbl_checklist_status.setProperty("variant", "success")
        refresh_style(self.lbl_checklist_status)

    def _on_checklist_row_changed(self, row: int) -> None:
        if row < 0 or row >= self.checklist.count():
            self._selected_node_id = None
        else:
            item = self.checklist.item(row)
            self._selected_node_id = str(item.data(_ROLE_NODE_ID)) if item is not None else None
        self.guidance_frame.setVisible(False)
        self._load_selected_node_into_editor()

    def _selected_node(self) -> ArchNode | None:
        return self._arch.get_node(self._selected_node_id)

    def _mark_node_edited(self, node: ArchNode) -> None:
        rel = self._arch.node_relative_path(node.id).lower()
        if rel:
            self._edited_paths.add(rel)

    def _load_selected_node_into_editor(self) -> None:
        node = self._selected_node()
        has_node = node is not None
        self.edit_folder_name.setEnabled(has_node)
        self.btn_remove_folder.setEnabled(has_node and len(self._arch.nodes) > 1)
        self.btn_add_subfolder.setEnabled(has_node)
        self.btn_intent_auto.setEnabled(has_node)
        self.btn_intent_manual.setEnabled(has_node)
        self.chk_manual.setEnabled(has_node)

        if node is None:
            self.cues_container.setEnabled(False)
            self.lbl_rule_sentence.setText("Add or select a folder on the left to configure its sorting rules.")
            return

        self._syncing_editor = True
        try:
            is_manual = node.id in self._manual_node_ids
            self.lbl_editor_heading.setText(f"Editing · {self._display_path(node.id)}")
            shown_name = self._name_drafts.get(node.id, node.name)
            if self.edit_folder_name.text() != shown_name:
                self.edit_folder_name.setText(shown_name)

            self.btn_intent_auto.setChecked(not is_manual)
            self.btn_intent_manual.setChecked(is_manual)
            self.chk_manual.setChecked(is_manual)
            self.cues_container.setEnabled(not is_manual)

            self._rebuild_parent_pills(node)
            self._rebuild_subfolder_suggestions(node)
            self._sync_ext_chips(node)
            self._rebuild_keyword_chips(node)
            self._rebuild_domain_chips(node)
            self._refresh_rule_sentence_and_guidance(node)
            self._refresh_live_test_result()
        finally:
            self._syncing_editor = False
        name_controls_for_screen_readers(self.editor_surface)

    def _rebuild_parent_pills(self, node: ArchNode) -> None:
        _clear_layout(self.parent_pills_layout)

        top_btn = QPushButton("Top-level in Downloads")
        top_btn.setObjectName("StudioChip")
        top_btn.setCheckable(True)
        top_btn.setChecked(node.parent_id is None)
        top_btn.setEnabled(node.parent_id is None or self._arch.can_reparent(node.id, None))
        top_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        top_btn.setToolTip("Place this folder directly inside Downloads")
        top_btn.setAccessibleName("Location: Top-level in Downloads")
        top_btn.clicked.connect(lambda: self._on_parent_pill_clicked(None))
        self.parent_pills_layout.addWidget(top_btn)

        for cand, _depth in self._ordered_nodes_with_depth():
            if cand.id == node.id:
                continue
            if cand.id != node.parent_id and not self._arch.can_reparent(node.id, cand.id):
                continue
            if cand.tier > 2 and cand.id != node.parent_id:
                continue
            disp = self._display_path(cand.id)
            p_btn = QPushButton(_esc_amp(f"Inside {disp}"))
            p_btn.setObjectName("StudioChip")
            p_btn.setCheckable(True)
            p_btn.setChecked(node.parent_id == cand.id)
            p_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            p_btn.setToolTip(f"Nest '{node.name}' inside '{disp}'")
            p_btn.setAccessibleName(f"Location: Inside {disp}")
            p_btn.clicked.connect(lambda _=False, pid=cand.id: self._on_parent_pill_clicked(pid))
            self.parent_pills_layout.addWidget(p_btn)

    def _rebuild_subfolder_suggestions(self, node: ArchNode) -> None:
        _clear_layout(self.sub_sug_chips_layout)
        if node.tier >= FolderArchitecture.MAX_TIERS:
            self.sub_sug_box.setVisible(False)
            return

        suggestions = get_tier2_suggestions(node.name, node.kind, mode="all")
        existing_child_names = {c.name.lower() for c in self._arch.children_of(node.id)}
        available = [s for s in suggestions if str(s.get("name", "")).lower() not in existing_child_names]
        if not available:
            self.sub_sug_box.setVisible(False)
            return

        self.sub_sug_box.setVisible(True)
        self.lbl_sub_sug_title.setText(f"1-click suggested subfolders inside {node.name}:")
        for sug in available[:8]:
            sname = str(sug["name"])
            chip = QPushButton(_esc_amp(f"+ {sname}"))
            chip.setObjectName("GhostSuggestionChip")
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setToolTip(str(sug.get("summary") or f"Add '{sname}' subfolder inside {node.name}"))
            chip.setAccessibleName(f"Add suggested subfolder {sname} inside {node.name}")
            chip.clicked.connect(lambda _=False, payload=sug: self._on_add_suggested_subfolder(payload))
            self.sub_sug_chips_layout.addWidget(chip)

    def _sync_ext_chips(self, node: ArchNode) -> None:
        covered_exts: set[str] = set()
        node_ext_set = set(node.extensions)
        for _label, (btn, exts) in self.ext_chip_buttons.items():
            btn.blockSignals(True)
            is_all_present = all(e in node_ext_set for e in exts)
            btn.setChecked(is_all_present)
            if is_all_present:
                covered_exts.update(exts)
            btn.blockSignals(False)

        _clear_layout(self.extra_exts_layout)
        extras = [e for e in node.extensions if e not in covered_exts]
        for ext in extras:
            chip = QPushButton(f"{ext}  ×")
            chip.setObjectName("RemovableTokenChip")
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setToolTip(f"Remove file extension {ext}")
            chip.setAccessibleName(f"Remove file extension {ext}")
            chip.clicked.connect(lambda _=False, e=ext: self._remove_single_ext(e))
            self.extra_exts_layout.addWidget(chip)

        # Offer inferred extension ghost chips if not already active
        parent = self._arch.get_node(node.parent_id)
        inferred = _infer_folder_suggestions(node.name, parent.name if parent else "")
        for ghost_ext in inferred["extensions"]:
            if ghost_ext not in node_ext_set:
                gbtn = QPushButton(f"+ {ghost_ext}")
                gbtn.setObjectName("GhostSuggestionChip")
                gbtn.setCursor(Qt.CursorShape.PointingHandCursor)
                gbtn.setToolTip(f"Suggested from folder name: add {ghost_ext}")
                gbtn.setAccessibleName(f"Suggested file extension {ghost_ext}")
                gbtn.clicked.connect(lambda _=False, e=ghost_ext: self.add_custom_extension(e))
                self.extra_exts_layout.addWidget(gbtn)

        self.extra_exts_host.setVisible(self.extra_exts_layout.count() > 0)

    def _rebuild_keyword_chips(self, node: ArchNode) -> None:
        _clear_layout(self.kw_chips_layout)
        existing_lower = {k.lower() for k in node.keywords}

        for kw in node.keywords:
            chip = QPushButton(_esc_amp(f"{kw}  ×"))
            chip.setObjectName("RemovableTokenChip")
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setToolTip(f"Remove filename word '{kw}'")
            chip.setAccessibleName(f"Remove filename word {kw}")
            chip.clicked.connect(lambda _=False, val=kw: self._remove_keyword(val))
            self.kw_chips_layout.addWidget(chip)

        parent = self._arch.get_node(node.parent_id)
        inferred = _infer_folder_suggestions(node.name, parent.name if parent else "")
        for sug_kw in inferred["keywords"]:
            if sug_kw.lower() not in existing_lower:
                gbtn = QPushButton(_esc_amp(f"+ {sug_kw}"))
                gbtn.setObjectName("GhostSuggestionChip")
                gbtn.setCursor(Qt.CursorShape.PointingHandCursor)
                gbtn.setToolTip(f"Suggested filename word for '{node.name}': click to add '{sug_kw}'")
                gbtn.setAccessibleName(f"Add suggested filename word {sug_kw}")
                gbtn.clicked.connect(lambda _=False, val=sug_kw: self.add_keyword(val))
                self.kw_chips_layout.addWidget(gbtn)

        self.kw_chips_host.setVisible(self.kw_chips_layout.count() > 0)

    def _rebuild_domain_chips(self, node: ArchNode) -> None:
        _clear_layout(self.dom_chips_layout)
        existing_lower = {d.lower() for d in node.domains}

        for dom in node.domains:
            chip = QPushButton(f"{dom}  ×")
            chip.setObjectName("RemovableTokenChip")
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setToolTip(f"Remove website source '{dom}'")
            chip.setAccessibleName(f"Remove website source {dom}")
            chip.clicked.connect(lambda _=False, val=dom: self._remove_domain(val))
            self.dom_chips_layout.addWidget(chip)

        parent = self._arch.get_node(node.parent_id)
        inferred = _infer_folder_suggestions(node.name, parent.name if parent else "")
        for sug_dom in inferred["domains"]:
            if sug_dom.lower() not in existing_lower:
                gbtn = QPushButton(f"+ {sug_dom}")
                gbtn.setObjectName("GhostSuggestionChip")
                gbtn.setCursor(Qt.CursorShape.PointingHandCursor)
                gbtn.setToolTip(f"Suggested website source for '{node.name}': click to add '{sug_dom}'")
                gbtn.setAccessibleName(f"Add suggested website {sug_dom}")
                gbtn.clicked.connect(lambda _=False, val=sug_dom: self.add_domain(val))
                self.dom_chips_layout.addWidget(gbtn)

        self.dom_chips_host.setVisible(self.dom_chips_layout.count() > 0)

    def _refresh_rule_sentence_and_guidance(self, node: ArchNode, *, force_guidance: bool = False) -> None:
        is_manual = node.id in self._manual_node_ids
        has_children = self._has_configured_descendant(node.id)
        sentence = format_plain_english_folder_rule(
            self._display_path(node.id),
            extensions=node.extensions,
            keywords=node.keywords,
            domains=node.domains,
            is_manual=is_manual,
            has_active_children=has_children,
        )
        self.lbl_rule_sentence.setText(sentence)

        name_err = self._folder_name_error(node)
        status_kind, _ = self._node_status(node)
        if name_err:
            self.lbl_guidance_icon.setText("!")
            self.lbl_guidance_icon.setStyleSheet(
                "color: #FF9F0A; font-weight: 800; font-size: 13px; border: none; background: transparent;"
            )
            self.lbl_guidance_text.setText(name_err)
            self.guidance_frame.setVisible(True)
        elif status_kind == "needs_rules":
            self.lbl_guidance_icon.setText("!")
            self.lbl_guidance_icon.setStyleSheet(
                "color: #FF9F0A; font-weight: 800; font-size: 13px; border: none; background: transparent;"
            )
            self.lbl_guidance_text.setText(
                f"Complete '{node.name}': click at least one file type, filename word, or website suggestion below — "
                "or switch it to 'Manual folder' if you prefer to drag files in yourself."
            )
            self.guidance_frame.setVisible(True)
        elif not force_guidance:
            self.guidance_frame.setVisible(False)

    # ------------------------------------------------------------------
    # Step 2 Interactive Editor Mutations
    # ------------------------------------------------------------------
    def _on_add_folder_clicked(self) -> None:
        base_name = "New Folder"
        existing = {n.name.lower() for n in self._arch.nodes_in_tier(1)}
        idx = 2
        cand_name = base_name
        while cand_name.lower() in existing:
            cand_name = f"New Folder {idx}"
            idx += 1
        node = self._arch.add_custom_node(name=cand_name, tier=1, parent_id=None)
        self._mark_node_edited(node)
        self._rebuild_checklist(keep_node_id=node.id)
        self.edit_folder_name.setFocus()
        self.edit_folder_name.selectAll()

    def _on_add_subfolder_clicked(self) -> None:
        cur = self._selected_node()
        if cur is None:
            self._on_add_folder_clicked()
            return
        parent = cur if cur.tier < FolderArchitecture.MAX_TIERS else self._arch.get_node(cur.parent_id)
        if parent is None:
            self._on_add_folder_clicked()
            return
        existing = {c.name.lower() for c in self._arch.children_of(parent.id)}
        cand_name = "New Subfolder"
        idx = 2
        while cand_name.lower() in existing:
            cand_name = f"New Subfolder {idx}"
            idx += 1
        child = self._arch.add_custom_node(name=cand_name, tier=parent.tier + 1, parent_id=parent.id)
        self._mark_node_edited(child)
        self._rebuild_checklist(keep_node_id=child.id)
        self.edit_folder_name.setFocus()
        self.edit_folder_name.selectAll()

    def _on_add_suggested_subfolder(self, sug: dict[str, Any]) -> None:
        parent = self._selected_node()
        if parent is None or parent.tier >= FolderArchitecture.MAX_TIERS:
            return
        child = self._arch.add_custom_node(
            name=str(sug["name"]),
            tier=parent.tier + 1,
            parent_id=parent.id,
            kind=str(sug.get("kind") or "default"),
            extensions=list(sug.get("extensions") or []),
            keywords=list(sug.get("keywords") or []),
            exclude_keywords=list(sug.get("exclude_keywords") or []),
            domains=list(sug.get("domains") or []),
        )
        self._mark_node_edited(child)
        self._rebuild_checklist(keep_node_id=parent.id)

    def _on_remove_folder_clicked(self) -> None:
        node = self._selected_node()
        if node is None or len(self._arch.nodes) <= 1:
            return
        cur_row = self.checklist.currentRow()
        for n in [node, *self._arch.descendants_of(node.id)]:
            rel = self._arch.node_relative_path(n.id).lower()
            if rel:
                self._removed_paths.add(rel)
            self._manual_node_ids.discard(n.id)
            self._stashed_cues.pop(n.id, None)
            self._name_drafts.pop(n.id, None)
            self._name_errors.pop(n.id, None)
        self._arch.remove_node(node.id)
        ordered = self._ordered_nodes_with_depth()
        next_id = ordered[min(max(0, cur_row), len(ordered) - 1)][0].id if ordered else None
        self._rebuild_checklist(keep_node_id=next_id)

    def _tidy_folder_name_field(self) -> None:
        node = self._selected_node()
        if node is None or node.id in self._name_errors:
            return
        if self.edit_folder_name.text() != node.name:
            self._syncing_editor = True
            try:
                self.edit_folder_name.setText(node.name)
            finally:
                self._syncing_editor = False

    def _on_folder_name_edited(self, raw_name: str) -> None:
        if self._syncing_editor:
            return
        node = self._selected_node()
        if node is None:
            return

        cleaned, err = validate_windows_folder_name(raw_name)
        if err:
            self._name_drafts[node.id] = raw_name
            self._name_errors[node.id] = err
            self._refresh_rule_sentence_and_guidance(node)
            self._refresh_current_checklist_item_in_place()
            return

        self._name_drafts.pop(node.id, None)
        self._name_errors.pop(node.id, None)
        self._edited_name_ids.add(node.id)

        old_rel = self._arch.node_relative_path(node.id).lower()
        node.name = cleaned
        new_rel = self._arch.node_relative_path(node.id).lower()
        if old_rel and old_rel != new_rel:
            self._removed_paths.add(old_rel)
        self._mark_node_edited(node)
        self.lbl_editor_heading.setText(f"Editing · {self._display_path(node.id)}")
        self._rebuild_subfolder_suggestions(node)
        self._sync_ext_chips(node)
        self._rebuild_keyword_chips(node)
        self._rebuild_domain_chips(node)
        self._refresh_rule_sentence_and_guidance(node)
        self._refresh_current_checklist_item_in_place()
        self._refresh_live_test_result()

    def _on_parent_pill_clicked(self, new_parent_id: str | None) -> None:
        node = self._selected_node()
        if node is None or node.parent_id == new_parent_id:
            if node is not None:
                self._rebuild_parent_pills(node)
            return
        old_rel = self._arch.node_relative_path(node.id).lower()
        if self._arch.reparent_node(node.id, new_parent_id):
            if old_rel:
                self._removed_paths.add(old_rel)
            self._edited_name_ids.add(node.id)
            self._mark_node_edited(node)
            self._rebuild_checklist(keep_node_id=node.id)

    def _set_selected_manual(self, manual: bool) -> None:
        if self._syncing_editor:
            return
        node = self._selected_node()
        if node is None:
            return
        self._mark_node_edited(node)
        if manual:
            if node.id not in self._manual_node_ids:
                if node.extensions or node.keywords or node.domains:
                    self._stashed_cues[node.id] = (
                        list(node.extensions),
                        list(node.keywords),
                        list(node.domains),
                    )
                node.extensions = []
                node.keywords = []
                node.domains = []
                node.premade_key = _MANUAL_MARKER
                self._manual_node_ids.add(node.id)
        else:
            if node.id in self._manual_node_ids:
                self._manual_node_ids.discard(node.id)
                if node.premade_key == _MANUAL_MARKER:
                    node.premade_key = ""
                stashed = self._stashed_cues.pop(node.id, None)
                if stashed is not None:
                    node.extensions, node.keywords, node.domains = stashed

        self._load_selected_node_into_editor()
        self._refresh_current_checklist_item_in_place()

    def _on_ext_chip_toggled(self, exts_group: tuple[str, ...], checked: bool) -> None:
        if self._syncing_editor:
            return
        node = self._selected_node()
        if node is None:
            return
        self._mark_node_edited(node)
        cur = list(node.extensions)
        if checked:
            for e in exts_group:
                if e not in cur:
                    cur.append(e)
        else:
            cur = [e for e in cur if e not in exts_group]
        node.extensions = _clean_exts(cur)
        self._sync_ext_chips(node)
        self._refresh_rule_sentence_and_guidance(node)
        self._refresh_current_checklist_item_in_place()
        self._refresh_live_test_result()

    def add_custom_extension(self, raw_text: str) -> list[str]:
        """Normalize and add one or more custom extensions (e.g. ``'pdf'`` -> ``'.pdf'``) to the selected folder."""
        node = self._selected_node()
        if node is None:
            return []
        added: list[str] = []
        cur = list(node.extensions)
        for part in str(raw_text or "").replace(";", ",").replace(" ", ",").split(","):
            norm = _normalize_single_ext(part)
            if norm and norm not in cur:
                cur.append(norm)
                added.append(norm)
        if added:
            if node.id in self._manual_node_ids:
                self._manual_node_ids.discard(node.id)
                if node.premade_key == _MANUAL_MARKER:
                    node.premade_key = ""
            node.extensions = _clean_exts(cur)
            self._mark_node_edited(node)
            self._load_selected_node_into_editor()
            self._refresh_current_checklist_item_in_place()
        return added

    def _on_add_custom_ext(self) -> None:
        raw = self.edit_custom_ext.text()
        if self.add_custom_extension(raw):
            self.edit_custom_ext.clear()

    def _remove_single_ext(self, ext: str) -> None:
        node = self._selected_node()
        if node is None:
            return
        node.extensions = [e for e in node.extensions if e != ext]
        self._mark_node_edited(node)
        self._sync_ext_chips(node)
        self._refresh_rule_sentence_and_guidance(node)
        self._refresh_current_checklist_item_in_place()
        self._refresh_live_test_result()

    def add_keyword(self, raw_text: str) -> list[str]:
        """Add one or more filename keywords to the selected folder."""
        node = self._selected_node()
        if node is None:
            return []
        cur = list(node.keywords)
        existing_low = {k.lower() for k in cur}
        added: list[str] = []
        for part in str(raw_text or "").replace(";", ",").split(","):
            clean = part.strip()
            if clean and clean.lower() not in existing_low:
                cur.append(clean)
                existing_low.add(clean.lower())
                added.append(clean)
        if added:
            if node.id in self._manual_node_ids:
                self._manual_node_ids.discard(node.id)
                if node.premade_key == _MANUAL_MARKER:
                    node.premade_key = ""
            node.keywords = _clean_strs(cur)
            self._mark_node_edited(node)
            self._load_selected_node_into_editor()
            self._refresh_current_checklist_item_in_place()
        return added

    def _on_add_keyword(self) -> None:
        raw = self.edit_keyword.text()
        if self.add_keyword(raw):
            self.edit_keyword.clear()

    def _remove_keyword(self, keyword: str) -> None:
        node = self._selected_node()
        if node is None:
            return
        node.keywords = [k for k in node.keywords if k.lower() != keyword.lower()]
        self._mark_node_edited(node)
        self._rebuild_keyword_chips(node)
        self._refresh_rule_sentence_and_guidance(node)
        self._refresh_current_checklist_item_in_place()
        self._refresh_live_test_result()

    def add_domain(self, raw_text: str) -> list[str]:
        """Normalize URLs/domains via ``extract_host`` and add them to the selected folder."""
        node = self._selected_node()
        if node is None:
            return []
        cur = list(node.domains)
        existing_low = {d.lower() for d in cur}
        added: list[str] = []
        for part in str(raw_text or "").replace(";", ",").split(","):
            token = part.strip()
            if not token:
                continue
            host = extract_host(token)
            if host and "." in host and host not in existing_low:
                cur.append(host)
                existing_low.add(host)
                added.append(host)
        if added:
            if node.id in self._manual_node_ids:
                self._manual_node_ids.discard(node.id)
                if node.premade_key == _MANUAL_MARKER:
                    node.premade_key = ""
            node.domains = cur
            self._mark_node_edited(node)
            self._load_selected_node_into_editor()
            self._refresh_current_checklist_item_in_place()
        return added

    def _on_add_domain(self) -> None:
        raw = self.edit_domain.text()
        if self.add_domain(raw):
            self.edit_domain.clear()

    def _remove_domain(self, domain: str) -> None:
        node = self._selected_node()
        if node is None:
            return
        node.domains = [d for d in node.domains if d.lower() != domain.lower()]
        self._mark_node_edited(node)
        self._rebuild_domain_chips(node)
        self._refresh_rule_sentence_and_guidance(node)
        self._refresh_current_checklist_item_in_place()
        self._refresh_live_test_result()

    def learn_from_sample_file(self, file_path: Path, *, source_url: str = "") -> dict[str, Any]:
        """Auto-populate extension, stem keywords, and Zone.Identifier domain from a dropped sample file."""
        node = self._selected_node()
        if node is None:
            return {"added_ext": "", "added_keywords": [], "added_domain": ""}

        if node.id in self._manual_node_ids:
            self._manual_node_ids.discard(node.id)
            if node.premade_key == _MANUAL_MARKER:
                node.premade_key = ""

        added_ext = ""
        ext = file_path.suffix.lower()
        if ext and ext not in node.extensions:
            node.extensions = _clean_exts([*node.extensions, ext])
            added_ext = ext

        added_kws: list[str] = []
        existing_kw_low = {k.lower() for k in node.keywords}
        for tok in _normalize_tokens(file_path.stem).split():
            if len(tok) >= 3 and not tok.isdigit() and tok not in _SAMPLE_STOPWORDS and tok not in existing_kw_low:
                node.keywords.append(tok)
                existing_kw_low.add(tok)
                added_kws.append(tok)
                if len(added_kws) >= 2:
                    break

        raw_zone = source_url or read_zone_url(file_path)
        host = extract_host(raw_zone)
        added_dom = ""
        if host and "." in host and host not in {d.lower() for d in node.domains}:
            node.domains.append(host)
            added_dom = host

        self._mark_node_edited(node)
        self._load_selected_node_into_editor()
        self._refresh_current_checklist_item_in_place()

        self.edit_test_filename.setText(f"{file_path.name} from {host}" if host else file_path.name)
        parts_learned = [p for p in (added_ext, ", ".join(added_kws), added_dom) if p]
        self.lbl_guidance_icon.setText("✓")
        self.lbl_guidance_icon.setStyleSheet(
            "color: #30D158; font-weight: 800; font-size: 13px; border: none; background: transparent;"
        )
        self.lbl_guidance_text.setText(
            f"Learned from '{file_path.name}'"
            + (f": added {' · '.join(parts_learned)}" if parts_learned else " — cues already covered!")
        )
        self.guidance_frame.setVisible(True)
        return {"added_ext": added_ext, "added_keywords": added_kws, "added_domain": added_dom}

    def _flush_pending_chip_inputs(self) -> None:
        self._tidy_folder_name_field()
        if self.edit_custom_ext.text().strip():
            self._on_add_custom_ext()
        if self.edit_keyword.text().strip():
            self._on_add_keyword()
        if self.edit_domain.text().strip():
            self._on_add_domain()

    def _refresh_live_test_result(self) -> None:
        raw = self.edit_test_filename.text().strip()
        node = self._selected_node()
        if not raw or node is None:
            self.lbl_test_result.setText("Type a filename to test")
            self.lbl_test_result.setStyleSheet("color: #8E8E93; font-size: 11.5px; font-weight: 600; border: none; background: transparent;")
            return

        fname = raw
        src_url = ""
        if " from " in raw.lower():
            idx = raw.lower().rfind(" from ")
            fname = raw[:idx].strip()
            src_url = raw[idx + 6 :].strip()
        elif raw.lower().startswith(("http://", "https://")) and "/" in raw:
            src_url = raw
            fname = Path(raw.split("?", 1)[0].split("#", 1)[0]).name or "download.bin"

        preview_cfg = self.target_config()
        dec = evaluate(FileCandidate(filename=fname, source_url=src_url), preview_cfg, include_trace=False)
        selected_rel = self._arch.node_relative_path(node.id).replace("/", "\\").strip("\\")

        if dec.action == "move" and dec.relative_dest is not None:
            landed_rel = str(dec.relative_dest.parent).replace("/", "\\").strip("\\")
            landed_disp = landed_rel.replace("\\", " › ")
            if landed_rel.lower() == selected_rel.lower():
                self.lbl_test_result.setText(f"Matched · Lands in {landed_disp}")
                self.lbl_test_result.setStyleSheet("color: #30D158; font-size: 11.5px; font-weight: 700; border: none; background: transparent;")
            else:
                self.lbl_test_result.setText(f"→ Lands in {landed_disp} instead")
                self.lbl_test_result.setStyleSheet("color: #5AC8FA; font-size: 11.5px; font-weight: 600; border: none; background: transparent;")
        else:
            self.lbl_test_result.setText("○ Stays in Downloads (no match)")
            self.lbl_test_result.setStyleSheet("color: #FF9F0A; font-size: 11.5px; font-weight: 600; border: none; background: transparent;")

    # ------------------------------------------------------------------
    # Step 3 Logic (Config Compilation, Collisions & Safe Shift Preview)
    # ------------------------------------------------------------------
    @staticmethod
    def _is_simple_folder_rule(rule: Rule) -> bool:
        return not (
            rule.regex_pattern
            or rule.rename_template
            or rule.content_keywords
            or rule.min_size_mb > 0
            or rule.max_size_mb > 0
            or rule.min_age_days > 0
            or rule.auto_unzip
            or rule.match_mode == "all"
        )

    def _detect_cue_collisions(self) -> list[str]:
        warnings: list[str] = []
        seen_signatures: dict[tuple[int, tuple[str, ...], tuple[str, ...], tuple[str, ...]], str] = {}
        for node, _depth in self._ordered_nodes_with_depth():
            if node.id in self._manual_node_ids or not self._node_has_own_cues(node):
                continue
            sig = (
                node.tier,
                tuple(sorted(node.extensions)),
                tuple(sorted(k.lower() for k in node.keywords)),
                tuple(sorted(d.lower() for d in node.domains)),
            )
            disp = self._display_path(node.id)
            prev = seen_signatures.get(sig)
            if prev and prev != disp:
                warnings.append(f"'{prev}' and '{disp}' share identical sorting cues — '{prev}' will match first.")
            else:
                seen_signatures[sig] = disp
        return warnings

    def _refresh_step3(self) -> None:
        target_cfg = self.target_config()
        self._last_preview_plan = self.service.preview_architecture_shift(target_cfg)
        moves = self._last_preview_plan.get("moves_planned", [])

        ordered = self._ordered_nodes_with_depth()
        top_cnt = sum(1 for _n, d in ordered if d == 0)
        sub_cnt = len(ordered) - top_cnt
        manual_cnt = sum(1 for n, _d in ordered if n.id in self._manual_node_ids)

        self.lbl_step3_folder_count.setText(f"{len(ordered)} folder{'s' if len(ordered) != 1 else ''}")
        manual_note = f" · {manual_cnt} manual" if manual_cnt else ""
        self.lbl_step3_folder_sub.setText(f"{top_cnt} top-level · {sub_cnt} subfolder{'s' if sub_cnt != 1 else ''}{manual_note}")

        prot = self._scan_info["protected_folders"]
        if prot:
            self.lbl_step3_prot_headline.setText(f"{len(prot)} Protected on Disk")
            self.lbl_step3_prot_sub.setText(f"Left untouched: {', '.join(prot[:5])}")
        else:
            self.lbl_step3_prot_headline.setText("100% Untouched")
            self.lbl_step3_prot_sub.setText("Games, Extracted, _Duplicates & project bundles stay safe")

        collisions = self._detect_cue_collisions()
        if collisions:
            self.lbl_step3_collision.setText("Note: " + "  ".join(collisions[:2]))
            self.step3_collision_frame.setVisible(True)
        else:
            self.step3_collision_frame.setVisible(False)

        move_cnt = len(moves)
        if move_cnt > 0:
            self.lbl_step3_moves_headline.setText(f"{move_cnt} matching file{'s' if move_cnt != 1 else ''}")
            self.lbl_step3_moves_headline.setStyleSheet("color: #64D2FF; font-size: 16px; font-weight: 700;")
            self.lbl_step3_moves_sub.setText("Opt in below to organize them right now (100% Undoable)")

            self.step3_zero_moves_card.setVisible(False)
            self.chk_shift_files.setEnabled(True)
            self.chk_shift_files.setText(
                f"Also organize {move_cnt} matching existing file{'s' if move_cnt != 1 else ''} into these folders right now (100% Undoable)"
            )
            self.table_preview.setVisible(True)
            sample = moves[:50]
            self.table_preview.setRowCount(len(sample))
            for r_idx, item in enumerate(sample):
                fname = Path(item["src"]).name
                src_parent = str(Path(item["src_rel"]).parent).replace("/", "\\").strip(".\\")
                cur_loc = src_parent.replace("\\", " › ") if src_parent else "Downloads (top-level)"
                dst_loc = str(Path(item["dest_rel"]).parent).replace("/", "\\").replace("\\", " › ")
                self.table_preview.setItem(r_idx, 0, QTableWidgetItem(fname))
                self.table_preview.setItem(r_idx, 1, QTableWidgetItem(cur_loc))
                self.table_preview.setItem(r_idx, 2, QTableWidgetItem(dst_loc))
        else:
            self.lbl_step3_moves_headline.setText("0 files need moving")
            self.lbl_step3_moves_headline.setStyleSheet("color: #30D158; font-size: 16px; font-weight: 700;")
            self.lbl_step3_moves_sub.setText("All existing Downloads files stay where they are")

            self.step3_zero_moves_card.setVisible(True)
            self.chk_shift_files.blockSignals(True)
            self.chk_shift_files.setChecked(False)
            self.chk_shift_files.blockSignals(False)
            self.chk_shift_files.setEnabled(False)
            self.chk_shift_files.setText(
                "Also organize 0 matching existing file(s) into these folders right now (100% Undoable)"
            )
            self.table_preview.setRowCount(0)
            self.table_preview.setVisible(False)

        self._sync_footer_summary()
        name_controls_for_screen_readers(self._page_step3)

    # ------------------------------------------------------------------
    # Navigation & Validation
    # ------------------------------------------------------------------
    def _ensure_step2_complete_or_guide(self) -> bool:
        """Ensure every folder has a valid unique Windows name and sorting cues (or Manual); guide if not."""
        self._flush_pending_chip_inputs()
        incomplete = self._incomplete_nodes()
        if not incomplete:
            return True

        # Prioritize name/duplicate errors first, keeping focus on the current folder if it is one of them
        cur_sel = self._selected_node()
        name_offenders = [n for n in incomplete if self._folder_name_error(n)]
        if name_offenders:
            first_missing = cur_sel if (cur_sel in name_offenders and cur_sel is not None) else name_offenders[0]
        else:
            first_missing = cur_sel if (cur_sel in incomplete and cur_sel is not None) else incomplete[0]
        name_err = self._folder_name_error(first_missing)

        if self.stack.currentIndex() != 1:
            self.stack.setCurrentIndex(1)
            self._sync_step_pills(1)
        self._rebuild_checklist(keep_node_id=first_missing.id)
        self.lbl_guidance_icon.setText("!")
        self.lbl_guidance_icon.setStyleSheet(
            "color: #FF9F0A; font-weight: 800; font-size: 13px; border: none; background: transparent;"
        )
        if name_err:
            self.lbl_guidance_text.setText(name_err)
            self.edit_folder_name.setFocus()
            self.edit_folder_name.selectAll()
        else:
            self.lbl_guidance_text.setText(
                f"Almost ready — please add at least one file type, filename word, or website for '{first_missing.name}', "
                "or mark it as a Manual folder before continuing."
            )
        self.guidance_frame.setVisible(True)
        self._sync_footer_buttons(1)
        return False

    def _on_step_pill_clicked(self, target_step: int) -> None:
        self._go_to_step(target_step)

    def _go_to_step(self, step_index: int) -> bool:
        step_index = max(0, min(2, int(step_index)))
        if step_index == 1:
            self._rebuild_checklist(keep_node_id=self._selected_node_id)
        elif step_index == 2:
            self._rebuild_checklist(keep_node_id=self._selected_node_id)
            if not self._ensure_step2_complete_or_guide():
                return False
            self._refresh_step3()

        self.stack.setCurrentIndex(step_index)
        self._sync_step_pills(step_index)
        self._sync_footer_buttons(step_index)
        return True

    def _sync_step_pills(self, active_step: int) -> None:
        for idx, btn in enumerate(self._step_buttons):
            btn.setProperty("active", "true" if idx == active_step else "false")
            btn.setProperty("done", "true" if idx < active_step else "false")
            refresh_style(btn)

    def _sync_footer_buttons(self, step_index: int) -> None:
        self.btn_back.setVisible(step_index > 0)
        self.btn_next.setVisible(step_index < 2)
        self.btn_apply.setVisible(step_index == 2)
        if step_index == 0:
            self.btn_next.setText("Next: Customize Folders →")
        elif step_index == 1:
            self.btn_next.setText(_esc_amp("Next: Review & Apply →"))
        self._sync_footer_summary()

    def _sync_footer_summary(self) -> None:
        self.lbl_footer_hint.setText(self.summary_text())

    def _on_back_clicked(self) -> None:
        cur = self.stack.currentIndex()
        if cur > 0:
            self._go_to_step(cur - 1)

    def _on_next_clicked(self) -> None:
        cur = self.stack.currentIndex()
        if cur == 0:
            self._go_to_step(1)
        elif cur == 1:
            self._go_to_step(2)

    def _on_apply_clicked(self) -> None:
        if not self._ensure_step2_complete_or_guide():
            return
        self.accept()

    # ------------------------------------------------------------------
    # Public API for SortingFlowTab & MainWindow (@flow-ux)
    # ------------------------------------------------------------------
    def target_architecture(self) -> FolderArchitecture:
        """Return a clean copy of the configured ``FolderArchitecture``."""
        self._flush_pending_chip_inputs()
        return self._arch.clone()

    def target_config(self) -> Config:
        """Compile the Studio's folder layout into a validated ``Config`` preserving advanced rules."""
        arch = self.target_architecture()
        base_cfg = copy.deepcopy(self.service.config)

        manual_paths = {
            arch.node_relative_path(nid).replace("/", "\\").strip("\\").lower()
            for nid in self._manual_node_ids
            if arch.get_node(nid) is not None
        }
        overridden_paths = self._edited_paths | self._removed_paths | manual_paths

        if self._selected_mode != "customize":
            # Starting from a fresh blueprint or disk scan: drop simple starter/template folder rules
            # whose folders were replaced, while keeping any advanced regex/rename/content user rules.
            surviving_paths = {p.replace("/", "\\").strip("\\").lower() for p in arch.all_target_folders()}
            kept_rules: list[Rule] = []
            for rule in base_cfg.rules:
                dest_low = rule.destination.replace("/", "\\").strip("\\").lower()
                if dest_low in manual_paths and self._is_simple_folder_rule(rule):
                    continue
                if dest_low in overridden_paths and self._is_simple_folder_rule(rule):
                    continue
                if dest_low not in surviving_paths and self._is_simple_folder_rule(rule):
                    continue
                kept_rules.append(rule)
            base_cfg.rules = kept_rules
        else:
            # Customizing current setup: drop simple rules on folders edited/removed/marked Manual in Step 2
            # so apply_architecture_to_config regenerates clean updated rules without ghost keywords.
            base_cfg.rules = [
                r
                for r in base_cfg.rules
                if not (
                    r.destination.replace("/", "\\").strip("\\").lower() in overridden_paths
                    and self._is_simple_folder_rule(r)
                )
            ]

        return apply_architecture_to_config(arch, base_cfg)

    def should_shift_files(self) -> bool:
        """Return True when the user opted in to organizing matching existing files right now."""
        return bool(self.chk_shift_files.isChecked())

    def summary_text(self) -> str:
        """Return a concise summary of the configured folder setup."""
        ordered = self._ordered_nodes_with_depth()
        top_cnt = sum(1 for _n, d in ordered if d == 0)
        sub_cnt = len(ordered) - top_cnt
        base = f"{len(ordered)} folder{'s' if len(ordered) != 1 else ''} ({top_cnt} top-level, {sub_cnt} subfolder{'s' if sub_cnt != 1 else ''})"
        moves = self._last_preview_plan.get("moves_planned", [])
        if self.should_shift_files() and moves:
            return f"{base} · Organizing {len(moves)} existing file{'s' if len(moves) != 1 else ''}"
        return f"{base} · Ready for new downloads"
