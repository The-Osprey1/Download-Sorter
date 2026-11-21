"""PySide6 floating docked Quick Switcher pill attached to active Windows file dialogs."""

from __future__ import annotations

import logging
from pathlib import Path
import sys
import time
from typing import Any

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer
from PySide6.QtGui import QCursor, QGuiApplication, QKeySequence, QShortcut
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

from download_sorter.quick_switcher import (
    DEFAULT_HOTKEY_ALL,
    DEFAULT_HOTKEY_COPY,
    DEFAULT_HOTKEY_SELECT,
    MODIFIER_VK,
    Hotkey,
    clear_recent_file,
    consume_items,
    copy_files_to_clipboard,
    inject_file_into_dialog,
    inject_files_into_dialog,
    parse_hotkey,
    start_file_drag,
)
from download_sorter.ui.motion import make_vector_icon
from download_sorter.ui.toasts import CATEGORY_BADGES, CATEGORY_COLORS

# Theme Palette Tokens
BG_COLOR = "#12151C"
BORDER_COLOR = "#2A303B"
BORDER_HOVER = "#3A4352"
TEXT_MAIN = "#F5F5F7"
TEXT_MUTED = "#8E8E93"
ACTION_COLOR = "#69E6AD"
CLOSE_COLOR = "#657084"
CLOSE_HOVER = "#FA7183"
CLOSE_HOVER_BG = "#1A1013"
HOVER_BG = "#1D222C"
BTN_BG = "#171B24"
BTN_HOVER = "#232936"
FONT_FAMILY = "Segoe UI"


def _blend_colors(c1: str, c2: str, factor: float) -> str:
    """Blend two hex colors together."""
    try:
        r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
        r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
        r = int(r1 * factor + r2 * (1 - factor))
        g = int(g1 * factor + g2 * (1 - factor))
        b = int(b1 * factor + b2 * (1 - factor))
        return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        return c1


def _apply_dwm_frame(hwnd: int) -> None:
    """Apply native Windows 11 DWM rounded corners and frame margins."""
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
    """Frameless topmost tool window for the Quick Switcher pill."""

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

    def enterEvent(self, event: QEvent) -> None:
        self._owner.on_enter(event)
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self._owner.on_leave(event)
        super().leaveEvent(event)


