"""Ask a running Download Sorter to close, cleanly.

The installer runs ``DownloadSorter.exe --quit`` before it replaces the app's
files: Windows will not overwrite a program that is still running, and the
background sorter (tray) is almost always running. ``--quit`` sets a named
Windows event; the tray and any open window watch that event and close the
same way the tray's Quit item does.

Everything here does nothing on other systems.
"""

from __future__ import annotations

import sys
import threading
from typing import Callable

EVENT_NAME = "Local\\DownloadSorter_Quit"

_EVENT_MODIFY_STATE = 0x0002
_SYNCHRONIZE = 0x00100000
_WAIT_OBJECT_0 = 0


def _kernel32():
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    k32.CreateEventW.restype = wintypes.HANDLE
    k32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    k32.OpenEventW.restype = wintypes.HANDLE
    k32.SetEvent.argtypes = [wintypes.HANDLE]
    k32.SetEvent.restype = wintypes.BOOL
    k32.ResetEvent.argtypes = [wintypes.HANDLE]
    k32.ResetEvent.restype = wintypes.BOOL
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.WaitForSingleObject.restype = wintypes.DWORD
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL
    return k32


def request_quit() -> bool:
    """Tell running copies to close. True when at least one was listening."""
    k32 = _kernel32()
    if k32 is None:
        return False
    handle = k32.OpenEventW(_EVENT_MODIFY_STATE | _SYNCHRONIZE, False, EVENT_NAME)
    if not handle:
        return False  # nothing is running (nobody created the event)
    try:
        return bool(k32.SetEvent(handle))
    finally:
        k32.CloseHandle(handle)


class QuitListener:
    """Watches for ``request_quit``. Call ``requested()`` to check without waiting."""

    def __init__(self, reset: bool = False) -> None:
        self._k32 = _kernel32()
        self._handle = None
        if self._k32 is not None:
            # Manual-reset, so every listening process sees the same request.
            self._handle = self._k32.CreateEventW(None, True, False, EVENT_NAME)
            if self._handle and reset:
                # A fresh background sorter must not react to a request that was
                # meant for the copy it replaces.
                self._k32.ResetEvent(self._handle)

    def requested(self, wait_ms: int = 0) -> bool:
        if not self._handle:
            return False
        return self._k32.WaitForSingleObject(self._handle, max(0, int(wait_ms))) == _WAIT_OBJECT_0

    def close(self) -> None:
        if self._handle:
            self._k32.CloseHandle(self._handle)
            self._handle = None

    def watch(self, on_quit: Callable[[], None], stop: threading.Event) -> threading.Thread | None:
        """Call ``on_quit`` once when a quit is requested, until ``stop`` is set."""
        if not self._handle:
            return None

        def run() -> None:
            while not stop.is_set():
                if self.requested(500):
                    on_quit()
                    return

        thread = threading.Thread(target=run, name="quit-listener", daemon=True)
        thread.start()
        return thread
