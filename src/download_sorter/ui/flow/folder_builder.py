"""Guided 3-section Custom Folder & Rules composer for Download Sorter v4."""

from __future__ import annotations

from collections import Counter
import copy
import os
from pathlib import Path
import re
from typing import Any

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from download_sorter.architecture import (
    KIND_COLORS,
    ArchNode,
    FolderArchitecture,
    _clean_exts,
    _clean_strs,
    _guess_kind,
    apply_architecture_to_config,
    get_tier2_suggestions,
)
from download_sorter.config import DEFAULT_IGNORE_EXTENSIONS, DEFAULT_IGNORE_NAMES, Config, Rule
from download_sorter.pipeline import infer_teach_cues
from download_sorter.router import (
    FileCandidate,
    _evaluate_rule,
    _normalize_tokens,
    extract_host,
    read_zone_url,
    sniff_magic_extension,
)
from download_sorter.ui.accessibility import name_controls_for_screen_readers
from download_sorter.ui.theme import STYLESHEET

_INVALID_WIN_CHARS = set('<>:"/\\|?*')
_RESERVED_DOS_NAMES: frozenset[str] = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)

_MULTI_PART_TLDS: frozenset[str] = frozenset(
    {
        "co.uk",
        "org.uk",
        "ac.uk",
        "gov.uk",
        "com.au",
        "edu.au",
        "org.au",
        "net.au",
        "co.jp",
        "ne.jp",
        "ac.jp",
        "co.nz",
        "com.br",
        "co.in",
        "co.za",
        "com.mx",
        "com.sg",
        "com.hk",
        "com.tw",
        "co.kr",
    }
)

# Multi-product root domains where the service subdomain itself is meaningful.
_KEEP_SUBDOMAIN_ROOTS: frozenset[str] = frozenset(
    {
        "google.com",
        "live.com",
        "githubusercontent.com",
    }
)

_STOPWORD_SUGGESTIONS: frozenset[str] = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "into",
        "files",
        "file",
        "folder",
        "folders",
        "downloads",
        "download",
        "new",
        "copy",
        "final",
        "draft",
        "misc",
        "other",
        "general",
        "personal",
        "my",
        "all",
        "stuff",
        "items",
        "docs",
        "documents",
    }
)

FILE_TYPE_PILL_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (".pdf", (".pdf",)),
    (".docx", (".docx",)),
    (".xlsx/.csv", (".xlsx", ".csv")),
    (".pptx", (".pptx",)),
    (".png/.jpg", (".png", ".jpg")),
    (".zip", (".zip",)),
    (".3mf/.stl", (".3mf", ".stl")),
    (".step/.f3d", (".step", ".f3d")),
    (".mp4", (".mp4",)),
    (".mp3", (".mp3",)),
    (".exe/.msi", (".exe", ".msi")),
    (".py/.js", (".py", ".js")),
)

UNIVERSAL_QUICK_PRESETS: tuple[dict[str, Any], ...] = (
    {
        "name": "Bills & Receipts",
        "kind": "finance",
        "extensions": [".pdf", ".csv", ".xlsx"],
        "keywords": ["invoice", "receipt", "statement", "bill", "tax", "w2", "1099"],
        "exclude_keywords": ["personal statement", "statement of purpose"],
        "domains": ["stripe.com", "paypal.com", "intuit.com", "turbotax.com"],
        "summary": "Invoices, receipts, statements & tax documents",
    },
    {
        "name": "School & Coursework",
        "kind": "school",
        "extensions": [".pdf", ".docx", ".pptx", ".xlsx", ".py", ".zip"],
        "keywords": ["syllabus", "homework", "lecture", "assignment", "lab_report", "exam", "study_guide"],
        "exclude_keywords": [],
        "domains": ["instructure.com", "blackboard.com", "brightspace.com", "gradescope.com", "classroom.google.com"],
        "summary": "Syllabi, lectures, homework & LMS downloads",
    },
    {
        "name": "Resumes & Career",
        "kind": "career",
        "extensions": [".pdf", ".docx"],
        "keywords": ["resume", "cv", "cover_letter", "offer_letter", "job_application"],
        "exclude_keywords": [],
        "domains": ["linkedin.com", "indeed.com", "greenhouse.io", "lever.co"],
        "summary": "Resumes, CVs, cover letters & job offers",
    },
    {
        "name": "Screenshots",
        "kind": "image",
        "extensions": [".png", ".jpg", ".webp"],
        "keywords": ["screenshot", "screen_shot", "snip", "capture", "cleanshot", "sharex"],
        "exclude_keywords": ["logo", "icon", "wallpaper"],
        "domains": [],
        "summary": "Screen captures, snips & app screenshots",
    },
    {
        "name": "Contracts & Legal",
        "kind": "doc",
        "extensions": [".pdf", ".docx"],
        "keywords": ["contract", "agreement", "nda", "lease", "signed"],
        "exclude_keywords": [],
        "domains": ["docusign.com", "hellosign.com"],
        "summary": "Signed contracts, NDAs, leases & agreements",
    },
    {
        "name": "3D Print Projects",
        "kind": "print",
        "extensions": [".3mf", ".stl", ".step", ".gcode"],
        "keywords": ["plate", "bambu", "bracket", "mount", "gridfinity"],
        "exclude_keywords": [],
        "domains": ["makerworld.com", "printables.com", "thingiverse.com"],
        "summary": "3MF plates, STL meshes & printable models",
    },
    {
        "name": "Manuals & Guides",
        "kind": "doc",
        "extensions": [".pdf", ".epub", ".txt"],
        "keywords": ["manual", "datasheet", "guide", "handbook", "spec"],
        "exclude_keywords": [],
        "domains": [],
        "summary": "Product manuals, datasheets & reference guides",
    },
)

_NAME_CONCEPT_HINTS: tuple[tuple[tuple[str, ...], dict[str, list[str]]], ...] = (
    (
        ("receipt", "receipts", "bill", "bills", "invoice", "invoices", "finance", "payment", "accounting"),
        {
            "extensions": [".pdf", ".csv", ".xlsx"],
            "keywords": ["receipt", "invoice", "statement", "bill", "payment"],
            "domains": ["stripe.com", "paypal.com", "amazon.com"],
        },
    ),
    (
        ("tax", "taxes", "irs", "w2", "1099"),
        {
            "extensions": [".pdf", ".csv", ".xlsx"],
            "keywords": ["tax", "w2", "1099", "irs", "return"],
            "domains": ["irs.gov", "turbotax.com", "intuit.com"],
        },
    ),
    (
        ("school", "course", "courses", "coursework", "class", "university", "college", "study", "lecture", "homework", "syllabi", "syllabus"),
        {
            "extensions": [".pdf", ".docx", ".pptx", ".zip"],
            "keywords": ["syllabus", "homework", "lecture", "assignment", "exam", "lab_report"],
            "domains": ["instructure.com", "classroom.google.com", "gradescope.com", "blackboard.com"],
        },
    ),
    (
        ("lab", "labs", "experiment", "research"),
        {
            "extensions": [".pdf", ".docx", ".xlsx", ".csv"],
            "keywords": ["lab", "lab_report", "experiment", "data"],
            "domains": ["gradescope.com", "instructure.com"],
        },
    ),
    (
        ("resume", "resumes", "career", "job", "jobs", "hiring", "interview", "cv"),
        {
            "extensions": [".pdf", ".docx"],
            "keywords": ["resume", "cv", "cover_letter", "offer_letter"],
            "domains": ["linkedin.com", "indeed.com", "greenhouse.io"],
        },
    ),
    (
        ("contract", "contracts", "legal", "nda", "lease", "agreements", "agreement"),
        {
            "extensions": [".pdf", ".docx"],
            "keywords": ["contract", "agreement", "nda", "lease", "signed"],
            "domains": ["docusign.com", "hellosign.com"],
        },
    ),
    (
        ("screenshot", "screenshots", "screen", "captures", "snips"),
        {
            "extensions": [".png", ".jpg", ".webp"],
            "keywords": ["screenshot", "snip", "capture", "cleanshot"],
            "domains": [],
        },
    ),
    (
        ("wallpaper", "wallpapers", "backgrounds", "art", "posters"),
        {
            "extensions": [".png", ".jpg", ".webp"],
            "keywords": ["wallpaper", "background", "desktop", "4k", "poster"],
            "domains": ["unsplash.com", "wallhaven.cc"],
        },
    ),
    (
        ("3d", "print", "prints", "printing", "bambu", "maker", "stl", "3mf", "models"),
        {
            "extensions": [".3mf", ".stl", ".step"],
            "keywords": ["plate", "bambu", "bracket", "mount", "gridfinity"],
            "domains": ["makerworld.com", "printables.com", "thingiverse.com"],
        },
    ),
    (
        ("cad", "fusion", "solidworks", "mechanical", "enclosure", "step"),
        {
            "extensions": [".step", ".f3d", ".dwg", ".dxf"],
            "keywords": ["bracket", "enclosure", "assembly", "housing", "drawing"],
            "domains": ["grabcad.com", "mcmaster.com"],
        },
    ),
    (
        ("manual", "manuals", "guide", "guides", "datasheet", "datasheets", "handbook"),
        {
            "extensions": [".pdf", ".epub", ".txt"],
            "keywords": ["manual", "guide", "datasheet", "handbook", "spec"],
            "domains": [],
        },
    ),
    (
        ("design", "brand", "logos", "logo", "icons", "figma", "mockups"),
        {
            "extensions": [".svg", ".png", ".pdf", ".fig"],
            "keywords": ["logo", "icon", "mockup", "wireframe", "brand"],
            "domains": ["figma.com", "canva.com", "dribbble.com"],
        },
    ),
)

_COMMON_DOMAINS_FALLBACK: tuple[str, ...] = (
    "stripe.com",
    "paypal.com",
    "instructure.com",
    "classroom.google.com",
    "linkedin.com",
    "docusign.com",
    "makerworld.com",
    "printables.com",
    "github.com",
)


def _esc_amp(text: str) -> str:
    """Escape '&' as '&&' for QPushButton labels so Qt never swallows '&' as a mnemonic."""
    return str(text or "").replace("&", "&&")


def normalize_ext_token(raw: str) -> str:
    """Normalize a user-entered extension token so ``pdf`` or ``*.PDF`` becomes ``.pdf``."""
    cleaned = str(raw or "").strip().lower().lstrip("*")
    cleaned = cleaned.strip().lstrip(".")
    cleaned = re.sub(r"[^a-z0-9_+-]", "", cleaned)
    if not cleaned:
        return ""
    return f".{cleaned}"


def normalize_ext_tokens(raw_or_list: str | list[str] | tuple[str, ...]) -> list[str]:
    """Parse and normalize comma/space/slash-separated extension tokens into deduplicated ``.ext`` strings."""
    if isinstance(raw_or_list, str):
        parts = re.split(r"[,;\s/]+", raw_or_list)
    else:
        parts = []
        for item in raw_or_list:
            parts.extend(re.split(r"[,;\s/]+", str(item or "")))
    out: list[str] = []
    for part in parts:
        norm = normalize_ext_token(part)
        if norm and norm not in out:
            out.append(norm)
    return out


def normalize_domain_token(raw: str) -> str:
    """Normalize a full URL or domain (e.g. ``https://dashboard.stripe.com/pay`` -> ``stripe.com``)."""
    text = str(raw or "").strip().strip("'\"")
    if not text:
        return ""
    host = extract_host(text)
    if not host or "." not in host:
        return ""
    parts = [p for p in host.split(".") if p]
    if len(parts) < 2 or any(not re.match(r"^[a-z0-9-]+$", p) for p in parts):
        return ""
    if len(parts) == 2:
        return host
    tail2 = ".".join(parts[-2:])
    if tail2 in _MULTI_PART_TLDS:
        return ".".join(parts[-3:]) if len(parts) >= 3 else host
    if tail2 in _KEEP_SUBDOMAIN_ROOTS:
        return ".".join(parts[-3:])
    return tail2


def normalize_domain_tokens(raw_or_list: str | list[str] | tuple[str, ...]) -> list[str]:
    """Parse and normalize comma/space-separated URLs or domain tokens."""
    if isinstance(raw_or_list, str):
        parts = re.split(r"[,;\s]+", raw_or_list)
    else:
        parts = []
        for item in raw_or_list:
            parts.extend(re.split(r"[,;\s]+", str(item or "")))
    out: list[str] = []
    for part in parts:
        norm = normalize_domain_token(part)
        if norm and norm not in out:
            out.append(norm)
    return out


