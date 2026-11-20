"""Whether now is a bad moment for a pop-up: a full-screen game or video, a
presentation, or the lock screen.

Windows answers this with ``SHQueryUserNotificationState``. Windows' own Do Not
Disturb switch is not part of that answer, so it is not covered here.
"""

from __future__ import annotations

import sys

# QUERY_USER_NOTIFICATION_STATE values that mean "don't pop anything up now".
_QUIET_STATES = {
    1,  # QUNS_NOT_PRESENT: screen locked, screen saver, or fast user switching
    2,  # QUNS_BUSY: a full-screen app
    3,  # QUNS_RUNNING_D3D_FULL_SCREEN: a full-screen game or video
    4,  # QUNS_PRESENTATION_MODE: presentation settings are on
    6,  # QUNS_QUIET_TIME: the first hour after a new Windows setup
    7,  # QUNS_APP: a full-screen Store app
}


def notification_state() -> int | None:
    """Windows' current notification state, or None when it can't be read."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        query = ctypes.WinDLL("shell32").SHQueryUserNotificationState
        query.argtypes = [ctypes.POINTER(ctypes.c_int)]
        query.restype = ctypes.c_long
        state = ctypes.c_int(0)
        if query(ctypes.byref(state)) != 0:
            return None
        return int(state.value)
    except Exception:  # noqa: BLE001 - unknown means "show the ribbon as usual"
        return None


def is_quiet_state(state: int | None) -> bool:
    return state in _QUIET_STATES


def is_quiet_time() -> bool:
    """True while pop-ups would get in the way (full-screen app, presentation, lock screen)."""
    return is_quiet_state(notification_state())
