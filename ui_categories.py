"""What kind of file is this, and which color shows it?

The Home list (Qt) and the desktop ribbon (Tk) both ask this, so the answer lives
here, in a module that imports neither toolkit. ``category_info`` returns
``(kind, color)`` where ``kind`` is one of ``KINDS``.

Colors are written out as hex because this is a categorical palette: the first
group are the suite's own colors (see suite_theme), the rest are extra hues for
media and packages that the suite has no token for.
"""

from __future__ import annotations

from pathlib import Path

# The suite's own colors (suite_theme: success / haasStudio / printLibrary /
# critical / info / warning / textMuted).
MINT = "#69E6AD"
RED = "#D74A4A"
ORANGE = "#E8833A"
CORAL = "#FA7183"
BLUE = "#61A5FF"
AMBER = "#F5AD62"
SLATE = "#8B94A7"
# Extra hues with no suite token.
PURPLE = "#A855F7"
PINK = "#EC4899"
INDIGO = "#8B5CF6"
CYAN = "#06B6D4"
ROSE = "#F43F5E"

DEFAULT_ACCENT = MINT

KINDS = ("cad", "cnc", "print", "pdf", "doc", "sheet", "image", "video", "audio",
         "archive", "installer", "code", "default")

LABEL_BY_KIND = {
    "cad": "3D or CAD file", "cnc": "CNC program", "print": "3D print file", "pdf": "PDF",
    "doc": "Document", "sheet": "Spreadsheet", "image": "Image", "video": "Video",
    "audio": "Audio", "archive": "Archive", "installer": "Installer", "code": "Code",
    "default": "File",
}

COLOR_BY_KIND = {
    "cad": MINT, "cnc": RED, "print": ORANGE, "pdf": CORAL, "doc": BLUE, "sheet": MINT,
    "image": PURPLE, "video": PINK, "audio": INDIGO, "archive": CYAN, "installer": ROSE,
    "code": AMBER, "default": SLATE,
}

# File extensions, checked first (they are the most precise signal).
_BY_EXTENSION: tuple[tuple[frozenset[str], str], ...] = (
    (frozenset({".step", ".stp", ".stl", ".3mf", ".obj", ".f3d", ".f3z", ".ipt", ".iam",
                ".sldprt", ".dwg", ".dxf"}), "cad"),
    (frozenset({".nc", ".tap", ".ngc"}), "cnc"),
    (frozenset({".gcode", ".bgcode"}), "print"),
    (frozenset({".pdf"}), "pdf"),
    (frozenset({".doc", ".docx", ".odt", ".rtf", ".txt", ".md"}), "doc"),
    (frozenset({".xls", ".xlsx", ".csv", ".ods"}), "sheet"),
    (frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp"}), "image"),
    (frozenset({".mp4", ".mov", ".mkv", ".avi", ".webm"}), "video"),
    (frozenset({".mp3", ".wav", ".flac", ".m4a", ".aac"}), "audio"),
    (frozenset({".zip", ".rar", ".7z", ".tar", ".gz"}), "archive"),
    (frozenset({".exe", ".msi", ".msix"}), "installer"),
    (frozenset({".py", ".js", ".ts", ".html", ".css", ".json", ".cpp", ".cs", ".rs", ".go",
                ".c", ".h"}), "code"),
)

# Words in the destination folder's name, for files whose extension says nothing.
# The first folder word found (in this order) decides.
CATEGORY_COLORS: dict[str, str] = {
    "step": MINT, "stl": MINT, "3mf": MINT, "obj": MINT, "cad": MINT, "3d models": MINT,
    "gcode": RED, "print files": ORANGE, "pdfs": CORAL,
    "school": BLUE, "documents": BLUE, "word": BLUE, "text": SLATE,
    "spreadsheets": MINT, "images": PURPLE, "videos": PINK, "audio": INDIGO,
    "installers": ROSE, "archives": CYAN, "code": AMBER, "other": SLATE,
}

_FOLDER_KIND = {
    "step": "cad", "stl": "cad", "3mf": "cad", "obj": "cad", "cad": "cad", "3d models": "cad",
    "gcode": "cnc", "print files": "print", "pdfs": "pdf",
    "school": "doc", "documents": "doc", "word": "doc", "text": "doc",
    "spreadsheets": "sheet", "images": "image", "videos": "video", "audio": "audio",
    "archives": "archive", "installers": "installer", "code": "code",
}


def category_info(dest_path: Path | str, folder_disp: str = "") -> tuple[str, str]:
    """``(kind, color)`` for a file, from its extension first and its folder second."""
    ext = Path(dest_path).suffix.lower()
    for extensions, kind in _BY_EXTENSION:
        if ext in extensions:
            return kind, COLOR_BY_KIND[kind]

    folder_lower = (folder_disp or "").lower()
    for key, color in CATEGORY_COLORS.items():
        if key in folder_lower:
            return _FOLDER_KIND.get(key, "default"), color

    return "default", DEFAULT_ACCENT


def kind_of(dest_path: Path | str, folder_disp: str = "") -> str:
    return category_info(dest_path, folder_disp)[0]