def normalize_keyword_tokens(raw_or_list: str | list[str] | tuple[str, ...]) -> list[str]:
    """Parse comma/semicolon-separated filename words while keeping multi-word phrases intact."""
    if isinstance(raw_or_list, str):
        parts = re.split(r"[,;]+", raw_or_list)
    else:
        parts = [str(item or "") for item in raw_or_list]
    out: list[str] = []
    seen: set[str] = set()
    for part in parts:
        cleaned = re.sub(r"\s+", " ", part.strip().strip("'\""))
        if not cleaned:
            continue
        low = cleaned.lower()
        if low not in seen:
            seen.add(low)
            out.append(cleaned)
    return out


def validate_folder_name(
    raw_name: str,
    *,
    sibling_names_lower: set[str] | None = None,
) -> tuple[bool, str]:
    """Validate a Windows folder name against empty, illegal characters, DOS reserved names, and duplicates."""
    if any(ch in _INVALID_WIN_CHARS or ord(ch) < 32 for ch in raw_name):
        return False, 'Folder names on Windows cannot contain < > : " / \\ | ? *'
    stripped = raw_name.strip()
    if not stripped:
        return False, "Enter a folder name to continue."
    cleaned = stripped.rstrip(". ").strip()
    if not cleaned or cleaned in {".", ".."}:
        return False, "Folder name cannot consist only of dots or spaces."
    stem_upper = cleaned.split(".", 1)[0].strip().upper()
    if stem_upper in _RESERVED_DOS_NAMES:
        return False, f"'{cleaned}' is a reserved Windows system name — please choose another name."
    if sibling_names_lower and cleaned.lower() in sibling_names_lower:
        return False, f"A folder named '{cleaned}' already exists in this location."
    return True, ""


def _human_or_list(items: list[str], *, limit: int = 4, overflow_noun: str = "more") -> str:
    if not items:
        return ""
    shown = items[:limit]
    extra = len(items) - len(shown)
    if extra > 0:
        shown = [*shown, f"{extra} {overflow_noun}"]
    if len(shown) == 1:
        return shown[0]
    if len(shown) == 2:
        return f"{shown[0]} or {shown[1]}"
    return f"{', '.join(shown[:-1])}, or {shown[-1]}"


def build_plain_english_summary(
    *,
    rel_path: str,
    extensions: list[str],
    keywords: list[str],
    domains: list[str],
    exclude_keywords: list[str] | None = None,
    manual_only: bool = False,
) -> str:
    """Translate exact router AND/OR semantics into one clear plain-English sentence."""
    clean_rel = (rel_path or "").replace("/", "\\").strip("\\")
    dest_display = f"Downloads › {clean_rel.replace(chr(92), ' › ')}" if clean_rel else "Downloads › Your Folder"

    if manual_only:
        return (
            f"Creates {dest_display} as a manual folder — Download Sorter won't move files "
            "here automatically unless you drag them in."
        )

    exts = _clean_exts(extensions)
    kws = _clean_strs(keywords)
    doms = normalize_domain_tokens(domains)
    ex_kws = _clean_strs(exclude_keywords or [])

    if not exts and not kws and not doms:
        return (
            f"Pick at least one file type, filename word, or website above to see how files "
            f"will move into {dest_display}."
        )

    if exts:
        ext_phrase = f"{_human_or_list(exts, limit=4, overflow_noun='other types')} files"
    else:
        ext_phrase = "any files"

    kw_clause = (
        f"contain {_human_or_list([repr(k) for k in kws], limit=4)}"
        if kws
        else ""
    )
    dom_clause = (
        f"come from {_human_or_list(doms, limit=3)}"
        if doms
        else ""
    )

    if kw_clause and dom_clause:
        # Exact router truth: extensions AND (keywords OR domains)
        cond_clause = f" that {kw_clause}, or {dom_clause}"
    elif kw_clause:
        cond_clause = f" that {kw_clause}"
    elif dom_clause:
        cond_clause = f" that {dom_clause}"
    else:
        cond_clause = ""

    excl_clause = ""
    if ex_kws:
        excl_clause = f" (skipping files with {_human_or_list([repr(x) for x in ex_kws], limit=3)})"

    return f"Moves {ext_phrase} into {dest_display}{cond_clause}{excl_clause}."


def _singularize_simple(word: str) -> str:
    if len(word) >= 5 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) >= 5 and word.endswith(("ses", "xes", "zes", "ches", "shes")):
        return word[:-2]
    if len(word) >= 4 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def infer_ghost_suggestions_from_name(folder_name: str) -> dict[str, list[str]]:
    """Infer smart ghost extensions, filename words, and domains from a typed folder name."""
    norm = _normalize_tokens(folder_name or "")
    tokens = [t for t in norm.split() if t]
    exts: list[str] = []
    kws: list[str] = []
    doms: list[str] = []

    token_set = set(tokens) | {_singularize_simple(t) for t in tokens}
    for triggers, pack in _NAME_CONCEPT_HINTS:
        if token_set & set(triggers):
            for e in pack.get("extensions", ()):
                if e not in exts:
                    exts.append(e)
            for k in pack.get("keywords", ()):
                if k not in kws:
                    kws.append(k)
            for d in pack.get("domains", ()):
                if d not in doms:
                    doms.append(d)

    for tok in tokens:
        if len(tok) < 3 or tok in _STOPWORD_SUGGESTIONS:
            continue
        # If the user typed an extension name directly (e.g. "EPUB Books" or "CSV Exports")
        cand_ext = f".{tok}"
        for _lbl, grp_exts in FILE_TYPE_PILL_GROUPS:
            if cand_ext in grp_exts and cand_ext not in exts:
                exts.append(cand_ext)
        singular = _singularize_simple(tok)
        if len(singular) >= 3 and singular not in _STOPWORD_SUGGESTIONS and singular not in kws:
            kws.insert(0, singular)

    return {
        "extensions": exts[:4],
        "keywords": kws[:6],
        "domains": doms[:4],
    }


