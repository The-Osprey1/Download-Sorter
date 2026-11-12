"""A global keyboard shortcut for the recent-files shelf (#25), using Windows' RegisterHotKey."""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
from ctypes import wintypes

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000
VK_SPACE = 0x20
WM_HOTKEY = 0x0312
SHELF_HOTKEY_ID = 0x5A11
DEFAULT_SHELF_MODS = MOD_CONTROL | MOD_SHIFT
DEFAULT_SHELF_KEY = VK_SPACE


def parse_shelf_hotkey(spec: str | None) -> tuple[int, int] | None:
    """Turn a shortcut string like ``Ctrl+Shift+Space`` into ``(modifiers, virtual_key)``."""
    if not spec:
        return None
    try:
        import batch_handoff
        return batch_handoff.parse_hotkey(str(spec))
    except Exception:
        return None


def start(
    callback,
    modifiers: int = DEFAULT_SHELF_MODS,
    key: int = DEFAULT_SHELF_KEY,
    spec: str | None = None,
) -> bool:
    """Run ``callback`` when the shortcut is pressed. Returns False when it can't be registered
    (another program has it, or this is not Windows). The message loop runs in its own thread."""
    if sys.platform != "win32":
        return False
    if spec is not None:
        parsed = parse_shelf_hotkey(spec)
        if parsed is None:
            logging.info("Recent-files shortcut %r is not valid; left off", spec)
            return False
        modifiers, key = parsed
    started = threading.Event()
    result = {"ok": False}

    def loop() -> None:
        user32 = ctypes.windll.user32
        ok = bool(user32.RegisterHotKey(None, SHELF_HOTKEY_ID, modifiers | MOD_NOREPEAT, key))
        result["ok"] = ok
        started.set()
        if not ok:
            logging.info("The recent-files shortcut is already used by another program")
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == WM_HOTKEY and msg.wParam == SHELF_HOTKEY_ID:
                try:
                    callback()
                except Exception:
                    logging.exception("Recent-files shortcut failed")
        user32.UnregisterHotKey(None, SHELF_HOTKEY_ID)

    threading.Thread(target=loop, name="shelf-hotkey", daemon=True).start()
    started.wait(2.0)
    return result["ok"]
