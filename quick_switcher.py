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

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer
from PySide6.QtGui import QCursor, QFont, QGuiApplication, QImage, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

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


def _pil_to_pixmap(icon_img) -> QPixmap | None:
    """Convert a PIL image to an owned QPixmap without ImageTk."""
    if icon_img is None:
        return None
    rgba = icon_img.convert("RGBA")
    raw = rgba.tobytes("raw", "RGBA")
    qimg = QImage(raw, rgba.width, rgba.height, rgba.width * 4, QImage.Format.Format_RGBA8888).copy()
    return QPixmap.fromImage(qimg)


def _apply_dwm_frame(hwnd: int) -> None:
    """Enable Windows 11 DWM rounded corners and soft drop shadow when available."""
    if sys.platform != "win32" or not isinstance(hwnd, int) or hwnd <= 0:
        return
    try:
        pref = ctypes.c_int(2)  # DWMWCP_ROUND
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref))

        class MARGINS(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_int),
                ("right", ctypes.c_int),
                ("top", ctypes.c_int),
                ("bottom", ctypes.c_int),
            ]

        margins = MARGINS(1, 1, 1, 1)
        ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))
    except Exception:
        pass


class _PillWindow(QFrame):
    """Frameless topmost tool window for the Quick Switcher pill and checklist."""

    def __init__(self, owner: QuickSwitcherPill) -> None:
        super().__init__(None)
        self._owner = owner
        self.setObjectName("qsRoot")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

    def enterEvent(self, event) -> None:
        self._owner.on_enter(event)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._owner.on_leave(event)
        super().leaveEvent(event)


class _DragSurfaceFilter(QObject):
    """Forwards click/drag gestures from non-button pill surfaces to QuickSwitcherPill."""

    def __init__(self, owner: QuickSwitcherPill) -> None:
        super().__init__(owner.root)
        self._owner = owner

    def eventFilter(self, _obj: QObject, event: QEvent) -> bool:
        etype = event.type()
        if etype == QEvent.Type.MouseButtonPress and getattr(event, "button", lambda: None)() == Qt.MouseButton.LeftButton:
            self._owner._on_press(event)
        elif etype == QEvent.Type.MouseMove and getattr(event, "buttons", lambda: Qt.MouseButton.NoButton)() & Qt.MouseButton.LeftButton:
            self._owner._on_motion(event)
        elif etype == QEvent.Type.MouseButtonRelease and getattr(event, "button", lambda: None)() == Qt.MouseButton.LeftButton:
            self._owner._on_release(event)
        return False