class _FlowLayout(QLayout):
    """Wrapping horizontal layout that preserves every child widget's exact sizeHint."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        h_spacing: int = 6,
        v_spacing: int = 6,
    ) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._h_spacing = h_spacing
        self._v_spacing = v_spacing

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
        return self._do_layout(QRect(0, 0, max(120, width), 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:  # noqa: N802
        eff_w = self.geometry().width()
        if eff_w <= 60:
            eff_w = 680
        h = self._do_layout(QRect(0, 0, eff_w, 0), test_only=True)
        return QSize(min(eff_w, 480), max(24, h))

    def minimumSize(self) -> QSize:  # noqa: N802
        max_w = 0
        max_h = 0
        for item in self._items:
            wid = item.widget()
            if wid is not None and wid.isHidden():
                continue
            s = item.sizeHint()
            max_w = max(max_w, s.width())
            max_h = max(max_h, s.height())
        m = self.contentsMargins()
        return QSize(max_w + m.left() + m.right(), max_h + m.top() + m.bottom())

    def _do_layout(self, rect: QRect, *, test_only: bool) -> int:
        m = self.contentsMargins()
        eff = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x = eff.x()
        y = eff.y()
        line_h = 0
        right_edge = eff.right()
        any_visible = False

        for item in self._items:
            wid = item.widget()
            if wid is not None and wid.isHidden():
                continue
            any_visible = True
            hint = item.sizeHint()
            w = hint.width()
            h = hint.height()
            if x > eff.x() and (x + w - 1 > right_edge):
                x = eff.x()
                y += line_h + self._v_spacing
                line_h = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += w + self._h_spacing
            line_h = max(line_h, h)

        if not any_visible:
            return m.top() + m.bottom()
        return (y + line_h - rect.y()) + m.bottom()


class _ChipFlowContainer(QWidget):
    """Height-for-width container wrapping a _FlowLayout cleanly inside vertical cards."""

    def __init__(self, *, h_spacing: int = 6, v_spacing: int = 6, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.flow = _FlowLayout(self, h_spacing=h_spacing, v_spacing=v_spacing)
        self.flow.setContentsMargins(0, 0, 0, 0)
        sp = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)

    def clear_chips(self) -> None:
        while self.flow.count():
            item = self.flow.takeAt(0)
            if item is not None:
                w = item.widget()
                if w is not None:
                    w.setParent(None)
                    w.deleteLater()
        self.flow.invalidate()
        self.updateGeometry()

    def add_chip(self, widget: QWidget) -> None:
        self.flow.addWidget(widget)
        self.flow.invalidate()
        self.updateGeometry()


class _CommaCommitLineEdit(QLineEdit):
    """QLineEdit that emits comma_typed whenever the user types a comma or semicolon."""

    comma_typed = Signal()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.text() in (",", ";"):
            if self.text().strip():
                self.comma_typed.emit()
                event.accept()
                return
        super().keyPressEvent(event)


_DIALOG_EXTRA_QSS = """
QDialog#GuidedFolderDialog {
    background-color: #111113;
    color: #F5F5F7;
}
QFrame#GuidedSectionCard {
    background-color: #1C1C1E;
    border: 1px solid #2C2C2E;
    border-radius: 12px;
}
QFrame#GuidedPreviewCard {
    background-color: #161618;
    border: 1px solid #2C2C2E;
    border-radius: 10px;
}
QFrame#GuidedFooterBar {
    background-color: #161618;
    border-top: 1px solid #2C2C2E;
}
QLabel#StepNumBadge {
    background-color: rgba(10, 132, 255, 0.18);
    color: #5AC8FA;
    border: 1px solid rgba(10, 132, 255, 0.42);
    border-radius: 10px;
    font-size: 11px;
    font-weight: 700;
    padding: 1px 7px;
}
QLabel#PathBreadcrumb {
    background-color: #141416;
    color: #64D2FF;
    border: 1px solid #2C2C2E;
    border-radius: 7px;
    padding: 3px 10px;
    font-size: 11.5px;
    font-weight: 600;
}
QPushButton#ChoicePillBtn {
    background-color: #161618;
    color: #AEAEB2;
    border: 1px solid #2C2C2E;
    border-radius: 8px;
    padding: 5px 12px;
    font-size: 12px;
    font-weight: 600;
}
QPushButton#ChoicePillBtn:hover {
    background-color: #242428;
    border-color: #3D3D41;
    color: #F5F5F7;
}
QPushButton#ChoicePillBtn:checked {
    background-color: rgba(10, 132, 255, 0.20);
    border: 1px solid #0A84FF;
    color: #FFFFFF;
}
QPushButton#ChoicePillBtn:disabled {
    background-color: #141416;
    color: #48484A;
    border-color: #222225;
}
QPushButton#PresetChipBtn {
    background-color: #161618;
    color: #D1D1D6;
    border: 1px solid #2F2F33;
    border-radius: 7px;
    padding: 4px 10px;
    font-size: 11.5px;
    font-weight: 500;
}
QPushButton#PresetChipBtn:hover {
    background-color: rgba(10, 132, 255, 0.14);
    border-color: #0A84FF;
    color: #FFFFFF;
}
QPushButton#PresetChipBtn:checked {
    background-color: rgba(10, 132, 255, 0.22);
    border: 1px solid #0A84FF;
    color: #FFFFFF;
    font-weight: 600;
}
QPushButton#TypeTogglePill {
    background-color: #161618;
    color: #AEAEB2;
    border: 1px solid #2C2C2E;
    border-radius: 7px;
    padding: 4px 10px;
    font-size: 11.5px;
    font-weight: 600;
}
QPushButton#TypeTogglePill:hover {
    background-color: #242428;
    border-color: #3D3D41;
    color: #F5F5F7;
}
QPushButton#TypeTogglePill:checked {
    background-color: rgba(10, 132, 255, 0.22);
    border: 1px solid #0A84FF;
    color: #FFFFFF;
}
QPushButton#ActiveChipBtn {
    background-color: rgba(10, 132, 255, 0.16);
    color: #F5F5F7;
    border: 1px solid rgba(10, 132, 255, 0.50);
    border-radius: 7px;
    padding: 3px 9px;
    font-size: 11.5px;
    font-weight: 600;
}
QPushButton#ActiveChipBtn:hover {
    background-color: rgba(255, 69, 58, 0.22);
    border-color: #FF453A;
    color: #FFFFFF;
}
QPushButton#ExcludeChipBtn {
    background-color: rgba(255, 159, 10, 0.16);
    color: #FFD60A;
    border: 1px solid rgba(255, 159, 10, 0.45);
    border-radius: 7px;
    padding: 3px 9px;
    font-size: 11.5px;
    font-weight: 600;
}
QPushButton#ExcludeChipBtn:hover {
    background-color: rgba(255, 69, 58, 0.22);
    border-color: #FF453A;
    color: #FFFFFF;
}
QPushButton#GhostChipBtn {
    background-color: rgba(10, 132, 255, 0.06);
    color: #64D2FF;
    border: 1px dashed rgba(10, 132, 255, 0.45);
    border-radius: 7px;
    padding: 3px 9px;
    font-size: 11px;
    font-weight: 500;
}
QPushButton#GhostChipBtn:hover {
    background-color: rgba(10, 132, 255, 0.18);
    border: 1px solid #0A84FF;
    color: #FFFFFF;
}
QPushButton#CompactAddBtn {
    background-color: #242428;
    color: #D1D1D6;
    border: 1px solid #38383C;
    border-radius: 7px;
    padding: 5px 11px;
    font-size: 11.5px;
    font-weight: 600;
}
QPushButton#CompactAddBtn:hover {
    background-color: #2F2F34;
    border-color: #0A84FF;
    color: #FFFFFF;
}
"""


class GuidedFolderDialog(QDialog):
    """Mistake-proof 3-section Custom Folder & Rules composer with chips and live Downloads preview."""

    def __init__(
        self,
        arch: FolderArchitecture,
        *,
        config: Config | None = None,
        watch_root: Path | None = None,
        node: ArchNode | None = None,
        default_parent_path: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._arch = arch.clone()
        self._config = copy.deepcopy(config) if config is not None else Config.new_install()
        self._watch_root = Path(watch_root) if watch_root is not None else None
        self.editing_node = node

        self._name_touched = node is not None
        self._initial_manual = bool(node is not None and node.premade_key == "__manual__")
        self._selected_kind: str = node.kind if node is not None else "default"
        self._extensions: list[str] = _clean_exts(node.extensions) if (node is not None and not self._initial_manual) else []
        self._keywords: list[str] = _clean_strs(node.keywords) if (node is not None and not self._initial_manual) else []
        self._exclude_keywords: list[str] = _clean_strs(node.exclude_keywords) if (node is not None and not self._initial_manual) else []
        self._domains: list[str] = normalize_domain_tokens(node.domains) if (node is not None and not self._initial_manual) else []
        self._active_preset_name: str = ""

        # Merge simple rules from config targeting this node so rules authored in SpinePanel appear on Edit
        if node is not None and not self._initial_manual and config is not None:
            raw_node_rel = arch.node_relative_path(node.id) or (node.name if node.tier == 1 else "")
            node_rel_low = raw_node_rel.replace("/", "\\").strip("\\").lower()
            if node_rel_low:
                for rule in config.rules:
                    if not rule.enabled:
                        continue
                    if rule.destination.replace("/", "\\").strip("\\").lower() != node_rel_low:
                        continue
                    if (
                        rule.regex_pattern
                        or rule.content_keywords
                        or rule.rename_template
                        or rule.min_size_mb > 0
                        or rule.max_size_mb > 0
                        or rule.min_age_days > 0
                    ):
                        continue
                    self._extensions = _clean_exts([*self._extensions, *rule.extensions])
                    self._keywords = _clean_strs([*self._keywords, *rule.keywords])
                    self._exclude_keywords = _clean_strs([*self._exclude_keywords, *rule.exclude_keywords])
                    self._domains = normalize_domain_tokens([*self._domains, *rule.domains])

        # Parent choice resolution (supports both existing ArchNodes and disk-only default_parent_path)
        norm_default_parent = (default_parent_path or "").replace("/", "\\").strip("\\")
        if norm_default_parent and self._find_node_by_rel_path(norm_default_parent) is None:
            self._arch.ensure_path_chain(norm_default_parent, is_custom=False)

        self._parent_options: list[tuple[str, str, ArchNode]] = self._collect_valid_parents()
        initial_parent_id: str | None = None
        initial_nested = False
        if node is not None:
            if node.tier > 1 and node.parent_id and any(pid == node.parent_id for pid, _, _ in self._parent_options):
                initial_nested = True
                initial_parent_id = node.parent_id
        elif norm_default_parent:
            matched_p = self._find_node_by_rel_path(norm_default_parent)
            if matched_p is not None and any(pid == matched_p.id for pid, _, _ in self._parent_options):
                initial_nested = True
                initial_parent_id = matched_p.id

        self._is_nested: bool = initial_nested
        self._selected_parent_id: str | None = (
            initial_parent_id
            if initial_parent_id is not None
            else (self._parent_options[0][0] if self._parent_options else None)
        )

        # Read-only loose file cache from watch_root for instant suggestions & live match preview
        self._loose_candidates: list[FileCandidate] = []
        self._watch_keywords: list[str] = []
        self._watch_domains: list[str] = []
        self._scan_loose_watch_root()
        self._matching_paths: list[Path] = []

        self.setObjectName("GuidedFolderDialog")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAcceptDrops(True)
        self.setWindowTitle("Edit Folder & Rules" if node is not None else "New Custom Folder")
        self.setMinimumSize(680, 520)
        self.resize(940, 720)
        self.setStyleSheet(STYLESHEET + "\n" + _DIALOG_EXTRA_QSS)

        self._type_pill_btns: dict[str, QPushButton] = {}
        self._parent_btns: dict[str, QPushButton] = {}
        self._quick_suggestion_btns: dict[str, QPushButton] = {}
        self._show_deep_parents: bool = bool(
            self._selected_parent_id
            and any(
                pid == self._selected_parent_id and pnode.tier >= 2
                for pid, _rel, pnode in self._parent_options
            )
        )

        self._build_ui()

        if node is not None:
            self.edit_name.setText(node.name)
        if self._initial_manual:
            self._set_manual_only(True)
        elif self._exclude_keywords:
            self._set_excludes_expanded(True)

        self._sync_location_controls()
        self._refresh_all()
        name_controls_for_screen_readers(self)

    # ------------------------------------------------------------------
    # Read-only Watch Root Scan
    # ------------------------------------------------------------------

    def _scan_loose_watch_root(self) -> None:
        if self._watch_root is None or not self._watch_root.is_dir():
            return
        ignore_exts = set(self._config.ignore_extensions or DEFAULT_IGNORE_EXTENSIONS) | {
            ".filepart",
            ".!qb",
        }
        ignore_names = {n.lower() for n in (self._config.ignore_names or DEFAULT_IGNORE_NAMES)}

        tok_counts: Counter[str] = Counter()
        dom_counts: Counter[str] = Counter()
        try:
            with os.scandir(self._watch_root) as it:
                entries = [
                    e
                    for e in it
                    if not e.name.startswith(".")
                    and not e.name.startswith("~$")
                    and e.name.lower() not in ignore_names
                    and e.is_file(follow_symlinks=False)
                ]
        except OSError:
            return

        for idx, entry in enumerate(entries[:250]):
            p = Path(entry.path)
            ext = p.suffix.lower()
            if ext in ignore_exts:
                continue
            zone_url = read_zone_url(p) if idx < 60 else ""
            cand = FileCandidate(
                filename=p.name,
                path=p,
                source_url=zone_url,
                _lazy_zone=(idx >= 60),
            )
            self._loose_candidates.append(cand)
            for tok in _normalize_tokens(p.stem).split():
                if len(tok) >= 3 and not tok.isdigit() and tok not in _STOPWORD_SUGGESTIONS:
                    tok_counts[tok] += 1
            if zone_url:
                dom = normalize_domain_token(zone_url)
                if dom:
                    dom_counts[dom] += 1

        self._watch_keywords = [tok for tok, _cnt in tok_counts.most_common(10)]
        self._watch_domains = [dom for dom, _cnt in dom_counts.most_common(6)]

    def _find_node_by_rel_path(self, rel_path: str) -> ArchNode | None:
        want = rel_path.replace("/", "\\").strip("\\").lower()
        if not want:
            return None
        for n in self._arch.nodes:
            if self._arch.node_relative_path(n.id).replace("/", "\\").strip("\\").lower() == want:
                return n
        return None

    def _collect_valid_parents(self) -> list[tuple[str, str, ArchNode]]:
        sub_depth = self._arch.subtree_depth(self.editing_node.id) if self.editing_node is not None else 0
        options: list[tuple[str, str, ArchNode]] = []
        ordered = sorted(
            self._arch.nodes,
            key=lambda n: (n.tier, self._arch.node_relative_path(n.id).lower()),
        )
        for cand in ordered:
            if not cand.enabled:
                continue
            if self.editing_node is not None:
                if cand.id == self.editing_node.id:
                    continue
                if self._arch.is_descendant_of(cand.id, self.editing_node.id):
                    continue
            if cand.tier + 1 + sub_depth > FolderArchitecture.MAX_TIERS:
                continue
            rel = self._arch.node_relative_path(cand.id)
            if rel:
                options.append((cand.id, rel, cand))
        return options

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Top header bar with Sample-File drop hint
        hdr_wrap = QWidget(self)
        hdr_lay = QHBoxLayout(hdr_wrap)
        hdr_lay.setContentsMargins(18, 10, 18, 6)
        hdr_lay.setSpacing(12)

        title_col = QVBoxLayout()
        title_col.setSpacing(1)
        lbl_title = QLabel(
            "Edit Folder & Sorting Rules" if self.editing_node is not None else "Set Up a Custom Folder",
            hdr_wrap,
        )
        lbl_title.setStyleSheet("font-size: 15.5px; font-weight: 700; color: #F5F5F7;")
        lbl_sub = QLabel(
            "Name your folder, pick what belongs inside, and check the plain-English preview below.",
            hdr_wrap,
        )
        lbl_sub.setStyleSheet("font-size: 11.5px; color: #98989D;")
        title_col.addWidget(lbl_title)
        title_col.addWidget(lbl_sub)
        hdr_lay.addLayout(title_col, 1)

        self.lbl_drop_badge = QLabel("Tip: Drop a sample file anywhere here to auto-fill cues", hdr_wrap)
        self.lbl_drop_badge.setStyleSheet(
            "background-color: rgba(10, 132, 255, 0.08); color: #64D2FF; "
            "border: 1px dashed rgba(10, 132, 255, 0.38); border-radius: 8px; "
            "padding: 4px 10px; font-size: 11px; font-weight: 500;"
        )
        hdr_lay.addWidget(self.lbl_drop_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addWidget(hdr_wrap)

        # Scrollable 3-section body
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        body = QWidget()
        body.setStyleSheet("background: transparent;")
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(18, 2, 18, 8)
        body_lay.setSpacing(8)

        body_lay.addWidget(self._build_section_1_name_and_location())
        body_lay.addWidget(self._build_section_2_cues())
        body_lay.addStretch(1)

        self.scroll.setWidget(body)
        outer.addWidget(self.scroll, 1)

        # Pinned Section 3: Live Plain-English Check & Downloads Preview (always visible!)
        sec3_wrap = QWidget(self)
        sec3_lay = QVBoxLayout(sec3_wrap)
        sec3_lay.setContentsMargins(18, 4, 18, 8)
        sec3_lay.setSpacing(0)
        sec3_lay.addWidget(self._build_section_3_preview())
        outer.addWidget(sec3_wrap)

        # Pinned footer bar
        footer = QFrame(self)
        footer.setObjectName("GuidedFooterBar")
        foot_lay = QHBoxLayout(footer)
        foot_lay.setContentsMargins(18, 9, 18, 10)
        foot_lay.setSpacing(12)

        self.lbl_completeness_hint = QLabel("", footer)
        self.lbl_completeness_hint.setWordWrap(True)
        self.lbl_completeness_hint.setStyleSheet("font-size: 12px; color: #FFB340;")
        foot_lay.addWidget(self.lbl_completeness_hint, 1)

        self.btn_cancel = QPushButton("Cancel", footer)
        self.btn_cancel.setAccessibleName("Cancel")
        self.btn_cancel.setToolTip("Close without saving folder changes")
        self.btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cancel.clicked.connect(self.reject)
        foot_lay.addWidget(self.btn_cancel)

        self.btn_save = QPushButton("Pick at least 1 rule above", footer)
        self.btn_save.setObjectName("PrimaryButton")
        self.btn_save.setAccessibleName("Create or save folder")
        self.btn_save.setToolTip("Save this custom folder and its sorting rules")
        self.btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_save.clicked.connect(self._on_save_clicked)
        foot_lay.addWidget(self.btn_save)

        outer.addWidget(footer)

    def _build_section_1_name_and_location(self) -> QFrame:
        card = QFrame(self)
        card.setObjectName("GuidedSectionCard")
        v = QVBoxLayout(card)
        v.setContentsMargins(14, 10, 14, 10)
        v.setSpacing(7)

        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        badge = QLabel("1", card)
        badge.setObjectName("StepNumBadge")
        lbl = QLabel("Folder Name & Location", card)
        lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #F5F5F7;")
        hdr.addWidget(badge)
        hdr.addWidget(lbl)
        hdr.addStretch(1)

        self.lbl_path_badge = QLabel("Downloads › Your Folder", card)
        self.lbl_path_badge.setObjectName("PathBreadcrumb")
        self.lbl_path_badge.setToolTip("Live preview of where this folder will live inside Downloads")
        hdr.addWidget(self.lbl_path_badge)
        v.addLayout(hdr)

        # Name-First input + inline validation
        name_row = QHBoxLayout()
        name_row.setSpacing(10)
        lbl_name = QLabel("Folder name:", card)
        lbl_name.setStyleSheet("color: #D1D1D6; font-size: 12px; font-weight: 600;")
        self.edit_name = QLineEdit(card)
        self.edit_name.setAccessibleName("Folder Name")
        self.edit_name.setPlaceholderText("e.g. Bills & Receipts, Lab Reports, Client Contracts...")
        self.edit_name.setToolTip("Type a clean folder name")
        self.edit_name.textEdited.connect(self._on_name_edited)
        self.edit_name.textChanged.connect(self._on_name_changed)
        lbl_name.setBuddy(self.edit_name)
        name_row.addWidget(lbl_name)
        name_row.addWidget(self.edit_name, 1)
        v.addLayout(name_row)

        self.lbl_name_error = QLabel("", card)
        self.lbl_name_error.setStyleSheet("color: #FF453A; font-size: 11.5px; font-weight: 500;")
        self.lbl_name_error.setVisible(False)
        v.addWidget(self.lbl_name_error)

        # 1-click Quick-Fill Suggestion Chips
        sugg_hdr = QLabel("Quick-fill a ready-made setup (fills name + smart file cues in 1 click):", card)
        sugg_hdr.setStyleSheet("color: #8E8E93; font-size: 11px;")
        v.addWidget(sugg_hdr)

        self.quick_sugg_wrap = _ChipFlowContainer(h_spacing=6, v_spacing=5, parent=card)
        v.addWidget(self.quick_sugg_wrap)

        # Compact Location choice pills: Top-level vs Inside an existing folder
        loc_row = QHBoxLayout()
        loc_row.setSpacing(8)
        lbl_loc = QLabel("Where should it live?", card)
        lbl_loc.setStyleSheet("color: #D1D1D6; font-size: 12px; font-weight: 600;")
        loc_row.addWidget(lbl_loc)

        self.btn_loc_root = QPushButton("Top-level in Downloads", card)
        self.btn_loc_root.setObjectName("ChoicePillBtn")
        self.btn_loc_root.setCheckable(True)
        self.btn_loc_root.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_loc_root.setToolTip("Place directly inside Downloads (e.g. Downloads › Bills & Receipts)")
        self.btn_loc_root.clicked.connect(lambda: self._set_nested_mode(False))
        loc_row.addWidget(self.btn_loc_root)

        self.btn_loc_nested = QPushButton("Inside an existing folder", card)
        self.btn_loc_nested.setObjectName("ChoicePillBtn")
        self.btn_loc_nested.setCheckable(True)
        self.btn_loc_nested.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_loc_nested.setEnabled(bool(self._parent_options))
        self.btn_loc_nested.setToolTip(
            "Nest inside one of your existing folders (e.g. Downloads › Documents › Bills & Receipts)"
            if self._parent_options
            else "No parent folders available yet — create a top-level folder first"
        )
        self.btn_loc_nested.clicked.connect(lambda: self._set_nested_mode(True))
        loc_row.addWidget(self.btn_loc_nested)
        loc_row.addStretch(1)
        v.addLayout(loc_row)

        self.parent_section = QWidget(card)
        p_lay = QVBoxLayout(self.parent_section)
        p_lay.setContentsMargins(0, 1, 0, 0)
        p_lay.setSpacing(4)

        parent_hdr_row = QHBoxLayout()
        parent_hdr_row.setSpacing(8)
        lbl_parent = QLabel("Pick parent folder:", self.parent_section)
        lbl_parent.setStyleSheet("color: #8E8E93; font-size: 11px;")
        parent_hdr_row.addWidget(lbl_parent)

        deep_count = sum(1 for _pid, _rel, pnode in self._parent_options if pnode.tier >= 2)
        self.btn_toggle_deep_parents = QPushButton("", self.parent_section)
        self.btn_toggle_deep_parents.setObjectName("GhostButton")
        self.btn_toggle_deep_parents.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_toggle_deep_parents.setToolTip("Show or hide deeper nested subfolders as parent choices")
        self.btn_toggle_deep_parents.setStyleSheet(
            "QPushButton { color: #64D2FF; font-size: 11px; font-weight: 500; "
            "background: transparent; border: none; padding: 0px 4px; }"
            "QPushButton:hover { color: #FFFFFF; }"
        )
        self.btn_toggle_deep_parents.setVisible(deep_count > 0)
        self.btn_toggle_deep_parents.clicked.connect(self._toggle_deep_parents)
        parent_hdr_row.addWidget(self.btn_toggle_deep_parents)
        parent_hdr_row.addStretch(1)
        p_lay.addLayout(parent_hdr_row)

        self.parent_pills_wrap = _ChipFlowContainer(h_spacing=6, v_spacing=5, parent=self.parent_section)
        for pid, rel, _pnode in self._parent_options:
            pretty = rel.replace("\\", " › ")
            pbtn = QPushButton(_esc_amp(pretty), self.parent_pills_wrap)
            pbtn.setObjectName("ChoicePillBtn")
            pbtn.setCheckable(True)
            pbtn.setCursor(Qt.CursorShape.PointingHandCursor)
            pbtn.setAccessibleName(f"Parent folder {pretty}")
            pbtn.setToolTip(f"Place inside Downloads › {pretty}")
            pbtn.clicked.connect(lambda _chk, target_id=pid: self._select_parent_id(target_id))
            self._parent_btns[pid] = pbtn
            self.parent_pills_wrap.add_chip(pbtn)
        p_lay.addWidget(self.parent_pills_wrap)
        v.addWidget(self.parent_section)

        return card

    def _build_section_2_cues(self) -> QFrame:
        card = QFrame(self)
        card.setObjectName("GuidedSectionCard")
        v = QVBoxLayout(card)
        v.setContentsMargins(14, 10, 14, 10)
        v.setSpacing(7)

        # Header + 2-way Intent Switch (Sort automatically | Manual folder)
        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        badge = QLabel("2", card)
        badge.setObjectName("StepNumBadge")
        lbl = QLabel("What Files Belong Here?", card)
        lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #F5F5F7;")
        hdr.addWidget(badge)
        hdr.addWidget(lbl)
        hdr.addStretch(1)

        self.btn_mode_auto = QPushButton("Sort automatically", card)
        self.btn_mode_auto.setObjectName("ChoicePillBtn")
        self.btn_mode_auto.setCheckable(True)
        self.btn_mode_auto.setChecked(True)
        self.btn_mode_auto.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_mode_auto.setToolTip("Automatically move matching downloads into this folder")
        self.btn_mode_auto.clicked.connect(lambda: self._set_manual_only(False))
        hdr.addWidget(self.btn_mode_auto)

        self.btn_mode_manual = QPushButton("Manual folder — I'll move files myself", card)
        self.btn_mode_manual.setObjectName("ChoicePillBtn")
        self.btn_mode_manual.setCheckable(True)
        self.btn_mode_manual.setChecked(False)
        self.btn_mode_manual.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_mode_manual.setToolTip("Keep as a manual folder with no automatic sorting rules")
        self.btn_mode_manual.clicked.connect(lambda: self._set_manual_only(True))
        hdr.addWidget(self.btn_mode_manual)
        v.addLayout(hdr)

        # Keep synchronized checkbox accessible for programmatic callers and explicit clarity
        self.chk_manual_only = QCheckBox(
            "Keep as manual folder only (I'll drag or pick files myself)",
            card,
        )
        self.chk_manual_only.setToolTip(
            "Skip automatic routing cues and create an empty destination folder you manage by hand"
        )
        self.chk_manual_only.toggled.connect(self._set_manual_only)
        self.chk_manual_only.setVisible(False)
        v.addWidget(self.chk_manual_only)

        self.cues_body = QWidget(card)
        cb = QVBoxLayout(self.cues_body)
        cb.setContentsMargins(0, 0, 0, 0)
        cb.setSpacing(6)

        # --- 2A. File Types ---
        lbl_ft = QLabel("File types (optional filter — click to toggle, or leave blank for any type):", self.cues_body)
        lbl_ft.setStyleSheet("color: #D1D1D6; font-size: 12px; font-weight: 600;")
        cb.addWidget(lbl_ft)

        self.type_pills_wrap = _ChipFlowContainer(h_spacing=6, v_spacing=6, parent=self.cues_body)
        for pill_label, exts in FILE_TYPE_PILL_GROUPS:
            tbtn = QPushButton(pill_label, self.type_pills_wrap)
            tbtn.setObjectName("TypeTogglePill")
            tbtn.setCheckable(True)
            tbtn.setCursor(Qt.CursorShape.PointingHandCursor)
            tbtn.setAccessibleName(f"File type {pill_label}")
            tbtn.setToolTip(f"Toggle {', '.join(exts)} files")
            tbtn.clicked.connect(lambda checked, grp=exts: self._on_type_pill_toggled(grp, checked))
            self._type_pill_btns[pill_label] = tbtn
            self.type_pills_wrap.add_chip(tbtn)
        cb.addWidget(self.type_pills_wrap)

        self.extra_ext_chips_wrap = _ChipFlowContainer(h_spacing=6, v_spacing=6, parent=self.cues_body)
        cb.addWidget(self.extra_ext_chips_wrap)

        ext_input_row = QHBoxLayout()
        ext_input_row.setSpacing(8)
        self.edit_custom_ext = _CommaCommitLineEdit(self.cues_body)
        self.edit_custom_ext.setAccessibleName("Custom file extension")
        self.edit_custom_ext.setPlaceholderText("Add custom extension (e.g. epub, 7z — dot added automatically)...")
        self.edit_custom_ext.setToolTip("Type any extension like pdf or epub and press Enter")
        self.edit_custom_ext.returnPressed.connect(self._commit_custom_ext_input)
        self.edit_custom_ext.comma_typed.connect(self._commit_custom_ext_input)
        self.edit_custom_ext.editingFinished.connect(self._commit_custom_ext_input)
        self.edit_custom_ext.textChanged.connect(lambda _t: self._refresh_preview_and_cta())
        self.btn_add_ext = QPushButton("+ Add Type", self.cues_body)
        self.btn_add_ext.setObjectName("CompactAddBtn")
        self.btn_add_ext.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_add_ext.setToolTip("Add the typed file extension")
        self.btn_add_ext.clicked.connect(self._commit_custom_ext_input)
        ext_input_row.addWidget(self.edit_custom_ext, 1)
        ext_input_row.addWidget(self.btn_add_ext)
        cb.addLayout(ext_input_row)

        # Divider
        div1 = QFrame(self.cues_body)
        div1.setProperty("divider", True)
        div1.setFixedHeight(1)
        div1.setStyleSheet("background-color: #262629;")
        cb.addWidget(div1)

        # --- 2B. Filename Words ---
        lbl_kw = QLabel("Filename words (matches when any of these words appear in the filename):", self.cues_body)
        lbl_kw.setStyleSheet("color: #D1D1D6; font-size: 12px; font-weight: 600;")
        cb.addWidget(lbl_kw)

        self.kw_chips_wrap = _ChipFlowContainer(h_spacing=6, v_spacing=6, parent=self.cues_body)
        cb.addWidget(self.kw_chips_wrap)

        kw_input_row = QHBoxLayout()
        kw_input_row.setSpacing(8)
        self.edit_keyword_input = _CommaCommitLineEdit(self.cues_body)
        self.edit_keyword_input.setAccessibleName("Filename word")
        self.edit_keyword_input.setPlaceholderText("Type a filename word and press Enter or comma (e.g. invoice, syllabus)...")
        self.edit_keyword_input.setToolTip("Add a word or phrase to look for in filenames")
        self.edit_keyword_input.returnPressed.connect(self._commit_keyword_input)
        self.edit_keyword_input.comma_typed.connect(self._commit_keyword_input)
        self.edit_keyword_input.editingFinished.connect(self._commit_keyword_input)
        self.edit_keyword_input.textChanged.connect(lambda _t: self._refresh_preview_and_cta())
        self.btn_add_keyword = QPushButton("+ Add Word", self.cues_body)
        self.btn_add_keyword.setObjectName("CompactAddBtn")
        self.btn_add_keyword.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_add_keyword.setToolTip("Add the typed filename word")
        self.btn_add_keyword.clicked.connect(self._commit_keyword_input)
        kw_input_row.addWidget(self.edit_keyword_input, 1)
        kw_input_row.addWidget(self.btn_add_keyword)
        cb.addLayout(kw_input_row)

        self.kw_ghost_wrap = _ChipFlowContainer(h_spacing=6, v_spacing=5, parent=self.cues_body)
        cb.addWidget(self.kw_ghost_wrap)

        # Collapsible Exclude Words
        self.btn_toggle_excludes = QPushButton("▸ Skip if filename contains (optional)", self.cues_body)
        self.btn_toggle_excludes.setObjectName("GhostButton")
        self.btn_toggle_excludes.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_toggle_excludes.setToolTip("Add negative words so matching filenames are skipped by this folder")
        self.btn_toggle_excludes.setStyleSheet(
            "QPushButton { color: #8E8E93; font-size: 11.5px; font-weight: 600; "
            "background: transparent; border: none; padding: 2px 4px; text-align: left; }"
            "QPushButton:hover { color: #F5F5F7; }"
        )
        self.btn_toggle_excludes.clicked.connect(
            lambda: self._set_excludes_expanded(not self.excludes_wrap.isVisible())
        )
        cb.addWidget(self.btn_toggle_excludes, 0, Qt.AlignmentFlag.AlignLeft)

        self.excludes_wrap = QWidget(self.cues_body)
        ex_lay = QVBoxLayout(self.excludes_wrap)
        ex_lay.setContentsMargins(10, 2, 0, 2)
        ex_lay.setSpacing(6)
        self.exclude_chips_wrap = _ChipFlowContainer(h_spacing=6, v_spacing=6, parent=self.excludes_wrap)
        ex_lay.addWidget(self.exclude_chips_wrap)

        ex_input_row = QHBoxLayout()
        ex_input_row.setSpacing(8)
        self.edit_exclude_input = _CommaCommitLineEdit(self.excludes_wrap)
        self.edit_exclude_input.setAccessibleName("Skip filename word")
        self.edit_exclude_input.setPlaceholderText("Words to skip (e.g. draft, wallpaper, personal statement)...")
        self.edit_exclude_input.setToolTip("Files containing any of these words will NOT move into this folder")
        self.edit_exclude_input.returnPressed.connect(self._commit_exclude_input)
        self.edit_exclude_input.comma_typed.connect(self._commit_exclude_input)
        self.edit_exclude_input.editingFinished.connect(self._commit_exclude_input)
        self.edit_exclude_input.textChanged.connect(lambda _t: self._refresh_preview_and_cta())
        self.btn_add_exclude = QPushButton("+ Add Skip Word", self.excludes_wrap)
        self.btn_add_exclude.setObjectName("CompactAddBtn")
        self.btn_add_exclude.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_add_exclude.setToolTip("Add word to skip")
        self.btn_add_exclude.clicked.connect(self._commit_exclude_input)
        ex_input_row.addWidget(self.edit_exclude_input, 1)
        ex_input_row.addWidget(self.btn_add_exclude)
        ex_lay.addLayout(ex_input_row)
        self.excludes_wrap.setVisible(False)
        cb.addWidget(self.excludes_wrap)

        # Divider
        div2 = QFrame(self.cues_body)
        div2.setFixedHeight(1)
        div2.setStyleSheet("background-color: #262629;")
        cb.addWidget(div2)

        # --- 2C. Website Source ---
        lbl_dom = QLabel(
            "Website source (paste any URL or site — automatically cleaned to its domain):",
            self.cues_body,
        )
        lbl_dom.setStyleSheet("color: #D1D1D6; font-size: 12px; font-weight: 600;")
        cb.addWidget(lbl_dom)

        self.domain_chips_wrap = _ChipFlowContainer(h_spacing=6, v_spacing=6, parent=self.cues_body)
        cb.addWidget(self.domain_chips_wrap)

        dom_input_row = QHBoxLayout()
        dom_input_row.setSpacing(8)
        self.edit_domain_input = _CommaCommitLineEdit(self.cues_body)
        self.edit_domain_input.setAccessibleName("Website URL or domain")
        self.edit_domain_input.setPlaceholderText(
            "Paste a link or domain (e.g. https://dashboard.stripe.com/pay → stripe.com)..."
        )
        self.edit_domain_input.setToolTip("Paste a full website URL or domain name and press Enter")
        self.edit_domain_input.returnPressed.connect(self._commit_domain_input)
        self.edit_domain_input.comma_typed.connect(self._commit_domain_input)
        self.edit_domain_input.editingFinished.connect(self._commit_domain_input)
        self.edit_domain_input.textChanged.connect(lambda _t: self._refresh_preview_and_cta())
        self.btn_add_domain = QPushButton("+ Add Site", self.cues_body)
        self.btn_add_domain.setObjectName("CompactAddBtn")
        self.btn_add_domain.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_add_domain.setToolTip("Normalize and add the website domain")
        self.btn_add_domain.clicked.connect(self._commit_domain_input)
        dom_input_row.addWidget(self.edit_domain_input, 1)
        dom_input_row.addWidget(self.btn_add_domain)
        cb.addLayout(dom_input_row)

        self.domain_ghost_wrap = _ChipFlowContainer(h_spacing=6, v_spacing=5, parent=self.cues_body)
        cb.addWidget(self.domain_ghost_wrap)

        v.addWidget(self.cues_body)

        self.lbl_manual_banner = QLabel(
            "Manual mode active — Download Sorter will create and display this folder, "
            "and leave file sorting up to your own drag-and-drop.",
            card,
        )
        self.lbl_manual_banner.setWordWrap(True)
        self.lbl_manual_banner.setStyleSheet(
            "background-color: rgba(10, 132, 255, 0.10); color: #64D2FF; "
            "border: 1px solid rgba(10, 132, 255, 0.30); border-radius: 8px; "
            "padding: 8px 12px; font-size: 12px;"
        )
        self.lbl_manual_banner.setVisible(False)
        v.addWidget(self.lbl_manual_banner)

        return card

    def _build_section_3_preview(self) -> QFrame:
        card = QFrame(self)
        card.setObjectName("GuidedSectionCard")
        v = QVBoxLayout(card)
        v.setContentsMargins(14, 10, 14, 10)
        v.setSpacing(7)

        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        badge = QLabel("3", card)
        badge.setObjectName("StepNumBadge")
        lbl = QLabel("Plain-English Check & Live Downloads Preview", card)
        lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #F5F5F7;")
        hdr.addWidget(badge)
        hdr.addWidget(lbl)
        hdr.addStretch(1)
        v.addLayout(hdr)

        sentence_box = QFrame(card)
        sentence_box.setObjectName("GuidedPreviewCard")
        sb_lay = QVBoxLayout(sentence_box)
        sb_lay.setContentsMargins(12, 8, 12, 8)
        sb_lay.setSpacing(5)

        self.lbl_plain_english = QLabel("", sentence_box)
        self.lbl_plain_english.setWordWrap(True)
        self.lbl_plain_english.setStyleSheet("font-size: 13px; font-weight: 600; color: #F5F5F7;")
        sb_lay.addWidget(self.lbl_plain_english)

        self.lbl_live_matches = QLabel("", sentence_box)
        self.lbl_live_matches.setWordWrap(True)
        self.lbl_live_matches.setStyleSheet("font-size: 12px; color: #30D158;")
        sb_lay.addWidget(self.lbl_live_matches)

        self.chk_align_now = QCheckBox(
            "Also move matching loose files in Downloads into this folder right now (Undoable)",
            sentence_box,
        )
        self.chk_align_now.setToolTip(
            "Immediately tidy the matching loose files shown above into this folder (can be undone in 1 click)"
        )
        self.chk_align_now.setVisible(False)
        self.chk_align_now.toggled.connect(lambda _c: self._update_cta_and_completeness())
        sb_lay.addWidget(self.chk_align_now)

        v.addWidget(sentence_box)
        return card

    # ------------------------------------------------------------------
    # State & Chip Handlers
    # ------------------------------------------------------------------

    def _set_excludes_expanded(self, expanded: bool) -> None:
        self.excludes_wrap.setVisible(expanded)
        self._update_excludes_toggle_label()

    def _update_excludes_toggle_label(self) -> None:
        n_ex = len(self._exclude_keywords)
        if not self.excludes_wrap.isHidden():
            self.btn_toggle_excludes.setText("▾ Skip if filename contains")
        elif n_ex > 0:
            self.btn_toggle_excludes.setText(f"▸ Skip if filename contains ({n_ex} active)")
        else:
            self.btn_toggle_excludes.setText("▸ Skip if filename contains (optional)")

    def _set_manual_only(self, manual: bool) -> None:
        is_manual = bool(manual)
        if self.chk_manual_only.isChecked() != is_manual:
            self.chk_manual_only.blockSignals(True)
            self.chk_manual_only.setChecked(is_manual)
            self.chk_manual_only.blockSignals(False)
        self.btn_mode_auto.setChecked(not is_manual)
        self.btn_mode_manual.setChecked(is_manual)
        self.cues_body.setVisible(not is_manual)
        self.lbl_manual_banner.setVisible(is_manual)
        self._refresh_preview_and_cta()

    def _set_nested_mode(self, nested: bool) -> None:
        if nested and not self._parent_options:
            nested = False
        self._is_nested = nested
        if nested and not self._selected_parent_id and self._parent_options:
            self._selected_parent_id = self._parent_options[0][0]
        self._sync_location_controls()
        self._refresh_all()

    def _select_parent_id(self, parent_id: str) -> None:
        self._is_nested = True
        self._selected_parent_id = parent_id
        self._sync_location_controls()
        self._refresh_all()

    def _toggle_deep_parents(self) -> None:
        self._show_deep_parents = not self._show_deep_parents
        self._sync_location_controls()

    def _sync_location_controls(self) -> None:
        self.btn_loc_root.setChecked(not self._is_nested)
        self.btn_loc_nested.setChecked(self._is_nested)
        self.parent_section.setVisible(self._is_nested and bool(self._parent_options))
        deep_count = sum(1 for _pid, _rel, pnode in self._parent_options if pnode.tier >= 2)
        if deep_count > 0:
            self.btn_toggle_deep_parents.setText(
                "Hide subfolders ▴" if self._show_deep_parents else f"Show {deep_count} subfolders ▾"
            )
        for pid, _rel, pnode in self._parent_options:
            pbtn = self._parent_btns.get(pid)
            if pbtn is None:
                continue
            visible = pnode.tier == 1 or self._show_deep_parents or pid == self._selected_parent_id
            pbtn.setVisible(visible)
            pbtn.setChecked(self._is_nested and pid == self._selected_parent_id)
        self.parent_pills_wrap.flow.invalidate()
        self.parent_pills_wrap.updateGeometry()

    def _on_name_edited(self, _text: str) -> None:
        self._name_touched = True

    def _on_name_changed(self, _text: str) -> None:
        if self._selected_kind == "default" or not self._active_preset_name:
            guessed = _guess_kind(self.edit_name.text().strip(), self._effective_extensions())
            if guessed != "default":
                self._selected_kind = guessed
        self._rebuild_ghost_suggestions()
        self._refresh_preview_and_cta()

    def _current_parent_node(self) -> ArchNode | None:
        if not self._is_nested or not self._selected_parent_id:
            return None
        return self._arch.get_node(self._selected_parent_id)

    def _sibling_names_lower(self) -> set[str]:
        pnode = self._current_parent_node()
        siblings = (
            self._arch.children_of(pnode.id)
            if pnode is not None
            else self._arch.nodes_in_tier(1)
        )
        own_id = self.editing_node.id if self.editing_node is not None else ""
        return {s.name.strip().lower() for s in siblings if s.id != own_id}

    def validate_name(self) -> tuple[bool, str]:
        """Return ``(is_valid, error_message)`` for the current folder name and parent location."""
        return validate_folder_name(
            self.edit_name.text(),
            sibling_names_lower=self._sibling_names_lower(),
        )

    def _available_quick_suggestions(self) -> list[dict[str, Any]]:
        siblings = self._sibling_names_lower()
        items: list[dict[str, Any]] = []
        seen_names: set[str] = set()

        preset_by_low = {str(p["name"]).strip().lower(): p for p in UNIVERSAL_QUICK_PRESETS}
        pnode = self._current_parent_node()
        if pnode is not None:
            # Prefer smart classification packs first, then file-type subfolders for this parent
            for mode in ("classification", "file_type"):
                for sug in get_tier2_suggestions(pnode.name, pnode.kind, mode=mode):
                    sname = str(sug.get("name") or "").strip()
                    low = sname.lower()
                    if sname and low not in seen_names and low not in siblings:
                        merged_sug = copy.deepcopy(sug)
                        rich = preset_by_low.get(low)
                        if rich is not None:
                            merged_sug["extensions"] = _clean_exts(
                                [*merged_sug.get("extensions", ()), *rich.get("extensions", ())]
                            )
                            merged_sug["keywords"] = _clean_strs(
                                [*merged_sug.get("keywords", ()), *rich.get("keywords", ())]
                            )
                            merged_sug["exclude_keywords"] = _clean_strs(
                                [*merged_sug.get("exclude_keywords", ()), *rich.get("exclude_keywords", ())]
                            )
                            merged_sug["domains"] = normalize_domain_tokens(
                                [*merged_sug.get("domains", ()), *rich.get("domains", ())]
                            )
                        seen_names.add(low)
                        items.append(merged_sug)

        for preset in UNIVERSAL_QUICK_PRESETS:
            pname = str(preset["name"]).strip()
            low = pname.lower()
            if low not in seen_names and low not in siblings:
                seen_names.add(low)
                items.append(copy.deepcopy(preset))

        return items[:10]

    def _rebuild_quick_suggestions(self) -> None:
        self._quick_suggestion_btns.clear()
        self.quick_sugg_wrap.clear_chips()
        for sug in self._available_quick_suggestions():
            sname = str(sug["name"])
            btn = QPushButton(_esc_amp(sname), self.quick_sugg_wrap)
            btn.setObjectName("PresetChipBtn")
            btn.setCheckable(True)
            btn.setChecked(self._active_preset_name.lower() == sname.lower())
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setAccessibleName(f"Quick-fill {sname}")
            summary = str(sug.get("summary") or f"Pre-fill '{sname}' with smart rules")
            btn.setToolTip(f"{summary} (1 click fills folder name and routing cues)")
            btn.clicked.connect(lambda _chk, payload=sug: self._apply_quick_suggestion_dict(payload))
            self._quick_suggestion_btns[sname] = btn
            self.quick_sugg_wrap.add_chip(btn)
        name_controls_for_screen_readers(self.quick_sugg_wrap)

    def apply_quick_suggestion(self, name: str) -> bool:
        """Apply a quick-fill suggestion chip by name (case-insensitive)."""
        want = name.strip().lower()
        for sug in [*self._available_quick_suggestions(), *UNIVERSAL_QUICK_PRESETS]:
            if str(sug.get("name") or "").strip().lower() == want:
                self._apply_quick_suggestion_dict(sug)
                return True
        return False

    def _apply_quick_suggestion_dict(self, sug: dict[str, Any]) -> None:
        sname = str(sug.get("name") or "").strip()
        self._active_preset_name = sname
        self._name_touched = True
        self._selected_kind = str(sug.get("kind") or _guess_kind(sname, list(sug.get("extensions") or [])))
        self._extensions = _clean_exts(list(sug.get("extensions") or []))
        self._keywords = _clean_strs(list(sug.get("keywords") or []))
        self._exclude_keywords = _clean_strs(list(sug.get("exclude_keywords") or []))
        self._domains = normalize_domain_tokens(list(sug.get("domains") or []))
        self.edit_custom_ext.clear()
        self.edit_keyword_input.clear()
        self.edit_exclude_input.clear()
        self.edit_domain_input.clear()
        self._set_manual_only(False)
        self._update_excludes_toggle_label()
        self.edit_name.setText(sname)
        self._refresh_all()

    def _on_type_pill_toggled(self, group_exts: tuple[str, ...], checked: bool) -> None:
        if checked:
            for ext in group_exts:
                if ext not in self._extensions:
                    self._extensions.append(ext)
        else:
            self._extensions = [e for e in self._extensions if e not in group_exts]
        self._sync_cue_chips()
        self._refresh_preview_and_cta()

    def _commit_custom_ext_input(self) -> None:
        raw = self.edit_custom_ext.text().strip()
        if not raw:
            return
        for ext in normalize_ext_tokens(raw):
            if ext not in self._extensions:
                self._extensions.append(ext)
        self.edit_custom_ext.clear()
        self._sync_cue_chips()
        self._refresh_preview_and_cta()

    def add_extension(self, raw_ext: str) -> str:
        """Normalize and add a file extension token (e.g. ``'pdf'`` -> ``'.pdf'``)."""
        norm = normalize_ext_token(raw_ext)
        if norm and norm not in self._extensions:
            self._extensions.append(norm)
            self._sync_cue_chips()
            self._refresh_preview_and_cta()
        return norm

    def remove_extension(self, ext: str) -> None:
        norm = normalize_ext_token(ext)
        self._extensions = [e for e in self._extensions if e != norm]
        self._sync_cue_chips()
        self._refresh_preview_and_cta()

    def _commit_keyword_input(self) -> None:
        raw = self.edit_keyword_input.text().strip()
        if not raw:
            return
        existing_low = {k.lower() for k in self._keywords}
        for kw in normalize_keyword_tokens(raw):
            if kw.lower() not in existing_low:
                self._keywords.append(kw)
                existing_low.add(kw.lower())
        self.edit_keyword_input.clear()
        self._sync_cue_chips()
        self._refresh_preview_and_cta()

    def add_keyword(self, word: str) -> None:
        for kw in normalize_keyword_tokens(word):
            if kw.lower() not in {k.lower() for k in self._keywords}:
                self._keywords.append(kw)
        self._sync_cue_chips()
        self._refresh_preview_and_cta()

    def remove_keyword(self, word: str) -> None:
        low = word.strip().lower()
        self._keywords = [k for k in self._keywords if k.lower() != low]
        self._sync_cue_chips()
        self._refresh_preview_and_cta()

    def _commit_exclude_input(self) -> None:
        raw = self.edit_exclude_input.text().strip()
        if not raw:
            return
        existing_low = {k.lower() for k in self._exclude_keywords}
        for kw in normalize_keyword_tokens(raw):
            if kw.lower() not in existing_low:
                self._exclude_keywords.append(kw)
                existing_low.add(kw.lower())
        self.edit_exclude_input.clear()
        self._sync_cue_chips()
        self._refresh_preview_and_cta()

    def add_exclude_keyword(self, word: str) -> None:
        for kw in normalize_keyword_tokens(word):
            if kw.lower() not in {k.lower() for k in self._exclude_keywords}:
                self._exclude_keywords.append(kw)
        if self._exclude_keywords:
            self._set_excludes_expanded(True)
        self._sync_cue_chips()
        self._refresh_preview_and_cta()

    def remove_exclude_keyword(self, word: str) -> None:
        low = word.strip().lower()
        self._exclude_keywords = [k for k in self._exclude_keywords if k.lower() != low]
        self._sync_cue_chips()
        self._refresh_preview_and_cta()

    def _commit_domain_input(self) -> None:
        raw = self.edit_domain_input.text().strip()
        if not raw:
            return
        for dom in normalize_domain_tokens(raw):
            if dom not in self._domains:
                self._domains.append(dom)
        self.edit_domain_input.clear()
        self._sync_cue_chips()
        self._refresh_preview_and_cta()

    def add_domain(self, raw_url_or_domain: str) -> str:
        """Normalize a pasted URL or domain and add it as a Website Source chip."""
        added = ""
        for dom in normalize_domain_tokens(raw_url_or_domain):
            if dom not in self._domains:
                self._domains.append(dom)
            added = dom
        if added:
            self._sync_cue_chips()
            self._refresh_preview_and_cta()
        return added

    def remove_domain(self, domain: str) -> None:
        norm = normalize_domain_token(domain) or domain.strip().lower()
        self._domains = [d for d in self._domains if d != norm]
        self._sync_cue_chips()
        self._refresh_preview_and_cta()

    def _flush_all_pending_inputs(self) -> None:
        self._commit_custom_ext_input()
        self._commit_keyword_input()
        self._commit_exclude_input()
        self._commit_domain_input()

    def _effective_extensions(self) -> list[str]:
        out = list(self._extensions)
        pending = self.edit_custom_ext.text().strip()
        if pending:
            for ext in normalize_ext_tokens(pending):
                if ext not in out:
                    out.append(ext)
        return out

    def _effective_keywords(self) -> list[str]:
        out = list(self._keywords)
        existing_low = {k.lower() for k in out}
        pending = self.edit_keyword_input.text().strip()
        if pending:
            for kw in normalize_keyword_tokens(pending):
                if kw.lower() not in existing_low:
                    out.append(kw)
                    existing_low.add(kw.lower())
        return out

    def _effective_exclude_keywords(self) -> list[str]:
        out = list(self._exclude_keywords)
        existing_low = {k.lower() for k in out}
        pending = self.edit_exclude_input.text().strip()
        if pending:
            for kw in normalize_keyword_tokens(pending):
                if kw.lower() not in existing_low:
                    out.append(kw)
                    existing_low.add(kw.lower())
        return out

    def _effective_domains(self) -> list[str]:
        out = list(self._domains)
        pending = self.edit_domain_input.text().strip()
        if pending:
            for dom in normalize_domain_tokens(pending):
                if dom not in out:
                    out.append(dom)
        return out

    # ------------------------------------------------------------------
    # Sample-File Drag-and-Drop Auto-Fill
    # ------------------------------------------------------------------

    def apply_sample_file(self, path: Path, source_url: str = "") -> None:
        """Populate extensions, filename words, and website domain from a dropped sample file."""
        zone = source_url or (read_zone_url(path) if path.is_file() else "")
        cues = infer_teach_cues(path.name, zone)
        ext = str(cues.get("ext") or "")
        if not ext and path.is_file():
            ext = sniff_magic_extension(path)
        if ext:
            norm_ext = normalize_ext_token(ext)
            if norm_ext and norm_ext not in self._extensions:
                self._extensions.append(norm_ext)

        if zone:
            dom = normalize_domain_token(zone)
            if dom and dom not in self._domains:
                self._domains.append(dom)

        # Pick the strongest semantic word from the filename (preferring known document concepts)
        word_cues = [str(w.get("value") or "") for w in (cues.get("word_cues") or []) if w.get("value")]
        all_concept_words = {
            kw
            for _trigs, pack in _NAME_CONCEPT_HINTS
            for kw in pack.get("keywords", ())
        }
        chosen_word = next((w for w in word_cues if w in all_concept_words), "")
        if not chosen_word and word_cues:
            chosen_word = str(cues.get("best_word") or word_cues[0])
        if chosen_word and chosen_word.lower() not in {k.lower() for k in self._keywords}:
            self._keywords.append(chosen_word)
        for extra_w in word_cues:
            if extra_w not in self._watch_keywords:
                self._watch_keywords.insert(0, extra_w)

        self._set_manual_only(False)
        self.lbl_drop_badge.setText(f"Auto-filled cues from {path.name}")
        self.lbl_drop_badge.setStyleSheet(
            "background-color: rgba(48, 209, 88, 0.14); color: #30D158; "
            "border: 1px solid rgba(48, 209, 88, 0.42); border-radius: 8px; "
            "padding: 4px 10px; font-size: 11px; font-weight: 600;"
        )
        self._sync_cue_chips()
        self._refresh_preview_and_cta()

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        mime = event.mimeData()
        if mime.hasUrls() and any(u.isLocalFile() for u in mime.urls()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        mime = event.mimeData()
        if mime.hasUrls() and any(u.isLocalFile() for u in mime.urls()):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        mime = event.mimeData()
        if mime.hasUrls():
            for url in mime.urls():
                if url.isLocalFile():
                    p = Path(url.toLocalFile())
                    if p.exists() and not p.is_dir():
                        event.acceptProposedAction()
                        self.apply_sample_file(p)
                        return
        super().dropEvent(event)

    # ------------------------------------------------------------------
    # Chip Rendering & Ghost Suggestions
    # ------------------------------------------------------------------

    def _sync_cue_chips(self) -> None:
        # 1. Sync built-in File Type toggle pills
        covered_by_pills: set[str] = set()
        for pill_label, grp_exts in FILE_TYPE_PILL_GROUPS:
            btn = self._type_pill_btns[pill_label]
            all_on = all(e in self._extensions for e in grp_exts)
            btn.blockSignals(True)
            btn.setChecked(all_on)
            btn.blockSignals(False)
            if all_on:
                covered_by_pills.update(grp_exts)

        # 2. Extra custom extensions + dashed ghost extension suggestions
        self.extra_ext_chips_wrap.clear_chips()
        ghost = infer_ghost_suggestions_from_name(self.edit_name.text())
        extra_exts = [e for e in self._extensions if e not in covered_by_pills]
        for ext in extra_exts:
            cbtn = QPushButton(f"{ext}  ×", self.extra_ext_chips_wrap)
            cbtn.setObjectName("ActiveChipBtn")
            cbtn.setCursor(Qt.CursorShape.PointingHandCursor)
            cbtn.setAccessibleName(f"Remove file extension {ext}")
            cbtn.setToolTip(f"Remove file type {ext}")
            cbtn.clicked.connect(lambda _c, target=ext: self.remove_extension(target))
            self.extra_ext_chips_wrap.add_chip(cbtn)

        ghost_exts = [e for e in ghost["extensions"] if e not in self._extensions]
        for gext in ghost_exts:
            gbtn = QPushButton(f"+ {gext}", self.extra_ext_chips_wrap)
            gbtn.setObjectName("GhostChipBtn")
            gbtn.setCursor(Qt.CursorShape.PointingHandCursor)
            gbtn.setAccessibleName(f"Suggested file extension {gext}")
            gbtn.setToolTip(f"Suggested from folder name — click to include {gext}")
            gbtn.clicked.connect(lambda _c, target=gext: self.add_extension(target))
            self.extra_ext_chips_wrap.add_chip(gbtn)
        self.extra_ext_chips_wrap.setVisible(bool(extra_exts or ghost_exts))
        name_controls_for_screen_readers(self.extra_ext_chips_wrap)

        # 3. Active Filename Word chips
        self.kw_chips_wrap.clear_chips()
        for kw in self._keywords:
            kbtn = QPushButton(_esc_amp(f"{kw}  ×"), self.kw_chips_wrap)
            kbtn.setObjectName("ActiveChipBtn")
            kbtn.setCursor(Qt.CursorShape.PointingHandCursor)
            kbtn.setAccessibleName(f"Remove filename word {kw}")
            kbtn.setToolTip(f"Remove filename word '{kw}'")
            kbtn.clicked.connect(lambda _c, target=kw: self.remove_keyword(target))
            self.kw_chips_wrap.add_chip(kbtn)
        self.kw_chips_wrap.setVisible(bool(self._keywords))
        name_controls_for_screen_readers(self.kw_chips_wrap)

        # 4. Active Exclude Word chips
        self.exclude_chips_wrap.clear_chips()
        for ex in self._exclude_keywords:
            ebtn = QPushButton(_esc_amp(f"not {ex}  ×"), self.exclude_chips_wrap)
            ebtn.setObjectName("ExcludeChipBtn")
            ebtn.setCursor(Qt.CursorShape.PointingHandCursor)
            ebtn.setAccessibleName(f"Remove excluded word {ex}")
            ebtn.setToolTip(f"Stop skipping files containing '{ex}'")
            ebtn.clicked.connect(lambda _c, target=ex: self.remove_exclude_keyword(target))
            self.exclude_chips_wrap.add_chip(ebtn)
        self.exclude_chips_wrap.setVisible(bool(self._exclude_keywords))
        self._update_excludes_toggle_label()
        name_controls_for_screen_readers(self.exclude_chips_wrap)

        # 5. Active Website Domain chips
        self.domain_chips_wrap.clear_chips()
        for dom in self._domains:
            dbtn = QPushButton(_esc_amp(f"{dom}  ×"), self.domain_chips_wrap)
            dbtn.setObjectName("ActiveChipBtn")
            dbtn.setCursor(Qt.CursorShape.PointingHandCursor)
            dbtn.setAccessibleName(f"Remove website {dom}")
            dbtn.setToolTip(f"Remove website source '{dom}'")
            dbtn.clicked.connect(lambda _c, target=dom: self.remove_domain(target))
            self.domain_chips_wrap.add_chip(dbtn)
        self.domain_chips_wrap.setVisible(bool(self._domains))
        name_controls_for_screen_readers(self.domain_chips_wrap)

        self._rebuild_ghost_suggestions()

    def _rebuild_ghost_suggestions(self) -> None:
        ghost = infer_ghost_suggestions_from_name(self.edit_name.text())

        # Keyword ghost chips (from folder name + loose files in watch_root)
        self.kw_ghost_wrap.clear_chips()
        active_kw_low = {k.lower() for k in self._keywords}
        cand_kws: list[str] = []
        for k in [*ghost["keywords"], *self._watch_keywords]:
            if k.lower() not in active_kw_low and k.lower() not in {c.lower() for c in cand_kws}:
                cand_kws.append(k)
        for kw in cand_kws[:8]:
            gbtn = QPushButton(_esc_amp(f'+ "{kw}"'), self.kw_ghost_wrap)
            gbtn.setObjectName("GhostChipBtn")
            gbtn.setCursor(Qt.CursorShape.PointingHandCursor)
            gbtn.setAccessibleName(f"Add suggested filename word {kw}")
            gbtn.setToolTip(f"Click to match filenames containing '{kw}'")
            gbtn.clicked.connect(lambda _c, target=kw: self.add_keyword(target))
            self.kw_ghost_wrap.add_chip(gbtn)
        self.kw_ghost_wrap.setVisible(bool(cand_kws))
        name_controls_for_screen_readers(self.kw_ghost_wrap)

        # Website domain ghost chips (detected in Downloads + folder name hints + common sites)
        self.domain_ghost_wrap.clear_chips()
        active_doms = set(self._domains)
        cand_doms: list[str] = []
        for d in [*self._watch_domains, *ghost["domains"], *_COMMON_DOMAINS_FALLBACK]:
            if d and d not in active_doms and d not in cand_doms:
                cand_doms.append(d)
        for dom in cand_doms[:7]:
            gbtn = QPushButton(f"+ {dom}", self.domain_ghost_wrap)
            gbtn.setObjectName("GhostChipBtn")
            gbtn.setCursor(Qt.CursorShape.PointingHandCursor)
            gbtn.setAccessibleName(f"Add suggested website {dom}")
            gbtn.setToolTip(f"Click to match files downloaded from {dom}")
            gbtn.clicked.connect(lambda _c, target=dom: self.add_domain(target))
            self.domain_ghost_wrap.add_chip(gbtn)
        self.domain_ghost_wrap.setVisible(bool(cand_doms))
        name_controls_for_screen_readers(self.domain_ghost_wrap)

    def _refresh_all(self) -> None:
        self._rebuild_quick_suggestions()
        self._sync_cue_chips()
        self._refresh_preview_and_cta()

    # ------------------------------------------------------------------
    # Live Path, Plain-English Sentence, Downloads Preview & CTA Guard
    # ------------------------------------------------------------------

    def _computed_paths(self) -> tuple[str, str, int, str | None]:
        raw_name = self.edit_name.text().strip().rstrip(". ").strip()
        pnode = self._current_parent_node()
        if pnode is not None:
            parent_path = self._arch.node_relative_path(pnode.id).replace("/", "\\").strip("\\")
            tier = min(FolderArchitecture.MAX_TIERS, pnode.tier + 1)
            parent_id: str | None = pnode.id
        else:
            parent_path = ""
            tier = 1
            parent_id = None

        rel_path = f"{parent_path}\\{raw_name}" if (parent_path and raw_name) else raw_name
        return parent_path, rel_path, tier, parent_id if pnode is not None else None

    def _example_filename_for_cues(self, exts: list[str], kws: list[str], doms: list[str]) -> str:
        ext = exts[0] if exts else ".pdf"
        if kws:
            slug = re.sub(r"[^a-z0-9_]+", "_", kws[0].lower()).strip("_") or "sample"
            return f"example_{slug}{ext}"
        if doms:
            stem = doms[0].split(".", 1)[0]
            return f"{stem}_download{ext}"
        raw_name = _normalize_tokens(self.edit_name.text()).replace(" ", "_") or "sample"
        return f"example_{raw_name[:18]}{ext}"

    def plain_english_summary(self) -> str:
        """Return the current plain-English sentence shown in Section 3."""
        return self.lbl_plain_english.text()

    def matching_loose_files(self) -> list[Path]:
        """Return top-level files in ``watch_root`` that currently match this folder's cues."""
        return list(self._matching_paths)

    def _refresh_preview_and_cta(self) -> None:
        parent_path, rel_path, _tier, _pid = self._computed_paths()
        raw_name = self.edit_name.text().strip().rstrip(". ").strip()
        leaf_label = raw_name or "Your Folder"
        full_crumb = (
            f"Downloads › {parent_path.replace(chr(92), ' › ')} › {leaf_label}"
            if parent_path
            else f"Downloads › {leaf_label}"
        )
        self.lbl_path_badge.setText(full_crumb)

        name_ok, name_err = self.validate_name()
        has_illegal_or_dup = bool(self.edit_name.text()) and not name_ok
        show_err = not name_ok and (self._name_touched or has_illegal_or_dup)
        self.lbl_name_error.setText(name_err if show_err else "")
        self.lbl_name_error.setVisible(show_err)

        is_manual = self.chk_manual_only.isChecked()
        exts = [] if is_manual else self._effective_extensions()
        kws = [] if is_manual else self._effective_keywords()
        ex_kws = [] if is_manual else self._effective_exclude_keywords()
        doms = [] if is_manual else self._effective_domains()
        has_cues = bool(exts or kws or doms)

        preview_rel = rel_path or (f"{parent_path}\\{leaf_label}" if parent_path else leaf_label)
        sentence = build_plain_english_summary(
            rel_path=preview_rel,
            extensions=exts,
            keywords=kws,
            domains=doms,
            exclude_keywords=ex_kws,
            manual_only=is_manual,
        )
        self.lbl_plain_english.setText(sentence)

        # Evaluate loose files in watch_root (read-only)
        self._matching_paths = []
        if not is_manual and has_cues and self._loose_candidates:
            probe_rule = Rule(
                id="guided-preview",
                name=leaf_label,
                destination=preview_rel or "Preview",
                extensions=exts,
                keywords=kws,
                exclude_keywords=ex_kws,
                domains=doms,
                enabled=True,
            )
            for cand in self._loose_candidates:
                ok, _detail = _evaluate_rule(probe_rule, cand)
                if ok and cand.path is not None:
                    self._matching_paths.append(cand.path)

        match_count = len(self._matching_paths)
        if is_manual:
            self.lbl_live_matches.setText(
                "Manual folder — existing Downloads files stay right where they are."
            )
            self.lbl_live_matches.setStyleSheet("font-size: 12px; color: #98989D;")
            self.chk_align_now.setVisible(False)
        elif not has_cues:
            loose_n = len(self._loose_candidates)
            if loose_n > 0:
                self.lbl_live_matches.setText(
                    f"{loose_n} loose file{'s' if loose_n != 1 else ''} in Downloads ready to preview "
                    "once you pick a file type, word, or website above."
                )
            else:
                self.lbl_live_matches.setText(
                    "Add a file type, filename word, or website above to preview how files will be sorted."
                )
            self.lbl_live_matches.setStyleSheet("font-size: 12px; color: #8E8E93;")
            self.chk_align_now.setVisible(False)
        elif match_count > 0:
            samples = [p.name for p in self._matching_paths[:4]]
            more = f" (+{match_count - 4} more)" if match_count > 4 else ""
            self.lbl_live_matches.setText(
                f"● {match_count} loose file{'s' if match_count != 1 else ''} in Downloads match right now: "
                f"{', '.join(samples)}{more}"
            )
            self.lbl_live_matches.setStyleSheet("font-size: 12px; color: #30D158; font-weight: 600;")
            self.chk_align_now.setText(
                f"Also move {match_count} matching loose file{'s' if match_count != 1 else ''} "
                "in Downloads into this folder right now (Undoable)"
            )
            self.chk_align_now.setVisible(True)
        else:
            ex_file = self._example_filename_for_cues(exts, kws, doms)
            self.lbl_live_matches.setText(
                f"● No loose files in Downloads match right now — ready for future files like {ex_file}."
            )
            self.lbl_live_matches.setStyleSheet("font-size: 12px; color: #30D158;")
            self.chk_align_now.setVisible(False)

        self._update_cta_and_completeness()

    def _update_cta_and_completeness(self) -> None:
        name_ok, name_err = self.validate_name()
        is_manual = self.chk_manual_only.isChecked()
        has_cues = bool(
            self._effective_extensions() or self._effective_keywords() or self._effective_domains()
        )
        raw_name = self.edit_name.text().strip().rstrip(". ").strip()
        short_name = raw_name if len(raw_name) <= 22 else f"{raw_name[:20]}…"
        match_count = len(self._matching_paths)
        verb = "Save" if self.editing_node is not None else "Create"

        if not is_manual and not has_cues:
            self.lbl_completeness_hint.setText(
                "Pick at least one file type, filename word, or website above so Download Sorter knows what belongs here."
            )
            self.lbl_completeness_hint.setStyleSheet("font-size: 12px; color: #FFB340;")
            self.btn_save.setEnabled(False)
            self.btn_save.setText("Pick at least 1 rule above")
            return

        if not name_ok:
            self.lbl_completeness_hint.setText(name_err)
            self.lbl_completeness_hint.setStyleSheet(
                "font-size: 12px; color: #FF453A;" if self._name_touched else "font-size: 12px; color: #98989D;"
            )
            self.btn_save.setEnabled(False)
            self.btn_save.setText("Enter a Folder Name Above")
            return

        self.btn_save.setEnabled(True)
        if is_manual:
            self.lbl_completeness_hint.setText(
                "● Ready — manual folder (drag files or rules here anytime)."
            )
            self.lbl_completeness_hint.setStyleSheet("font-size: 12px; color: #64D2FF;")
            self.btn_save.setText(f"{verb} Manual Folder")
        elif not self.chk_align_now.isHidden() and self.chk_align_now.isChecked() and match_count > 0:
            self.lbl_completeness_hint.setText(
                f"● Ready — will configure folder and move {match_count} loose file{'s' if match_count != 1 else ''} (Undoable)."
            )
            self.lbl_completeness_hint.setStyleSheet("font-size: 12px; color: #30D158;")
            self.btn_save.setText(
                _esc_amp(f"{verb} '{short_name}' & Sort {match_count} File{'s' if match_count != 1 else ''}")
            )
        else:
            self.lbl_completeness_hint.setText(
                "● Ready — matching downloads will route here automatically."
            )
            self.lbl_completeness_hint.setStyleSheet("font-size: 12px; color: #30D158;")
            self.btn_save.setText(_esc_amp(f"{verb} '{short_name}' Folder"))

    def _on_save_clicked(self) -> None:
        self._name_touched = True
        self._flush_all_pending_inputs()
        name_ok, _err = self.validate_name()
        is_manual = self.chk_manual_only.isChecked()
        has_cues = bool(self._extensions or self._keywords or self._domains)
        if not name_ok or (not is_manual and not has_cues):
            self._refresh_preview_and_cta()
            return
        self.accept()

    # ------------------------------------------------------------------
    # Public API for @flow-ux & Architecture Integration
    # ------------------------------------------------------------------

    def result_data(self) -> dict[str, Any]:
        """Return structured folder & routing data ready for architecture/config application."""
        self._flush_all_pending_inputs()
        parent_path, rel_path, tier, parent_id = self._computed_paths()
        clean_name = self.edit_name.text().strip().rstrip(". ").strip()
        is_manual = bool(self.chk_manual_only.isChecked())
        exts = [] if is_manual else _clean_exts(self._extensions)
        kws = [] if is_manual else _clean_strs(self._keywords)
        ex_kws = [] if is_manual else _clean_strs(self._exclude_keywords)
        doms = [] if is_manual else normalize_domain_tokens(self._domains)
        resolved_kind = self._selected_kind
        if not resolved_kind or resolved_kind == "default":
            resolved_kind = _guess_kind(clean_name, exts)
        if resolved_kind not in KIND_COLORS:
            resolved_kind = "default"

        align_now = bool(
            not is_manual
            and self.chk_align_now.isChecked()
            and len(self._matching_paths) > 0
        )
        return {
            "name": clean_name,
            "tier": tier,
            "parent_id": parent_id,
            "parent_path": parent_path,
            "rel_path": rel_path,
            "kind": resolved_kind,
            "extensions": exts,
            "keywords": kws,
            "exclude_keywords": ex_kws,
            "domains": doms,
            "manual_only": is_manual,
            "align_now": align_now,
        }

    def apply_to_architecture(
        self,
        arch: FolderArchitecture,
        base_config: Config,
    ) -> tuple[FolderArchitecture, Config, str]:
        """Create or update the custom folder in ``arch`` and compile a routing-ready ``Config``."""
        data = self.result_data()
        new_arch = arch.clone()
        working_base = copy.deepcopy(base_config)

        clean_name: str = data["name"]
        parent_path: str = data["parent_path"]
        exts: list[str] = data["extensions"]
        kws: list[str] = data["keywords"]
        ex_kws: list[str] = data["exclude_keywords"]
        doms: list[str] = data["domains"]
        kind: str = data["kind"]
        manual_only: bool = data["manual_only"]

        # Resolve target parent inside new_arch (ensuring path chain if parent was disk-only)
        resolved_parent_id: str | None = None
        target_tier = 1
        if parent_path:
            pnode = new_arch.get_node(data["parent_id"]) if data["parent_id"] else None
            if pnode is None:
                for cand in new_arch.nodes:
                    if new_arch.node_relative_path(cand.id).replace("/", "\\").strip("\\").lower() == parent_path.lower():
                        pnode = cand
                        break
            if pnode is None:
                pnode = new_arch.ensure_path_chain(parent_path, is_custom=False)
            if pnode is not None:
                resolved_parent_id = pnode.id
                target_tier = min(FolderArchitecture.MAX_TIERS, pnode.tier + 1)

        old_rel_path = ""
        target_node: ArchNode | None = None
        if self.editing_node is not None:
            existing = new_arch.get_node(self.editing_node.id)
            if existing is not None:
                old_rel_path = new_arch.node_relative_path(existing.id).replace("/", "\\").strip("\\")
                if existing.parent_id != resolved_parent_id:
                    new_arch.reparent_node(existing.id, resolved_parent_id)
                target_node = new_arch.update_node(
                    existing.id,
                    name=clean_name,
                    tier=target_tier,
                    parent_id=resolved_parent_id,
                    kind=kind,
                    extensions=exts,
                    keywords=kws,
                    exclude_keywords=ex_kws,
                    domains=doms,
                    enabled=True,
                )

        if target_node is None:
            # Reuse an identical sibling node if present rather than duplicating
            dup = new_arch.find_by_name_and_tier(clean_name, target_tier, resolved_parent_id)
            if dup is not None:
                target_node = new_arch.update_node(
                    dup.id,
                    kind=kind,
                    extensions=exts,
                    keywords=kws,
                    exclude_keywords=ex_kws,
                    domains=doms,
                    enabled=True,
                )
            else:
                target_node = new_arch.add_custom_node(
                    name=clean_name,
                    tier=target_tier,
                    parent_id=resolved_parent_id,
                    kind=kind,
                    extensions=exts,
                    keywords=kws,
                    exclude_keywords=ex_kws,
                    domains=doms,
                    enabled=True,
                )

        if manual_only:
            target_node.premade_key = "__manual__"
        elif target_node.premade_key == "__manual__":
            target_node.premade_key = ""

        rel_path = new_arch.node_relative_path(target_node.id).replace("/", "\\").strip("\\")

        # When this folder is a pure extension bucket (no keywords or domains), release those
        # extensions from competing pure-extension buckets so category_map/type_subfolders route cleanly.
        if exts and not kws and not doms and not manual_only:
            claimed = set(exts)
            for other in new_arch.nodes:
                if other.id == target_node.id or other.keywords or other.domains:
                    continue
                if target_node.tier == 1:
                    other.extensions = [e for e in other.extensions if e not in claimed]
                elif target_node.tier == 2 and other.tier >= 2:
                    other.extensions = [e for e in other.extensions if e not in claimed]

        # Sync any pre-existing user rules that pointed to the old/current folder path during an edit
        target_norms = {p.lower() for p in (old_rel_path, rel_path) if p}
        if manual_only and target_norms:
            working_base.rules = [
                r
                for r in working_base.rules
                if r.destination.replace("/", "\\").strip("\\").lower() not in target_norms
            ]
        elif old_rel_path:
            old_norm = old_rel_path.lower()
            kept_rules: list[Rule] = []
            for r in working_base.rules:
                if r.destination.replace("/", "\\").strip("\\").lower() != old_norm:
                    kept_rules.append(r)
                    continue
                is_advanced = bool(
                    r.regex_pattern
                    or r.content_keywords
                    or r.rename_template
                    or r.min_size_mb > 0
                    or r.max_size_mb > 0
                    or r.min_age_days > 0
                )
                if is_advanced:
                    r.destination = rel_path
                    kept_rules.append(r)
                # Plain rules merged into GuidedFolderDialog are regenerated cleanly from target_node below
            working_base.rules = kept_rules

        new_cfg = apply_architecture_to_config(new_arch, working_base)

        # Ensure custom folder rules take priority over starter template rules so evaluate() / trace_file()
        # immediately route matching files into this folder.
        if not manual_only and (kws or doms or (exts and target_node.tier >= 3)):
            rule_id = f"arch-{target_node.id}"
            custom_rule = Rule(
                id=rule_id,
                name=clean_name if target_node.tier == 1 else rel_path,
                destination=rel_path,
                keywords=list(kws),
                exclude_keywords=list(ex_kws),
                domains=list(doms),
                extensions=list(exts),
                enabled=True,
            )
            remaining = [r for r in new_cfg.rules if r.id != rule_id]
            new_cfg.rules = [custom_rule, *remaining]
        elif not manual_only and exts and not kws and not doms:
            if target_node.tier == 1:
                for ext in exts:
                    new_cfg.category_map[ext] = target_node.name
                    new_cfg.type_subfolders.pop(ext, None)
            elif target_node.tier == 2 and resolved_parent_id is not None:
                p_obj = new_arch.get_node(resolved_parent_id)
                if p_obj is not None and p_obj.tier == 1:
                    for ext in exts:
                        new_cfg.category_map[ext] = p_obj.name
                        new_cfg.type_subfolders[ext] = target_node.name

        new_cfg.architecture = new_arch.to_dict()
        return new_arch, new_cfg, rel_path
