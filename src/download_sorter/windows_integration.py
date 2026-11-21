"""Clean Windows Registry helpers for Startup launch and Explorer context menus."""

from __future__ import annotations

from pathlib import Path
import sys

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "DownloadSorter"
SORT_MENU_KEY = r"Software\Classes\Directory\shell\DownloadSorter"
UNDO_MENU_KEY = r"Software\Classes\Directory\Background\shell\DownloadSorterUndo"


def resolve_executable(exe_path: str | None = None) -> str:
    """Return executable path appropriate for Startup and Explorer commands."""
    if exe_path:
        return str(Path(exe_path).resolve())
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).resolve())
    return str(Path(sys.executable).resolve())


def is_startup_enabled() -> bool:
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, RUN_VALUE)
            return bool(str(val).strip())
    except OSError:
        return False


def set_startup_enabled(enabled: bool, exe_path: str | None = None) -> bool:
    if sys.platform != "win32":
        return False
    import winreg

    try:
        if enabled:
            exe = resolve_executable(exe_path)
            cmd = f'"{exe}" --tray'
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, cmd)
            return True
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, RUN_VALUE)
        return True
    except OSError:
        return not enabled


def is_context_menu_enabled() -> bool:
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SORT_MENU_KEY, 0, winreg.KEY_READ):
            return True
    except OSError:
        return False


def _delete_tree(winreg_mod, sub_key: str) -> None:
    try:
        with winreg_mod.OpenKey(winreg_mod.HKEY_CURRENT_USER, sub_key, 0, winreg_mod.KEY_READ) as key:
            children: list[str] = []
            idx = 0
            while True:
                try:
                    children.append(winreg_mod.EnumKey(key, idx))
                    idx += 1
                except OSError:
                    break
        for child in children:
            _delete_tree(winreg_mod, rf"{sub_key}\{child}")
        winreg_mod.DeleteKey(winreg_mod.HKEY_CURRENT_USER, sub_key)
    except OSError:
        pass


def set_context_menu_enabled(enabled: bool, exe_path: str | None = None) -> bool:
    if sys.platform != "win32":
        return False
    import winreg

    try:
        if enabled:
            exe = resolve_executable(exe_path)
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, SORT_MENU_KEY) as key:
                winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "Sort with Download Sorter")
                winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, exe)
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{SORT_MENU_KEY}\command") as key:
                winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f'"{exe}" "%V"')

            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNDO_MENU_KEY) as key:
                winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "Undo Last Sort")
                winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, exe)
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{UNDO_MENU_KEY}\command") as key:
                winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f'"{exe}" --undo')
            return True

        _delete_tree(winreg, SORT_MENU_KEY)
        _delete_tree(winreg, UNDO_MENU_KEY)
        return True
    except OSError:
        return False
