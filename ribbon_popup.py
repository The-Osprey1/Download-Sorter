"""Top-right interactive ribbon notification popup for download-sorter.

Displays a modern floating banner in the top-right corner when a file is sorted.
Features:
- Integrated with the shared design tokens (suite_theme.py) (`suite_theme.py`).
- Crisp High-DPI rendering on Windows (fixes blurriness on 125%/150%/175%/200% displays).
- Dynamic category-based accent colors (3D/CAD, CNC, Slicer, PDFs, Code, Media, Installers).
- Modern anti-aliased vector icon emblems for all major file categories.
- Silky 60fps cubic ease-out entrance slide and snappy ease-in exit slide animations.
- Smooth 50fps progress countdown bar with pause-on-hover and glow highlight.
- Tinted category-reactive border and Windows 11 rounded corners + native drop shadow.
- Atomic slot synchronization for multi-file downloads using Windows file locking.
- Automatic vertical stacking when multiple files are downloaded at once.
- Safe File Explorer reveal supporting commas and special characters in paths.
- One-click Undo of just this file, next to "Jump to File".
- Folder names are fitted to the space left by measuring them with the ribbon's font,
  so the buttons are never pushed out of view.
- Auto-dismisses after timeout (pauses on hover, dismisses on close button).
- Rate-capped to avoid spamming the screen during large bulk operations.
"""

from __future__ import annotations

import argparse
import about
from contextlib import contextmanager
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid

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
from tkinter import font as tkfont

try:
    from PIL import Image, ImageDraw, ImageTk
    HAS_PIL = True
except Exception:
    HAS_PIL = False

ROOT = Path(__file__).resolve().parent
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
if not PYTHON.exists():
    PYTHON = Path(sys.executable)

# Load the shared design tokens if available, else graceful defaults
try:
    from suite_theme import ACCENTS, COLORS, RIBBON, TYPE
    BG_COLOR = COLORS.get("surface", "#12151C")
    SURFACE_RAISED = COLORS.get("surfaceRaised", "#171B24")
    SURFACE_HOVER = COLORS.get("surfaceHover", "#1D222C")
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
    SURFACE_RAISED = "#171B24"
    SURFACE_HOVER = "#1D222C"
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
RIBBON_DISMISS_FILE = Path(tempfile.gettempdir()) / "download_sorter_ribbon_dismiss.json"

# Big batches (a zip unpacking, a browser's "download all"): the first few files
# get their own ribbon, then one ribbon counts the rest instead of a stack of
# popups. The background sorter writes the running totals to BATCH_FILE and the
# batch ribbon reads them while it is open.
BATCH_FILE = Path(tempfile.gettempdir()) / "download_sorter_ribbon_batch.json"
BATCH_SINGLE_LIMIT = 3       # files in a burst that still get their own ribbon
BATCH_GAP_SECONDS = 4.0      # this long without a new file ends the burst
BATCH_IDLE_CLOSE_MS = 6000   # the batch ribbon closes this long after the last file
BATCH_POLL_MS = 300

# Category colors and the file-kind rules are shared with the Qt window (ui_categories.py).
from ui_categories import CATEGORY_COLORS, category_info  # noqa: E402,F401

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


ELLIPSIS = "…"
COLLAPSED = "...\\"   # stands for the leading folders of a path that is too long to show
HELD_TEXTS = ("Holding in Downloads (Bambu Studio)", "Holding in Downloads", "Held in Downloads")


def ellipsize_middle(text: str, max_px: int, measure, keep_head: int = 0, ellipsis: str = ELLIPSIS) -> str:
    """Shorten ``text`` in the middle until ``measure(result) <= max_px``.

    ``measure`` is ``tkinter.font.Font.measure`` of the font that draws the text
    (or any function from text to pixels), so the result fits however wide the
    letters are. The first ``keep_head`` characters always stay, for a fixed
    start such as ``...\\``. Text that already fits is returned as it is.
    """
    if measure(text) <= max_px:
        return text
    head, body = text[:keep_head], text[keep_head:]

    def shortened(kept: int) -> str:
        front, back = (kept + 1) // 2, kept // 2
        return head + body[:front] + ellipsis + body[len(body) - back:]

    best = -1
    low, high = 0, len(body) - 1
    while low <= high:  # the most characters of the body that still fit
        middle = (low + high) // 2
        if measure(shortened(middle)) <= max_px:
            best, low = middle, middle + 1
        else:
            high = middle - 1
    return shortened(best) if best >= 0 else ellipsis


def folder_text_candidates(folder_disp: str, prefix: str = "Saved to ") -> list[str]:
    """Ways to say where a file went, longest first.

    ``Documents\\Invoices & Receipts`` gives ``Saved to Documents\\Invoices & Receipts``,
    then ``Saved to ...\\Invoices & Receipts`` (the leading folders collapse into
    ``...\\``), then ``...\\Invoices & Receipts`` without "Saved to". The folder the
    file is in is the last thing to be shortened.
    """
    parts = [part for part in re.split(r"[\\/]+", folder_disp) if part]
    if not parts:
        return [prefix.rstrip()]
    candidates = [prefix + "\\".join(parts)]
    candidates += [prefix + COLLAPSED + "\\".join(parts[start:]) for start in range(1, len(parts))]
    candidates.append(COLLAPSED + parts[-1] if len(parts) > 1 else parts[-1])
    return candidates


def fit_text(candidates, max_px: int, measure, keep_head: int = 0) -> str:
    """The first of ``candidates`` (longest first) that fits, else the last one shortened in the middle."""
    candidates = list(candidates)
    for text in candidates:
        if measure(text) <= max_px:
            return text
    return ellipsize_middle(candidates[-1], max_px, measure, keep_head)