class _ChecklistRowFilter(QObject):
    """Toggles a checklist checkbox on filename click or starts a single-file drag."""

    def __init__(self, owner: QuickSwitcherPill, checkbox: QCheckBox, path: str) -> None:
        super().__init__(checkbox)
        self._owner = owner
        self._checkbox = checkbox
        self._path = path
        self._origin: QPoint | None = None
        self._dragged = False

    def eventFilter(self, _obj: QObject, event: QEvent) -> bool:
        etype = event.type()
        if etype == QEvent.Type.MouseButtonPress and getattr(event, "button", lambda: None)() == Qt.MouseButton.LeftButton:
            self._origin = event.globalPosition().toPoint()
            self._dragged = False
            return True
        if etype == QEvent.Type.MouseMove and getattr(event, "buttons", lambda: Qt.MouseButton.NoButton)() & Qt.MouseButton.LeftButton:
            if self._origin is not None and not self._dragged:
                pos = event.globalPosition().toPoint()
                if (pos - self._origin).manhattanLength() > round(6 * self._owner.scale):
                    self._dragged = True
                    self._owner._begin_drag([self._path])
                    return True
        elif etype == QEvent.Type.MouseButtonRelease and getattr(event, "button", lambda: None)() == Qt.MouseButton.LeftButton:
            was_dragged = self._dragged
            self._origin = None
            self._dragged = False
            if not was_dragged:
                self._checkbox.toggle()
            return True
        return False


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
        self._running = False
        self._drag_origin: tuple[int, int] | None = None
        self._drag_started = False
        self._cooldown_until = 0.0
        self.checklist_win: QFrame | None = None
        self.checklist_scroll: QScrollArea | None = None
        self.checklist_canvas: QScrollArea | None = None
        self._check_boxes: dict[str, QCheckBox] = {}
        self._hovered = False

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

        if QApplication.instance() is None:
            try:
                QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
                    Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
                )
            except Exception:
                pass
            self.app = QApplication(sys.argv[:1])
            self._owns_app = True
        else:
            self.app = QApplication.instance()
            self._owns_app = False

        # Mark dialog window with property to prevent duplicate switchers
        if sys.platform == "win32" and self.dialog_hwnd:
            try:
                ctypes.windll.user32.SetPropW(
                    self.dialog_hwnd, "DownloadSorterQuickSwitcher", 1
                )
            except Exception:
                pass

        self.normal_border = blend_colors(self.accent_color, BORDER_COLOR, 0.40)
        self.hover_border = blend_colors(self.accent_color, COLORS.get("borderStrong", "#3A4352"), 0.65)

        self.root = _PillWindow(self)
        self.scale = 1.0
        screen = self.root.screen() or QGuiApplication.primaryScreen()
        dpr = float(screen.devicePixelRatio()) if screen is not None else 1.0
        self._dpr = max(1.0, dpr)

        self.pill_w = round(520 if self.is_batch else 350)
        self.pill_h = round(54 if self.is_batch else 44)
        self.icon_pixmap: QPixmap | None = None
        self.last_x: int | None = None
        self.last_y: int | None = None
        self.root.setFixedSize(self.pill_w, self.pill_h)

        self.action_btn: QPushButton | None = None
        self.all_btn: QPushButton | None = None
        self.copy_btn: QPushButton | None = None
        self.close_btn: QPushButton | None = None

        self._timeout_timer = QTimer(self.root)
        self._timeout_timer.setSingleShot(True)
        self._timeout_timer.timeout.connect(self.close)

        self._leave_timer = QTimer(self.root)
        self._leave_timer.setSingleShot(True)
        self._leave_timer.timeout.connect(self._leave_if_pointer_gone)

        self._track_timer = QTimer(self.root)
        self._track_timer.setInterval(100)
        self._track_timer.timeout.connect(self._track_dialog)

        self._build_ui()
        self._apply_pill_style(hovered=False)
        _apply_dwm_frame(self._root_hwnd())
        self._position_pill()
        self._register_hotkey()

        self._shortcut_escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), self.root)
        self._shortcut_escape.activated.connect(self.close)

        self._track_timer.start()
        self._arm_timeout(10000)  # 10s auto-timeout

    def _root_hwnd(self) -> int:
        try:
            wid = self.root.winId()
            return int(wid) if wid else 0
        except Exception:
            return 0

    def _apply_pill_style(self, hovered: bool) -> None:
        self._hovered = hovered
        bg = HOVER_BG if hovered else BG_COLOR
        border = self.hover_border if hovered else self.normal_border
        btn_bg = BTN_HOVER if hovered else BTN_BG
        self.root.setStyleSheet(
            f"#qsRoot {{ background: {bg}; border: 1px solid {border}; border-radius: 6px; }}"
            f"QWidget#qsContainer, QWidget#qsCenter {{ background: transparent; border: none; }}"
            f"QLabel {{ background: transparent; border: none; font-family: '{FONT_FAMILY}'; }}"
        )
        if self.action_btn is not None:
            self._style_action_button(self.action_btn, primary=True, bg=btn_bg)
        for btn in (self.all_btn, self.copy_btn):
            if btn is not None:
                self._style_action_button(btn, primary=(btn is self.all_btn), bg=btn_bg)

    def _style_action_button(self, btn: QPushButton, primary: bool, bg: str = BTN_BG) -> None:
        fg = self.accent_color if primary else TEXT_MAIN
        btn.setStyleSheet(
            f"QPushButton {{ background: {bg}; color: {fg}; border: 1px solid {BORDER_COLOR};"
            f" border-radius: 5px; padding: 3px 8px; font-family: '{FONT_FAMILY}';"
            f" font-size: {FONT_SIZE_XS}pt; font-weight: 700; }}"
            f"QPushButton:hover {{ background: {BTN_HOVER}; }}"
        )

    # ---- construction ---------------------------------------------------

    def _arm_timeout(self, ms: int) -> None:
        try:
            self._timeout_timer.start(max(1, int(ms)))
        except Exception:
            pass

    def _make_button(self, text: str, command, primary: bool = False) -> QPushButton:
        btn = QPushButton(text, self.container)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._style_action_button(btn, primary=primary)
        btn.clicked.connect(lambda _checked=False: command())
        self.container_layout.addWidget(btn)
        return btn

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self.root)
        root_layout.setContentsMargins(1, 1, 1, 1)
        root_layout.setSpacing(0)

        self.container = QWidget(self.root)
        self.container.setObjectName("qsContainer")
        self.container.setCursor(Qt.CursorShape.PointingHandCursor)
        self.container_layout = QHBoxLayout(self.container)
        self.container_layout.setContentsMargins(0, 2, 4, 2)
        self.container_layout.setSpacing(6)
        root_layout.addWidget(self.container)

        # Left accent stripe
        self.stripe = QFrame(self.container)
        self.stripe.setFixedWidth(4)
        self.stripe.setStyleSheet(f"background: {self.accent_color}; border: none;")
        self.container_layout.addWidget(self.stripe)

        # Category icon badge
        icon_size = 26
        icon_img = render_category_icon(self.category, self.accent_color, size=icon_size)
        self.icon_pixmap = _pil_to_pixmap(icon_img)
        self.icon_lbl = QLabel(self.container)
        if self.icon_pixmap is not None and not self.icon_pixmap.isNull():
            self.icon_lbl.setPixmap(self.icon_pixmap)
        else:
            glyph = CATEGORY_GLYPHS.get(self.category, "⚡")
            self.icon_lbl.setText(glyph)
            self.icon_lbl.setStyleSheet(
                f"color: {self.accent_color}; font-family: '{FONT_FAMILY}'; font-size: {FONT_SIZE_SM}pt; font-weight: 700;"
            )
        self.icon_lbl.setContentsMargins(4, 0, 2, 0)
        self.container_layout.addWidget(self.icon_lbl)

        # Center content: Filename & context
        self.center_frame = QWidget(self.container)
        self.center_frame.setObjectName("qsCenter")
        center_layout = QVBoxLayout(self.center_frame)
        center_layout.setContentsMargins(2, 1, 0, 1)
        center_layout.setSpacing(0)

        disp_name = self.filename
        if self.is_batch:
            disp_name = f"{len(self.items)} files · {self.filename}"
        limit = 40 if self.is_batch else 24
        if len(disp_name) > limit:
            disp_name = disp_name[: limit - 3] + "…"

        tag_text = f"⚡ BATCH · {len(self.items)} FILES" if self.is_batch else "⚡ USE RECENT"
        self.tag_lbl = QLabel(tag_text, self.center_frame)
        self.tag_lbl.setStyleSheet(
            f"color: {self.accent_color}; font-family: '{FONT_FAMILY}'; font-size: 8pt; font-weight: 700;"
        )
        center_layout.addWidget(self.tag_lbl)

        self.name_lbl = QLabel(disp_name, self.center_frame)
        self.name_lbl.setStyleSheet(
            f"color: {TEXT_MAIN}; font-family: '{FONT_FAMILY}'; font-size: {FONT_SIZE_XS}pt; font-weight: 700;"
        )
        center_layout.addWidget(self.name_lbl)

        self.hint_lbl: QLabel | None = None
        if self.is_batch:
            self.hint_lbl = QLabel("drag me anywhere · hover to untick files", self.center_frame)
            self.hint_lbl.setStyleSheet(
                f"color: {TEXT_MUTED}; font-family: '{FONT_FAMILY}'; font-size: 8pt;"
            )
            center_layout.addWidget(self.hint_lbl)

        self.container_layout.addWidget(self.center_frame, 1)

        # Action buttons
        latest_label = "↵ Latest" if self.is_batch else "↵ Select (Ctrl+G)"
        self.action_btn = self._make_button(latest_label, self.select_file, primary=True)
        self.all_btn = None
        self.copy_btn = None
        if self.is_batch:
            self.all_btn = self._make_button(f"⇉ All {len(self.items)}", self.select_all, primary=True)
            self.copy_btn = self._make_button("⧉ Copy", self.copy_files)

        # Close button placed on far right
        self.close_btn = QPushButton("✕", self.container)
        self.close_btn.setFixedSize(22, 22)
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.close_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {CLOSE_COLOR}; border: none; border-radius: 4px;"
            f" font-family: '{FONT_FAMILY}'; font-size: {FONT_SIZE_XS}pt; font-weight: 700; }}"
            f"QPushButton:hover {{ background: {CLOSE_HOVER_BG}; color: {CLOSE_HOVER}; }}"
        )
        self.close_btn.clicked.connect(lambda _checked=False: self.close())
        self.container_layout.addWidget(self.close_btn)

        # Click selects the latest; press-and-drag starts a native file drag
        self._drag_filter = _DragSurfaceFilter(self)
        drag_widgets = (self.container, self.center_frame, self.tag_lbl, self.name_lbl, self.stripe, self.icon_lbl)
        if self.hint_lbl is not None:
            drag_widgets = drag_widgets + (self.hint_lbl,)
        for w in drag_widgets:
            w.installEventFilter(self._drag_filter)

    # ---- hover checklist ----------------------------------------------------

    def _expand_checklist(self) -> None:
        if not self.is_batch or self.checklist_win is not None or self.is_closed:
            return
        try:
            win = QFrame(None)
            win.setObjectName("qsChecklist")
            win.setWindowFlags(
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.Tool
                | Qt.WindowType.WindowDoesNotAcceptFocus
            )
            win.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            win.setStyleSheet(
                f"#qsChecklist {{ background: {BG_COLOR}; border: 1px solid {self.hover_border}; border-radius: 6px; }}"
                f"QScrollArea {{ background: transparent; border: none; }}"
                f"QWidget#qsChecklistInner {{ background: {BG_COLOR}; }}"
                f"QLabel {{ color: {TEXT_MAIN}; background: transparent; font-family: '{FONT_FAMILY}'; font-size: {FONT_SIZE_XS}pt; }}"
            )

            outer = QVBoxLayout(win)
            outer.setContentsMargins(1, 1, 1, 1)
            outer.setSpacing(0)

            visible_rows = min(len(self.items), 8)
            row_h = 26
            scroll = QScrollArea(win)
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setFixedSize(self.pill_w - 2, visible_rows * row_h)
            outer.addWidget(scroll)

            inner = QWidget(scroll)
            inner.setObjectName("qsChecklistInner")
            inner_layout = QVBoxLayout(inner)
            inner_layout.setContentsMargins(8, 4, 8, 4)
            inner_layout.setSpacing(2)

            self._check_boxes = {}
            self._row_filters: list[_ChecklistRowFilter] = []
            for item in reversed(self.items):  # newest first
                path = item["path"]
                row = QWidget(inner)
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(2, 1, 2, 1)
                row_layout.setSpacing(6)

                cb = QCheckBox(row)
                cb.setChecked(self.checked.get(path, True))
                cb.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                cb.toggled.connect(lambda checked, p=path: self._on_toggle(p, checked))
                self._check_boxes[path] = cb
                row_layout.addWidget(cb)

                name = item.get("name", Path(path).name)
                lbl = QLabel(name if len(name) <= 52 else name[:49] + "…", row)
                lbl.setCursor(Qt.CursorShape.PointingHandCursor)
                row_filter = _ChecklistRowFilter(self, cb, path)
                lbl.installEventFilter(row_filter)
                self._row_filters.append(row_filter)
                row_layout.addWidget(lbl, 1)

                inner_layout.addWidget(row)

            scroll.setWidget(inner)
            self.checklist_scroll = scroll
            self.checklist_canvas = scroll

            pos = self.root.pos()
            win.move(pos.x(), pos.y() + self.root.height() + 4)
            win.setFixedWidth(self.pill_w)
            win.show()
            _apply_dwm_frame(int(win.winId()))
            self.checklist_win = win
        except Exception as exc:
            logging.debug("Checklist unavailable: %s", exc)
            self.checklist_win = None

    def _collapse_checklist(self) -> None:
        self.checklist_scroll = None
        self.checklist_canvas = None
        win, self.checklist_win = self.checklist_win, None
        if win is not None:
            try:
                win.close()
                win.deleteLater()
            except Exception:
                pass

    def _on_toggle(self, path: str, state) -> None:
        if isinstance(state, bool):
            is_checked = state
        elif hasattr(state, "isChecked"):
            is_checked = bool(state.isChecked())
        elif hasattr(state, "get"):
            is_checked = bool(state.get())
        else:
            is_checked = bool(state)
        self.checked[path] = is_checked
        n = len(self._checked_paths())
        if self.all_btn is not None:
            self.all_btn.setText(f"⇉ All {n}")
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
                hwnd = self._root_hwnd()
                if hwnd > 0:
                    user32 = ctypes.windll.user32
                    for name, (mods, vk) in self.hotkeys.items():
                        if name != "latest" and not self.is_batch:
                            continue
                        user32.RegisterHotKey(hwnd, self._HOTKEY_IDS[name], mods, vk)
            except Exception:
                pass

    def _unregister_hotkey(self) -> None:
        if sys.platform == "win32":
            try:
                hwnd = self._root_hwnd()
                if hwnd > 0:
                    user32 = ctypes.windll.user32
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
                    class MONITORINFO(ctypes.Structure):
                        _fields_ = [
                            ("cbSize", ctypes.c_ulong),
                            ("rcMonitor", wintypes.RECT),
                            ("rcWork", wintypes.RECT),
                            ("dwFlags", ctypes.c_ulong),
                        ]

                    mon = user32.MonitorFromWindow(self.dialog_hwnd, 2)
                    minfo = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
                    dpr = self._dpr
                    screen_geo = (self.root.screen() or QGuiApplication.primaryScreen()).availableGeometry()
                    work_top = screen_geo.top()
                    work_left = screen_geo.left()
                    work_right = screen_geo.right() + 1
                    if mon and user32.GetMonitorInfoW(mon, ctypes.byref(minfo)):
                        work_top = round(minfo.rcWork.top / dpr)
                        work_left = round(minfo.rcWork.left / dpr)
                        work_right = round(minfo.rcWork.right / dpr)

                    dlg_top = round(rect.top / dpr)
                    dlg_right = round(rect.right / dpr)

                    x = dlg_right - self.pill_w - 16
                    x = max(work_left + 8, min(x, work_right - self.pill_w - 8))
                    y = dlg_top - self.pill_h - 6
                    if y < work_top + 4:
                        y = dlg_top + 36

                    if (x, y) != (self.last_x, self.last_y):
                        self.last_x = x
                        self.last_y = y
                        self.root.setGeometry(x, y, self.pill_w, self.pill_h)
                    return
            except Exception:
                pass

        screen_geo = (self.root.screen() or QGuiApplication.primaryScreen()).availableGeometry()
        x = screen_geo.right() - self.pill_w - 40
        y = screen_geo.top() + 60
        self.root.setGeometry(x, y, self.pill_w, self.pill_h)

    def _track_dialog(self) -> None:
        if self.is_closed:
            return

        if sys.platform == "win32" and not self._in_drag:
            user32 = ctypes.windll.user32
            if (
                not user32.IsWindow(self.dialog_hwnd)
                or not user32.IsWindowVisible(self.dialog_hwnd)
                or user32.IsIconic(self.dialog_hwnd)
            ):
                self.close()
                return

            fg = user32.GetForegroundWindow()
            pill_hwnd = self._root_hwnd()
            checklist_hwnd = int(self.checklist_win.winId()) if self.checklist_win is not None else 0
            if fg not in (self.dialog_hwnd, pill_hwnd, checklist_hwnd):
                self.unfocused_ticks = getattr(self, "unfocused_ticks", 0) + 1
                if self.unfocused_ticks >= 10:  # ~1 second unfocused
                    self.close()
                    return
            else:
                self.unfocused_ticks = 0
                if user32.GetAsyncKeyState(0x1B) & 0x8000:
                    self.close()
                    return

        self._position_pill()
        self._check_hotkey()

    def _check_hotkey(self) -> None:
        """Poll the pill-scoped hotkeys while the dialog or pill has keyboard focus."""
        if sys.platform != "win32" or self._in_drag or time.monotonic() < self._cooldown_until:
            return
        try:
            user32 = ctypes.windll.user32
            fg = user32.GetForegroundWindow()
            pill_hwnd = self._root_hwnd()
            if fg not in (self.dialog_hwnd, pill_hwnd):
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

    def on_enter(self, _event=None) -> None:
        self._leave_timer.stop()
        self._apply_pill_style(hovered=True)
        self._arm_timeout(20000 if self.is_batch else 10000)
        self._expand_checklist()

    def on_leave(self, _event=None) -> None:
        if not self.is_closed:
            self._leave_timer.start(350)

    def _leave_if_pointer_gone(self) -> None:
        if self.is_closed or self._in_drag:
            return
        try:
            pos = QCursor.pos()
            under = QApplication.widgetAt(pos)
            if under is not None:
                top = under.window()
                if top is self.root or (self.checklist_win is not None and top is self.checklist_win):
                    return
        except Exception:
            pass
        self._apply_pill_style(hovered=False)
        self._collapse_checklist()
        self._arm_timeout(6000)

    # ---- press / drag / click ----------------------------------------------------------------

    @staticmethod
    def _event_global_xy(event) -> tuple[int, int]:
        if hasattr(event, "globalPosition"):
            pt = event.globalPosition().toPoint()
            return pt.x(), pt.y()
        return int(getattr(event, "x_root", 0)), int(getattr(event, "y_root", 0))

    def _on_press(self, event) -> None:
        self._drag_origin = self._event_global_xy(event)
        self._drag_started = False

    def _on_motion(self, event) -> None:
        if self._drag_origin is None or self._drag_started or self._in_drag:
            return
        gx, gy = self._event_global_xy(event)
        dx = abs(gx - self._drag_origin[0])
        dy = abs(gy - self._drag_origin[1])
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
            hwnd = self._root_hwnd() or None
            effect = batch_handoff.start_file_drag(paths, hwnd)
        except Exception as exc:
            logging.debug("Drag failed: %s", exc)
            effect = 0
        finally:
            self._in_drag = False
        if effect:
            self._finish_handoff(paths, clear_all=len(paths) >= len(self.items))
            QTimer.singleShot(120, self.close)

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

    def _flash(self, button: QPushButton | None, text: str, ok: bool) -> None:
        if button is None:
            return
        try:
            button.setText(text)
            bg = self.accent_color if ok else CLOSE_HOVER
            fg = COLORS.get("canvas", "#0B0D12") if ok else "#FFFFFF"
            button.setStyleSheet(
                f"QPushButton {{ background: {bg}; color: {fg}; border: none; border-radius: 5px;"
                f" padding: 3px 8px; font-family: '{FONT_FAMILY}'; font-size: {FONT_SIZE_XS}pt; font-weight: 700; }}"
            )
            self.app.processEvents()
        except Exception:
            pass

    def select_file(self) -> None:
        """Inject the latest (checked) file into the dialog, dismiss ribbons, and close the pill."""
        if self.is_closed or self._is_injecting:
            return

        checked = self._checked_paths()
        target = checked[-1] if checked else self.file_path
        if not Path(target).exists():
            self._flash(self.action_btn, "✕ File not found", False)
            QTimer.singleShot(1000, self.close)
            return

        self._is_injecting = True
        self._unregister_hotkey()

        success = inject_file_into_dialog(self.dialog_hwnd, target)

        if success:
            self._flash(self.action_btn, "✓ Injected!", True)
            self._finish_handoff([target], clear_all=False)
            QTimer.singleShot(180, self.close)
        else:
            self._flash(self.action_btn, "✕ Dialog unavailable", False)
            QTimer.singleShot(800, self.close)

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
            QTimer.singleShot(250 if result.mode == "multi" else 1800, self.close)
        else:
            self._flash(button, f"✕ {result.message or 'Dialog unavailable'}", False)
            QTimer.singleShot(2500, self.close)

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
            QTimer.singleShot(700, self.close)
        else:
            self._flash(button, "✕ Clipboard busy", False)

    def close(self) -> None:
        if self.is_closed:
            return
        self.is_closed = True
        self._timeout_timer.stop()
        self._leave_timer.stop()
        self._track_timer.stop()
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
            self.root.close()
            self.root.deleteLater()
            self.app.processEvents()
            self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
        except Exception:
            pass
        if self._running:
            self._running = False
            try:
                self.app.exit(0)
            except Exception:
                pass

    def run(self) -> None:
        self._running = True
        self.root.show()
        try:
            self.app.exec()
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
