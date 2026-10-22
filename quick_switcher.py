"""Recency-Gated Quick Switcher for Download Sorter.

When an application opens a Windows File Open / Upload dialog within a configurable
recency window (default 5 minutes) of downloading a file, this module surfaces an
unobtrusive, floating Quick Switcher pill docked to the dialog window.

Features:
- Instant One-Click Selection: Click 'Select' (or press Ctrl+G) to immediately inject
  and choose the recently downloaded file in the active file dialog.
- Zero Friction, Non-Intrusive: Never hijacks or navigates without user consent.
  If the user wants another file, they simply ignore the pill and it auto-dismisses.
- Category-Aware Theming: Integrates with Devyn Personal Suite tokens and vector icons.
- Robust Win32 Dialog Discovery: Accurately identifies Common Item Dialogs (#32770)
  across Chrome, Edge, Discord, Slack, CAD, and native desktop applications.
- Process-Isolated: Runs as a lightweight ephemeral process, preventing UI hangs.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
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
    from PIL import Image, ImageTk
    HAS_PIL = True
except Exception:
    HAS_PIL = False

from ribbon_popup import (
    CATEGORY_GLYPHS,
    blend_colors,
    get_category_info,
    render_category_icon,
)

try:
    from suite_theme import ACCENTS, COLORS, TYPE
    BG_COLOR = COLORS.get("surface", "#12151C")
    BORDER_COLOR = COLORS.get("border", "#2A303B")
    TEXT_MAIN = COLORS.get("text", "#EDF1F8")
    TEXT_MUTED = COLORS.get("textMuted", "#8B94A7")
    ACTION_COLOR = COLORS.get("info", "#61A5FF")
    CLOSE_COLOR = COLORS.get("textFaint", "#657084")
    CLOSE_HOVER = COLORS.get("critical", "#FA7183")
    HOVER_BG = COLORS.get("surfaceHover", "#1D222C")
    FONT_FAMILY = TYPE.get("ui", "Segoe UI")
except Exception:
    BG_COLOR = "#12151C"
    BORDER_COLOR = "#2A303B"
    TEXT_MAIN = "#EDF1F8"
    TEXT_MUTED = "#8B94A7"
    ACTION_COLOR = "#61A5FF"
    CLOSE_COLOR = "#657084"
    CLOSE_HOVER = "#FA7183"
    HOVER_BG = "#1D222C"
    FONT_FAMILY = "Segoe UI"

ROOT = Path(__file__).resolve().parent
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
if not PYTHON.exists():
    PYTHON = Path(sys.executable)

RECENT_STATE_FILE = Path(tempfile.gettempdir()) / "download_sorter_recent_file.json"

# Win32 Constants
WM_SETTEXT = 0x000C
WM_COMMAND = 0x0111
IDOK = 1
MOD_CONTROL = 0x0002
VK_G = 0x47
HOTKEY_ID = 9101


def record_recent_file(
    dest_path: str | Path,
    downloads_root: str | Path | None = None,
) -> None:
    """Record the most recently sorted file for the Quick Switcher and Windows Recent Docs."""
    dest = Path(dest_path).resolve()
    if not dest.exists() or not dest.is_file():
        return

    # Calculate folder label
    dl_root = Path(downloads_root).resolve() if downloads_root else Path.home() / "Downloads"
    try:
        rel = dest.relative_to(dl_root)
        folder_disp = str(rel.parent) if rel.parent != Path(".") else "Downloads"
    except ValueError:
        folder_disp = dest.parent.name

    cat_type, accent_color = get_category_info(dest, folder_disp)

    data = {
        "path": str(dest),
        "name": dest.name,
        "folder": folder_disp,
        "category": cat_type,
        "color": accent_color,
        "timestamp": time.time(),
    }

    temp_file = RECENT_STATE_FILE.with_suffix(".tmp")
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f)
        temp_file.replace(RECENT_STATE_FILE)
    except Exception as exc:
        logging.debug("Failed to record recent file: %s", exc)

    # Synchronize with Windows Recent Docs API
    if sys.platform == "win32":
        try:
            SHARD_PATHW = 0x00000003
            ctypes.windll.shell32.SHAddToRecentDocs(SHARD_PATHW, str(dest))
        except Exception:
            pass


def get_recent_file(max_age_sec: float = 300.0) -> dict | None:
    """Return the recent file record if it was sorted within max_age_sec and still exists."""
    if not RECENT_STATE_FILE.exists():
        return None

    try:
        with open(RECENT_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None

    if not isinstance(data, dict) or "path" not in data or "timestamp" not in data:
        return None

    age = time.time() - float(data.get("timestamp", 0))
    if age > max_age_sec:
        return None

    target = Path(data["path"])
    if not target.exists() or not target.is_file():
        return None

    data["age_sec"] = age
    return data


def clear_recent_file() -> None:
    """Clear the recent file state."""
    try:
        if RECENT_STATE_FILE.exists():
            RECENT_STATE_FILE.unlink()
    except Exception:
        pass


def is_file_dialog(hwnd: int) -> bool:
    """Return True if hwnd is an active Open/Upload File Dialog (#32770), skipping Save dialogs."""
    if sys.platform != "win32" or not hwnd:
        return False

    user32 = ctypes.windll.user32
    if not user32.IsWindow(hwnd) or not user32.IsWindowVisible(hwnd):
        return False

    class_name = (ctypes.c_wchar * 256)()
    user32.GetClassNameW(hwnd, class_name, 256)
    if class_name.value != "#32770":
        return False

    title = (ctypes.c_wchar * 256)()
    user32.GetWindowTextW(hwnd, title, 256)
    title_lower = title.value.lower()
    # Filter out Save As dialogs
    if "save as" in title_lower or "save file" in title_lower:
        return False

    has_edit = False
    is_save_btn = False

    def enum_child(h, _):
        nonlocal has_edit, is_save_btn
        cn = (ctypes.c_wchar * 256)()
        user32.GetClassNameW(h, cn, 256)
        cname = cn.value
        if cname in ("ComboBoxEx32", "Edit"):
            has_edit = True
        elif cname == "Button":
            btn_txt = (ctypes.c_wchar * 256)()
            user32.GetWindowTextW(h, btn_txt, 256)
            txt = btn_txt.value.strip().lower().replace("&", "")
            if txt == "save":
                is_save_btn = True
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumChildWindows(hwnd, WNDENUMPROC(enum_child), 0)

    if not has_edit or is_save_btn:
        return False

    return True


def find_dialog_edit_control(dialog_hwnd: int) -> int | None:
    """Find the filename Edit control inside the Windows file dialog."""
    if sys.platform != "win32" or not dialog_hwnd:
        return None

    user32 = ctypes.windll.user32
    found_edit = None

    def enum_child(hwnd, _lparam):
        nonlocal found_edit
        class_name = (ctypes.c_wchar * 256)()
        user32.GetClassNameW(hwnd, class_name, 256)
        if class_name.value == "Edit":
            # Verify it is enabled and visible
            if user32.IsWindowVisible(hwnd) and user32.IsWindowEnabled(hwnd):
                found_edit = hwnd
                return False
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumChildWindows(dialog_hwnd, WNDENUMPROC(enum_child), 0)
    return found_edit


def inject_file_into_dialog(dialog_hwnd: int, file_path: str | Path) -> bool:
    """Directly inject the target file path into the dialog and trigger the Open command."""
    if sys.platform != "win32" or not dialog_hwnd:
        return False

    user32 = ctypes.windll.user32
    target = Path(file_path).resolve()
    if not target.exists():
        return False

    edit_hwnd = find_dialog_edit_control(dialog_hwnd)
    if not edit_hwnd:
        return False

    path_str = str(target)
    # 1. Set full path in the edit box
    user32.SendMessageW(edit_hwnd, WM_SETTEXT, 0, path_str)
    time.sleep(0.06)

    # 2. Trigger Open via both Return key on edit control and IDOK command on dialog
    VK_RETURN = 0x0D
    WM_KEYDOWN = 0x0100
    WM_KEYUP = 0x0101
    user32.PostMessageW(edit_hwnd, WM_KEYDOWN, VK_RETURN, 0)
    user32.PostMessageW(edit_hwnd, WM_KEYUP, VK_RETURN, 0)
    user32.PostMessageW(dialog_hwnd, WM_COMMAND, IDOK, 0)
    return True


class QuickSwitcherPill:
    """Floating docked action pill attached to an active Windows File Dialog."""

    def __init__(self, dialog_hwnd: int, recent_data: dict) -> None:
        self.dialog_hwnd = dialog_hwnd
        self.recent_data = recent_data
        self.file_path = recent_data["path"]
        self.filename = recent_data["name"]
        self.category = recent_data.get("category", "default")
        self.accent_color = recent_data.get("color", ACTION_COLOR)
        self.is_closed = False

        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)

        self.normal_border = blend_colors(self.accent_color, BORDER_COLOR, 0.40)
        self.hover_border = blend_colors(self.accent_color, COLORS.get("borderStrong", "#3A4352"), 0.65)
        self.root.config(bg=self.normal_border)

        # Enable Windows 11 DWM rounded corners and native drop shadow
        if sys.platform == "win32":
            try:
                self.root.update_idletasks()
                hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
                pref = ctypes.c_int(2)  # DWMWCP_ROUND
                ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref))
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

        self.pill_w = round(350 * self.scale)
        self.pill_h = round(42 * self.scale)
        self.icon_photo: ImageTk.PhotoImage | None = None
        self.last_x: int | None = None
        self.last_y: int | None = None

        self._build_ui()
        self._position_pill()

        # Register Ctrl+G global hotkey for quick activation
        self._register_hotkey()

        # Track dialog position and poll hotkey
        self.root.after(100, self._track_dialog)
        self.root.after(25000, self.close)  # 25s auto-timeout

    def _build_ui(self) -> None:
        self.container = tk.Frame(self.root, bg=BG_COLOR, cursor="hand2")
        self.container.pack(fill="both", expand=True, padx=1, pady=1)

        # Left accent stripe
        stripe_w = max(3, int(3 * self.scale))
        self.stripe = tk.Frame(self.container, bg=self.accent_color, width=stripe_w)
        self.stripe.pack(side="left", fill="y")

        # Category icon badge
        icon_size = max(24, int(26 * self.scale))
        icon_img = render_category_icon(self.category, self.accent_color, size=icon_size)
        if icon_img and HAS_PIL:
            self.icon_photo = ImageTk.PhotoImage(icon_img)
            self.icon_lbl = tk.Label(self.container, image=self.icon_photo, bg=BG_COLOR, bd=0)
            self.icon_lbl.pack(side="left", padx=(int(6 * self.scale), int(2 * self.scale)))
        else:
            glyph = CATEGORY_GLYPHS.get(self.category, "⚡")
            self.icon_lbl = tk.Label(
                self.container,
                text=glyph,
                font=(FONT_FAMILY, 11, "bold"),
                fg=self.accent_color,
                bg=BG_COLOR,
            )
            self.icon_lbl.pack(side="left", padx=(int(6 * self.scale), int(2 * self.scale)))

        # Center content: Filename & context
        self.center_frame = tk.Frame(self.container, bg=BG_COLOR)
        self.center_frame.pack(side="left", fill="both", expand=True, padx=(int(4 * self.scale), 0))

        disp_name = self.filename
        if len(disp_name) > 24:
            disp_name = disp_name[:21] + "…"

        self.tag_lbl = tk.Label(
            self.center_frame,
            text="⚡ USE RECENT",
            font=(FONT_FAMILY, 7, "bold"),
            fg=self.accent_color,
            bg=BG_COLOR,
            anchor="w",
        )
        self.tag_lbl.pack(fill="x", pady=(int(2 * self.scale), 0))

        self.name_lbl = tk.Label(
            self.center_frame,
            text=disp_name,
            font=(FONT_FAMILY, 9, "bold"),
            fg=TEXT_MAIN,
            bg=BG_COLOR,
            anchor="w",
        )
        self.name_lbl.pack(fill="x")

        # Action Button: [ ↵ Select ]
        self.action_btn = tk.Label(
            self.container,
            text="↵ Select (Ctrl+G)",
            font=(FONT_FAMILY, 8, "bold"),
            fg=self.accent_color,
            bg="#171C26",
            cursor="hand2",
            padx=int(6 * self.scale),
            pady=int(2 * self.scale),
        )
        self.action_btn.pack(side="left", padx=int(4 * self.scale))

        # Close button
        self.close_btn = tk.Label(
            self.container,
            text="✕",
            font=(FONT_FAMILY, 9, "bold"),
            fg=CLOSE_COLOR,
            bg=BG_COLOR,
            cursor="hand2",
            padx=int(6 * self.scale),
        )
        self.close_btn.pack(side="right")
        self.close_btn.bind("<Button-1>", lambda e: (self.close(), "break")[1])
        self.close_btn.bind("<Enter>", lambda e: self.close_btn.config(fg=CLOSE_HOVER, bg="#282F3D"))
        self.close_btn.bind("<Leave>", lambda e: self.close_btn.config(fg=CLOSE_COLOR, bg=BG_COLOR))

        # Bind click-to-select across interactive surfaces
        for w in (self.container, self.center_frame, self.tag_lbl, self.name_lbl, self.action_btn, self.stripe, self.icon_lbl):
            w.bind("<Button-1>", lambda e: self.select_file())
            w.bind("<Enter>", self.on_enter)
            w.bind("<Leave>", self.on_leave)

    def _register_hotkey(self) -> None:
        if sys.platform == "win32":
            try:
                user32 = ctypes.windll.user32
                hwnd = user32.GetParent(self.root.winfo_id())
                user32.RegisterHotKey(hwnd, HOTKEY_ID, MOD_CONTROL, VK_G)
            except Exception:
                pass

    def _unregister_hotkey(self) -> None:
        if sys.platform == "win32":
            try:
                user32 = ctypes.windll.user32
                hwnd = user32.GetParent(self.root.winfo_id())
                user32.UnregisterHotKey(hwnd, HOTKEY_ID)
            except Exception:
                pass

    def _position_pill(self) -> None:
        """Position the pill docked neatly above or below the target dialog."""
        if sys.platform == "win32":
            try:
                user32 = ctypes.windll.user32
                rect = wintypes.RECT()
                if user32.GetWindowRect(self.dialog_hwnd, ctypes.byref(rect)):
                    # Right-aligned, docked right above the dialog's top title bar
                    x = rect.right - self.pill_w - round(16 * self.scale)
                    x = max(rect.left + round(8 * self.scale), x)
                    y = rect.top - self.pill_h - round(6 * self.scale)
                    if y < 8:
                        # If dialog is at the top of the monitor, dock just inside below title bar
                        y = rect.top + round(36 * self.scale)

                    if (x, y) != (self.last_x, self.last_y):
                        self.last_x = x
                        self.last_y = y
                        self.root.geometry(f"{self.pill_w}x{self.pill_h}+{x}+{y}")
                    return
            except Exception:
                pass

        # Fallback positioning
        sw = self.root.winfo_screenwidth()
        self.root.geometry(f"{self.pill_w}x{self.pill_h}+{sw - self.pill_w - 40}+60")

    def _track_dialog(self) -> None:
        if self.is_closed:
            return

        if sys.platform == "win32":
            user32 = ctypes.windll.user32
            # If the dialog window was closed or minimized by the user, close the pill immediately
            if (
                not user32.IsWindow(self.dialog_hwnd)
                or not user32.IsWindowVisible(self.dialog_hwnd)
                or user32.IsIconic(self.dialog_hwnd)
            ):
                self.close()
                return

        self._position_pill()
        self._check_hotkey()
        self.root.after(100, self._track_dialog)

    def _check_hotkey(self) -> None:
        """Poll Ctrl+G when the dialog or pill has keyboard focus."""
        if sys.platform == "win32":
            try:
                user32 = ctypes.windll.user32
                fg = user32.GetForegroundWindow()
                pill_parent = user32.GetParent(self.root.winfo_id())
                if fg in (self.dialog_hwnd, pill_parent, self.root.winfo_id()):
                    if (user32.GetAsyncKeyState(0x11) & 0x8000) and (user32.GetAsyncKeyState(VK_G) & 0x8000):
                        self.select_file()
            except Exception:
                pass

    def on_enter(self, _event=None) -> None:
        self.root.config(bg=self.hover_border)
        self.container.config(bg=HOVER_BG)
        self.center_frame.config(bg=HOVER_BG)
        self.tag_lbl.config(bg=HOVER_BG)
        self.name_lbl.config(bg=HOVER_BG)
        self.icon_lbl.config(bg=HOVER_BG)
        self.action_btn.config(bg="#212836")

    def on_leave(self, _event=None) -> None:
        self.root.config(bg=self.normal_border)
        self.container.config(bg=BG_COLOR)
        self.center_frame.config(bg=BG_COLOR)
        self.tag_lbl.config(bg=BG_COLOR)
        self.name_lbl.config(bg=BG_COLOR)
        self.icon_lbl.config(bg=BG_COLOR)
        self.action_btn.config(bg="#171C26")

    def select_file(self) -> None:
        """Inject the recent file into the dialog and dismiss the pill."""
        if self.is_closed:
            return

        if not Path(self.file_path).exists():
            self.action_btn.config(text="✕ File not found", fg="#FFFFFF", bg=CLOSE_HOVER)
            self.root.update_idletasks()
            self.root.after(1000, self.close)
            return

        self.is_closed = True
        self._unregister_hotkey()

        # Visually indicate selection
        self.action_btn.config(text="✓ Injected!", fg="#FFFFFF", bg=self.accent_color)
        self.root.update_idletasks()

        inject_file_into_dialog(self.dialog_hwnd, self.file_path)
        self.root.after(180, self.close)

    def close(self) -> None:
        if self.is_closed:
            return
        self.is_closed = True
        self._unregister_hotkey()
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def run(self) -> None:
        try:
            self.root.mainloop()
        finally:
            self.close()


