"""Top-right interactive ribbon notification popup for download-sorter.

Displays a modern floating banner in the top-right corner when a file is sorted.
Features:
- Integrated with Devyn Personal Suite design tokens (`suite_theme.py`).
- Crisp High-DPI rendering on Windows (fixes blurriness on 125%/150%/175%/200% displays).
- Dynamic category-based accent colors (3D/CAD, CNC, Slicer, PDFs, Code, Media, Installers).
- Modern anti-aliased vector icon emblems for all major file categories.
- Silky 60fps cubic ease-out entrance slide and snappy ease-in exit slide animations.
- Smooth 50fps progress countdown bar with pause-on-hover and glow highlight.
- Tinted category-reactive border and Windows 11 rounded corners + native drop shadow.
- Atomic slot synchronization for multi-file downloads using Windows file locking.
- Automatic vertical stacking when multiple files are downloaded at once.
- Safe File Explorer reveal supporting commas and special characters in paths.
- Auto-dismisses after timeout (pauses on hover, dismisses on close button).
- Rate-capped to avoid spamming the screen during large bulk operations.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

# Enable Windows High-DPI awareness BEFORE importing or initializing Tkinter
if sys.platform == "win32":
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Per-Monitor V2
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

import tkinter as tk

try:
    from PIL import Image, ImageDraw, ImageTk
    HAS_PIL = True
except Exception:
    HAS_PIL = False

ROOT = Path(__file__).resolve().parent
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
if not PYTHON.exists():
    PYTHON = Path(sys.executable)

# Load Devyn Personal Suite tokens if available, else graceful defaults
try:
    from suite_theme import ACCENTS, COLORS, RIBBON, TYPE
    BG_COLOR = COLORS.get("surface", "#12151C")
    BORDER_COLOR = COLORS.get("border", "#2A303B")
    DEFAULT_ACCENT = ACCENTS.get("downloadSorter", "#69E6AD")
    TEXT_MAIN = COLORS.get("text", "#EDF1F8")
    TEXT_MUTED = COLORS.get("textMuted", "#8B94A7")
    ACTION_COLOR = COLORS.get("info", "#61A5FF")
    CLOSE_COLOR = COLORS.get("textFaint", "#657084")
    CLOSE_HOVER = COLORS.get("critical", "#FA7183")
    HOVER_BG = COLORS.get("surfaceHover", "#1D222C")

    BASE_WIDTH = int(RIBBON.get("width", 360))
    BASE_HEIGHT = int(RIBBON.get("height", 90))
    BASE_OFFSET_X = int(RIBBON.get("offset", 18))
    BASE_OFFSET_Y = int(RIBBON.get("offset", 18))
    BASE_GAP = int(RIBBON.get("gap", 8))
    AUTO_DISMISS_MS = int(RIBBON.get("timeoutMs", 8000))
    MAX_SLOTS = int(RIBBON.get("maxVisible", 4))
    FONT_FAMILY = TYPE.get("ui", "Segoe UI")
except Exception:
    RIBBON = {
        "width": 360, "height": 90, "offset": 18, "gap": 8,
        "timeoutMs": 8000, "maxVisible": 4, "opacity": 0.88,
    }
    BG_COLOR = "#12151C"
    BORDER_COLOR = "#2A303B"
    DEFAULT_ACCENT = "#69E6AD"
    TEXT_MAIN = "#EDF1F8"
    TEXT_MUTED = "#8B94A7"
    ACTION_COLOR = "#61A5FF"
    CLOSE_COLOR = "#657084"
    CLOSE_HOVER = "#FA7183"
    HOVER_BG = "#1D222C"

    BASE_WIDTH = 360
    BASE_HEIGHT = 90
    BASE_OFFSET_X = 18
    BASE_OFFSET_Y = 18
    BASE_GAP = 8
    AUTO_DISMISS_MS = 8000
    MAX_SLOTS = 4
    FONT_FAMILY = "Segoe UI"

SLOTS_FILE = Path(tempfile.gettempdir()) / "download_sorter_ribbon_slots.json"
SLOTS_LOCK = Path(tempfile.gettempdir()) / "download_sorter_ribbon_slots.lock"

# Category color map aligned with Devyn Personal Suite tokens
CATEGORY_COLORS: dict[str, str] = {
    "step": "#69E6AD",         # 3D / CAD: Suite mint
    "stl": "#69E6AD",
    "3mf": "#69E6AD",
    "obj": "#69E6AD",
    "cad": "#69E6AD",
    "3d models": "#69E6AD",
    "gcode": "#D74A4A",        # CNC / GCode: HaasStudio red
    "print files": "#E8833A",  # Slicer: PrintLibrary orange
    "pdfs": "#FA7183",         # PDFs: Critical coral red
    "school": "#61A5FF",       # Documents: Suite blue
    "documents": "#61A5FF",
    "word": "#61A5FF",
    "text": "#8B94A7",
    "spreadsheets": "#69E6AD", # Excel / CSV: Green
    "images": "#A855F7",       # Purple / Violet
    "videos": "#EC4899",       # Pink / Magenta
    "audio": "#8B5CF6",        # Indigo
    "installers": "#F43F5E",   # Rose
    "archives": "#06B6D4",     # Cyan
    "code": "#F5AD62",         # Warning amber / Gold
    "other": "#8B94A7",        # Muted slate
}

# Fallback text glyphs when Pillow is not available
CATEGORY_GLYPHS: dict[str, str] = {
    "cad": "◇",
    "cnc": "⚙",
    "print": "◈",
    "pdf": "📄",
    "doc": "📝",
    "sheet": "▦",
    "image": "🖼",
    "video": "▶",
    "audio": "♫",
    "archive": "🗜",
    "installer": "⬇",
    "code": "⟨/⟩",
    "default": "✓",
}


def hex_to_rgb(h: str) -> tuple[int, int, int]:
    """Parse a hex color string (#RGB, #RRGGBB) to an RGB tuple."""
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except Exception:
        return 105, 230, 173


def blend_colors(color1: str, color2: str, factor: float) -> str:
    """Blend two hex colors: factor 1.0 means 100% color1, 0.0 means 100% color2."""
    try:
        r1, g1, b1 = hex_to_rgb(color1)
        r2, g2, b2 = hex_to_rgb(color2)
        r = max(0, min(255, round(r1 * factor + r2 * (1.0 - factor))))
        g = max(0, min(255, round(g1 * factor + g2 * (1.0 - factor))))
        b = max(0, min(255, round(b1 * factor + b2 * (1.0 - factor))))
        return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        return color1


def get_category_info(dest_path: Path, folder_disp: str) -> tuple[str, str]:
    """Return (category_type, accent_color) based on file extension and folder context."""
    folder_lower = folder_disp.lower()
    ext = dest_path.suffix.lower()

    # Direct extension mappings (primary precision)
    if ext in (".step", ".stp", ".stl", ".3mf", ".obj", ".f3d", ".f3z", ".ipt", ".iam", ".sldprt", ".dwg", ".dxf"):
        return "cad", "#69E6AD"
    if ext in (".nc", ".tap", ".ngc"):
        return "cnc", "#D74A4A"
    if ext in (".gcode", ".bgcode"):
        return "print", "#E8833A"
    if ext == ".pdf":
        return "pdf", "#FA7183"
    if ext in (".doc", ".docx", ".odt", ".rtf", ".txt", ".md"):
        return "doc", "#61A5FF"
    if ext in (".xls", ".xlsx", ".csv", ".ods"):
        return "sheet", "#69E6AD"
    if ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp"):
        return "image", "#A855F7"
    if ext in (".mp4", ".mov", ".mkv", ".avi", ".webm"):
        return "video", "#EC4899"
    if ext in (".mp3", ".wav", ".flac", ".m4a", ".aac"):
        return "audio", "#8B5CF6"
    if ext in (".zip", ".rar", ".7z", ".tar", ".gz"):
        return "archive", "#06B6D4"
    if ext in (".exe", ".msi", ".msix"):
        return "installer", "#F43F5E"
    if ext in (".py", ".js", ".ts", ".html", ".css", ".json", ".cpp", ".cs", ".rs", ".go", ".c", ".h"):
        return "code", "#F5AD62"

    # Destination folder keyword matches
    for key, color in CATEGORY_COLORS.items():
        if key in folder_lower:
            if key in ("step", "stl", "3mf", "obj", "cad", "3d models"):
                return "cad", color
            if key == "gcode":
                return "cnc", color
            if key == "print files":
                return "print", color
            if key == "pdfs":
                return "pdf", color
            if key in ("school", "documents", "word", "text"):
                return "doc", color
            if key == "spreadsheets":
                return "sheet", color
            if key == "images":
                return "image", color
            if key == "videos":
                return "video", color
            if key == "audio":
                return "audio", color
            if key == "archives":
                return "archive", color
            if key == "installers":
                return "installer", color
            if key == "code":
                return "code", color
            return "default", color

    return "default", DEFAULT_ACCENT


def get_accent_color(dest_path: Path, folder_disp: str) -> str:
    """Return a distinct theme color depending on file type or category folder."""
    _cat, color = get_category_info(dest_path, folder_disp)
    return color


def render_category_icon(category_type: str, accent_hex: str, size: int = 38) -> Image.Image | None:
    """Render a modern anti-aliased category emblem badge using Pillow with 3x supersampling."""
    if not HAS_PIL:
        return None

    try:
        scale = 3
        S = size * scale
        im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        r, g, b = hex_to_rgb(accent_hex)

        # Background squircle container with dual-layer depth
        rad = int(9 * scale)
        # Outer soft glow outline
        d.rounded_rectangle(
            [1, 1, S - 2, S - 2],
            radius=rad,
            fill=(r, g, b, 24),
            outline=(r, g, b, 50),
            width=max(1, int(1 * scale)),
        )
        # Inset solid-glass badge
        d.rounded_rectangle(
            [int(2 * scale), int(2 * scale), S - int(3 * scale), S - int(3 * scale)],
            radius=int(7 * scale),
            fill=(r, g, b, 38),
            outline=(r, g, b, 115),
            width=max(1, int(1.2 * scale)),
        )

        cx, cy = S / 2.0, S / 2.0
        fg = (r, g, b, 255)
        fg_dim = (r, g, b, 135)
        w = max(2, int(2.0 * scale))
        w_thin = max(1, int(1.2 * scale))

        if category_type == "cad":
            # 3D isometric faceted cube
            cr = S * 0.26
            top = (cx, cy - cr)
            tr = (cx + cr * 0.866, cy - cr * 0.5)
            br = (cx + cr * 0.866, cy + cr * 0.5)
            bot = (cx, cy + cr)
            bl = (cx - cr * 0.866, cy + cr * 0.5)
            tl = (cx - cr * 0.866, cy - cr * 0.5)
            c = (cx, cy)
            d.polygon([c, top, tr, c], fill=(r, g, b, 180), outline=fg)
            d.polygon([c, tr, br, bot], fill=(r, g, b, 120), outline=fg)
            d.polygon([c, bot, bl, tl], fill=(r, g, b, 70), outline=fg)
            d.line([c, top], fill=fg, width=w)
            d.line([c, br], fill=fg, width=w)
            d.line([c, bl], fill=fg, width=w)

        elif category_type == "cnc":
            # Haas milling endmill cutter with helical flutes
            x1, x2 = cx - S * 0.13, cx + S * 0.13
            top_y, fl_y, bot_y = cy - S * 0.28, cy - S * 0.06, cy + S * 0.26
            d.rounded_rectangle([x1, top_y, x2, fl_y], radius=int(2 * scale), fill=(r, g, b, 60), outline=fg, width=w)
            d.line([(x1, fl_y), (x2, fl_y + S * 0.08)], fill=fg, width=w)
            d.line([(x1, fl_y + S * 0.09), (x2, fl_y + S * 0.17)], fill=fg, width=w)
            d.line([(x1, fl_y + S * 0.18), (x2, fl_y + S * 0.26)], fill=fg, width=w)
            d.line([(x1, fl_y), (x1, bot_y)], fill=fg, width=w)
            d.line([(x2, fl_y), (x2, bot_y)], fill=fg, width=w)
            d.line([(x1, bot_y), (x2, bot_y)], fill=fg, width=w)

        elif category_type == "print":
            # 3D printer hotend extruder & deposited layer
            top_y = cy - S * 0.25
            mid_y = cy + S * 0.02
            tip_y = cy + S * 0.18
            d.polygon(
                [(cx - S * 0.20, top_y), (cx + S * 0.20, top_y), (cx + S * 0.20, mid_y),
                 (cx + S * 0.06, tip_y), (cx - S * 0.06, tip_y), (cx - S * 0.20, mid_y)],
                fill=(r, g, b, 60), outline=fg, width=w,
            )
            d.line([(cx - S * 0.26, cy + S * 0.26), (cx + S * 0.26, cy + S * 0.26)], fill=fg, width=int(w * 1.4))

        elif category_type in ("pdf", "doc"):
            # Clean document page with folded corner and text lines
            x1, y1 = cx - S * 0.22, cy - S * 0.27
            x2, y2 = cx + S * 0.22, cy + S * 0.27
            fold = S * 0.14
            d.polygon([(x1, y1), (x2 - fold, y1), (x2, y1 + fold), (x2, y2), (x1, y2)], fill=(r, g, b, 45), outline=fg, width=w)
            d.polygon([(x2 - fold, y1), (x2 - fold, y1 + fold), (x2, y1 + fold)], fill=(r, g, b, 140), outline=fg, width=w)
            lx1, lx2 = x1 + S * 0.08, x2 - S * 0.08
            d.line([(lx1, cy - S * 0.04), (lx2 - S * 0.06, cy - S * 0.04)], fill=fg_dim, width=w)
            d.line([(lx1, cy + S * 0.08), (lx2, cy + S * 0.08)], fill=fg_dim, width=w)
            d.line([(lx1, cy + S * 0.20), (lx1 + S * 0.14, cy + S * 0.20)], fill=fg_dim, width=w)

        elif category_type == "sheet":
            # Spreadsheet data grid with header
            x1, y1 = cx - S * 0.26, cy - S * 0.23
            x2, y2 = cx + S * 0.26, cy + S * 0.23
            d.rounded_rectangle([x1, y1, x2, y2], radius=int(3 * scale), fill=(r, g, b, 45), outline=fg, width=w)
            hdr_y = y1 + (y2 - y1) * 0.35
            d.line([(x1, hdr_y), (x2, hdr_y)], fill=fg, width=w)
            col_x = x1 + (x2 - x1) * 0.45
            d.line([(col_x, y1), (col_x, y2)], fill=fg, width=w)

        elif category_type == "image":
            # Picture frame with mountain peaks and sun
            x1, y1 = cx - S * 0.26, cy - S * 0.22
            x2, y2 = cx + S * 0.26, cy + S * 0.22
            d.rounded_rectangle([x1, y1, x2, y2], radius=int(3 * scale), fill=(r, g, b, 45), outline=fg, width=w)
            d.ellipse([cx + S * 0.06, y1 + S * 0.06, cx + S * 0.18, y1 + S * 0.18], fill=fg)
            d.polygon([(x1 + S * 0.04, y2 - S * 0.03), (cx - S * 0.06, cy - S * 0.02), (cx + S * 0.08, y2 - S * 0.03)], fill=(r, g, b, 150))
            d.polygon([(cx, y2 - S * 0.03), (cx + S * 0.14, cy + S * 0.04), (x2 - S * 0.04, y2 - S * 0.03)], fill=(r, g, b, 100))

        elif category_type == "video":
            # Media screen with centered play triangle
            x1, y1 = cx - S * 0.26, cy - S * 0.22
            x2, y2 = cx + S * 0.26, cy + S * 0.22
            d.rounded_rectangle([x1, y1, x2, y2], radius=int(4 * scale), fill=(r, g, b, 45), outline=fg, width=w)
            pw = S * 0.12
            d.polygon([(cx - pw * 0.6, cy - pw * 0.9), (cx + pw * 0.9, cy), (cx - pw * 0.6, cy + pw * 0.9)], fill=fg)

        elif category_type == "audio":
            # Audio spectrum waveform bars
            bar_ws = [0.10, 0.22, 0.16, 0.26, 0.12]
            step_x = S * 0.11
            start_x = cx - 2 * step_x
            for i, bh in enumerate(bar_ws):
                bx = start_x + i * step_x
                d.line([(bx, cy - S * bh), (bx, cy + S * bh)], fill=fg, width=int(2.2 * scale))

        elif category_type == "archive":
            # Archive package box with zipper teeth
            x1, y1 = cx - S * 0.24, cy - S * 0.24
            x2, y2 = cx + S * 0.24, cy + S * 0.24
            d.rounded_rectangle([x1, y1, x2, y2], radius=int(3 * scale), fill=(r, g, b, 45), outline=fg, width=w)
            d.line([(cx, y1), (cx, cy + S * 0.08)], fill=fg, width=w)
            for ty in [y1 + S * 0.06, y1 + S * 0.14, y1 + S * 0.22]:
                d.line([(cx - S * 0.06, ty), (cx + S * 0.06, ty)], fill=fg, width=w_thin)
            d.rectangle([cx - S * 0.05, cy + S * 0.08, cx + S * 0.05, cy + S * 0.18], fill=fg)

        elif category_type == "installer":
            # System app window with download arrow
            x1, y1 = cx - S * 0.24, cy - S * 0.24
            x2, y2 = cx + S * 0.24, cy + S * 0.24
            d.rounded_rectangle([x1, y1, x2, y2], radius=int(3 * scale), fill=(r, g, b, 45), outline=fg, width=w)
            d.line([(cx, cy - S * 0.12), (cx, cy + S * 0.10)], fill=fg, width=w)
            d.polygon([(cx - S * 0.10, cy + S * 0.06), (cx + S * 0.10, cy + S * 0.06), (cx, cy + S * 0.16)], fill=fg)

        elif category_type == "code":
            # Developer angle brackets < / >
            gap = S * 0.16
            d.line([(cx - gap, cy - S * 0.18), (cx - gap - S * 0.14, cy), (cx - gap, cy + S * 0.18)], fill=fg, width=w)
            d.line([(cx + S * 0.06, cy - S * 0.22), (cx - S * 0.06, cy + S * 0.22)], fill=fg_dim, width=w)
            d.line([(cx + gap, cy - S * 0.18), (cx + gap + S * 0.14, cy), (cx + gap, cy + S * 0.18)], fill=fg, width=w)

        else:
            # Clean checkmark badge
            pts = [(cx - S * 0.16, cy + S * 0.02), (cx - S * 0.04, cy + S * 0.14), (cx + S * 0.18, cy - S * 0.12)]
            d.line(pts, fill=fg, width=int(2.4 * scale), joint="curve")

        return im.resize((size, size), Image.Resampling.LANCZOS)
    except Exception:
        return None


@contextmanager
def slot_lock():
    """Inter-process file lock ensuring atomic slot assignment."""
    if sys.platform == "win32":
        import msvcrt
        try:
            with open(SLOTS_LOCK, "a+") as f:
                deadline = time.time() + 2.0
                acquired = False
                while time.time() < deadline:
                    try:
                        f.seek(0)
                        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                        acquired = True
                        break
                    except OSError:
                        time.sleep(0.02)
                try:
                    yield
                finally:
                    if acquired:
                        try:
                            f.seek(0)
                            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                        except OSError:
                            pass
        except Exception:
            yield
    else:
        yield


def is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def claim_slot(pid: int) -> int:
    with slot_lock():
        slots: dict[str, int] = {}
        try:
            if SLOTS_FILE.exists():
                with open(SLOTS_FILE, "r", encoding="utf-8") as f:
                    slots = json.load(f)
        except Exception:
            slots = {}

        active_slots = {int(s): p for s, p in slots.items() if is_pid_alive(p)}

        chosen = 0
        for s in range(MAX_SLOTS):
            if s not in active_slots:
                chosen = s
                break
        else:
            chosen = MAX_SLOTS - 1

        active_slots[chosen] = pid
        try:
            with open(SLOTS_FILE, "w", encoding="utf-8") as f:
                json.dump({str(s): p for s, p in active_slots.items()}, f)
        except Exception:
            pass
        return chosen


def release_slot(slot: int, pid: int) -> None:
    with slot_lock():
        try:
            if SLOTS_FILE.exists():
                with open(SLOTS_FILE, "r", encoding="utf-8") as f:
                    slots = json.load(f)
                if slots.get(str(slot)) == pid:
                    del slots[str(slot)]
                    with open(SLOTS_FILE, "w", encoding="utf-8") as f:
                        json.dump(slots, f)
        except Exception:
            pass


def scaled_ribbon_geometry(
    scale: float,
    slot: int,
    work_area: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    """Return width, height, x and y for a DPI-scaled work area."""
    scale = max(1.0, float(scale))
    width = round(BASE_WIDTH * scale)
    height = round(BASE_HEIGHT * scale)
    offset_x = round(BASE_OFFSET_X * scale)
    offset_y = round(BASE_OFFSET_Y * scale)
    gap = round(BASE_GAP * scale)
    _left, top, right, _bottom = work_area
    return width, height, right - width - offset_x, top + offset_y + slot * (height + gap)


class RibbonPopup:
    def __init__(
        self,
        dest_path: Path,
        downloads_root: Path | None = None,
        enable_animation: bool = True,
    ) -> None:
        self.dest_path = dest_path.resolve()
        self.downloads_root = downloads_root.resolve() if downloads_root else Path.home() / "Downloads"
        self.pid = os.getpid()
        self.slot = claim_slot(self.pid)
        self.enable_animation = enable_animation

        # Calculate relative folder display
        try:
            rel = self.dest_path.relative_to(self.downloads_root)
            self.folder_disp = str(rel.parent) if rel.parent != Path(".") else "Downloads"
        except ValueError:
            self.folder_disp = self.dest_path.parent.name

        self.category_type, self.accent_color = get_category_info(self.dest_path, self.folder_disp)

        # Theme-derived color tokens & reactive border styling
        self.normal_border = blend_colors(self.accent_color, BORDER_COLOR, 0.32)
        self.hover_border = blend_colors(self.accent_color, COLORS.get("borderStrong", "#3A4352"), 0.50)
        self.accent_glow = blend_colors(self.accent_color, "#FFFFFF", 0.25)

        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.config(bg=self.normal_border)

        # Enable DWM rounded corners + native drop shadow on Windows 11
        if sys.platform == "win32":
            try:
                self.root.update_idletasks()
                hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
                # DWMWA_WINDOW_CORNER_PREFERENCE (33) = DWMWCP_ROUND (2)
                pref = ctypes.c_int(2)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref)
                )
                # Enable DWM shadow via DwmExtendFrameIntoClientArea
                class MARGINS(ctypes.Structure):
                    _fields_ = [("left", ctypes.c_int), ("right", ctypes.c_int),
                                ("top", ctypes.c_int), ("bottom", ctypes.c_int)]
                m = MARGINS(1, 1, 1, 1)
                ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(m))
            except Exception:
                pass

        # Calculate DPI-scaled dimensions
        try:
            dpi = float(self.root.winfo_fpixels("1i"))
            self.scale = max(1.0, dpi / 96.0)
        except Exception:
            self.scale = 1.0

        width, height, self.target_x, self.target_y = scaled_ribbon_geometry(
            self.scale, self.slot, self._work_area()
        )
        self.target_w = width
        self.target_h = height

        # Animation parameters (220ms ease-out enter, 160ms ease-in exit)
        self.slide_distance = round(140 * self.scale)
        self.slide_job: str | None = None
        self.dismiss_job: str | None = None
        self.progress_job: str | None = None
        self.remaining_ms = float(AUTO_DISMISS_MS)
        self.dismiss_started = 0.0
        self.is_hovered = False
        self.is_closing = False
        self.icon_photo: ImageTk.PhotoImage | None = None

        self._build_ui(self.scale)

        if self.enable_animation:
            start_x = self.target_x + self.slide_distance
            self.root.geometry(f"{width}x{height}+{start_x}+{self.target_y}")
            self._animate_slide_in(start_x, self.target_x, self.target_y, duration_ms=220)
        else:
            self.root.geometry(f"{width}x{height}+{self.target_x}+{self.target_y}")
            self._schedule_dismiss()

    def _work_area(self) -> tuple[int, int, int, int]:
        """Return the foreground monitor's work area, excluding its taskbar."""
        if sys.platform == "win32":
            try:
                class RECT(ctypes.Structure):
                    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

                class MONITORINFO(ctypes.Structure):
                    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT),
                                ("rcWork", RECT), ("dwFlags", ctypes.c_ulong)]

                hwnd = ctypes.windll.user32.GetForegroundWindow()
                monitor = ctypes.windll.user32.MonitorFromWindow(hwnd, 2)
                info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
                if monitor and ctypes.windll.user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                    work = info.rcWork
                    return work.left, work.top, work.right, work.bottom
            except Exception:
                pass
        return 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()

    def _build_ui(self, scale: float) -> None:
        pad_y = int(9 * scale)
        stripe_w = max(4, int(4 * scale))
        icon_size = max(32, int(38 * scale))
        progress_h = max(2, int(2.5 * scale))

        self.container = tk.Frame(self.root, bg=BG_COLOR, cursor="hand2")
        self.container.pack(fill="both", expand=True, padx=1, pady=1)

        # Left accent stripe (vertical colored border indicator)
        self.stripe = tk.Frame(self.container, bg=self.accent_color, width=stripe_w)
        self.stripe.pack(side="left", fill="y")

        # Category Emblem Badge (anti-aliased Pillow icon or high-contrast fallback)
        icon_img = render_category_icon(self.category_type, self.accent_color, size=icon_size)
        if icon_img and HAS_PIL:
            self.icon_photo = ImageTk.PhotoImage(icon_img)
            self.status = tk.Label(
                self.container,
                image=self.icon_photo,
                bg=BG_COLOR,
                bd=0,
                cursor="hand2",
            )
            self.status.pack(side="left", padx=(int(10 * scale), int(2 * scale)), pady=int(6 * scale))
        else:
            fallback_glyph = CATEGORY_GLYPHS.get(self.category_type, "✓")
            self.status = tk.Label(
                self.container,
                text=fallback_glyph,
                font=(FONT_FAMILY, 14, "bold"),
                fg=self.accent_color,
                bg=BG_COLOR,
                width=2,
                anchor="center",
            )
            self.status.pack(side="left", padx=(int(8 * scale), int(2 * scale)), fill="y")

        # Main content area
        self.content = tk.Frame(self.container, bg=BG_COLOR, padx=int(8 * scale), pady=pad_y)
        self.content.pack(side="left", fill="both", expand=True)

        # Top row: Eyebrow tag badge + Close button
        self.top_row = tk.Frame(self.content, bg=BG_COLOR)
        self.top_row.pack(fill="x")

        folder_upper = self.folder_disp.upper()
        if len(folder_upper) > 22:
            folder_upper = folder_upper[:20] + "…"
        tag_text = f"●  SORTED  ·  {folder_upper}"

        self.tag_lbl = tk.Label(
            self.top_row,
            text=tag_text,
            font=(FONT_FAMILY, 8, "bold"),
            fg=self.accent_color,
            bg=BG_COLOR,
            anchor="w",
        )
        self.tag_lbl.pack(side="left")

        # Close button with guaranteed minimum 24px hit target per RIBBON_SPEC.md
        hit_target = max(24, int(24 * scale))
        self.close_frame = tk.Frame(
            self.top_row,
            bg=BG_COLOR,
            width=hit_target,
            height=hit_target,
            cursor="hand2",
        )
        self.close_frame.pack_propagate(False)
        self.close_frame.pack(side="right")

        self.close_btn = tk.Label(
            self.close_frame,
            text="✕",
            font=(FONT_FAMILY, 9, "bold"),
            fg=CLOSE_COLOR,
            bg=BG_COLOR,
            cursor="hand2",
            anchor="center",
        )
        self.close_btn.pack(fill="both", expand=True)
        self.close_btn.bind("<Button-1>", lambda e: (self.close(), "break")[1])
        self.close_frame.bind("<Button-1>", lambda e: (self.close(), "break")[1])

        def on_close_enter(_e):
            self.close_btn.config(fg=CLOSE_HOVER, bg="#282F3D")
            self.close_frame.config(bg="#282F3D")

        def on_close_leave(_e):
            curr_bg = HOVER_BG if self.is_hovered else BG_COLOR
            self.close_btn.config(fg=CLOSE_COLOR, bg=curr_bg)
            self.close_frame.config(bg=curr_bg)

        self.close_btn.bind("<Enter>", on_close_enter)
        self.close_btn.bind("<Leave>", on_close_leave)
        self.close_frame.bind("<Enter>", on_close_enter)
        self.close_frame.bind("<Leave>", on_close_leave)

        # Middle row: Filename (Title)
        fname = self.dest_path.name
        if len(fname) > 36:
            fname = fname[:33] + "..."

        self.name_lbl = tk.Label(
            self.content,
            text=fname,
            font=(FONT_FAMILY, 10, "bold"),
            fg=TEXT_MAIN,
            bg=BG_COLOR,
            anchor="w",
        )
        self.name_lbl.pack(fill="x", pady=(2, 1))

        # Bottom row: Context subtitle & Explorer action hint
        action_text = f"Saved to {self.folder_disp}  ·  Click to reveal ›"
        if len(action_text) > 42:
            action_text = "Click to reveal in File Explorer ›"

        self.action_lbl = tk.Label(
            self.content,
            text=action_text,
            font=(FONT_FAMILY, 9),
            fg=TEXT_MUTED,
            bg=BG_COLOR,
            anchor="w",
        )
        self.action_lbl.pack(fill="x")

        # Bind hover and click events across all interactive surface widgets
        interactive_widgets = [
            self.container, self.content, self.top_row,
            self.tag_lbl, self.name_lbl, self.action_lbl,
            self.stripe, self.status
        ]
        for widget in interactive_widgets:
            widget.bind("<Button-1>", self.on_click)
            widget.bind("<Enter>", self.on_enter)
            widget.bind("<Leave>", self.on_leave)

        # Subtle progress track underlay & active timer progress bar
        self.progress_track = tk.Frame(self.container, bg="#181C25", height=progress_h)
        self.progress_track.place(x=stripe_w, rely=1.0, anchor="sw", relwidth=1.0)

        self.progress = tk.Frame(self.container, bg=self.accent_color, height=progress_h)
        self.progress.place(x=stripe_w, rely=1.0, anchor="sw", relwidth=1.0)

    def _animate_slide_in(
        self,
        start_x: int,
        target_x: int,
        target_y: int,
        duration_ms: float = 220.0,
    ) -> None:
        """Silky 60fps entrance slide using a cubic ease-out curve."""
        t0 = time.monotonic()

        def step() -> None:
            if self.is_closing:
                return
            now = time.monotonic()
            elapsed = (now - t0) * 1000.0
            p = min(1.0, max(0.0, elapsed / duration_ms))
            # Cubic ease-out: 1 - (1 - p)^3
            ease_out = 1.0 - (1.0 - p) ** 3
            curr_x = round(start_x + (target_x - start_x) * ease_out)
            try:
                self.root.geometry(f"{self.target_w}x{self.target_h}+{curr_x}+{target_y}")
                if p < 1.0:
                    self.slide_job = self.root.after(16, step)
                else:
                    self.root.geometry(f"{self.target_w}x{self.target_h}+{target_x}+{target_y}")
                    self.slide_job = None
                    self._schedule_dismiss()
            except tk.TclError:
                pass

        self.slide_job = self.root.after(10, step)

    def _schedule_dismiss(self) -> None:
        if self.dismiss_job:
            self.root.after_cancel(self.dismiss_job)
        self.dismiss_started = time.monotonic()
        self.dismiss_job = self.root.after(max(1, int(self.remaining_ms)), self.close)
        self._tick_progress()

    def _tick_progress(self) -> None:
        if self.progress_job:
            self.root.after_cancel(self.progress_job)
            self.progress_job = None
        if self.is_hovered or self.is_closing:
            return
        elapsed_ms = (time.monotonic() - self.dismiss_started) * 1000.0
        fraction = max(0.0, min(1.0, (self.remaining_ms - elapsed_ms) / AUTO_DISMISS_MS))
        try:
            self.progress.place_configure(relwidth=fraction)
            if fraction > 0:
                self.progress_job = self.root.after(20, self._tick_progress)
        except tk.TclError:
            pass

    def on_enter(self, _event=None) -> None:
        if self.is_hovered or self.is_closing:
            return
        self.is_hovered = True
        if self.dismiss_job:
            elapsed_ms = (time.monotonic() - self.dismiss_started) * 1000.0
            self.remaining_ms = max(1.0, self.remaining_ms - elapsed_ms)
            self.root.after_cancel(self.dismiss_job)
            self.dismiss_job = None
        if self.progress_job:
            self.root.after_cancel(self.progress_job)
            self.progress_job = None
        self._set_hover_state(True)

    def on_leave(self, _event=None) -> None:
        if self.is_closing:
            return
        self.root.after(20, self._resume_if_pointer_left)

    def _resume_if_pointer_left(self) -> None:
        if self.is_closing:
            return
        try:
            pointer_widget = self.root.winfo_containing(
                self.root.winfo_pointerx(), self.root.winfo_pointery()
            )
            if pointer_widget is not None and pointer_widget.winfo_toplevel() == self.root:
                return
        except Exception:
            pass
        self.is_hovered = False
        self._set_hover_state(False)
        self._schedule_dismiss()

    def _set_hover_state(self, hovered: bool) -> None:
        bg = HOVER_BG if hovered else BG_COLOR
        border = self.hover_border if hovered else self.normal_border
        action_col = ACTION_COLOR if hovered else TEXT_MUTED
        bar_col = self.accent_glow if hovered else self.accent_color

        self.root.config(bg=border)
        self.container.config(bg=bg)
        self.content.config(bg=bg)
        self.top_row.config(bg=bg)
        self.tag_lbl.config(bg=bg)
        self.name_lbl.config(bg=bg)
        self.action_lbl.config(bg=bg, fg=action_col)
        self.status.config(bg=bg)
        self.close_frame.config(bg=bg)
        self.close_btn.config(bg=bg)
        self.progress.config(bg=bar_col)

    def on_click(self, _event=None) -> None:
        """Trigger reveal in File Explorer and play a fast exit dismiss animation."""
        try:
            if sys.platform == "win32":
                dest_str = str(self.dest_path)
                if not self.dest_path.exists():
                    if self.dest_path.parent.exists():
                        os.startfile(str(self.dest_path.parent))
                elif "," in dest_str:
                    # Windows explorer.exe /select splits on commas. PowerShell handles it safely.
                    cmd = (
                        "Start-Process explorer.exe -ArgumentList "
                        f"('/select,\"' + [System.IO.Path]::GetFullPath('{dest_str}') + '\"')"
                    )
                    subprocess.Popen(
                        ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", cmd],
                        creationflags=subprocess.CREATE_NO_WINDOW
                    )
                else:
                    subprocess.Popen(["explorer.exe", f"/select,{dest_str}"])
            else:
                subprocess.Popen(["open", "-R", str(self.dest_path)])
        except Exception as exc:
            print(f"Failed to open explorer: {exc}", file=sys.stderr)
        finally:
            self.close()

    def close(self) -> None:
        """Dismiss the ribbon with a snappy slide-out exit animation."""
        if self.is_closing:
            return
        self.is_closing = True

        if self.dismiss_job:
            self.root.after_cancel(self.dismiss_job)
            self.dismiss_job = None
        if self.progress_job:
            self.root.after_cancel(self.progress_job)
            self.progress_job = None
        if self.slide_job:
            self.root.after_cancel(self.slide_job)
            self.slide_job = None

        release_slot(self.slot, self.pid)

        if not self.enable_animation:
            try:
                self.root.destroy()
            except tk.TclError:
                pass
            return

        try:
            curr_x = self.root.winfo_x()
            curr_y = self.root.winfo_y()
        except Exception:
            curr_x = self.target_x
            curr_y = self.target_y

        end_x = curr_x + self.slide_distance
        t0 = time.monotonic()
        duration_ms = 160.0

        def out_step() -> None:
            now = time.monotonic()
            elapsed = (now - t0) * 1000.0
            p = min(1.0, max(0.0, elapsed / duration_ms))
            # Quadratic ease-in: p^2
            ease_in = p * p
            x = round(curr_x + (end_x - curr_x) * ease_in)
            try:
                self.root.geometry(f"{self.target_w}x{self.target_h}+{x}+{curr_y}")
                if p < 1.0:
                    self.slide_job = self.root.after(16, out_step)
                else:
                    self.root.destroy()
            except tk.TclError:
                pass

        out_step()

    def run(self) -> None:
        try:
            self.root.mainloop()
        finally:
            release_slot(self.slot, self.pid)