class _DragSurfaceFilter(QObject):
    """Filter mouse press/motion on pill body to initiate OLE drag-drop."""

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
    """Toggles checklist checkbox on row click or starts single file drag."""

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
    """Docked floating action pill attached to active Windows File Dialog."""

    def __init__(
        self,
        dialog_hwnd: int,
        recent_data: dict[str, Any],
        parent: QWidget | None = None,
        *,
        hotkey_select: str = DEFAULT_HOTKEY_SELECT,
        hotkey_all: str = DEFAULT_HOTKEY_ALL,
        hotkey_copy: str = DEFAULT_HOTKEY_COPY,
    ) -> None:
        self.dialog_hwnd = dialog_hwnd
        # The keys set in Settings -> Quick Switcher; anything unusable falls back.
        self.hotkey_select = parse_hotkey(hotkey_select) or parse_hotkey(DEFAULT_HOTKEY_SELECT)
        self.hotkey_all = parse_hotkey(hotkey_all) or parse_hotkey(DEFAULT_HOTKEY_ALL)
        self.hotkey_copy = parse_hotkey(hotkey_copy) or parse_hotkey(DEFAULT_HOTKEY_COPY)
        self.recent_data = recent_data
        self.file_path = recent_data["path"]
        self.filename = recent_data["name"]
        self.category = recent_data.get("category", "default")
        self.accent_color = recent_data.get("color") or CATEGORY_COLORS.get(self.category, ACTION_COLOR)
        self.is_closed = False
        self._is_injecting = False
        self._in_drag = False
        self._running = False
        self._drag_origin: tuple[int, int] | None = None
        self._drag_started = False
        self._cooldown_until = 0.0
        self.checklist_win: QFrame | None = None
        self.checklist_scroll: QScrollArea | None = None
        self.checklist_canvas: Any = None
        self._check_boxes: dict[str, QCheckBox] = {}
        self._hovered = False
        self.unfocused_ticks = 0

        items = recent_data.get("items")
        if not items:
            items = [{k: recent_data[k] for k in ("path", "name", "category", "color", "timestamp") if k in recent_data}]
        self.items: list[dict[str, Any]] = list(items)
        self.checked: dict[str, bool] = {i["path"]: True for i in self.items}
        self.is_batch = len(self.items) > 1
        self.batch_id = str(recent_data.get("batch_id") or "")

        self.app = QApplication.instance()
        if self.app is None:
            self.app = QApplication(sys.argv[:1])
            self._owns_app = True
        else:
            self._owns_app = False

        # Tag dialog with Win32 property to prevent duplicate switchers
        if sys.platform == "win32" and self.dialog_hwnd:
            try:
                ctypes.windll.user32.SetPropW(self.dialog_hwnd, "DownloadSorterQuickSwitcher", 1)
            except Exception:
                pass

        self.normal_border = _blend_colors(self.accent_color, BORDER_COLOR, 0.40)
        self.hover_border = _blend_colors(self.accent_color, BORDER_HOVER, 0.65)

        self.root = _PillWindow(self)
        self.scale = 1.0
        screen = self.root.screen() or QGuiApplication.primaryScreen()
        dpr = float(screen.devicePixelRatio()) if screen is not None else 1.0
        self._dpr = max(1.0, dpr)

        self.pill_w = round(520 if self.is_batch else 360)
        self.pill_h = round(52 if self.is_batch else 44)
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

        # Keyboard shortcuts
        self._shortcut_escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), self.root)
        self._shortcut_escape.activated.connect(self.close)
        self._shortcut_select = QShortcut(QKeySequence(self.hotkey_select.text), self.root)
        self._shortcut_select.activated.connect(self.select_file)
        if self.is_batch:
            self._shortcut_all = QShortcut(QKeySequence(self.hotkey_all.text), self.root)
            self._shortcut_all.activated.connect(self.select_all)
            self._shortcut_copy = QShortcut(QKeySequence(self.hotkey_copy.text), self.root)
            self._shortcut_copy.activated.connect(self.copy_files)

        self._track_timer.start()
        self._arm_timeout(10000)

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
            f"#qsRoot {{ background: {bg}; border: 1px solid {border}; border-radius: 8px; }}"
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
            f" border-radius: 6px; padding: 4px 10px; font-family: '{FONT_FAMILY}';"
            f" font-size: 11px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: {BTN_HOVER}; border-color: {self.accent_color}; }}"
        )

    def _arm_timeout(self, ms: int) -> None:
        try:
            self._timeout_timer.start(max(1, int(ms)))
        except Exception:
            pass

    def _make_button(self, text: str, command: Any, primary: bool = False) -> QPushButton:
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
        self.container_layout.setContentsMargins(0, 2, 6, 2)
        self.container_layout.setSpacing(6)
        root_layout.addWidget(self.container)

        # Left accent stripe
        self.stripe = QFrame(self.container)
        self.stripe.setFixedWidth(4)
        self.stripe.setStyleSheet(f"background: {self.accent_color}; border: none; border-top-left-radius: 7px; border-bottom-left-radius: 7px;")
        self.container_layout.addWidget(self.stripe)

        # Category icon badge
        self.icon_lbl = QLabel(self.container)
        icon_name = "folder"
        if self.category in ("cad", "print"):
            icon_name = "sparkle"
        elif self.category == "cnc":
            icon_name = "settings"
        elif self.category == "code":
            icon_name = "rules"
        self.icon_lbl.setPixmap(make_vector_icon(icon_name, self.accent_color, 18).pixmap(18, 18))
        self.icon_lbl.setContentsMargins(4, 0, 2, 0)
        self.container_layout.addWidget(self.icon_lbl)

        # Center content: Filename & context
        self.center_frame = QWidget(self.container)
        self.center_frame.setObjectName("qsCenter")
        center_layout = QVBoxLayout(self.center_frame)
        center_layout.setContentsMargins(2, 2, 0, 2)
        center_layout.setSpacing(1)

        disp_name = self.filename
        if self.is_batch:
            disp_name = f"{len(self.items)} files · {self.filename}"
        limit = 38 if self.is_batch else 24
        if len(disp_name) > limit:
            disp_name = disp_name[: limit - 3] + "…"

        badge_txt = CATEGORY_BADGES.get(self.category, "RECENT")
        tag_text = f"⚡ {badge_txt} · {len(self.items)} FILES" if self.is_batch else f"⚡ {badge_txt} · READY"
        self.tag_lbl = QLabel(tag_text, self.center_frame)
        self.tag_lbl.setStyleSheet(
            f"color: {self.accent_color}; font-family: '{FONT_FAMILY}'; font-size: 9px; font-weight: 700; text-transform: uppercase;"
        )
        center_layout.addWidget(self.tag_lbl)

        self.name_lbl = QLabel(disp_name, self.center_frame)
        self.name_lbl.setStyleSheet(
            f"color: {TEXT_MAIN}; font-family: '{FONT_FAMILY}'; font-size: 11.5px; font-weight: 600;"
        )
        center_layout.addWidget(self.name_lbl)

        self.hint_lbl: QLabel | None = None
        if self.is_batch:
            self.hint_lbl = QLabel("drag me anywhere · hover to untick files", self.center_frame)
            self.hint_lbl.setStyleSheet(
                f"color: {TEXT_MUTED}; font-family: '{FONT_FAMILY}'; font-size: 9px;"
            )
            center_layout.addWidget(self.hint_lbl)

        self.container_layout.addWidget(self.center_frame, 1)

        # Action buttons
        latest_label = "↵ Latest" if self.is_batch else f"↵ Select ({self.hotkey_select.text})"
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
            f" font-family: '{FONT_FAMILY}'; font-size: 11px; font-weight: 700; }}"
            f"QPushButton:hover {{ background: {CLOSE_HOVER_BG}; color: {CLOSE_HOVER}; }}"
        )
        self.close_btn.clicked.connect(lambda _checked=False: self.close())
        self.container_layout.addWidget(self.close_btn)

        # Drag filter
        self._drag_filter = _DragSurfaceFilter(self)
        drag_widgets = [self.container, self.center_frame, self.tag_lbl, self.name_lbl, self.stripe, self.icon_lbl]
        if self.hint_lbl is not None:
            drag_widgets.append(self.hint_lbl)
        for w in drag_widgets:
            w.installEventFilter(self._drag_filter)

    # ---- Expandable Batch Checklist -----------------------------------------

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
                f"#qsChecklist {{ background: {BG_COLOR}; border: 1px solid {self.hover_border}; border-radius: 8px; }}"
                f"QScrollArea {{ background: transparent; border: none; }}"
                f"QWidget#qsChecklistInner {{ background: {BG_COLOR}; }}"
                f"QLabel {{ color: {TEXT_MAIN}; background: transparent; font-family: '{FONT_FAMILY}'; font-size: 11px; }}"
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
            for item in reversed(self.items):  # Newest on top
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
                lbl = QLabel(name if len(name) <= 50 else name[:47] + "…", row)
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
            logging.debug("Checklist display error: %s", exc)
            self.checklist_win = None

    def _collapse_checklist(self) -> None:
        self.checklist_scroll = None
        if hasattr(self, "checklist_canvas") and hasattr(self.checklist_canvas, "unbind_all"):
            try:
                self.checklist_canvas.unbind_all("<MouseWheel>")
            except Exception:
                pass
        self.checklist_canvas = None
        win, self.checklist_win = self.checklist_win, None
        if win is not None:
            try:
                win.close()
                win.deleteLater()
            except Exception:
                pass

    def _on_toggle(self, path: str, state: Any) -> None:
        is_checked = bool(state)
        self.checked[path] = is_checked
        n = len(self._checked_paths())
        if self.all_btn is not None:
            self.all_btn.setText(f"⇉ All {n}")
        self._arm_timeout(10000)

    def _checked_items(self) -> list[dict[str, Any]]:
        return [i for i in self.items if self.checked.get(i["path"], True) and Path(i["path"]).exists()]

    def _checked_paths(self) -> list[str]:
        return [i["path"] for i in self._checked_items()]

    # ---- Positioning & Tracking ---------------------------------------------

    def _position_pill(self) -> None:
        """Position pill docked above (or below if top-edge) dialog."""
        if sys.platform == "win32" and self.dialog_hwnd:
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
                self.unfocused_ticks += 1
                if self.unfocused_ticks >= 10:  # ~1.0s unfocused
                    self.close()
                    return
            else:
                self.unfocused_ticks = 0
                if user32.GetAsyncKeyState(0x1B) & 0x8000:  # Escape
                    self.close()
                    return

        self._position_pill()
        self._check_hotkeys()

    def _check_hotkeys(self) -> None:
        if sys.platform != "win32" or self._in_drag or time.monotonic() < self._cooldown_until:
            return
        try:
            user32 = ctypes.windll.user32
            fg = user32.GetForegroundWindow()
            pill_hwnd = self._root_hwnd()
            if fg not in (self.dialog_hwnd, pill_hwnd):
                return

            def down(vk: int) -> bool:
                return bool(user32.GetAsyncKeyState(vk) & 0x8000)

            held = {name for name, vk in MODIFIER_VK.items() if down(vk)}

            def pressed(hotkey: Hotkey | None) -> bool:
                # Exactly these modifiers, so Ctrl+G does not also fire for Ctrl+Shift+G.
                return hotkey is not None and held == set(hotkey.modifiers) and down(hotkey.vk)

            if self.is_batch and pressed(self.hotkey_all):
                self._cooldown_until = time.monotonic() + 0.8
                self.select_all()
            elif self.is_batch and pressed(self.hotkey_copy):
                self._cooldown_until = time.monotonic() + 0.8
                self.copy_files()
            elif pressed(self.hotkey_select):
                self._cooldown_until = time.monotonic() + 0.8
                self.select_file()
        except Exception:
            pass

    # ---- Hover & Gestures ---------------------------------------------------

    def on_enter(self, _event: Any = None) -> None:
        self._leave_timer.stop()
        self._apply_pill_style(hovered=True)
        self._arm_timeout(20000 if self.is_batch else 10000)
        self._expand_checklist()

    def on_leave(self, _event: Any = None) -> None:
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

    @staticmethod
    def _event_global_xy(event: Any) -> tuple[int, int]:
        if hasattr(event, "globalPosition"):
            pt = event.globalPosition().toPoint()
            return pt.x(), pt.y()
        return int(getattr(event, "x_root", 0)), int(getattr(event, "y_root", 0))

    def _on_press(self, event: Any) -> None:
        self._drag_origin = self._event_global_xy(event)
        self._drag_started = False

    def _on_motion(self, event: Any) -> None:
        if self._drag_origin is None or self._drag_started or self._in_drag:
            return
        gx, gy = self._event_global_xy(event)
        dx = abs(gx - self._drag_origin[0])
        dy = abs(gy - self._drag_origin[1])
        if max(dx, dy) > round(6 * self.scale):
            self._drag_started = True
            self._begin_drag(self._checked_paths())

    def _on_release(self, _event: Any = None) -> None:
        started, self._drag_origin = self._drag_started, None
        self._drag_started = False
        if not started:
            self.select_file()

    def _begin_drag(self, paths: list[str]) -> None:
        if self._in_drag or self.is_closed or not paths:
            return
        self._in_drag = True
        try:
            if sys.platform == "win32":
                ctypes.windll.user32.ReleaseCapture()
            self._collapse_checklist()
            hwnd = self._root_hwnd() or None
            effect = start_file_drag(paths, hwnd)
        except Exception as exc:
            logging.debug("Drag error: %s", exc)
            effect = 0
        finally:
            self._in_drag = False

        if effect:
            self._finish_handoff(paths, clear_all=len(paths) >= len(self.items))
            QTimer.singleShot(120, self.close)

    def _finish_handoff(self, paths: list[str], clear_all: bool = False) -> None:
        try:
            if clear_all:
                clear_recent_file()
            else:
                consume_items(paths)
        except Exception:
            pass

    def _flash(self, button: QPushButton | None, text: str, ok: bool) -> None:
        if button is None:
            return
        try:
            button.setText(text)
            bg = self.accent_color if ok else CLOSE_HOVER
            fg = "#082411" if ok else "#FFFFFF"
            button.setStyleSheet(
                f"QPushButton {{ background: {bg}; color: {fg}; border: none; border-radius: 6px;"
                f" padding: 4px 10px; font-family: '{FONT_FAMILY}'; font-size: 11px; font-weight: 700; }}"
            )
            self.app.processEvents()
        except Exception:
            pass

    # ---- Actions ------------------------------------------------------------

    def select_file(self) -> None:
        """Inject latest (checked) file into dialog and close."""
        if self.is_closed or self._is_injecting:
            return
        checked = self._checked_paths()
        target = checked[-1] if checked else self.file_path
        if not Path(target).exists():
            self._flash(self.action_btn, "✕ File not found", False)
            QTimer.singleShot(1000, self.close)
            return

        self._is_injecting = True
        success = inject_file_into_dialog(self.dialog_hwnd, target)
        if success:
            self._flash(self.action_btn, "✓ Injected!", True)
            self._finish_handoff([target], clear_all=len(checked) <= 1)
            QTimer.singleShot(180, self.close)
        else:
            self._is_injecting = False
            self._flash(self.action_btn, "✕ Dialog unavailable", False)
            QTimer.singleShot(800, self.close)

    select_latest = select_file

    def select_all(self) -> None:
        """Inject all checked files at once with empty selection guard."""
        if self.is_closed or self._is_injecting:
            return
        paths = self._checked_paths()
        button = self.all_btn or self.action_btn
        if len(paths) == 0:
            self._flash(button, "✕ No files selected", False)
            self._arm_timeout(10000)
            return
        if len(paths) == 1:
            self.select_file()
            return

        self._is_injecting = True
        result = inject_files_into_dialog(
            self.dialog_hwnd,
            paths,
            batch_id=self.batch_id,
        )
        if result.ok:
            label = f"✓ {len(result.injected)} injected" if result.mode == "multi" else "✓ Latest only"
            self._flash(button, label, True)
            self._finish_handoff(result.injected or paths, clear_all=result.mode == "multi")
            QTimer.singleShot(250 if result.mode == "multi" else 1800, self.close)
        else:
            self._is_injecting = False
            self._flash(button, f"✕ {result.message or 'Dialog unavailable'}", False)
            QTimer.singleShot(2000, self.close)

    def copy_files(self) -> None:
        """Copy checked files to clipboard as CF_HDROP payload."""
        if self.is_closed:
            return
        paths = self._checked_paths()
        button = self.copy_btn or self.action_btn
        if len(paths) == 0:
            self._flash(button, "✕ No files selected", False)
            self._arm_timeout(10000)
            return

        if copy_files_to_clipboard(paths):
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
        self._collapse_checklist()

        if sys.platform == "win32" and self.dialog_hwnd:
            try:
                ctypes.windll.user32.RemovePropW(self.dialog_hwnd, "DownloadSorterQuickSwitcher")
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
