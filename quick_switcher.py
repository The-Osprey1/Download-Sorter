"""Recency-Gated Quick Switcher for Download Sorter.

When an application opens a Windows File Open / Upload dialog within a configurable
recency window (default 5 minutes) of downloading a file, this module surfaces an
unobtrusive, floating Quick Switcher pill docked to the dialog window.

Features:
- Instant One-Click Selection: Click 'Select' (or press Ctrl+G) to immediately inject
  and choose the recently downloaded file in the active file dialog.
- Zero Friction, Non-Intrusive: Never hijacks or navigates without user consent.
  If the user wants another file, they simply ignore the pill and it auto-dismisses.
- Category-Aware Theming: Integrates with the shared design tokens and vector icons.
- Robust Win32 Dialog Discovery: Accurately identifies Common Item Dialogs (#32770)
  across Chrome, Edge, Discord, Slack, CAD, and native desktop applications.
- Process-Isolated: Runs as a lightweight ephemeral process, preventing UI hangs.
"""

from __future__ import annotations

import argparse
import about
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
    ACTION_COLOR = ACCENTS.get("downloadSorter", "#69E6AD")
    CLOSE_COLOR = COLORS.get("textFaint", "#657084")
    CLOSE_HOVER = COLORS.get("critical", "#FA7183")
    CLOSE_HOVER_BG = COLORS.get("criticalSubtle", "#1A1013")
    HOVER_BG = COLORS.get("surfaceHover", "#1D222C")
    BTN_BG = COLORS.get("surfaceRaised", "#171B24")
    BTN_HOVER = COLORS.get("surfaceHover", "#1D222C")
    FONT_FAMILY = TYPE.get("ui", "Segoe UI")
    FONT_SIZE_XS = TYPE.get("sizeXs", 9)
    FONT_SIZE_SM = TYPE.get("sizeSm", 11)
except Exception:
    BG_COLOR = "#12151C"
    BORDER_COLOR = "#2A303B"
    TEXT_MAIN = "#EDF1F8"
    TEXT_MUTED = "#8B94A7"
    ACTION_COLOR = "#69E6AD"
    CLOSE_COLOR = "#657084"
    CLOSE_HOVER = "#FA7183"
    CLOSE_HOVER_BG = "#1A1013"
    HOVER_BG = "#1D222C"
    BTN_BG = "#171B24"
    BTN_HOVER = "#1D222C"
    FONT_FAMILY = "Segoe UI"
    FONT_SIZE_XS = 9
    FONT_SIZE_SM = 11

ROOT = Path(__file__).resolve().parent
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
if not PYTHON.exists():
    PYTHON = Path(sys.executable)

import batch_handoff
from batch_handoff import (
    RECENT_STATE_FILE,
    clear_recent_file,
    consume_items,
    get_recent_file,
    record_recent_file,
)

# Win32 Constants
WM_SETTEXT = 0x000C
WM_COMMAND = 0x0111
IDOK = 1
MOD_CONTROL = 0x0002
VK_G = 0x47
HOTKEY_ID = 9101

