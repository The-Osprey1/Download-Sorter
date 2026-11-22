"""Explicit application paths container (no hidden module-level state)."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import sys

# Stored in settings when the user hasn't picked a folder: "wherever Downloads is".
DEFAULT_WATCH_FOLDER = r"${USERPROFILE}\Downloads"

# Windows' identifier for the Downloads folder (FOLDERID_Downloads).
_DOWNLOADS_FOLDER_ID = "374DE290-123F-4565-9164-39C4925E467B"


def known_downloads_dir() -> Path | None:
    """Where Windows says the Downloads folder is, or None if it can't say.

    People move Downloads to another drive (Properties > Location), and OneDrive
    can move it too, so ``%USERPROFILE%\\Downloads`` is not always the right folder.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        import uuid

        class _GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        folder_id = _GUID.from_buffer_copy(uuid.UUID(_DOWNLOADS_FOLDER_ID).bytes_le)
        get_path = ctypes.WinDLL("shell32").SHGetKnownFolderPath
        get_path.argtypes = [
            ctypes.POINTER(_GUID),
            wintypes.DWORD,
            wintypes.HANDLE,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        get_path.restype = ctypes.c_long
        free = ctypes.WinDLL("ole32").CoTaskMemFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = None

        buffer = ctypes.c_void_p()
        result = get_path(ctypes.byref(folder_id), 0, None, ctypes.byref(buffer))
        try:
            if result != 0 or not buffer.value:
                return None
            text = ctypes.wstring_at(buffer.value)
        finally:
            if buffer.value:
                free(buffer)
    except Exception:  # noqa: BLE001 - any failure means "fall back to the profile folder"
        return None
    found = Path(text) if text else None
    return found if found is not None and found.is_dir() else None


def default_downloads_dir() -> Path:
    """The Downloads folder: where Windows says it is, else the one in the profile."""
    return known_downloads_dir() or Path.home() / "Downloads"


def is_default_watch_folder(text: str) -> bool:
    norm = str(text or "").strip().replace("/", "\\").rstrip("\\").lower()
    return norm in ("", DEFAULT_WATCH_FOLDER.lower(), r"%userprofile%\downloads", r"$userprofile\downloads")


def _local_app_data() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base)
    return Path.home() / "AppData" / "Local"


@dataclass(frozen=True)
class AppPaths:
    """All storage locations for a Download Sorter v3 instance."""

    data_dir: Path
    downloads_dir: Path | None = None

    @classmethod
    def default(cls) -> AppPaths:
        return cls(data_dir=_local_app_data() / "download-sorter-v3")

    @staticmethod
    def v2_config_file() -> Path:
        return _local_app_data() / "download-sorter" / "config.json"

    @property
    def config_file(self) -> Path:
        return self.data_dir / "config.json"

    @property
    def backup_config_file(self) -> Path:
        return self.data_dir / "config.json.bak"

    @property
    def db_file(self) -> Path:
        return self.data_dir / "state.db"

    @property
    def log_file(self) -> Path:
        return self.data_dir / "sorter_v3.log"

    @property
    def pause_file(self) -> Path:
        return self.data_dir / "paused"

    @property
    def recent_state_file(self) -> Path:
        return self.data_dir / "recent_files.json"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