def fit_folder_text(folder_disp: str, max_px: int, measure, prefix: str = "Saved to ") -> str:
    """"Saved to <folder>", as much of it as fits in ``max_px`` (see folder_text_candidates)."""
    candidates = folder_text_candidates(folder_disp, prefix)
    return fit_text(candidates, max_px, measure, len(COLLAPSED) if candidates[-1].startswith(COLLAPSED) else 0)


def add_to_recent_docs(path: str | Path) -> None:
    """Register the file in Windows Recent Documents so it immediately appears in all file upload dialogs."""
    if sys.platform == "win32":
        try:
            import ctypes
            SHARD_PATHW = 0x00000003
            ctypes.windll.shell32.SHAddToRecentDocs(SHARD_PATHW, str(Path(path).resolve()))
        except Exception:
            pass


def get_category_info(dest_path: Path, folder_disp: str) -> tuple[str, str]:
    """Return (category_type, accent_color) based on file extension and folder context."""
    return category_info(dest_path, folder_disp)


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
        from ctypes import wintypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            exit_code = wintypes.DWORD()
            res = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            ctypes.windll.kernel32.CloseHandle(handle)
            # STILL_ACTIVE is 259
            return bool(res and exit_code.value == 259)
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


def dismiss_active_ribbons(
    dest_path: str | Path | None = None,
    paths: list[str | Path] | None = None,
) -> None:
    """Request active ribbon popups (for one file, several files, or all) to dismiss immediately."""
    data = {
        "timestamp": time.time(),
        "path": str(Path(dest_path).resolve()) if dest_path else None,
        "paths": [str(Path(p).resolve()) for p in paths] if paths else None,
    }
    tmp = RIBBON_DISMISS_FILE.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        tmp.replace(RIBBON_DISMISS_FILE)
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
        is_held: bool = False,
        planned_dest: Path | None = None,
    ) -> None:
        self.is_held = is_held
        self.planned_dest = Path(planned_dest).resolve() if planned_dest else None
        if not self.is_held and Path(dest_path).suffix.lower() == ".3mf":
            try:
                import hold_manager
                if hold_manager.is_held(dest_path):
                    self.is_held = True
                    rec = hold_manager.get_held(dest_path)
                    if rec and rec.get("planned_dest"):
                        self.planned_dest = Path(rec["planned_dest"])
            except Exception:
                pass
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
        add_to_recent_docs(self.dest_path)
        try:
            from quick_switcher import record_recent_file
            record_recent_file(self.dest_path, self.downloads_root)
        except Exception:
            pass

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
        self.start_time = time.time()
        self.dismiss_signal_job: str | None = None

        self._build_ui(self.scale)

        if self.enable_animation:
            start_x = self.target_x + self.slide_distance
            self.root.geometry(f"{width}x{height}+{start_x}+{self.target_y}")
            self._animate_slide_in(start_x, self.target_x, self.target_y, duration_ms=220)
        else:
            self.root.geometry(f"{width}x{height}+{self.target_x}+{self.target_y}")
            self._schedule_dismiss()

        # Poll external dismiss signals (e.g. file injected in Quick Switcher)
        try:
            self.dismiss_signal_job = self.root.after(100, self._check_dismiss_signal)
        except tk.TclError:
            pass

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
        icon_img = None
        if self.dest_path.suffix.lower() == ".3mf" and HAS_PIL:
            try:
                from gcode_peek import parse_3mf
                import io
                meta_3mf = parse_3mf(self.dest_path)
                if meta_3mf.get("has_thumbnail") and meta_3mf.get("thumbnail_data"):
                    timg = Image.open(io.BytesIO(meta_3mf["thumbnail_data"]))
                    timg.thumbnail((icon_size, icon_size), Image.Resampling.LANCZOS)
                    icon_img = timg
            except Exception:
                pass
        if icon_img is None:
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
            status_padx = (int(10 * scale), int(2 * scale))
            self.status.pack(side="left", padx=status_padx, pady=int(6 * scale))
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
            status_padx = (int(8 * scale), int(2 * scale))
            self.status.pack(side="left", padx=status_padx, fill="y")

        # Main content area
        content_padx = int(8 * scale)
        self.content = tk.Frame(self.container, bg=BG_COLOR, padx=content_padx, pady=pad_y)
        self.content.pack(side="left", fill="both", expand=True)

        # Tk has not laid anything out yet, so work out how wide the bottom row will be
        # from what is packed to its left: the frame around the container (1 px each
        # side), the stripe, the icon with its padding, and the content padding.
        row_width = self.target_w - 2 - stripe_w - self.status.winfo_reqwidth() - sum(status_padx) - 2 * content_padx

        # Top row: Eyebrow tag badge + Close button
        self.top_row = tk.Frame(self.content, bg=BG_COLOR)
        self.top_row.pack(fill="x")

        folder_upper = self.folder_disp.upper()
        if len(folder_upper) > 22:
            folder_upper = folder_upper[:20] + "…"
        if self.is_held:
            tag_text = f"●  3MF HOLD  ·  {folder_upper}"
        else:
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
            self.close_btn.config(fg=CLOSE_HOVER, bg=SURFACE_HOVER)
            self.close_frame.config(bg=SURFACE_HOVER)

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

        # Bottom row: where the file went on the left, the action pills on the right
        self.bottom_row = tk.Frame(self.content, bg=BG_COLOR)
        self.bottom_row.pack(fill="x", pady=(1, 0))

        # The text is measured with this same font object, so it is drawn as wide as it measured
        self._action_font = tkfont.Font(root=self.root, family=FONT_FAMILY, size=9)
        self.action_lbl = tk.Label(
            self.bottom_row,
            text=f"Saved to {self.folder_disp}",
            font=self._action_font,
            fg=TEXT_MUTED,
            bg=BG_COLOR,
            anchor="w",
        )

        # Explicit interactive "Jump to File" pill button (or File now / Keep here when held)
        self.jump_btn = tk.Label(
            self.bottom_row,
            text="Jump to File ›",
            font=(FONT_FAMILY, 8, "bold"),
            fg=ACTION_COLOR,
            bg=SURFACE_RAISED,
            cursor="hand2",
            padx=int(6 * scale),
            pady=int(1 * scale),
        )
        pill_gap = int(3 * scale)
        if self.is_held:
            self.file_now_btn = tk.Label(
                self.bottom_row,
                text="File now",
                font=(FONT_FAMILY, 8, "bold"),
                fg=COLORS.get("canvas", "#0B0D12"),
                bg=self.accent_color,
                cursor="hand2",
                padx=int(5 * scale),
                pady=int(1 * scale),
            )
            self.file_now_btn.pack(side="right", padx=(pill_gap, 0))
            self.file_now_btn.bind("<Button-1>", lambda e: (self._on_file_now(), "break")[1])
            self.file_now_btn.bind("<Enter>", self.on_enter)
            self.file_now_btn.bind("<Leave>", self.on_leave)

            self.keep_here_btn = tk.Label(
                self.bottom_row,
                text="Keep here",
                font=(FONT_FAMILY, 8, "bold"),
                fg=TEXT_MAIN,
                bg=SURFACE_RAISED,
                cursor="hand2",
                padx=int(5 * scale),
                pady=int(1 * scale),
            )
            self.keep_here_btn.pack(side="right")
            self.keep_here_btn.bind("<Button-1>", lambda e: (self._on_keep_here(), "break")[1])
            self.keep_here_btn.bind("<Enter>", self.on_enter)
            self.keep_here_btn.bind("<Leave>", self.on_leave)
            pills = [self.file_now_btn, self.keep_here_btn]
        else:
            # Undo sits left of "Jump to File": the file is already filed, so this puts it back
            self.undo_btn = tk.Label(
                self.bottom_row,
                text="Undo",
                font=(FONT_FAMILY, 8, "bold"),
                fg=TEXT_MAIN,
                bg=SURFACE_RAISED,
                cursor="hand2",
                padx=int(6 * scale),
                pady=int(1 * scale),
            )
            self.jump_btn.pack(side="right")
            self.undo_btn.pack(side="right", padx=(0, pill_gap))
            self.undo_btn.bind("<Button-1>", lambda e: (self._on_undo(), "break")[1])
            self.undo_btn.bind("<Enter>", self.on_enter)
            self.undo_btn.bind("<Leave>", self.on_leave)
            pills = [self.jump_btn, self.undo_btn]
            # Quick Route: send the file on to one of your hub projects (Projects page).
            self.route_btn = None
            self._route_targets = self._route_targets_available()
            if self._route_targets:
                self.route_btn = tk.Label(
                    self.bottom_row,
                    text="Route ›",
                    font=(FONT_FAMILY, 8, "bold"),
                    fg=ACTION_COLOR,
                    bg=SURFACE_RAISED,
                    cursor="hand2",
                    padx=int(6 * scale),
                    pady=int(1 * scale),
                )
                self.route_btn.pack(side="right", padx=(0, pill_gap))
                self.route_btn.bind("<Button-1>", lambda e: (self._show_route_menu(e), "break")[1])
                self.route_btn.bind("<Enter>", self.on_enter)
                self.route_btn.bind("<Leave>", self.on_leave)
                pills.append(self.route_btn)
            # A sorted .3mf can be opened in Bambu Studio from here: Bambu then remembers
            # the sorted path in its Recently Opened list from the start.
            self.open_bambu_btn = None
            if self.dest_path.suffix.lower() == ".3mf" and self._bambu_available():
                self.open_bambu_btn = tk.Label(
                    self.bottom_row,
                    text="Open in Bambu ›",
                    font=(FONT_FAMILY, 8, "bold"),
                    fg=ACTION_COLOR,
                    bg=SURFACE_RAISED,
                    cursor="hand2",
                    padx=int(6 * scale),
                    pady=int(1 * scale),
                )
                self.open_bambu_btn.pack(side="right", padx=(0, pill_gap))
                self.open_bambu_btn.bind("<Button-1>", lambda e: (self._on_open_in_bambu(), "break")[1])
                self.open_bambu_btn.bind("<Enter>", self.on_enter)
                self.open_bambu_btn.bind("<Leave>", self.on_leave)
                pills.append(self.open_bambu_btn)

        # The text goes in last: Tk lays out in packing order, so a long folder name can
        # only squeeze the text and never push a pill out of view.
        self.action_lbl.pack(side="left")
        self._fit_action_text(row_width, pills, pill_gap, scale)

        # Bind hover and click events across all interactive surface widgets
        interactive_widgets = [
            self.container, self.content, self.top_row,
            self.tag_lbl, self.name_lbl, self.bottom_row,
            self.action_lbl, self.jump_btn, self.stripe, self.status
        ]
        for widget in interactive_widgets:
            widget.bind("<Button-1>", self.on_click)
            widget.bind("<Enter>", self.on_enter)
            widget.bind("<Leave>", self.on_leave)

        # Subtle progress track underlay & active timer progress bar
        self.progress_track = tk.Frame(self.container, bg=SURFACE_RAISED, height=progress_h)
        self.progress_track.place(x=stripe_w, rely=1.0, anchor="sw", relwidth=1.0)

        self.progress = tk.Frame(self.container, bg=self.accent_color, height=progress_h)
        self.progress.place(x=stripe_w, rely=1.0, anchor="sw", relwidth=1.0)

    def _fit_action_text(self, row_width: int, pills: list, pill_gap: int, scale: float) -> None:
        """Show as much of "Saved to <folder>" as the pills leave room for.

        Widths come from Tk (``Font.measure`` for the text, ``winfo_reqwidth`` for the
        pills), not from counting characters, so it holds at every display scale.
        """
        measure = self._action_font.measure
        text_padding = self.action_lbl.winfo_reqwidth() - measure(self.action_lbl.cget("text"))
        room = (
            row_width
            - sum(pill.winfo_reqwidth() for pill in pills)
            - pill_gap * max(0, len(pills) - 1)  # between the pills
            - int(6 * scale)    # between the text and the first pill
            - text_padding
        )
        if self.is_held:
            text = fit_text(HELD_TEXTS, room, measure)
        else:
            text = fit_folder_text(self.folder_disp, room, measure)
        self.action_lbl.config(text=text)

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
        btn_bg = SURFACE_HOVER if hovered else SURFACE_RAISED
        btn_fg = self.accent_color if hovered else ACTION_COLOR

        self.root.config(bg=border)
        self.container.config(bg=bg)
        self.content.config(bg=bg)
        self.top_row.config(bg=bg)
        self.tag_lbl.config(bg=bg)
        self.name_lbl.config(bg=bg)
        self.bottom_row.config(bg=bg)
        self.action_lbl.config(bg=bg, fg=action_col)
        self.jump_btn.config(bg=btn_bg, fg=btn_fg)
        if hasattr(self, "keep_here_btn"):
            self.keep_here_btn.config(bg=btn_bg)
        if hasattr(self, "undo_btn"):
            self.undo_btn.config(bg=btn_bg)
        self.status.config(bg=bg)
        self.close_frame.config(bg=bg)
        self.close_btn.config(bg=bg)
        self.progress.config(bg=bar_col)

    def _on_file_now(self) -> None:
        try:
            import hold_manager
            hold_manager.file_now(self.dest_path)
        except Exception as exc:
            logging.debug("File now failed: %s", exc)
        self.close()

    def _on_keep_here(self) -> None:
        try:
            import hold_manager
            hold_manager.keep_here(self.dest_path)
        except Exception as exc:
            logging.debug("Keep here failed: %s", exc)
        self.close()

    def _on_undo(self) -> None:
        """Put this one file back where it came from, then close.

        Another process does the work (``--undo --file``) and shows a short ribbon
        when it is done, so this one can close at once. A second click while it
        slides out does nothing, so the file is not undone twice.
        """
        if self.is_closing:
            return
        _spawn(_app_command("--undo", "--file", str(self.dest_path), "--quiet-toast"))
        self.close()

    @staticmethod
    def _bambu_available() -> bool:
        try:
            import bambu_recent
            return bambu_recent.bambu_exe() is not None
        except Exception:
            return False

    @staticmethod
    def _route_targets_available() -> list:
        try:
            import projects_hub
            return projects_hub.route_targets()[:8]
        except Exception:
            return []

    def _show_route_menu(self, event) -> None:
        """A small menu of hub projects; picking one moves the file into that project."""
        menu = tk.Menu(self.root, tearoff=0)
        for project in self._route_targets:
            menu.add_command(label=f"Route to {project.get('name')}",
                             command=lambda p=project: self._route_to(p))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _route_to(self, project: dict) -> None:
        try:
            import projects_hub
            target = projects_hub.route_file(self.dest_path, project)
            self.dest_path = target
        except Exception as exc:
            print(f"Could not route to {project.get('name')}: {exc}", file=sys.stderr)
        finally:
            self.close()

    def _on_open_in_bambu(self) -> None:
        try:
            import bambu_recent
            bambu_recent.open_in_bambu(self.dest_path)
        except Exception as exc:
            print(f"Could not open in Bambu Studio: {exc}", file=sys.stderr)
        finally:
            self.close()

    def on_click(self, _event=None) -> None:
        """Trigger reveal in File Explorer and play a fast exit dismiss animation."""
        try:
            if sys.platform == "win32":
                dest_str = os.path.abspath(str(self.dest_path))
                if not self.dest_path.exists():
                    if self.dest_path.parent.exists():
                        os.startfile(str(self.dest_path.parent))
                    elif self.downloads_root.exists():
                        os.startfile(str(self.downloads_root))
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
                    # Pass unquoted string command line so CreateProcess does not wrap /select
                    cmd = f'explorer.exe /select,"{dest_str}"'
                    subprocess.Popen(cmd)
            else:
                subprocess.Popen(["open", "-R", str(self.dest_path)])
        except Exception as exc:
            print(f"Failed to open explorer: {exc}", file=sys.stderr)
        finally:
            self.close()

    def _check_dismiss_signal(self) -> None:
        if self.is_closing:
            return
        if RIBBON_DISMISS_FILE.exists():
            try:
                with open(RIBBON_DISMISS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                sig_time = float(data.get("timestamp", 0))
                if sig_time >= self.start_time:
                    targets = data.get("paths") or ([data["path"]] if data.get("path") else [])
                    mine = self.dest_path.resolve()
                    if not targets or any(Path(t).resolve() == mine for t in targets):
                        self.close()
                        return
            except Exception:
                pass
        try:
            self.dismiss_signal_job = self.root.after(100, self._check_dismiss_signal)
        except tk.TclError:
            pass

    def close(self) -> None:
        """Dismiss the ribbon with a snappy slide-out exit animation."""
        if self.is_closing:
            return
        self.is_closing = True

        if self.dismiss_signal_job:
            try:
                self.root.after_cancel(self.dismiss_signal_job)
            except Exception:
                pass
            self.dismiss_signal_job = None
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


def _ribbon_command(*args: str) -> list[str]:
    """The command that shows a ribbon in its own process.

    In the installed app this file is bundled inside DownloadSorter.exe, so the
    ribbon is started through the app's own ``--ribbon`` command. (Passing this
    file's path to the .exe, as before, made it quit with "Unknown command", so
    no ribbon ever appeared.)
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, "--ribbon", *args]
    return [str(PYTHON), str(Path(__file__).resolve()), *args]


def _app_command(*args: str) -> list[str]:
    """A Download Sorter command (such as ``--undo``) started from a ribbon."""
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    return [str(PYTHON), str(ROOT / "main.py"), *args]


def _spawn(args: list[str]) -> None:
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        subprocess.Popen(args, creationflags=flags)
    except Exception as exc:
        logging.debug("Failed to start ribbon process: %s", exc)


def notify_file_held(
    dest_path: str | Path,
    planned_dest: str | Path,
    meta: dict | None = None,
    downloads_root: str | Path | None = None,
) -> None:
    """Spawn the ribbon popup in held mode."""
    dest = Path(dest_path).resolve()
    planned = Path(planned_dest).resolve()
    extra = [str(Path(downloads_root).resolve())] if downloads_root else []
    _spawn(_ribbon_command(str(dest), *extra, "--held", "--planned", str(planned)))


_burst_lock = threading.Lock()
_burst: dict = {}


def _folder_label(dest: Path, downloads_root: Path | None) -> str:
    """The folder a file went to, as shown to the user (relative to Downloads)."""
    if downloads_root:
        try:
            rel = dest.relative_to(Path(downloads_root).resolve())
            return str(rel.parent) if rel.parent != Path(".") else "Downloads"
        except ValueError:
            pass
    return dest.parent.name or str(dest.parent)


def record_burst(
    dest: Path,
    downloads_root: Path | None,
    batch_id: str | None = None,
    now: float | None = None,
) -> tuple[str, dict]:
    """Count a filed file into the current burst.

    Returns ("single", state) while the burst is small enough for one ribbon per
    file, then ("batch", state). A burst ends after BATCH_GAP_SECONDS without a
    new file.
    """
    now = time.time() if now is None else now
    with _burst_lock:
        if not _burst or now - float(_burst.get("last", 0)) > BATCH_GAP_SECONDS:
            _burst.clear()
            _burst.update({
                "id": uuid.uuid4().hex[:8], "started": now, "last": now, "count": 0,
                "folders": {}, "batches": [], "singles": [], "ribbon": False,
                "root": str(Path(downloads_root).resolve()) if downloads_root else "",
            })
        _burst["last"] = now
        _burst["count"] += 1
        label = _folder_label(dest, downloads_root)
        _burst["folders"][label] = _burst["folders"].get(label, 0) + 1
        # "hold_release" is shared by every released 3MF hold, so undoing it
        # would reach far beyond this batch.
        if batch_id and batch_id != "hold_release" and batch_id not in _burst["batches"]:
            _burst["batches"].append(batch_id)
        if _burst["count"] <= BATCH_SINGLE_LIMIT:
            _burst["singles"].append(str(dest))
            return "single", json.loads(json.dumps(_burst))
        first_batch_file = not _burst["ribbon"]
        _burst["ribbon"] = True
        state = json.loads(json.dumps(_burst))
        state["first_batch_file"] = first_batch_file
        return "batch", state


def _write_batch_state(state: dict) -> None:
    tmp = BATCH_FILE.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(state), encoding="utf-8")
        tmp.replace(BATCH_FILE)
    except Exception:
        logging.debug("Could not write the batch ribbon state", exc_info=True)


def read_batch_state() -> dict:
    try:
        data = json.loads(BATCH_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def batch_summary(state: dict, max_folders: int = 3) -> tuple[str, str]:
    """Title and message for the batch ribbon, e.g. ("Filed 37 files", "Images 25 · Documents 12")."""
    count = int(state.get("count", 0))
    title = f"Filed {count} file{'s' if count != 1 else ''}"
    folders = sorted((state.get("folders") or {}).items(), key=lambda item: (-item[1], item[0]))
    parts = [f"{name} {n}" for name, n in folders[:max_folders]]
    if len(folders) > max_folders:
        parts.append(f"+{len(folders) - max_folders} more")
    return title, " · ".join(parts)


def notify_file_moved(
    dest_path: str | Path,
    downloads_root: str | Path | None = None,
    batch_id: str | None = None,
) -> None:
    """Show that a file was filed, without flooding the screen.

    The first few files of a burst each get their own ribbon. From then on one
    batch ribbon counts the whole burst ("Filed 37 files: Images 25, ...") and
    the single ribbons of that burst are closed.
    """
    dest = Path(dest_path).resolve()
    root = Path(downloads_root).resolve() if downloads_root else None
    kind, state = record_burst(dest, root, batch_id)

    if kind == "batch":
        _write_batch_state(state)
        if state.get("first_batch_file"):
            dismiss_active_ribbons(paths=state.get("singles") or None)
            _spawn(_ribbon_command("--batch"))
        return

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

    _spawn(_ribbon_command(str(dest), *([str(root)] if root else [])))


class SummaryRibbonPopup:
    def __init__(
        self,
        title: str,
        message: str,
        accent: str = DEFAULT_ACCENT,
        timeout_ms: int = 5000,
        enable_animation: bool = True,
    ) -> None:
        self.title = title
        self.message = message
        self.accent_color = accent
        self.timeout_ms = max(500, timeout_ms)
        self.enable_animation = enable_animation
        self.pid = os.getpid()
        self.slot = claim_slot(self.pid)

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
                pref = ctypes.c_int(2)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref)
                )
                class MARGINS(ctypes.Structure):
                    _fields_ = [("left", ctypes.c_int), ("right", ctypes.c_int),
                                ("top", ctypes.c_int), ("bottom", ctypes.c_int)]
                m = MARGINS(1, 1, 1, 1)
                ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(m))
            except Exception:
                pass

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

        self.slide_distance = round(140 * self.scale)
        self.slide_job: str | None = None
        self.dismiss_job: str | None = None
        self.progress_job: str | None = None
        self.remaining_ms = float(self.timeout_ms)
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

        self.stripe = tk.Frame(self.container, bg=self.accent_color, width=stripe_w)
        self.stripe.pack(side="left", fill="y")

        icon_img = render_category_icon("other", self.accent_color, size=icon_size) if HAS_PIL else None
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
            fallback_glyph = "↺" if "undo" in self.title.lower() else "✓"
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

        self.content = tk.Frame(self.container, bg=BG_COLOR, padx=int(8 * scale), pady=pad_y)
        self.content.pack(side="left", fill="both", expand=True)

        self.top_row = tk.Frame(self.content, bg=BG_COLOR)
        self.top_row.pack(fill="x")

        self.tag_lbl = tk.Label(
            self.top_row,
            text=f"●  {about.APP_NAME.upper()}",
            font=(FONT_FAMILY, 8, "bold"),
            fg=self.accent_color,
            bg=BG_COLOR,
            anchor="w",
        )
        self.tag_lbl.pack(side="left")

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
            self.close_btn.config(fg=CLOSE_HOVER, bg=SURFACE_HOVER)
            self.close_frame.config(bg=SURFACE_HOVER)

        def on_close_leave(_e):
            curr_bg = HOVER_BG if self.is_hovered else BG_COLOR
            self.close_btn.config(fg=CLOSE_COLOR, bg=curr_bg)
            self.close_frame.config(bg=curr_bg)

        self.close_btn.bind("<Enter>", on_close_enter)
        self.close_btn.bind("<Leave>", on_close_leave)
        self.close_frame.bind("<Enter>", on_close_enter)
        self.close_frame.bind("<Leave>", on_close_leave)

        title_text = self.title
        if len(title_text) > 36:
            title_text = title_text[:33] + "..."
        self.title_lbl = tk.Label(
            self.content,
            text=title_text,
            font=(FONT_FAMILY, 10, "bold"),
            fg=TEXT_MAIN,
            bg=BG_COLOR,
            anchor="w",
        )
        self.title_lbl.pack(fill="x", pady=(2, 1))

        msg_text = self.message
        if len(msg_text) > 48:
            msg_text = msg_text[:45] + "…"
        self.msg_lbl = tk.Label(
            self.content,
            text=msg_text,
            font=(FONT_FAMILY, 9),
            fg=TEXT_MUTED,
            bg=BG_COLOR,
            anchor="w",
        )
        self.msg_lbl.pack(fill="x", pady=(1, 0))

        interactive_widgets = [
            self.container, self.content, self.top_row,
            self.tag_lbl, self.title_lbl, self.msg_lbl,
            self.stripe, self.status,
        ]
        for widget in interactive_widgets:
            widget.bind("<Button-1>", lambda _e: self.close())
            widget.bind("<Enter>", self.on_enter)
            widget.bind("<Leave>", self.on_leave)

        self.progress_track = tk.Frame(self.container, bg=SURFACE_RAISED, height=progress_h)
        self.progress_track.place(x=stripe_w, rely=1.0, anchor="sw", relwidth=1.0)

        self.progress = tk.Frame(self.container, bg=self.accent_color, height=progress_h)
        self.progress.place(x=stripe_w, rely=1.0, anchor="sw", relwidth=1.0)

    def _animate_slide_in(self, start_x: int, target_x: int, target_y: int, duration_ms: float = 220.0) -> None:
        t0 = time.monotonic()

        def step() -> None:
            if self.is_closing:
                return
            now = time.monotonic()
            elapsed = (now - t0) * 1000.0
            p = min(1.0, max(0.0, elapsed / duration_ms))
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
        fraction = max(0.0, min(1.0, (self.remaining_ms - elapsed_ms) / self.timeout_ms))
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
        bar_col = self.accent_glow if hovered else self.accent_color

        self.root.config(bg=border)
        self.container.config(bg=bg)
        self.content.config(bg=bg)
        self.top_row.config(bg=bg)
        self.tag_lbl.config(bg=bg)
        self.title_lbl.config(bg=bg)
        self.msg_lbl.config(bg=bg)
        self.status.config(bg=bg)
        self.close_frame.config(bg=bg)
        self.close_btn.config(bg=bg)
        self.progress.config(bg=bar_col)

    def close(self) -> None:
        if self.is_closing:
            return
        self.is_closing = True
        if self.slide_job:
            try:
                self.root.after_cancel(self.slide_job)
            except Exception:
                pass
        if self.dismiss_job:
            try:
                self.root.after_cancel(self.dismiss_job)
            except Exception:
                pass
        if self.progress_job:
            try:
                self.root.after_cancel(self.progress_job)
            except Exception:
                pass

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


class BatchRibbonPopup(SummaryRibbonPopup):
    """One ribbon for a big batch of files, counting up while they arrive.

    It reads the totals the background sorter writes to BATCH_FILE, restarts
    its countdown whenever another file is filed, and closes a few seconds
    after the last one. "Undo" puts the whole batch back; "Open" shows the
    folder the files went to (or Downloads when they went to several).
    """

    def __init__(self, enable_animation: bool = True) -> None:
        self.state = read_batch_state()
        self.burst_id = self.state.get("id")
        self.last_seen = self.state.get("last")
        title, message = batch_summary(self.state)
        self.poll_job: str | None = None
        super().__init__(
            title=title,
            message=message,
            accent=DEFAULT_ACCENT,
            timeout_ms=BATCH_IDLE_CLOSE_MS,
            enable_animation=enable_animation,
        )
        self.action_labels: list[tk.Label] = []
        for text, handler in (("Undo", self.undo), ("Open", self.open_folder)):
            label = tk.Label(
                self.top_row, text=text, font=(FONT_FAMILY, 9, "bold"),
                fg=ACTION_COLOR, bg=BG_COLOR, cursor="hand2", padx=int(6 * self.scale),
            )
            label.pack(side="right")
            label.bind("<Button-1>", lambda _e, h=handler: (h(), "break")[1])
            label.bind("<Enter>", self.on_enter)
            label.bind("<Leave>", self.on_leave)
            self.action_labels.append(label)
        self.poll_job = self.root.after(BATCH_POLL_MS, self._poll)

    def _poll(self) -> None:
        if self.is_closing:
            return
        state = read_batch_state()
        if state.get("id") != self.burst_id:
            self.close()  # a newer batch has its own ribbon
            return
        if state.get("last") != self.last_seen:
            self.state = state
            self.last_seen = state.get("last")
            title, message = batch_summary(state)
            try:
                self.title_lbl.config(text=title)
                self.msg_lbl.config(text=message if len(message) <= 48 else message[:45] + "…")
            except tk.TclError:
                return
            self.remaining_ms = float(self.timeout_ms)
            if not self.is_hovered and self.slide_job is None:
                self._schedule_dismiss()
        try:
            self.poll_job = self.root.after(BATCH_POLL_MS, self._poll)
        except tk.TclError:
            pass

    def _set_hover_state(self, hovered: bool) -> None:
        super()._set_hover_state(hovered)
        bg = HOVER_BG if hovered else BG_COLOR
        for label in getattr(self, "action_labels", []):
            label.config(bg=bg)

    def folder_to_open(self) -> Path | None:
        root = self.state.get("root")
        if not root:
            return None
        folders = list((self.state.get("folders") or {}).keys())
        if len(folders) == 1 and folders[0] != "Downloads":
            return Path(root) / folders[0]
        return Path(root)

    def open_folder(self) -> None:
        folder = self.folder_to_open()
        if folder is not None and sys.platform == "win32":
            try:
                os.startfile(str(folder))  # type: ignore[attr-defined]
            except OSError as exc:
                logging.debug("Could not open %s: %s", folder, exc)
        self.close()

    def undo(self) -> None:
        batches = [str(b) for b in self.state.get("batches") or []]
        if batches:
            args = ["--undo"]
            for batch in batches:
                args += ["--batch", batch]
            _spawn(_app_command(*args, "--notify"))
        self.close()

    def close(self) -> None:
        if self.poll_job:
            try:
                self.root.after_cancel(self.poll_job)
            except Exception:
                pass
            self.poll_job = None
        super().close()


class SuggestionRibbonPopup(SummaryRibbonPopup):
    """Asks before learning adds a rule: "Put files like this in Banking?" Yes / No.

    Yes adds the rule (it then shows on the Rules page); No means it won't ask
    about that word or website for that folder again. Closing it, or letting it
    time out, answers nothing.
    """

    TIMEOUT_MS = 20000

    def __init__(self, suggestion_id: str, enable_animation: bool = True) -> None:
        import learning

        self.suggestion_id = suggestion_id
        self.suggestion = learning.pending_suggestion(suggestion_id) or {}
        title, message = learning.describe_suggestion(self.suggestion) if self.suggestion else (
            "Nothing to ask", "This suggestion was already answered.")
        super().__init__(
            title=title,
            message=message,
            accent=ACTION_COLOR,
            timeout_ms=self.TIMEOUT_MS if self.suggestion else 2000,
            enable_animation=enable_animation,
        )
        self.action_labels: list[tk.Label] = []
        if not self.suggestion:
            return
        for text, handler in (("No", self.no), ("Yes", self.yes)):
            label = tk.Label(
                self.top_row, text=text, font=(FONT_FAMILY, 9, "bold"),
                fg=ACTION_COLOR, bg=BG_COLOR, cursor="hand2", padx=int(6 * self.scale),
            )
            label.pack(side="right")
            label.bind("<Button-1>", lambda _e, h=handler: (h(), "break")[1])
            label.bind("<Enter>", self.on_enter)
            label.bind("<Leave>", self.on_leave)
            self.action_labels.append(label)

    def _set_hover_state(self, hovered: bool) -> None:
        super()._set_hover_state(hovered)
        bg = HOVER_BG if hovered else BG_COLOR
        for label in getattr(self, "action_labels", []):
            label.config(bg=bg)

    def yes(self) -> None:
        import learning

        try:
            rule = learning.accept_suggestion(self.suggestion_id)
        except Exception as exc:
            logging.warning("Could not add the learned rule: %s", exc)
            rule = None
        self.close()
        if rule:
            notify_summary(
                "Rule added",
                f"New files like this go to {rule['folder']}. Change it on the Rules page.",
            )

    def no(self) -> None:
        import learning

        try:
            learning.decline_suggestion(self.suggestion_id)
        except Exception as exc:
            logging.debug("Could not record the declined suggestion: %s", exc)
        self.close()


def notify_suggestion(suggestion_id: str) -> None:
    """Ask the user whether to add a rule learning suggested."""
    _spawn(_ribbon_command("--suggest", str(suggestion_id)))


class BambuPromptRibbonPopup(SummaryRibbonPopup):
    """One-time opt-in prompt before Download Sorter touches BambuStudio.conf."""

    TIMEOUT_MS = 20000

    def __init__(self, source_path: str = "", dest_path: str = "", enable_animation: bool = True) -> None:
        self.source_path = source_path
        self.dest_path = dest_path
        super().__init__(
            title="Update Bambu Studio's Recent list?",
            message="Keep Bambu's Recently Opened pointing at moved .3mf files (makes a backup first).",
            accent=ACTION_COLOR,
            timeout_ms=self.TIMEOUT_MS,
            enable_animation=enable_animation,
        )
        self.action_labels: list[tk.Label] = []
        for text, handler in (("No", self.no), ("Yes", self.yes)):
            label = tk.Label(
                self.top_row, text=text, font=(FONT_FAMILY, 9, "bold"),
                fg=ACTION_COLOR, bg=BG_COLOR, cursor="hand2", padx=int(6 * self.scale),
            )
            label.pack(side="right")
            label.bind("<Button-1>", lambda _e, h=handler: (h(), "break")[1])
            label.bind("<Enter>", self.on_enter)
            label.bind("<Leave>", self.on_leave)
            self.action_labels.append(label)

    def _set_hover_state(self, hovered: bool) -> None:
        super()._set_hover_state(hovered)
        bg = HOVER_BG if hovered else BG_COLOR
        for label in getattr(self, "action_labels", []):
            label.config(bg=bg)

    def yes(self) -> None:
        import bambu_recent
        bambu_recent.save_preference(True, asked=True)
        if self.source_path and self.dest_path:
            bambu_recent.record_move(self.source_path, self.dest_path)
        self.close()
        notify_summary(
            "Bambu Studio recent list enabled",
            "Moved .3mf files will stay updated in Bambu's Recently Opened (change in Settings).",
        )

    def no(self) -> None:
        import bambu_recent
        bambu_recent.save_preference(False, asked=True)
        self.close()


def notify_bambu_prompt(source: Path, dest: Path) -> None:
    """Offer once to keep Bambu Studio's Recently Opened list pointing at moved .3mf files."""
    _spawn(_ribbon_command("--bambu-prompt", "--bambu-source", str(source), "--bambu-dest", str(dest)))


def notify_summary(
    title: str,
    message: str,
    accent: str = "#69E6AD",
    timeout_ms: int = 5000,
) -> None:
    """Spawn or display a sleek desktop ribbon summary toast using suite_theme.py design tokens."""
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    if getattr(sys, "frozen", False):
        cmd = [
            sys.executable,
            "--ribbon-summary",
            "--summary",
            "--title", title,
            "--message", message,
            "--accent", accent,
            "--timeout", str(timeout_ms),
        ]
    else:
        python_bin = Path(sys.executable).resolve()
        if python_bin.name.lower() == "python.exe":
            pythonw = python_bin.with_name("pythonw.exe")
            if pythonw.exists():
                python_bin = pythonw
        else:
            python_bin = PYTHON
        cmd = [
            str(python_bin),
            str(Path(__file__).resolve()),
            "--summary",
            "--title", title,
            "--message", message,
            "--accent", accent,
            "--timeout", str(timeout_ms),
        ]
    try:
        subprocess.Popen(cmd, creationflags=flags)
    except Exception as exc:
        logging.debug("Failed to spawn ribbon summary notification: %s", exc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Show a top-right download ribbon notification.")
    parser.add_argument("--version", action="version", version=about.version_string())
    parser.add_argument("dest_path", nargs="?", default="", help="Path to the destination file.")
    parser.add_argument("downloads_root", nargs="?", default="", help="Root Downloads folder.")
    parser.add_argument("--test", action="store_true", help="Show a test notification.")
    parser.add_argument("--no-anim", action="store_true", help="Disable slide animations.")
    parser.add_argument("--held", action="store_true", help="Show held preview ribbon with File now / Keep here.")
    parser.add_argument("--planned", type=str, default="", help="Planned destination path when held.")
    parser.add_argument("--ribbon-summary", "--summary", dest="summary", action="store_true", help="Display summary toast.")
    parser.add_argument("--batch", action="store_true", help="Show the ribbon that counts a big batch of files.")
    parser.add_argument("--suggest", type=str, default="", help="Ask whether to add a learned rule (its id).")
    parser.add_argument("--bambu-prompt", action="store_true", help="Offer once to enable Bambu recent-list updates.")
    parser.add_argument("--bambu-source", type=str, default="", help="Original .3mf path for --bambu-prompt.")
    parser.add_argument("--bambu-dest", type=str, default="", help="Moved .3mf path for --bambu-prompt.")
    parser.add_argument("--title", type=str, default="Download Sorter", help="Summary notification title.")
    parser.add_argument("--message", type=str, default="", help="Summary notification message.")
    parser.add_argument("--accent", type=str, default="#69E6AD", help="Accent hex color.")
    parser.add_argument("--timeout", type=int, default=5000, help="Timeout in milliseconds.")
    args = parser.parse_args(argv)

    enable_anim = not args.no_anim

    try:
        if args.bambu_prompt:
            popup = BambuPromptRibbonPopup(args.bambu_source, args.bambu_dest, enable_animation=enable_anim)
        elif args.suggest:
            popup = SuggestionRibbonPopup(args.suggest, enable_animation=enable_anim)
        elif args.batch:
            popup = BatchRibbonPopup(enable_animation=enable_anim)
        elif args.summary:
            popup = SummaryRibbonPopup(
                title=args.title,
                message=args.message,
                accent=args.accent,
                timeout_ms=args.timeout,
                enable_animation=enable_anim,
            )
        elif args.test or not args.dest_path:
            sample = Path.home() / "Downloads" / "3D Models" / "STEP" / "Flanged Mace Iteration 1 STEP.step"
            popup = RibbonPopup(sample, enable_animation=enable_anim)
        else:
            dest = Path(args.dest_path)
            dl_root = Path(args.downloads_root) if args.downloads_root else None
            planned = Path(args.planned) if args.planned else None
            popup = RibbonPopup(dest, dl_root, enable_animation=enable_anim, is_held=args.held, planned_dest=planned)

        popup.run()
        return 0
    except Exception as exc:
        logging.warning("Ribbon notification failed to initialize: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())