def spawn_quick_switcher(dialog_hwnd: int) -> None:
    """Spawn the Quick Switcher pill as a lightweight decoupled process."""
    recent = get_recent_file()
    if not recent:
        return

    args = [str(PYTHON), str(Path(__file__).resolve()), "--dialog", str(dialog_hwnd)]
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        subprocess.Popen(args, creationflags=flags)
    except Exception as exc:
        logging.debug("Failed to spawn quick switcher: %s", exc)


class QuickSwitcherMonitor(threading.Thread):
    """Background monitor that detects Windows File Dialogs and surfaces the Quick Switcher."""

    def __init__(
        self,
        stop_event: threading.Event | None = None,
        check_interval: float = 0.25,
    ) -> None:
        super().__init__(daemon=True, name="QuickSwitcherMonitor")
        self.stop_event = stop_event or threading.Event()
        self.check_interval = check_interval
        self.handled_dialogs: set[int] = set()

    def run(self) -> None:
        if sys.platform != "win32":
            return

        user32 = ctypes.windll.user32

        while not self.stop_event.is_set():
            try:
                # Purge closed dialogs from handled history
                self.handled_dialogs = {
                    h for h in self.handled_dialogs if user32.IsWindow(h) and user32.IsWindowVisible(h)
                }

                # Check current foreground window
                fg = user32.GetForegroundWindow()
                if fg and fg not in self.handled_dialogs:
                    if is_file_dialog(fg):
                        recent = get_recent_file()
                        if recent:
                            self.handled_dialogs.add(fg)
                            spawn_quick_switcher(fg)

            except Exception as exc:
                logging.debug("Quick switcher monitor error: %s", exc)

            self.stop_event.wait(self.check_interval)


def main() -> int:
    parser = argparse.ArgumentParser(description="Quick Switcher for Download Sorter.")
    parser.add_argument("--dialog", type=int, help="Target dialog HWND to dock against.")
    parser.add_argument("--test", action="store_true", help="Test Quick Switcher on a mock dialog.")
    args = parser.parse_args()

    if args.test:
        sample = Path.home() / "Downloads" / "3D Models" / "STEP" / "Flanged Mace Iteration 1 STEP.step"
        sample.parent.mkdir(parents=True, exist_ok=True)
        sample.touch(exist_ok=True)
        record_recent_file(sample)
        print("Recorded mock recent file:", sample)
        return 0

    if args.dialog:
        recent = get_recent_file()
        if not recent:
            return 1
        pill = QuickSwitcherPill(args.dialog, recent)
        pill.run()
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