if sys.platform == "win32":
    try:
        user32 = ctypes.windll.user32
        user32.SetPropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.HANDLE]
        user32.GetPropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
        user32.GetPropW.restype = wintypes.HANDLE
        user32.RemovePropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
        user32.RemovePropW.restype = wintypes.HANDLE
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
    # Filter out Save As dialogs across languages
    if (
        title_lower.startswith("save")
        or "save as" in title_lower
        or "save file" in title_lower
        or "speichern unter" in title_lower
        or "enregistrer sous" in title_lower
    ):
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
            if txt == "save" or txt.startswith("save "):
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
    target_edit = None
    fallback_edit = None

    def enum_child(hwnd, _lparam):
        nonlocal target_edit, fallback_edit
        class_name = (ctypes.c_wchar * 256)()
        user32.GetClassNameW(hwnd, class_name, 256)
        if class_name.value == "Edit":
            # Verify it is enabled and visible
            if user32.IsWindowVisible(hwnd) and user32.IsWindowEnabled(hwnd):
                ctrl_id = user32.GetDlgCtrlID(hwnd)
                # 0x47C (1148) is the standard Windows ComboBoxEx32 filename box
                # 0x480 (1152) is the standard Windows direct Edit filename box
                if ctrl_id in (1148, 1152):
                    target_edit = hwnd
                    return False
                if isinstance(hwnd, int) and hwnd > 0:
                    parent = user32.GetParent(hwnd)
                    if parent and user32.GetDlgCtrlID(parent) in (1148, 1152):
                        target_edit = hwnd
                        return False
                if fallback_edit is None:
                    fallback_edit = hwnd
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumChildWindows(dialog_hwnd, WNDENUMPROC(enum_child), 0)
    return target_edit or fallback_edit


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

    # Bring dialog into foreground to guarantee proper input routing
    try:
        user32.SetForegroundWindow(dialog_hwnd)
    except Exception:
        pass

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
    """Floating docked action pill attached to an active Windows File Dialog.

    Single recent file: ``↵ Select``.  Batch of 2+ files: ``↵ Latest`` / ``⇉ All N`` /
    ``⧉ Copy``, a hover checklist to untick files, and drag-from-pill (native
    ``CF_HDROP``) so the whole batch can be dropped into any application.
    """

    def __init__(self, dialog_hwnd: int, recent_data: dict) -> None:
        self.dialog_hwnd = dialog_hwnd
        self.recent_data = recent_data
        self.file_path = recent_data["path"]
        self.filename = recent_data["name"]
        self.category = recent_data.get("category", "default")
        self.accent_color = recent_data.get("color", ACTION_COLOR)
        self.is_closed = False
        self._is_injecting = False
        self._in_drag = False
        self._drag_origin: tuple[int, int] | None = None
        self._drag_started = False
        self._cooldown_until = 0.0
        self._timeout_job: str | None = None
        self.checklist_win: tk.Toplevel | None = None
        self.checklist_canvas: tk.Canvas | None = None
        self._leave_job: str | None = None

        items = recent_data.get("items")
        if not items:
            items = [{k: recent_data[k] for k in ("path", "name", "category", "color", "timestamp") if k in recent_data}]
        self.items: list[dict] = list(items)
        self.checked: dict[str, bool] = {i["path"]: True for i in self.items}
        self.is_batch = len(self.items) > 1
        self.batch_id = str(recent_data.get("batch_id") or "")
        self.settings = batch_handoff.batch_settings()
        self.hotkeys: dict[str, tuple[int, int]] = {
            "latest": batch_handoff.parse_hotkey(self.settings.get("hotkey_latest", "Ctrl+G")) or (MOD_CONTROL, VK_G),
            "all": batch_handoff.parse_hotkey(self.settings.get("hotkey_all", "Ctrl+Shift+G")) or (MOD_CONTROL | 0x4, VK_G),
            "copy": batch_handoff.parse_hotkey(self.settings.get("hotkey_copy", "Ctrl+Shift+C")) or (MOD_CONTROL | 0x4, 0x43),
        }

        # Mark dialog window with property to prevent duplicate switchers
        if sys.platform == "win32" and self.dialog_hwnd:
            try:
                ctypes.windll.user32.SetPropW(
                    self.dialog_hwnd, "DownloadSorterQuickSwitcher", 1
                )
            except Exception:
                pass

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
                wid = self.root.winfo_id()
                if isinstance(wid, int) and wid > 0:
                    hwnd = ctypes.windll.user32.GetParent(wid)
                    if isinstance(hwnd, int) and hwnd > 0:
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

        self.pill_w = round((520 if self.is_batch else 350) * self.scale)
        self.pill_h = round((52 if self.is_batch else 42) * self.scale)
        self.icon_photo: ImageTk.PhotoImage | None = None
        self.last_x: int | None = None
        self.last_y: int | None = None

        self._build_ui()
        self._position_pill()

        # Hotkeys exist only while this pill is alive (unregistered in close())
        self._register_hotkey()

        # Bind Escape key to instantly dismiss
        self.root.bind("<Escape>", lambda e: self.close())

        # Track dialog position and poll hotkey
        self.root.after(100, self._track_dialog)
        self._arm_timeout(10000)  # 10s auto-timeout

    # ---- construction ---------------------------------------------------

    def _arm_timeout(self, ms: int) -> None:
        try:
            if self._timeout_job:
                self.root.after_cancel(self._timeout_job)
            self._timeout_job = self.root.after(ms, self.close)
        except Exception:
            self._timeout_job = None

    def _make_button(self, text: str, command, primary: bool = False) -> tk.Label:
        btn = tk.Label(
            self.container,
            text=text,
            font=(FONT_FAMILY, FONT_SIZE_XS, "bold"),
            fg=self.accent_color if primary else TEXT_MAIN,
            bg=BTN_BG,
            cursor="hand2",
            padx=int(6 * self.scale),
            pady=int(2 * self.scale),
        )
        btn.pack(side="left", padx=int(3 * self.scale))
        btn.bind("<ButtonRelease-1>", lambda e: (command(), "break")[1])
        btn.bind("<Enter>", lambda e: (btn.config(bg=BTN_HOVER), self.on_enter()))
        btn.bind("<Leave>", lambda e: (btn.config(bg=BTN_BG), self.on_leave()))
        return btn

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
        else:
            glyph = CATEGORY_GLYPHS.get(self.category, "⚡")
            self.icon_lbl = tk.Label(
                self.container, text=glyph, font=(FONT_FAMILY, FONT_SIZE_SM, "bold"),
                fg=self.accent_color, bg=BG_COLOR,
            )
        self.icon_lbl.pack(side="left", padx=(int(6 * self.scale), int(2 * self.scale)))

        # Center content: Filename & context
        self.center_frame = tk.Frame(self.container, bg=BG_COLOR)
        self.center_frame.pack(side="left", fill="both", expand=True, padx=(int(4 * self.scale), 0))

        disp_name = self.filename
        if self.is_batch:
            disp_name = f"{len(self.items)} files · {self.filename}"
        limit = 40 if self.is_batch else 24
        if len(disp_name) > limit:
            disp_name = disp_name[: limit - 3] + "…"

        tag_text = f"⚡ BATCH · {len(self.items)} FILES" if self.is_batch else "⚡ USE RECENT"
        self.tag_lbl = tk.Label(
            self.center_frame, text=tag_text, font=(FONT_FAMILY, FONT_SIZE_XS, "bold"),
            fg=self.accent_color, bg=BG_COLOR, anchor="w",
        )
        self.tag_lbl.pack(fill="x", pady=(int(2 * self.scale), 0))

        self.name_lbl = tk.Label(
            self.center_frame, text=disp_name, font=(FONT_FAMILY, FONT_SIZE_XS, "bold"),
            fg=TEXT_MAIN, bg=BG_COLOR, anchor="w",
        )
        self.name_lbl.pack(fill="x")

        self.hint_lbl = None
        if self.is_batch:
            self.hint_lbl = tk.Label(
                self.center_frame, text="drag me anywhere · hover to untick files",
                font=(FONT_FAMILY, FONT_SIZE_XS), fg=TEXT_MUTED, bg=BG_COLOR, anchor="w",
            )
            self.hint_lbl.pack(fill="x")

        # Close button (packed first on the right so it never gets clipped)
        self.close_btn = tk.Label(
            self.container, text="✕", font=(FONT_FAMILY, FONT_SIZE_XS, "bold"),
            fg=CLOSE_COLOR, bg=BG_COLOR, cursor="hand2", padx=int(6 * self.scale),
        )
        self.close_btn.pack(side="right")
        self.close_btn.bind("<Button-1>", lambda e: (self.close(), "break")[1])
        self.close_btn.bind("<Enter>", lambda e: self.close_btn.config(fg=CLOSE_HOVER, bg=CLOSE_HOVER_BG))
        self.close_btn.bind("<Leave>", lambda e: self.close_btn.config(fg=CLOSE_COLOR, bg=BG_COLOR))

        # Action buttons
        latest_label = "↵ Latest" if self.is_batch else "↵ Select (Ctrl+G)"
        self.action_btn = self._make_button(latest_label, self.select_file, primary=True)
        self.all_btn = None
        self.copy_btn = None
        if self.is_batch:
            self.all_btn = self._make_button(f"⇉ All {len(self.items)}", self.select_all, primary=True)
            self.copy_btn = self._make_button("⧉ Copy", self.copy_files)

        # Click selects the latest; press-and-drag starts a native file drag
        for w in (self.container, self.center_frame, self.tag_lbl, self.name_lbl,
                  self.stripe, self.icon_lbl) + ((self.hint_lbl,) if self.hint_lbl else ()):
            w.bind("<ButtonPress-1>", self._on_press)
            w.bind("<B1-Motion>", self._on_motion)
            w.bind("<ButtonRelease-1>", self._on_release)
            w.bind("<Enter>", self.on_enter)
            w.bind("<Leave>", self.on_leave)

    # ---- hover checklist ----------------------------------------------------

    def _expand_checklist(self) -> None:
        if not self.is_batch or self.checklist_win is not None or self.is_closed:
            return
        try:
            win = tk.Toplevel(self.root)
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            win.config(bg=self.hover_border)
            body = tk.Frame(win, bg=BG_COLOR)
            body.pack(fill="both", expand=True, padx=1, pady=1)
            visible_rows = min(len(self.items), 8)
            row_h = round(24 * self.scale)
            canvas = tk.Canvas(body, bg=BG_COLOR, highlightthickness=0, height=visible_rows * row_h,
                               width=self.pill_w - 2)
            canvas.pack(side="left", fill="both", expand=True)
            self.checklist_canvas = canvas
            inner = tk.Frame(canvas, bg=BG_COLOR)
            canvas_window = canvas.create_window((0, 0), window=inner, anchor="nw")
            canvas.bind("<Configure>", lambda e: canvas.itemconfig(canvas_window, width=e.width))
            self._check_vars: dict[str, tk.BooleanVar] = {}
            for item in reversed(self.items):  # newest first
                var = tk.BooleanVar(value=self.checked.get(item["path"], True))
                self._check_vars[item["path"]] = var
                row = tk.Frame(inner, bg=BG_COLOR)
                row.pack(fill="x")
                cb = tk.Checkbutton(
                    row, variable=var, bg=BG_COLOR, activebackground=HOVER_BG, selectcolor=BG_COLOR,
                    fg=self.accent_color, bd=0, highlightthickness=0,
                    command=lambda p=item["path"], v=var: self._on_toggle(p, v),
                )
                cb.pack(side="left")
                name = item.get("name", Path(item["path"]).name)
                lbl = tk.Label(row, text=name if len(name) <= 52 else name[:49] + "…",
                               font=(FONT_FAMILY, FONT_SIZE_XS), fg=TEXT_MAIN, bg=BG_COLOR, anchor="w", cursor="hand2")
                lbl.pack(side="left", fill="x", expand=True)
                lbl.bind("<B1-Motion>", lambda e, p=item["path"]: self._begin_drag([p]))
                lbl.bind("<Button-1>", lambda e, p=item["path"], v=var: (v.set(not v.get()), self._on_toggle(p, v)))
                for w in (row, cb, lbl):
                    w.bind("<Enter>", lambda e: self.on_enter())
                    w.bind("<Leave>", lambda e: self.on_leave())
            inner.update_idletasks()
            canvas.config(scrollregion=canvas.bbox("all"))
            if len(self.items) > 8:
                canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(-1 * (e.delta // 120), "units"))
            win.update_idletasks()
            x, y = self.root.winfo_x(), self.root.winfo_y() + self.root.winfo_height() + 4
            win.geometry(f"+{x}+{y}")
            self.checklist_win = win
        except Exception as exc:
            logging.debug("Checklist unavailable: %s", exc)
            self.checklist_win = None

    def _collapse_checklist(self) -> None:
        if self.checklist_canvas is not None:
            try:
                self.checklist_canvas.unbind_all("<MouseWheel>")
            except Exception:
                pass
            self.checklist_canvas = None
        win, self.checklist_win = self.checklist_win, None
        if win is not None:
            try:
                win.destroy()
            except Exception:
                pass

    def _on_toggle(self, path: str, var) -> None:
        self.checked[path] = bool(var.get())
        n = len(self._checked_paths())
        if self.all_btn is not None:
            self.all_btn.config(text=f"⇉ All {n}")
        self._arm_timeout(10000)

    def _checked_items(self) -> list[dict]:
        return [i for i in self.items if self.checked.get(i["path"], True) and Path(i["path"]).exists()]

    def _checked_paths(self) -> list[str]:
        return [i["path"] for i in self._checked_items()]

    # ---- hotkeys ----------------------------------------------------------------

    _HOTKEY_IDS = {"latest": HOTKEY_ID, "all": HOTKEY_ID + 1, "copy": HOTKEY_ID + 2}

    def _register_hotkey(self) -> None:
        if sys.platform == "win32":
            try:
                user32 = ctypes.windll.user32
                wid = self.root.winfo_id()
                if isinstance(wid, int) and wid > 0:
                    hwnd = user32.GetParent(wid)
                    for name, (mods, vk) in self.hotkeys.items():
                        if name != "latest" and not self.is_batch:
                            continue
                        user32.RegisterHotKey(hwnd, self._HOTKEY_IDS[name], mods, vk)
            except Exception:
                pass

    def _unregister_hotkey(self) -> None:
        if sys.platform == "win32":
            try:
                user32 = ctypes.windll.user32
                wid = self.root.winfo_id()
                if isinstance(wid, int) and wid > 0:
                    hwnd = user32.GetParent(wid)
                    for hid in self._HOTKEY_IDS.values():
                        user32.UnregisterHotKey(hwnd, hid)
            except Exception:
                pass

    def _position_pill(self) -> None:
        """Position the pill docked neatly above or below the target dialog."""
        if sys.platform == "win32":
            try:
                user32 = ctypes.windll.user32
                rect = wintypes.RECT()
                if user32.GetWindowRect(self.dialog_hwnd, ctypes.byref(rect)):
                    # Determine monitor work area bounds for multi-monitor / virtual desktop accuracy
                    class MONITORINFO(ctypes.Structure):
                        _fields_ = [
                            ("cbSize", ctypes.c_ulong),
                            ("rcMonitor", wintypes.RECT),
                            ("rcWork", wintypes.RECT),
                            ("dwFlags", ctypes.c_ulong),
                        ]

                    mon = user32.MonitorFromWindow(self.dialog_hwnd, 2)
                    minfo = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
                    work_top = 0
                    work_left = 0
                    sw = self.root.winfo_screenwidth()
                    work_right = sw
                    if mon and user32.GetMonitorInfoW(mon, ctypes.byref(minfo)):
                        work_top = minfo.rcWork.top
                        work_left = minfo.rcWork.left
                        work_right = minfo.rcWork.right

                    # Right-aligned, docked right above the dialog's top title bar
                    x = rect.right - self.pill_w - round(16 * self.scale)
                    x = max(work_left + round(8 * self.scale), min(x, work_right - self.pill_w - round(8 * self.scale)))
                    y = rect.top - self.pill_h - round(6 * self.scale)
                    if y < work_top + 4:
                        # If dialog is at the top of its monitor, dock neatly below title bar
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

        if sys.platform == "win32" and not self._in_drag:
            user32 = ctypes.windll.user32
            # If the dialog window was closed or minimized by the user, close the pill immediately
            if (
                not user32.IsWindow(self.dialog_hwnd)
                or not user32.IsWindowVisible(self.dialog_hwnd)
                or user32.IsIconic(self.dialog_hwnd)
            ):
                self.close()
                return

            # Check foreground window: if user switched away to another application, auto-dismiss
            fg = user32.GetForegroundWindow()
            wid = self.root.winfo_id()
            pill_hwnd = user32.GetParent(wid) if (isinstance(wid, int) and wid > 0) else 0
            checklist_hwnd = 0
            if self.checklist_win is not None:
                try:
                    cid = self.checklist_win.winfo_id()
                    if isinstance(cid, int) and cid > 0:
                        checklist_hwnd = user32.GetParent(cid)
                except Exception:
                    checklist_hwnd = 0
            if fg not in (self.dialog_hwnd, pill_hwnd, wid, checklist_hwnd):
                self.unfocused_ticks = getattr(self, "unfocused_ticks", 0) + 1
                if self.unfocused_ticks >= 10:  # ~1 second unfocused
                    self.close()
                    return
            else:
                self.unfocused_ticks = 0
                # Check if user pressed Escape while dialog/pill has keyboard focus
                if user32.GetAsyncKeyState(0x1B) & 0x8000:
                    self.close()
                    return

        self._position_pill()
        self._check_hotkey()
        self.root.after(100, self._track_dialog)

    def _check_hotkey(self) -> None:
        """Poll the pill-scoped hotkeys while the dialog or pill has keyboard focus."""
        if sys.platform != "win32" or self._in_drag or time.monotonic() < self._cooldown_until:
            return
        try:
            user32 = ctypes.windll.user32
            fg = user32.GetForegroundWindow()
            wid = self.root.winfo_id()
            pill_parent = user32.GetParent(wid) if (isinstance(wid, int) and wid > 0) else 0
            if fg not in (self.dialog_hwnd, pill_parent, wid):
                return
            if self.is_batch:
                if batch_handoff.hotkey_is_down(*self.hotkeys["all"]):
                    self._cooldown_until = time.monotonic() + 1.0
                    self.select_all()
                    return
                if batch_handoff.hotkey_is_down(*self.hotkeys["copy"]):
                    self._cooldown_until = time.monotonic() + 1.0
                    self.copy_files()
                    return
            if batch_handoff.hotkey_is_down(*self.hotkeys["latest"]):
                self.select_file()
        except Exception:
            pass

    # ---- hover / colors ---------------------------------------------------------------

    def _recolor(self, bg: str) -> None:
        for w in (self.container, self.center_frame, self.tag_lbl, self.name_lbl, self.icon_lbl):
            w.config(bg=bg)
        if self.hint_lbl is not None:
            self.hint_lbl.config(bg=bg)

    def on_enter(self, _event=None) -> None:
        if self._leave_job:
            try:
                self.root.after_cancel(self._leave_job)
            except Exception:
                pass
            self._leave_job = None
        self.root.config(bg=self.hover_border)
        self._recolor(HOVER_BG)
        self.action_btn.config(bg=BTN_HOVER)
        self._arm_timeout(20000 if self.is_batch else 10000)
        self._expand_checklist()

    def on_leave(self, _event=None) -> None:
        try:
            if self._leave_job:
                self.root.after_cancel(self._leave_job)
            self._leave_job = self.root.after(350, self._leave_if_pointer_gone)
        except Exception:
            pass

    def _leave_if_pointer_gone(self) -> None:
        self._leave_job = None
        if self.is_closed or self._in_drag:
            return
        try:
            under = self.root.winfo_containing(self.root.winfo_pointerx(), self.root.winfo_pointery())
            if under is not None:
                top = under.winfo_toplevel()
                if top is self.root or (self.checklist_win is not None and top is self.checklist_win):
                    return
        except Exception:
            pass
        self.root.config(bg=self.normal_border)
        self._recolor(BG_COLOR)
        self.action_btn.config(bg=BTN_BG)
        self._collapse_checklist()
        self._arm_timeout(6000)

    # ---- press / drag / click ----------------------------------------------------------------

    def _on_press(self, event) -> None:
        self._drag_origin = (event.x_root, event.y_root)
        self._drag_started = False

    def _on_motion(self, event) -> None:
        if self._drag_origin is None or self._drag_started or self._in_drag:
            return
        dx = abs(event.x_root - self._drag_origin[0])
        dy = abs(event.y_root - self._drag_origin[1])
        if max(dx, dy) > round(6 * self.scale):
            self._drag_started = True
            self._begin_drag(self._checked_paths())

    def _on_release(self, _event=None) -> None:
        started, self._drag_origin = self._drag_started, None
        self._drag_started = False
        if not started:
            self.select_file()

    def _begin_drag(self, paths: list[str]) -> None:
        """Native Windows file drag (copy) of the given files; clears them when dropped."""
        if self._in_drag or self.is_closed or not paths:
            return
        self._in_drag = True
        try:
            if sys.platform == "win32":
                ctypes.windll.user32.ReleaseCapture()
            self._collapse_checklist()
            wid = self.root.winfo_id() if hasattr(self.root, "winfo_id") else 0
            hwnd = ctypes.windll.user32.GetParent(wid) if (sys.platform == "win32" and isinstance(wid, int) and wid > 0) else None
            effect = batch_handoff.start_file_drag(paths, hwnd)
        except Exception as exc:
            logging.debug("Drag failed: %s", exc)
            effect = 0
        finally:
            self._in_drag = False
        if effect:
            self._finish_handoff(paths, clear_all=len(paths) >= len(self.items))
            self.root.after(120, self.close)

    # ---- actions --------------------------------------------------------------------------------

    def _downloads_root(self) -> Path | None:
        try:
            return batch_handoff.downloads_root()
        except Exception:
            return None

    def _finish_handoff(self, paths: list[str], clear_all: bool = False) -> None:
        """Remove handed-off files from the batch buffer and dismiss their ribbons."""
        try:
            if clear_all:
                clear_recent_file()
            else:
                consume_items(paths)
        except Exception:
            pass
        try:
            from ribbon_popup import dismiss_active_ribbons
            for p in paths:
                dismiss_active_ribbons(p)
        except Exception:
            pass

    def _flash(self, button, text: str, ok: bool) -> None:
        try:
            if ok:
                button.config(text=text, fg="#FFFFFF", bg=self.accent_color)
            else:
                button.config(text=text, fg="#FFFFFF", bg=CLOSE_HOVER)
            self.root.update_idletasks()
        except Exception:
            pass

    def select_file(self) -> None:
        """Inject the latest (checked) file into the dialog, dismiss ribbons, and close the pill."""
        if self.is_closed or self._is_injecting:
            return

        checked = self._checked_paths()
        target = checked[-1] if checked else self.file_path
        if not Path(target).exists():
            self.action_btn.config(text="✕ File not found", fg="#FFFFFF", bg=CLOSE_HOVER)
            self.root.update_idletasks()
            self.root.after(1000, self.close)
            return

        self._is_injecting = True
        self._unregister_hotkey()

        # Perform file injection into the Windows file dialog
        success = inject_file_into_dialog(self.dialog_hwnd, target)

        if success:
            # Visually indicate selection
            self.action_btn.config(text="✓ Injected!", fg="#FFFFFF", bg=self.accent_color)
            self.root.update_idletasks()

            # Latest keeps the rest of the batch for the next dialog
            # and dismisses the ribbon notification for the injected file.
            self._finish_handoff([target], clear_all=False)

            # Close the pill cleanly after short confirmation delay
            self.root.after(180, self.close)
        else:
            self.action_btn.config(text="✕ Dialog unavailable", fg="#FFFFFF", bg=CLOSE_HOVER)
            self.root.update_idletasks()
            self.root.after(800, self.close)

    select_latest = select_file

    def select_all(self) -> None:
        """Inject every ticked file at once (falls back gracefully when the dialog can't take many)."""
        if self.is_closed or self._is_injecting:
            return
        paths = self._checked_paths()
        button = self.all_btn or self.action_btn
        if len(paths) == 0:
            self._flash(button, "✕ No files selected", False)
            return
        if len(paths) == 1:
            self.select_file()
            return
        self._is_injecting = True
        self._unregister_hotkey()
        result = batch_handoff.inject_files_into_dialog(
            self.dialog_hwnd, paths, batch_id=self.batch_id, downloads_root=self._downloads_root(),
            settings=self.settings,
        )
        if result.ok:
            label = f"✓ {len(result.injected)} injected" if result.mode == "multi" else "✓ Latest only"
            self._flash(button, label, True)
            self._finish_handoff(result.injected or paths, clear_all=result.mode == "multi")
            self.root.after(250 if result.mode == "multi" else 1800, self.close)
        else:
            self._flash(button, f"✕ {result.message or 'Dialog unavailable'}", False)
            self.root.after(2500, self.close)

    def copy_files(self) -> None:
        """Copy the ticked files to the clipboard as real files (Ctrl+V pastes them anywhere)."""
        if self.is_closed:
            return
        paths = self._checked_paths()
        button = self.copy_btn or self.action_btn
        if len(paths) == 0:
            self._flash(button, "✕ No files selected", False)
            return
        if batch_handoff.copy_files_to_clipboard(paths):
            self._flash(button, f"✓ Copied {len(paths)}", True)
            self._finish_handoff(paths, clear_all=len(paths) >= len(self.items))
            self.root.after(700, self.close)
        else:
            self._flash(button, "✕ Clipboard busy", False)

    def close(self) -> None:
        if self.is_closed:
            return
        self.is_closed = True
        self._unregister_hotkey()
        self._collapse_checklist()

        if sys.platform == "win32" and self.dialog_hwnd:
            try:
                ctypes.windll.user32.RemovePropW(
                    self.dialog_hwnd, "DownloadSorterQuickSwitcher"
                )
            except Exception:
                pass

        try:
            self.root.quit()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def run(self) -> None:
        try:
            self.root.mainloop()
        finally:
            self.close()


def spawn_quick_switcher(dialog_hwnd: int) -> None:
    """Spawn the Quick Switcher pill as a lightweight decoupled process."""
    if sys.platform == "win32" and dialog_hwnd:
        try:
            if ctypes.windll.user32.GetPropW(dialog_hwnd, "DownloadSorterQuickSwitcher"):
                return
        except Exception:
            pass

    recent = get_recent_file()
    if not recent:
        return

    if getattr(sys, "frozen", False):
        args = [sys.executable, "--dialog", str(dialog_hwnd)]
    else:
        python_bin = ROOT / ".venv" / "Scripts" / "pythonw.exe"
        if not python_bin.exists():
            python_bin = PYTHON
        args = [str(python_bin), str(Path(__file__).resolve()), "--dialog", str(dialog_hwnd)]

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
    parser.add_argument("--version", action="version", version=about.version_string())
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
        if sys.platform == "win32" and args.dialog:
            try:
                if ctypes.windll.user32.GetPropW(args.dialog, "DownloadSorterQuickSwitcher"):
                    return 0
            except Exception:
                pass
        recent = get_recent_file()
        if not recent:
            return 1
        pill = QuickSwitcherPill(args.dialog, recent)
        pill.run()
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