def notify_file_moved(dest_path: str | Path, downloads_root: str | Path | None = None) -> None:
    """Spawn the ribbon popup non-blocking in a separate detached process."""
    dest = Path(dest_path).resolve()

    # Rate check: skip spawning if all notification slots are already occupied
    try:
        if SLOTS_FILE.exists():
            with open(SLOTS_FILE, "r", encoding="utf-8") as f:
                slots = json.load(f)
            alive = sum(1 for p in slots.values() if is_pid_alive(p))
            if alive >= MAX_SLOTS:
                return
    except Exception:
        pass

    args = [str(PYTHON), str(Path(__file__).resolve()), str(dest)]
    if downloads_root:
        args.append(str(downloads_root))

    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        subprocess.Popen(args, creationflags=flags)
    except Exception as exc:
        print(f"Failed to spawn ribbon notification: {exc}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description="Show a top-right download ribbon notification.")
    parser.add_argument("dest_path", nargs="?", default="", help="Path to the destination file.")
    parser.add_argument("downloads_root", nargs="?", default="", help="Root Downloads folder.")
    parser.add_argument("--test", action="store_true", help="Show a test notification.")
    parser.add_argument("--no-anim", action="store_true", help="Disable slide animations.")
    args = parser.parse_args()

    enable_anim = not args.no_anim

    if args.test or not args.dest_path:
        sample = Path.home() / "Downloads" / "3D Models" / "STEP" / "Flanged Mace Iteration 1 STEP.step"
        popup = RibbonPopup(sample, enable_animation=enable_anim)
    else:
        dest = Path(args.dest_path)
        dl_root = Path(args.downloads_root) if args.downloads_root else None
        popup = RibbonPopup(dest, dl_root, enable_animation=enable_anim)

    popup.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
