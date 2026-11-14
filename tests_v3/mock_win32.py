"""In-memory Win32 headless mocking harness for Download Sorter v3 E2E tests.

Enables 100% deterministic, sub-second headless execution of Windows Common Item Dialog
detection, control enumeration, path injection, token elevation, and clipboard/shell APIs
without requiring an interactive desktop session.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path
import struct
import sys
from typing import Any, Callable
from unittest.mock import MagicMock


@dataclass
class MockControl:
    hwnd: int
    class_name: str
    text: str = ""
    ctrl_id: int = 0
    visible: bool = True
    enabled: bool = True
    parent_hwnd: int = 0


@dataclass
class MockWindow:
    hwnd: int
    parent: int = 0
    class_name: str = "#32770"
    title: str = "Open"
    ctrl_id: int = 0
    rect: tuple[int, int, int, int] = (100, 100, 800, 600)  # (left, top, right, bottom)
    visible: bool = True
    enabled: bool = True
    iconic: bool = False
    text: str = ""
    props: dict[str, Any] = field(default_factory=dict)
    is_elevated: bool = False
    pid: int = 0
    messages: list[tuple[int, int, Any]] = field(default_factory=list)


class FakeWin32Desktop:
    """Simulates Windows user32, kernel32, advapi32, and shell32 subsystems in memory."""

    def __init__(self) -> None:
        self.windows: dict[int, MockWindow] = {}
        self._next_hwnd: int = 1000
        self._next_mem_id: int = 5000
        self.foreground_hwnd: int = 0
        self.clipboard_locked: bool = False
        self.clipboard_open: bool = False
        self.clipboard_files: list[str] = []
        self.clipboard_data: dict[int, Any] = {}
        self.memory_buffers: dict[int, Any] = {}
        self.recent_docs: list[str] = []
        self.hidden_paths: list[str] = []
        self.pressed_keys: set[int] = set()
        self.self_elevated: bool = False
        self.process_handles: dict[int, bool] = {}
        self.token_elevations: dict[int, bool] = {}
        self.monitor_work_rect: tuple[int, int, int, int] = (0, 0, 1920, 1080)
        self.monitor_rect: tuple[int, int, int, int] = (0, 0, 1920, 1080)
        self.simulate_navigation: bool = True
        self.simulate_navigation_timeout: bool = False
        self.simulate_rejection_popup: bool = False
        self.drag_drop_effect: int = 1  # DROPEFFECT_COPY

    def _alloc_hwnd(self) -> int:
        h = self._next_hwnd
        self._next_hwnd += 1
        return h

    def create_dialog(
        self,
        title: str = "Open",
        is_save: bool = False,
        elevated: bool = False,
        edit_type: str = "ComboBoxEx32",  # or "Edit"
        button_text: str | None = None,
        rect: tuple[int, int, int, int] = (100, 100, 800, 600),
    ) -> int:
        """Create a mock dialog window with standard child controls."""
        dlg_hwnd = self._alloc_hwnd()
        actual_title = "Save As" if is_save and title == "Open" else title
        self.windows[dlg_hwnd] = MockWindow(
            hwnd=dlg_hwnd,
            parent=0,
            class_name="#32770",
            title=actual_title,
            is_elevated=elevated,
            pid=dlg_hwnd + 1000,
            rect=rect,
        )

        # Edit control creation
        if edit_type == "ComboBoxEx32":
            combo_hwnd = self._alloc_hwnd()
            self.windows[combo_hwnd] = MockWindow(
                hwnd=combo_hwnd,
                parent=dlg_hwnd,
                class_name="ComboBoxEx32",
                ctrl_id=1148,
            )
            edit_hwnd = self._alloc_hwnd()
            self.windows[edit_hwnd] = MockWindow(
                hwnd=edit_hwnd,
                parent=combo_hwnd,
                class_name="Edit",
                ctrl_id=1148,
            )
        else:
            edit_hwnd = self._alloc_hwnd()
            self.windows[edit_hwnd] = MockWindow(
                hwnd=edit_hwnd,
                parent=dlg_hwnd,
                class_name="Edit",
                ctrl_id=1152,
            )

        # Action button
        btn_hwnd = self._alloc_hwnd()
        if button_text is not None:
            btn_title = button_text
        else:
            btn_title = "&Save" if is_save else "&Open"
        self.windows[btn_hwnd] = MockWindow(
            hwnd=btn_hwnd,
            parent=dlg_hwnd,
            class_name="Button",
            title=btn_title,
            ctrl_id=1,  # IDOK
        )

        self.foreground_hwnd = dlg_hwnd
        return dlg_hwnd

    def create_error_popup(self, owner_dialog_hwnd: int, message: str = "Error") -> int:
        """Create an owned error dialog (#32770) that popup dismissal detects."""
        popup_hwnd = self._alloc_hwnd()
        self.windows[popup_hwnd] = MockWindow(
            hwnd=popup_hwnd,
            parent=owner_dialog_hwnd,
            class_name="#32770",
            title=message,
            visible=True,
        )
        return popup_hwnd

    def get_edit_control(self, dialog_hwnd: int) -> MockWindow | None:
        """Find the filename Edit MockWindow belonging to dialog_hwnd."""
        for w in self.windows.values():
            if w.class_name == "Edit":
                if w.parent == dialog_hwnd:
                    return w
                parent_w = self.windows.get(w.parent)
                if parent_w and parent_w.parent == dialog_hwnd:
                    return w
        return None

    def _is_descendant(self, child_hwnd: int, ancestor_hwnd: int) -> bool:
        curr = self.windows.get(child_hwnd)
        while curr and curr.parent:
            if curr.parent == ancestor_hwnd:
                return True
            curr = self.windows.get(curr.parent)
        return False

    def patch(self, monkeypatch: Any) -> FakeWin32Desktop:
        """Monkeypatch ctypes Win32 calls across user32, kernel32, advapi32, shell32, dwmapi."""
        desktop = self

        # ----------------- user32 -----------------
        def _IsWindow(h: int) -> int:
            return 1 if (isinstance(h, int) and h in desktop.windows) else 0

        def _IsWindowVisible(h: int) -> int:
            return 1 if (isinstance(h, int) and h in desktop.windows and desktop.windows[h].visible) else 0

        def _IsWindowEnabled(h: int) -> int:
            return 1 if (isinstance(h, int) and h in desktop.windows and desktop.windows[h].enabled) else 0

        def _IsIconic(h: int) -> int:
            return 1 if (isinstance(h, int) and h in desktop.windows and desktop.windows[h].iconic) else 0

        def _GetForegroundWindow() -> int:
            return desktop.foreground_hwnd

        def _SetForegroundWindow(h: int) -> int:
            desktop.foreground_hwnd = h
            return 1

        def _GetClassNameW(h: int, buf: Any, max_count: int) -> int:
            if isinstance(h, int) and h in desktop.windows:
                name = desktop.windows[h].class_name[: max_count - 1]
                if hasattr(buf, "value"):
                    buf.value = name
                return len(name)
            return 0

        def _GetWindowTextW(h: int, buf: Any, max_count: int) -> int:
            if isinstance(h, int) and h in desktop.windows:
                text = desktop.windows[h].title[: max_count - 1]
                if hasattr(buf, "value"):
                    buf.value = text
                return len(text)
            return 0

        def _GetDlgCtrlID(h: int) -> int:
            return desktop.windows[h].ctrl_id if (isinstance(h, int) and h in desktop.windows) else 0

        def _GetParent(h: int) -> int:
            return desktop.windows[h].parent if (isinstance(h, int) and h in desktop.windows) else 0

        def _GetWindow(h: int, cmd: int) -> int:
            # cmd=4 is GW_OWNER
            if cmd == 4 and isinstance(h, int) and h in desktop.windows:
                return desktop.windows[h].parent
            return 0

        def _EnumWindows(cb: Callable[[int, int], bool], lparam: int) -> int:
            for h in list(desktop.windows.keys()):
                if not cb(h, lparam):
                    break
            return 1

        def _EnumChildWindows(h: int, cb: Callable[[int, int], bool], lparam: int) -> int:
            for child_h in list(desktop.windows.keys()):
                if desktop._is_descendant(child_h, h):
                    if not cb(child_h, lparam):
                        break
            return 1

        def _SendMessageW(h: int, msg: int, wparam: int, lparam: Any) -> int:
            if not isinstance(h, int) or h not in desktop.windows:
                return 0
            w = desktop.windows[h]
            w.messages.append((msg, wparam, lparam))
            if msg == 0x000C:  # WM_SETTEXT
                if isinstance(lparam, str):
                    text = lparam
                elif hasattr(lparam, "value"):
                    text = str(lparam.value)
                elif isinstance(lparam, int) and lparam:
                    try:
                        text = ctypes.wstring_at(lparam)
                    except Exception:
                        text = ""
                else:
                    text = ""
                w.text = text
                if desktop.simulate_rejection_popup and ('"' in text):
                    # Spawns error popup owned by top-level dialog if rejection mode is active
                    owner = h
                    curr_node = desktop.windows.get(h)
                    while curr_node and curr_node.parent:
                        owner = curr_node.parent
                        curr_node = desktop.windows.get(curr_node.parent)
                    desktop.create_error_popup(owner, "File not found / Multi-selection invalid")
                return 1
            elif msg == 0x000E:  # WM_GETTEXTLENGTH
                return len(w.text)
            elif msg == 0x000D:  # WM_GETTEXT
                text_slice = w.text[: max(0, wparam - 1)]
                if hasattr(lparam, "value"):
                    lparam.value = text_slice
                elif isinstance(lparam, int) and lparam:
                    raw = (text_slice + "\0").encode("utf-16-le")
                    ctypes.memmove(lparam, raw, len(raw))
                return len(text_slice)
            return 0

        def _PostMessageW(h: int, msg: int, wparam: int, lparam: Any) -> int:
            if not isinstance(h, int) or h not in desktop.windows:
                return 0
            w = desktop.windows[h]
            w.messages.append((msg, wparam, lparam))
            if msg == 0x0010:  # WM_CLOSE
                w.visible = False
            elif msg == 0x0100 and wparam == 0x0D:  # WM_KEYDOWN VK_RETURN
                if desktop.simulate_navigation and not desktop.simulate_navigation_timeout:
                    # Navigating into folder clears edit control, but entering a filename keeps it
                    try:
                        if w.text and Path(w.text).is_dir():
                            w.text = ""
                    except Exception:
                        pass
            return 1

        def _SetPropW(h: int, name: str, val: Any) -> int:
            if isinstance(h, int) and h in desktop.windows:
                desktop.windows[h].props[name] = val
                return 1
            return 0

        def _GetPropW(h: int, name: str) -> Any:
            if isinstance(h, int) and h in desktop.windows:
                return desktop.windows[h].props.get(name, 0)
            return 0

        def _RemovePropW(h: int, name: str) -> Any:
            if isinstance(h, int) and h in desktop.windows:
                return desktop.windows[h].props.pop(name, 0)
            return 0

        def _GetWindowRect(h: int, rect_ptr: Any) -> int:
            if isinstance(h, int) and h in desktop.windows:
                w = desktop.windows[h]
                l, t, r, b = w.rect
                try:
                    target = rect_ptr._obj
                except AttributeError:
                    target = rect_ptr.contents
                target.left = l
                target.top = t
                target.right = r
                target.bottom = b
                return 1
            return 0

        def _MonitorFromWindow(h: int, flags: int) -> int:
            return 2001

        def _GetMonitorInfoW(mon: int, minfo_ptr: Any) -> int:
            try:
                target = minfo_ptr._obj if hasattr(minfo_ptr, "_obj") else minfo_ptr.contents
                target.rcWork.left = desktop.monitor_work_rect[0]
                target.rcWork.top = desktop.monitor_work_rect[1]
                target.rcWork.right = desktop.monitor_work_rect[2]
                target.rcWork.bottom = desktop.monitor_work_rect[3]
                target.rcMonitor.left = desktop.monitor_rect[0]
                target.rcMonitor.top = desktop.monitor_rect[1]
                target.rcMonitor.right = desktop.monitor_rect[2]
                target.rcMonitor.bottom = desktop.monitor_rect[3]
                return 1
            except Exception:
                return 0

        def _GetAsyncKeyState(vkey: int) -> int:
            return 0x8000 if vkey in desktop.pressed_keys else 0

        def _GetWindowThreadProcessId(h: int, pid_ptr: Any) -> int:
            if isinstance(h, int) and h in desktop.windows:
                pid = desktop.windows[h].pid or (h + 1000)
                try:
                    pid_ptr._obj.value = pid
                except AttributeError:
                    pid_ptr.contents.value = pid
                return 1
            return 0

        def _OpenClipboard(hwnd: Any) -> int:
            if desktop.clipboard_locked:
                return 0
            desktop.clipboard_open = True
            return 1

        def _CloseClipboard() -> int:
            desktop.clipboard_open = False
            return 1

        def _EmptyClipboard() -> int:
            desktop.clipboard_files = []
            desktop.clipboard_data.clear()
            return 1

        def _SetClipboardData(fmt: int, handle: int) -> int:
            desktop.clipboard_data[fmt] = handle
            if fmt == 15:  # CF_HDROP
                buf = desktop.memory_buffers.get(handle)
                if buf:
                    raw = bytes(buf)
                    if len(raw) > 20:
                        pFiles = struct.unpack("<I", raw[:4])[0]
                        paths_raw = raw[pFiles:].decode("utf-16-le", errors="ignore")
                        desktop.clipboard_files = [p for p in paths_raw.split("\0") if p]
            return handle

        def _GetClipboardData(fmt: int) -> int:
            return desktop.clipboard_data.get(fmt, 0)

        def _RegisterClipboardFormatW(name: str) -> int:
            return 49999

        def _ReleaseCapture() -> int:
            return 1

        # ----------------- kernel32 -----------------
        _real_CloseHandle = ctypes.windll.kernel32.CloseHandle

        def _OpenProcess(access: int, inherit: bool, pid: Any) -> int:
            p_val = pid.value if hasattr(pid, "value") else pid
            for w in desktop.windows.values():
                if w.pid == p_val:
                    handle = int(p_val) + 10000
                    desktop.process_handles[handle] = w.is_elevated
                    return handle
            return 0

        def _GetCurrentProcess() -> int:
            handle = 88888
            desktop.process_handles[handle] = desktop.self_elevated
            return handle

        def _CloseHandle(h: Any) -> int:
            try:
                val = h.value if hasattr(h, "value") else h
                _real_CloseHandle(val)
            except Exception:
                pass
            return 1

        def _GlobalAlloc(flags: int, size: int) -> int:
            mid = desktop._next_mem_id
            desktop._next_mem_id += 1
            buf = ctypes.create_string_buffer(max(1, size))
            desktop.memory_buffers[mid] = buf
            return mid

        def _GlobalLock(handle: int) -> int:
            buf = desktop.memory_buffers.get(handle)
            if buf:
                return ctypes.addressof(buf)
            return 0

        def _GlobalUnlock(handle: int) -> int:
            return 0

        def _GlobalFree(handle: int) -> int:
            desktop.memory_buffers.pop(handle, None)
            return 0

        def _SetFileAttributesW(path: str, attr: int) -> int:
            desktop.hidden_paths.append(str(path))
            return 1

        # ----------------- advapi32 -----------------
        def _OpenProcessToken(process_handle: Any, access: int, token_ptr: Any) -> int:
            p_h = process_handle.value if hasattr(process_handle, "value") else process_handle
            tok = int(p_h or 0) + 20000
            desktop.token_elevations[tok] = desktop.process_handles.get(p_h, False)
            try:
                token_ptr._obj.value = tok
            except AttributeError:
                token_ptr.contents.value = tok
            return 1

        def _GetTokenInformation(tok: Any, info_class: int, buf: Any, size: int, ret_size_ptr: Any) -> int:
            if info_class == 20:  # TokenElevation
                tok_key = tok.value if hasattr(tok, "value") else tok
                is_elev = 1 if desktop.token_elevations.get(tok_key, False) else 0
                try:
                    buf._obj.value = is_elev
                except AttributeError:
                    ctypes.cast(buf, ctypes.POINTER(ctypes.c_ulong)).contents.value = is_elev
                return 1
            return 0

        # ----------------- shell32 -----------------
        def _SHAddToRecentDocs(uFlags: int, path: Any) -> None:
            if isinstance(path, str):
                desktop.recent_docs.append(path)
            elif path:
                try:
                    desktop.recent_docs.append(ctypes.wstring_at(path))
                except Exception:
                    pass

        def _SHDoDragDrop(hwnd: Any, data_obj: Any, drop_source: Any, ok_effects: int, effect_ptr: Any) -> int:
            try:
                effect_ptr._obj.value = desktop.drag_drop_effect
            except AttributeError:
                effect_ptr.contents.value = desktop.drag_drop_effect
            return 0  # S_OK

        def _DragQueryFileW(hdrop: int, index: int, buf: Any, cch: int) -> int:
            if index == 0xFFFFFFFF:
                return len(desktop.clipboard_files)
            if 0 <= index < len(desktop.clipboard_files):
                s = desktop.clipboard_files[index]
                if buf is None or cch == 0:
                    return len(s)
                if hasattr(buf, "value"):
                    buf.value = s[: cch - 1]
                return min(len(s), cch - 1)
            return 0

        # ----------------- dwmapi -----------------
        def _DwmSetWindowAttribute(h: int, attr: int, val_ptr: Any, sz: int) -> int:
            return 0

        def _DwmExtendFrameIntoClientArea(h: int, margins_ptr: Any) -> int:
            return 0

        # Apply monkeypatches to ctypes windll
        user32 = ctypes.windll.user32
        monkeypatch.setattr(user32, "IsWindow", _IsWindow)
        monkeypatch.setattr(user32, "IsWindowVisible", _IsWindowVisible)
        monkeypatch.setattr(user32, "IsWindowEnabled", _IsWindowEnabled)
        monkeypatch.setattr(user32, "IsIconic", _IsIconic)
        monkeypatch.setattr(user32, "GetForegroundWindow", _GetForegroundWindow)
        monkeypatch.setattr(user32, "SetForegroundWindow", _SetForegroundWindow)
        monkeypatch.setattr(user32, "GetClassNameW", _GetClassNameW)
        monkeypatch.setattr(user32, "GetWindowTextW", _GetWindowTextW)
        monkeypatch.setattr(user32, "GetDlgCtrlID", _GetDlgCtrlID)
        monkeypatch.setattr(user32, "GetParent", _GetParent)
        monkeypatch.setattr(user32, "GetWindow", _GetWindow)
        monkeypatch.setattr(user32, "EnumWindows", _EnumWindows)
        monkeypatch.setattr(user32, "EnumChildWindows", _EnumChildWindows)
        monkeypatch.setattr(user32, "SendMessageW", _SendMessageW)
        monkeypatch.setattr(user32, "PostMessageW", _PostMessageW)
        monkeypatch.setattr(user32, "SetPropW", _SetPropW)
        monkeypatch.setattr(user32, "GetPropW", _GetPropW)
        monkeypatch.setattr(user32, "RemovePropW", _RemovePropW)
        monkeypatch.setattr(user32, "GetWindowRect", _GetWindowRect)
        monkeypatch.setattr(user32, "MonitorFromWindow", _MonitorFromWindow)
        monkeypatch.setattr(user32, "GetMonitorInfoW", _GetMonitorInfoW)
        monkeypatch.setattr(user32, "GetAsyncKeyState", _GetAsyncKeyState)
        monkeypatch.setattr(user32, "GetWindowThreadProcessId", _GetWindowThreadProcessId)
        monkeypatch.setattr(user32, "OpenClipboard", _OpenClipboard)
        monkeypatch.setattr(user32, "CloseClipboard", _CloseClipboard)
        monkeypatch.setattr(user32, "EmptyClipboard", _EmptyClipboard)
        monkeypatch.setattr(user32, "SetClipboardData", _SetClipboardData)
        monkeypatch.setattr(user32, "GetClipboardData", _GetClipboardData)
        monkeypatch.setattr(user32, "RegisterClipboardFormatW", _RegisterClipboardFormatW)
        monkeypatch.setattr(user32, "ReleaseCapture", _ReleaseCapture)

        kernel32 = ctypes.windll.kernel32
        monkeypatch.setattr(kernel32, "OpenProcess", _OpenProcess)
        monkeypatch.setattr(kernel32, "GetCurrentProcess", _GetCurrentProcess)
        monkeypatch.setattr(kernel32, "CloseHandle", _CloseHandle)
        monkeypatch.setattr(kernel32, "GlobalAlloc", _GlobalAlloc)
        monkeypatch.setattr(kernel32, "GlobalLock", _GlobalLock)
        monkeypatch.setattr(kernel32, "GlobalUnlock", _GlobalUnlock)
        monkeypatch.setattr(kernel32, "GlobalFree", _GlobalFree)
        monkeypatch.setattr(kernel32, "SetFileAttributesW", _SetFileAttributesW)

        advapi32 = ctypes.windll.advapi32
        monkeypatch.setattr(advapi32, "OpenProcessToken", _OpenProcessToken)
        monkeypatch.setattr(advapi32, "GetTokenInformation", _GetTokenInformation)

        shell32 = ctypes.windll.shell32
        monkeypatch.setattr(shell32, "SHAddToRecentDocs", _SHAddToRecentDocs)
        monkeypatch.setattr(shell32, "SHDoDragDrop", _SHDoDragDrop)
        monkeypatch.setattr(shell32, "DragQueryFileW", _DragQueryFileW)

        if hasattr(ctypes.windll, "dwmapi"):
            dwmapi = ctypes.windll.dwmapi
            monkeypatch.setattr(dwmapi, "DwmSetWindowAttribute", _DwmSetWindowAttribute)
            monkeypatch.setattr(dwmapi, "DwmExtendFrameIntoClientArea", _DwmExtendFrameIntoClientArea)

        return desktop
