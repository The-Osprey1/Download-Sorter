"""Adversarial stress-test suite for Milestone 1 Registry & Launch Commands.

Empirical verification of:
1. _get_launch_command: frozen vs unfrozen, pythonw preference, spaces & special chars in paths.
2. _delete_reg_key_tree: deep nested subkeys (5 levels), missing keys, and permission denial.
3. Startup and Context Menu toggling: repeated enable/disable cycles and registry cleanliness.
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import config_manager as cm

# Check if running on Windows
IS_WINDOWS = sys.platform == "win32"


def parse_command_line_windows(cmd: str) -> list[str]:
    """Parse a Windows command line string using CommandLineToArgvW."""
    if not IS_WINDOWS:
        import shlex
        return shlex.split(cmd)

    shell32 = ctypes.windll.shell32
    kernel32 = ctypes.windll.kernel32

    num_args = ctypes.c_int()
    shell32.CommandLineToArgvW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)

    argv = shell32.CommandLineToArgvW(cmd, ctypes.byref(num_args))
    if not argv:
        raise RuntimeError("CommandLineToArgvW failed to parse command line")

    try:
        args = [argv[i] for i in range(num_args.value)]
        return args
    finally:
        kernel32.LocalFree(argv)


# ============================================================================
# 1. Verification of _get_launch_command
# ============================================================================

def test_launch_command_unfrozen_prefers_pythonw():
    """Verify in unfrozen development environment pythonw.exe is selected when present."""
    cmd = cm._get_launch_command(["--tray"])
    args = parse_command_line_windows(cmd)

    assert len(args) >= 3
    exe_name = Path(args[0]).name.lower()
    # If pythonw.exe exists in the venv, it must be pythonw.exe
    venv_pythonw = Path(sys.executable).with_name("pythonw.exe")
    if venv_pythonw.exists():
        assert exe_name == "pythonw.exe", f"Expected pythonw.exe, got {exe_name}"
    else:
        assert exe_name == "python.exe", f"Expected python.exe fallback, got {exe_name}"

    assert Path(args[1]).name.lower() == "main.py"
    assert args[2] == "--tray"


def test_launch_command_unfrozen_fallback_when_pythonw_missing(monkeypatch):
    """Verify fallback to python.exe when pythonw.exe is absent."""
    original_exists = Path.exists

    def mock_exists(self):
        if self.name.lower() == "pythonw.exe":
            return False
        return original_exists(self)

    monkeypatch.setattr(Path, "exists", mock_exists)
    cmd = cm._get_launch_command(["--tray"])
    args = parse_command_line_windows(cmd)
    exe_name = Path(args[0]).name.lower()
    assert exe_name == "python.exe", f"Expected fallback to python.exe, got {exe_name}"


def test_launch_command_frozen_environment(monkeypatch):
    """Verify frozen environment uses sys.executable directly without main.py."""
    frozen_path = r"C:\Program Files\Download Sorter\DownloadSorter.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", frozen_path)

    cmd = cm._get_launch_command(["--tray"])
    args = parse_command_line_windows(cmd)
    assert len(args) == 2
    assert args[0] == frozen_path
    assert args[1] == "--tray"


def test_launch_command_spaces_and_special_chars_in_paths(monkeypatch):
    """Adversarial stress-test: Spaces, ampersands, exclamation marks, parentheses, unicode."""
    adversarial_frozen_paths = [
        r"C:\Users\John Doe\AppData\Local\Programs\Download Sorter\DownloadSorter.exe",
        r"C:\Program Files (x86)\Devyn's Suite & Tools (v2)\DownloadSorter.exe",
        r"C:\Users\José Müller\Downloads & Apps! (Test)\DownloadSorter.exe",
        r"C:\Apps\Space And [Brackets] & Symbols! #1\DownloadSorter.exe",
    ]

    for p in adversarial_frozen_paths:
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", p)

        # Startup launch command
        cmd_startup = cm._get_launch_command(["--tray"])
        args_startup = parse_command_line_windows(cmd_startup)
        assert args_startup[0] == p, f"Failed parsing executable path with spaces/special chars: {p}"
        assert args_startup[1] == "--tray"

        # Context menu sort command with simulated Explorer %1 substitution
        cmd_sort = cm._get_launch_command(["--sort", '--target "%1"', "--notify"])
        # Simulate Explorer substituting %1 with a path containing spaces & special chars
        target_dir = r"C:\Users\José Müller (Test & Docs)\Downloads"
        cmd_sort_expanded = cmd_sort.replace("%1", target_dir)
        args_sort = parse_command_line_windows(cmd_sort_expanded)

        assert args_sort[0] == p
        assert args_sort[1] == "--sort"
        assert args_sort[2] == "--target"
        assert args_sort[3] == target_dir, f"Target directory was split or corrupted by Explorer substitution: {args_sort[3]}"
        assert args_sort[4] == "--notify"


def test_launch_command_empty_args():
    """Verify _get_launch_command handles empty args list cleanly."""
    cmd = cm._get_launch_command([])
    args = parse_command_line_windows(cmd)
    assert len(args) >= 1
    assert not cmd.endswith(" ")


# ============================================================================
# 2. Verification of _delete_reg_key_tree
# ============================================================================

@pytest.mark.skipif(not IS_WINDOWS, reason="Registry tests require Windows")
def test_delete_reg_key_tree_deep_nested_real_registry():
    """Adversarial test: Delete a real 5-level deep nested registry tree with values and siblings."""
    import winreg

    test_root = r"Software\DownloadSorter_Adversarial_Test_Key"

    # Clean up before test in case of remnants
    cm._delete_reg_key_tree(winreg.HKEY_CURRENT_USER, test_root)

    # Build deep tree:
    # HKCU\Software\DownloadSorter_Adversarial_Test_Key
    #   ├── Level1
    #   │     ├── Level2
    #   │     │     ├── Level3
    #   │     │     │     ├── Level4
    #   │     │     │     │     └── Level5 (value: LeafData="deep")
    #   │     │     │     └── Level4_sibling (value: Num=42)
    #   │     │     └── Level3_sibling
    #   │     └── Level2_sibling
    #   └── Level1_branchB
    paths_to_create = [
        rf"{test_root}\Level1\Level2\Level3\Level4\Level5",
        rf"{test_root}\Level1\Level2\Level3\Level4_sibling",
        rf"{test_root}\Level1\Level2\Level3_sibling",
        rf"{test_root}\Level1\Level2_sibling",
        rf"{test_root}\Level1_branchB",
    ]

    for p in paths_to_create:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, p) as key:
            winreg.SetValueEx(key, "TestVal", 0, winreg.REG_SZ, "data_at_" + p)
            winreg.SetValueEx(key, "TestInt", 0, winreg.REG_DWORD, 12345)

    # Verify deep key exists
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{test_root}\Level1\Level2\Level3\Level4\Level5", 0, winreg.KEY_READ) as key:
        val, _ = winreg.QueryValueEx(key, "TestVal")
        assert "Level5" in val

    # Execute recursive deletion
    cm._delete_reg_key_tree(winreg.HKEY_CURRENT_USER, test_root)

    # Verify that the entire root key is gone
    with pytest.raises(FileNotFoundError):
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, test_root, 0, winreg.KEY_READ)

    # Verify children are also gone
    for p in paths_to_create:
        with pytest.raises(FileNotFoundError):
            winreg.OpenKey(winreg.HKEY_CURRENT_USER, p, 0, winreg.KEY_READ)


@pytest.mark.skipif(not IS_WINDOWS, reason="Registry tests require Windows")
def test_delete_reg_key_tree_missing_key_no_crash():
    """Verify deleting non-existent key completes cleanly without raising FileNotFoundError."""
    import winreg

    missing_key = r"Software\DownloadSorter_Def_Not_Found_98765"
    # Should not raise exception
    cm._delete_reg_key_tree(winreg.HKEY_CURRENT_USER, missing_key)


@pytest.mark.skipif(not IS_WINDOWS, reason="Registry tests require Windows")
def test_delete_reg_key_tree_permission_denial_no_crash(monkeypatch):
    """Verify permission denial (WinError 5) on root key does not crash _delete_reg_key_tree."""
    import winreg

    def mock_open_key_denied(*args, **kwargs):
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(winreg, "OpenKey", mock_open_key_denied)
    # Should catch OSError/PermissionError and pass silently
    cm._delete_reg_key_tree(winreg.HKEY_CURRENT_USER, r"Software\SomeProtectedKey")


@pytest.mark.skipif(not IS_WINDOWS, reason="Registry tests require Windows")
def test_delete_reg_key_tree_child_permission_denial_loop(monkeypatch):
    """Adversarial challenge: What if a child key cannot be deleted due to permission denial?
    In config_manager.py:
        while True:
            child = winreg.EnumKey(key, 0)
            _delete_reg_key_tree(...)
    If a child fails to delete, EnumKey(key, 0) returns the same child, causing an infinite loop.
    Verify whether this failure mode occurs.
    """
    import winreg
    import threading

    # Create a real test parent with one child
    test_root = r"Software\DownloadSorter_Loop_Test"
    cm._delete_reg_key_tree(winreg.HKEY_CURRENT_USER, test_root)

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{test_root}\ChildA"):
        pass

    orig_delete = winreg.DeleteKey
    call_counts = {"ChildA": 0}

    def failing_delete(root, subkey):
        if "ChildA" in subkey:
            call_counts["ChildA"] += 1
            if call_counts["ChildA"] > 2:
                # Break loop to prevent test hang
                orig_delete(root, subkey)
                return
            raise PermissionError(5, "Access is denied")
        orig_delete(root, subkey)

    monkeypatch.setattr(winreg, "DeleteKey", failing_delete)

    cm._delete_reg_key_tree(winreg.HKEY_CURRENT_USER, test_root)

    # Clean up
    cm._delete_reg_key_tree(winreg.HKEY_CURRENT_USER, test_root)

    # If call_counts["ChildA"] > 1, it retried because EnumKey(key, 0) kept returning ChildA!
    assert call_counts["ChildA"] > 1, "EnumKey(key, 0) retried the undeleted child"



# ============================================================================
# 3. Repeated Toggling of Startup & Context Menu on Real Registry
# ============================================================================

@pytest.mark.skipif(not IS_WINDOWS, reason="Registry tests require Windows")
def test_startup_repeated_toggle_cleanliness():
    """Empirical verification: Toggle startup 5 times in succession on real HKCU registry."""
    import winreg

    # Record initial state to restore at end of test
    initial_state = cm.is_startup_enabled()

    try:
        for cycle in range(5):
            # 1. Enable
            res_en = cm.set_startup_enabled(True)
            assert res_en is True, f"Cycle {cycle}: set_startup_enabled(True) failed"
            assert cm.is_startup_enabled() is True, f"Cycle {cycle}: is_startup_enabled() returned False after enable"

            # Directly inspect registry value
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ) as key:
                val, reg_type = winreg.QueryValueEx(key, "DownloadSorter")
                assert reg_type == winreg.REG_SZ
                assert "--tray" in val

            # 2. Disable
            res_dis = cm.set_startup_enabled(False)
            assert res_dis is True, f"Cycle {cycle}: set_startup_enabled(False) failed"
            assert cm.is_startup_enabled() is False, f"Cycle {cycle}: is_startup_enabled() returned True after disable"

            # Directly verify value deleted
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ) as key:
                with pytest.raises(FileNotFoundError):
                    winreg.QueryValueEx(key, "DownloadSorter")

        # Idempotence: Disable when already disabled
        assert cm.set_startup_enabled(False) is True
        assert cm.is_startup_enabled() is False

        # Idempotence: Enable when already enabled
        assert cm.set_startup_enabled(True) is True
        assert cm.is_startup_enabled() is True
        assert cm.set_startup_enabled(True) is True
        assert cm.is_startup_enabled() is True
    finally:
        # Restore initial state
        cm.set_startup_enabled(initial_state)
        assert cm.is_startup_enabled() == initial_state


@pytest.mark.skipif(not IS_WINDOWS, reason="Registry tests require Windows")
def test_context_menu_repeated_toggle_cleanliness():
    """Empirical verification: Toggle context menus 5 times in succession on real HKCU registry."""
    import winreg

    folder_key = r"Software\Classes\Directory\shell\DownloadSorter"
    bg_key = r"Software\Classes\Directory\Background\shell\DownloadSorterUndo"

    initial_state = cm.is_context_menu_enabled()

    try:
        for cycle in range(5):
            # 1. Enable
            res_en = cm.set_context_menu_enabled(True)
            assert res_en is True, f"Cycle {cycle}: set_context_menu_enabled(True) failed"
            assert cm.is_context_menu_enabled() is True, f"Cycle {cycle}: is_context_menu_enabled() returned False"

            # Verify Directory\shell\DownloadSorter keys & commands
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, folder_key, 0, winreg.KEY_READ) as k:
                val, _ = winreg.QueryValueEx(k, "")
                assert val == "Sort with DownloadSorter"
                icon, _ = winreg.QueryValueEx(k, "Icon")
                assert "imageres.dll" in icon
                with winreg.OpenKey(k, "command", 0, winreg.KEY_READ) as cmd_k:
                    cmd_val, _ = winreg.QueryValueEx(cmd_k, "")
                    assert "--sort" in cmd_val
                    assert '--target "%1"' in cmd_val
                    assert "--notify" in cmd_val

            # Verify Directory\Background\shell\DownloadSorterUndo keys & commands
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, bg_key, 0, winreg.KEY_READ) as k:
                val, _ = winreg.QueryValueEx(k, "")
                assert val == "Undo Last DownloadSorter Batch"
                icon, _ = winreg.QueryValueEx(k, "Icon")
                assert "imageres.dll" in icon
                with winreg.OpenKey(k, "command", 0, winreg.KEY_READ) as cmd_k:
                    cmd_val, _ = winreg.QueryValueEx(cmd_k, "")
                    assert "--undo" in cmd_val
                    assert "--notify" in cmd_val

            # 2. Disable
            res_dis = cm.set_context_menu_enabled(False)
            assert res_dis is True, f"Cycle {cycle}: set_context_menu_enabled(False) failed"
            assert cm.is_context_menu_enabled() is False, f"Cycle {cycle}: is_context_menu_enabled() returned True"

            # Verify both keys and subkeys are completely removed
            with pytest.raises(FileNotFoundError):
                winreg.OpenKey(winreg.HKEY_CURRENT_USER, folder_key, 0, winreg.KEY_READ)
            with pytest.raises(FileNotFoundError):
                winreg.OpenKey(winreg.HKEY_CURRENT_USER, bg_key, 0, winreg.KEY_READ)

        # Idempotence: Disable when already disabled
        assert cm.set_context_menu_enabled(False) is True
        assert cm.is_context_menu_enabled() is False

        # Idempotence: Enable when already enabled
        assert cm.set_context_menu_enabled(True) is True
        assert cm.is_context_menu_enabled() is True
        assert cm.set_context_menu_enabled(True) is True
        assert cm.is_context_menu_enabled() is True
    finally:
        # Restore initial state
        cm.set_context_menu_enabled(initial_state)
        assert cm.is_context_menu_enabled() == initial_state
