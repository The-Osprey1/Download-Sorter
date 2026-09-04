"""Top-right interactive ribbon notification popup for download-sorter.

Displays a modern floating banner in the top-right corner when a file is sorted.
Features:
- Integrated with Devyn Personal Suite design tokens (`suite_theme.py`).
- Crisp High-DPI rendering on Windows (fixes blurriness on 125%/150%/175%/200% displays).
- Dynamic category-based accent colors (3D/CAD, PDFs, Code, Media, Installers).
- Atomic slot synchronization for multi-file downloads using Windows file locking.
- Automatic vertical stacking when multiple files are downloaded at once.
- Safe File Explorer reveal supporting commas and special characters in paths.
- Auto-dismisses after timeout (pauses on hover).
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

    BASE_WIDTH = int(RIBBON.get("width", 350))
    BASE_HEIGHT = int(RIBBON.get("height", 84))
    BASE_OFFSET_X = int(RIBBON.get("offset", 20))
    BASE_OFFSET_Y = int(RIBBON.get("offset", 20))
    BASE_GAP = int(RIBBON.get("gap", 10))
    AUTO_DISMISS_MS = int(RIBBON.get("timeoutMs", 7000))
    MAX_SLOTS = int(RIBBON.get("maxVisible", 4))
    FONT_FAMILY = TYPE.get("ui", "Segoe UI")
except Exception:
    RIBBON = {
        "width": 336, "height": 84, "offset": 18, "gap": 8,
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

    BASE_WIDTH = 336
    BASE_HEIGHT = 84
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


def get_accent_color(dest_path: Path, folder_disp: str) -> str:
    """Return a distinct theme color depending on file type or category folder."""
    folder_lower = folder_disp.lower()
    ext = dest_path.suffix.lower()

    for key, color in CATEGORY_COLORS.items():
        if key in folder_lower:
            return color

    if ext in (".step", ".stp", ".stl", ".3mf", ".obj", ".f3d", ".f3z", ".ipt", ".iam", ".sldprt", ".dwg", ".dxf"):
        return "#69E6AD"
    if ext in (".nc", ".tap", ".ngc"):
        return "#D74A4A"
    if ext in (".gcode", ".bgcode"):
        return "#E8833A"
    if ext == ".pdf":
        return "#FA7183"
    if ext in (".doc", ".docx", ".odt", ".rtf", ".txt", ".md"):
        return "#61A5FF"
    if ext in (".xls", ".xlsx", ".csv", ".ods"):
        return "#69E6AD"
    if ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp"):
        return "#A855F7"
    if ext in (".mp4", ".mov", ".mkv", ".avi", ".webm"):
        return "#EC4899"
    if ext in (".mp3", ".wav", ".flac", ".m4a", ".aac"):
        return "#8B5CF6"
    if ext in (".zip", ".rar", ".7z", ".tar", ".gz"):
        return "#06B6D4"
    if ext in (".exe", ".msi", ".msix"):
        return "#F43F5E"
    if ext in (".py", ".js", ".ts", ".html", ".css", ".json", ".cpp", ".cs"):
        return "#F5AD62"

    return DEFAULT_ACCENT


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
    def __init__(self, dest_path: Path, downloads_root: Path | None = None) -> None:
        self.dest_path = dest_path.resolve()
        self.downloads_root = downloads_root.resolve() if downloads_root else Path.home() / "Downloads"
        self.pid = os.getpid()
        self.slot = claim_slot(self.pid)

        # Calculate relative folder display
        try:
            rel = self.dest_path.relative_to(self.downloads_root)
            self.folder_disp = str(rel.parent) if rel.parent != Path(".") else "Downloads"
        except ValueError:
            self.folder_disp = self.dest_path.parent.name

        self.accent_color = get_accent_color(self.dest_path, self.folder_disp)

        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.config(bg=BORDER_COLOR)

        try:
            self.root.attributes("-alpha", float(RIBBON.get("opacity", 0.88)))
        except tk.TclError:
            pass

        # Calculate DPI-scaled dimensions
        try:
            dpi = float(self.root.winfo_fpixels("1i"))
            scale = max(1.0, dpi / 96.0)
        except Exception:
            scale = 1.0

        width, height, pos_x, pos_y = scaled_ribbon_geometry(scale, self.slot, self._work_area())
        self.root.geometry(f"{width}x{height}+{pos_x}+{pos_y}")

        self.dismiss_job: str | None = None
        self.progress_job: str | None = None
        self.remaining_ms = AUTO_DISMISS_MS
        self.dismiss_started = 0.0
        self.is_hovered = False
        self._build_ui(scale)
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
        pad_x = int(12 * scale)
        pad_y = int(8 * scale)
        stripe_w = max(4, int(4 * scale))

        self.container = tk.Frame(self.root, bg=BG_COLOR, cursor="hand2")
        self.container.pack(fill="both", expand=True, padx=1, pady=1)

        # Left accent stripe
        self.stripe = tk.Frame(self.container, bg=self.accent_color, width=stripe_w)
        self.stripe.pack(side="left", fill="y")

        # A text glyph remains legible in high-contrast mode and gives success a
        # second signal beyond color alone.
        self.status = tk.Label(
            self.container,
            text="✓",
            font=(FONT_FAMILY, 14, "bold"),
            fg=self.accent_color,
            bg=BG_COLOR,
            width=2,
            anchor="center",
        )
        self.status.pack(side="left", fill="y")

        # Content area
        self.content = tk.Frame(self.container, bg=BG_COLOR, padx=pad_x, pady=pad_y)
        self.content.pack(side="left", fill="both", expand=True)

        # Top row: Tag badge + Close button
        self.top_row = tk.Frame(self.content, bg=BG_COLOR)
        self.top_row.pack(fill="x")

        tag_text = "DOWNLOAD SORTER  ·  COMPLETE"
        self.tag_lbl = tk.Label(
            self.top_row,
            text=tag_text,
            font=(FONT_FAMILY, 9, "bold"),
            fg=self.accent_color,
            bg=BG_COLOR,
            anchor="w",
        )
        self.tag_lbl.pack(side="left")

        close_btn = tk.Label(
            self.top_row,
            text="✕",
            font=(FONT_FAMILY, 10, "bold"),
            fg=CLOSE_COLOR,
            bg=BG_COLOR,
            cursor="arrow",
            padx=int(4 * scale),
        )
        close_btn.pack(side="right")
        close_btn.bind("<Button-1>", lambda e: self.close())
        close_btn.bind("<Enter>", lambda e: close_btn.config(fg=CLOSE_HOVER))
        close_btn.bind("<Leave>", lambda e: close_btn.config(fg=CLOSE_COLOR))

        # Middle row: Filename
        fname = self.dest_path.name
        if len(fname) > 38:
            fname = fname[:35] + "..."

        self.name_lbl = tk.Label(
            self.content,
            text=fname,
            font=(FONT_FAMILY, 10, "bold"),
            fg=TEXT_MAIN,
            bg=BG_COLOR,
            anchor="w",
        )
        self.name_lbl.pack(fill="x", pady=(2, 1))

        # Bottom row: Click action hint
        self.action_lbl = tk.Label(
            self.content,
            text=f"Moved to {self.folder_disp}  ·  Click to reveal",
            font=(FONT_FAMILY, 8),
            fg=TEXT_MUTED,
            bg=BG_COLOR,
            anchor="w",
        )
        self.action_lbl.pack(fill="x")

        # Bind hover and click events across interactive widgets
        for widget in [self.container, self.content, self.top_row, self.tag_lbl, self.name_lbl, self.action_lbl, self.stripe, self.status]:
            widget.bind("<Button-1>", self.on_click)
            widget.bind("<Enter>", self.on_enter)
            widget.bind("<Leave>", self.on_leave)

        self.progress = tk.Frame(self.container, bg=self.accent_color, height=max(2, int(2 * scale)))
        self.progress.place(x=stripe_w, rely=1.0, anchor="sw", relwidth=1.0)

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
        if self.is_hovered:
            return
        elapsed_ms = (time.monotonic() - self.dismiss_started) * 1000
        fraction = max(0.0, min(1.0, (self.remaining_ms - elapsed_ms) / AUTO_DISMISS_MS))
        self.progress.place_configure(relwidth=fraction)
        if fraction > 0:
            self.progress_job = self.root.after(50, self._tick_progress)

    def on_enter(self, _event=None) -> None:
        if self.is_hovered:
            return
        self.is_hovered = True
        if self.dismiss_job:
            elapsed_ms = (time.monotonic() - self.dismiss_started) * 1000
            self.remaining_ms = max(1, self.remaining_ms - elapsed_ms)
            self.root.after_cancel(self.dismiss_job)
            self.dismiss_job = None
        if self.progress_job:
            self.root.after_cancel(self.progress_job)
            self.progress_job = None
        self._set_bg(HOVER_BG)

    def on_leave(self, _event=None) -> None:
        self.root.after(20, self._resume_if_pointer_left)

    def _resume_if_pointer_left(self) -> None:
        pointer_widget = self.root.winfo_containing(self.root.winfo_pointerx(), self.root.winfo_pointery())
        if pointer_widget is not None and pointer_widget.winfo_toplevel() == self.root:
            return
        self.is_hovered = False
        self._set_bg(BG_COLOR)
        self._schedule_dismiss()

    def _set_bg(self, color: str) -> None:
        self.container.config(bg=color)
        self.content.config(bg=color)
        self.top_row.config(bg=color)
        self.tag_lbl.config(bg=color)
        self.name_lbl.config(bg=color)
        self.action_lbl.config(bg=color)
        self.status.config(bg=color)

    def on_click(self, _event=None) -> None:
        try:
            if sys.platform == "win32":
                dest_str = str(self.dest_path)
                if not self.dest_path.exists():
                    if self.dest_path.parent.exists():
                        os.startfile(str(self.dest_path.parent))
                elif "," in dest_str:
                    # Windows explorer.exe /select splits on commas. PowerShell handles it safely.
                    cmd = f"Start-Process explorer.exe -ArgumentList ('/select,\"' + [System.IO.Path]::GetFullPath('{dest_str}') + '\"')"
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
        if self.dismiss_job:
            self.root.after_cancel(self.dismiss_job)
            self.dismiss_job = None
        if self.progress_job:
            self.root.after_cancel(self.progress_job)
            self.progress_job = None
        release_slot(self.slot, self.pid)
        try:
            self.root.destroy()
        except tk.TclError:
            pass

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
    args = parser.parse_args()

    if args.test or not args.dest_path:
        sample = Path.home() / "Downloads" / "3D Models" / "STEP" / "Flanged Mace Iteration 1 STEP.step"
        popup = RibbonPopup(sample)
    else:
        dest = Path(args.dest_path)
        dl_root = Path(args.downloads_root) if args.downloads_root else None
        popup = RibbonPopup(dest, dl_root)

    popup.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
