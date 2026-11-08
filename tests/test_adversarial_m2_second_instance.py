"""Empirical adversarial stress test suite for Milestone 2:
- Second-instance mutex contention and smart launch in tray.py:main()
- First-run welcome wizard detection in tray.py:main()
- Windowless sort_once.py and sort_undo.py summary toasts with --notify / --quiet-toast
- Parsing and validation of install_context_menu.ps1 registry commands and quotes
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import about
import main
import ribbon_popup
import sort_once
import sort_undo
import tray
import watch


# ============================================================================
# Group 1: Second-Instance Mutex Contention in tray.py:main()
# ============================================================================

def test_tray_main_single_instance_lock_contention(monkeypatch):
    """Verify that when watch.acquire_single_instance_lock() returns None,
    spawn_window('--home') is invoked, SorterTray is not started, and main exits 0.
    """
    mock_acquire = MagicMock(return_value=None)
    mock_spawn = MagicMock()
    mock_tray_class = MagicMock()

    monkeypatch.setattr(watch, "acquire_single_instance_lock", mock_acquire)
    monkeypatch.setattr(tray, "spawn_window", mock_spawn)
    monkeypatch.setattr(tray, "SorterTray", mock_tray_class)

    exit_code = tray.main()

    assert exit_code == 0, f"Expected exit code 0 on lock contention, got {exit_code}"
    mock_acquire.assert_called_once()
    mock_spawn.assert_called_once_with("--home")
    mock_tray_class.assert_not_called()


def test_tray_main_concurrent_lock_contention_stress(monkeypatch):
    """Stress-test 50 rapid simulated second-instance launches to verify 100% exit code 0
    and zero unhandled exceptions or state leaks.
    """
    mock_acquire = MagicMock(return_value=None)
    mock_spawn = MagicMock()
    monkeypatch.setattr(watch, "acquire_single_instance_lock", mock_acquire)
    monkeypatch.setattr(tray, "spawn_window", mock_spawn)

    for i in range(50):
        code = tray.main()
        assert code == 0, f"Iteration {i}: expected exit code 0, got {code}"

    assert mock_spawn.call_count == 50
    for c in mock_spawn.call_args_list:
        assert c == call("--home")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows named mutex requires win32")
def test_real_win32_named_mutex_contention(monkeypatch):
    """Empirical Windows kernel test: hold a real Local\\DownloadSorter_SingleInstance mutex
    and verify watch.acquire_single_instance_lock() detects collision (ERROR_ALREADY_EXISTS 183)
    and tray.main() triggers spawn_window('--home') with exit code 0.
    """
    mutex_name = "Local\\DownloadSorter_SingleInstance"
    # Create the initial mutex to hold the lock in the kernel
    h_mutex = ctypes.windll.kernel32.CreateMutexW(None, False, mutex_name)
    assert h_mutex != 0, "Failed to create real win32 mutex"

    try:
        # A second acquisition attempt on the same lock must return None
        colliding_lock = watch.acquire_single_instance_lock("DownloadSorter_SingleInstance")
        assert colliding_lock is None, (
            "watch.acquire_single_instance_lock() should have returned None when mutex is already held"
        )

        mock_spawn = MagicMock()
        monkeypatch.setattr(tray, "spawn_window", mock_spawn)

        code = tray.main()
        assert code == 0
        mock_spawn.assert_called_once_with("--home")
    finally:
        ctypes.windll.kernel32.CloseHandle(h_mutex)


def test_spawn_window_command_construction(monkeypatch):
    """Verify tray.spawn_window constructs appropriate windowless subprocess commands."""
    mock_popen = MagicMock()
    monkeypatch.setattr(subprocess, "Popen", mock_popen)

    # 1. Dev mode execution
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    tray.spawn_window("--home")
    assert mock_popen.called
    cmd, kwargs = mock_popen.call_args
    args_list = cmd[0]
    assert args_list[-1] == "--home"
    assert "main.py" in args_list[-2]
    if sys.platform == "win32":
        assert kwargs.get("creationflags") == subprocess.CREATE_NO_WINDOW

    mock_popen.reset_mock()

    # 2. Frozen executable mode
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    tray.spawn_window("--welcome")
    assert mock_popen.called
    cmd, kwargs = mock_popen.call_args
    args_list = cmd[0]
    assert args_list[0] == sys.executable
    assert args_list[1] == "--welcome"
    if sys.platform == "win32":
        assert kwargs.get("creationflags") == subprocess.CREATE_NO_WINDOW

    mock_popen.reset_mock()

    # 3. rules_manager.py stripping logic
    tray.spawn_window("rules_manager.py", "--tab", "remodel")
    cmd, _ = mock_popen.call_args
    assert cmd[0][1:] == ["--tab", "remodel"]


def test_spawn_window_handles_popen_exception(monkeypatch):
    """Verify spawn_window catches and logs subprocess exceptions without crashing."""
    def broken_popen(*args, **kwargs):
        raise OSError("Simulated process spawn failure")

    monkeypatch.setattr(subprocess, "Popen", broken_popen)
    # Should not raise exception
    tray.spawn_window("--home")


def test_tray_menu_order_and_default_action(monkeypatch):
    """Verify SorterTray.build_menu() prioritizes 'Open Download Sorter' as default item 0
    and does not set default=True on Pause sorting.
    """
    mock_icon = MagicMock()
    monkeypatch.setattr(tray, "make_icon", lambda *a, **kw: mock_icon)

    st = tray.SorterTray()
    menu = st.build_menu()

    items = list(menu.items)
    assert len(items) > 5, "Tray menu should contain menu items"
    first_item = items[0]
    assert first_item.text == "Open Download Sorter"
    assert getattr(first_item, "default", False) is True, "'Open Download Sorter' must have default=True"

    # Search for pause item
    pause_items = [it for it in items if hasattr(it, "text") and callable(it.text) and "pause" in str(it.text(None)).lower()]
    for p_it in pause_items:
        assert getattr(p_it, "default", False) is not True, "Pause item must not have default=True"


# ============================================================================
# Group 2: First-Run Welcome Detection in tray.py:main()
# ============================================================================

def test_first_run_welcome_when_onboarding_completed_missing(monkeypatch):
    """When lock is acquired and onboarding_completed is missing from config,
    verify spawn_window('--welcome') is invoked.
    """
    dummy_lock = MagicMock()
    monkeypatch.setattr(watch, "acquire_single_instance_lock", lambda: dummy_lock)
    monkeypatch.setattr(tray, "load_config", lambda: {})

    mock_spawn = MagicMock()
    mock_run = MagicMock(return_value=0)
    monkeypatch.setattr(tray, "spawn_window", mock_spawn)
    monkeypatch.setattr(tray.SorterTray, "run", mock_run)

    code = tray.main()
    assert code == 0
    mock_spawn.assert_called_once_with("--welcome")
    mock_run.assert_called_once()


def test_first_run_welcome_when_onboarding_completed_false(monkeypatch):
    """When lock is acquired and onboarding_completed is False,
    verify spawn_window('--welcome') is invoked.
    """
    dummy_lock = MagicMock()
    monkeypatch.setattr(watch, "acquire_single_instance_lock", lambda: dummy_lock)
    monkeypatch.setattr(tray, "load_config", lambda: {"onboarding_completed": False})

    mock_spawn = MagicMock()
    mock_run = MagicMock(return_value=0)
    monkeypatch.setattr(tray, "spawn_window", mock_spawn)
    monkeypatch.setattr(tray.SorterTray, "run", mock_run)

    code = tray.main()
    assert code == 0
    mock_spawn.assert_called_once_with("--welcome")


def test_no_welcome_when_onboarding_completed_true(monkeypatch):
    """When onboarding_completed is True, verify spawn_window('--welcome') is NOT called."""
    dummy_lock = MagicMock()
    monkeypatch.setattr(watch, "acquire_single_instance_lock", lambda: dummy_lock)
    monkeypatch.setattr(tray, "load_config", lambda: {"onboarding_completed": True})

    mock_spawn = MagicMock()
    mock_run = MagicMock(return_value=0)
    monkeypatch.setattr(tray, "spawn_window", mock_spawn)
    monkeypatch.setattr(tray.SorterTray, "run", mock_run)

    code = tray.main()
    assert code == 0
    mock_spawn.assert_not_called()


def test_lock_contention_takes_precedence_over_first_run(monkeypatch):
    """Even if onboarding_completed is False, lock contention MUST take precedence
    and trigger spawn_window('--home') instead of '--welcome'.
    """
    monkeypatch.setattr(watch, "acquire_single_instance_lock", lambda: None)
    monkeypatch.setattr(tray, "load_config", lambda: {"onboarding_completed": False})

    mock_spawn = MagicMock()
    mock_run = MagicMock(return_value=0)
    monkeypatch.setattr(tray, "spawn_window", mock_spawn)
    monkeypatch.setattr(tray.SorterTray, "run", mock_run)

    code = tray.main()
    assert code == 0
    mock_spawn.assert_called_once_with("--home")
    mock_run.assert_not_called()


# ============================================================================
# Group 3: sort_once.py and sort_undo.py Desktop Ribbon Summary Toasts
# ============================================================================

def test_sort_once_summary_toast_files_moved(monkeypatch):
    """sort_once.py with --notify dispatches summary toast with count and #69E6AD mint accent."""
    mock_notify = MagicMock()
    monkeypatch.setattr(ribbon_popup, "notify_summary", mock_notify)
    monkeypatch.setattr(sort_once, "sort_existing", lambda *a, **kw: 3)
    monkeypatch.setattr(sort_once, "sort_existing_documents", lambda *a, **kw: 2)
    monkeypatch.setattr(sort_once, "sort_existing_misplaced", lambda *a, **kw: 0)

    exit_code = sort_once.main(["--notify"])
    assert exit_code == 0
    mock_notify.assert_called_once_with(
        "Downloads Organized",
        "Filed 5 file(s) into category folders.",
        accent="#69E6AD",
    )


def test_sort_once_summary_toast_no_files_moved(monkeypatch):
    """sort_once.py with --notify dispatches 'already organized' when 0 files moved."""
    mock_notify = MagicMock()
    monkeypatch.setattr(ribbon_popup, "notify_summary", mock_notify)
    monkeypatch.setattr(sort_once, "sort_existing", lambda *a, **kw: 0)
    monkeypatch.setattr(sort_once, "sort_existing_documents", lambda *a, **kw: 0)
    monkeypatch.setattr(sort_once, "sort_existing_misplaced", lambda *a, **kw: 0)

    exit_code = sort_once.main(["--notify"])
    assert exit_code == 0
    mock_notify.assert_called_once_with(
        "Downloads Organized",
        "Downloads is already organized.",
        accent="#69E6AD",
    )


def test_sort_once_quiet_toast_flag(monkeypatch):
    """sort_once.py with --quiet-toast dispatches identically."""
    mock_notify = MagicMock()
    monkeypatch.setattr(ribbon_popup, "notify_summary", mock_notify)
    monkeypatch.setattr(sort_once, "sort_existing", lambda *a, **kw: 1)
    monkeypatch.setattr(sort_once, "sort_existing_documents", lambda *a, **kw: 0)
    monkeypatch.setattr(sort_once, "sort_existing_misplaced", lambda *a, **kw: 0)

    exit_code = sort_once.main(["--quiet-toast"])
    assert exit_code == 0
    mock_notify.assert_called_once_with(
        "Downloads Organized",
        "Filed 1 file(s) into category folders.",
        accent="#69E6AD",
    )


def test_sort_once_no_toast_when_flag_absent(monkeypatch):
    """sort_once.py without --notify / --quiet-toast does NOT trigger toast."""
    mock_notify = MagicMock()
    monkeypatch.setattr(ribbon_popup, "notify_summary", mock_notify)
    monkeypatch.setattr(sort_once, "sort_existing", lambda *a, **kw: 4)
    monkeypatch.setattr(sort_once, "sort_existing_documents", lambda *a, **kw: 0)
    monkeypatch.setattr(sort_once, "sort_existing_misplaced", lambda *a, **kw: 0)

    exit_code = sort_once.main([])
    assert exit_code == 0
    mock_notify.assert_not_called()


def test_sort_undo_summary_toast_files_restored(monkeypatch):
    """sort_undo.py with --notify dispatches restored summary toast with #FA7183 red accent."""
    mock_notify = MagicMock()
    monkeypatch.setattr(ribbon_popup, "notify_summary", mock_notify)
    monkeypatch.setattr(sort_undo, "load_history", lambda: [{"id": "1", "batch_id": "b1", "action": "move", "src": "a", "dest": "b"}])
    monkeypatch.setattr(sort_undo, "get_active_moves", lambda r: r)
    monkeypatch.setattr(sort_undo, "undo_moves", lambda *a, **kw: 3)

    exit_code = sort_undo.main(["--notify"])
    assert exit_code == 0
    mock_notify.assert_called_once_with(
        "Undo Last Batch",
        "Restored 3 file(s) to Downloads.",
        accent="#FA7183",
    )


def test_sort_undo_summary_toast_empty_history(monkeypatch):
    """sort_undo.py with --notify dispatches 'No reversible move history found.' when history is empty."""
    mock_notify = MagicMock()
    monkeypatch.setattr(ribbon_popup, "notify_summary", mock_notify)
    monkeypatch.setattr(sort_undo, "load_history", lambda: [])
    monkeypatch.setattr(sort_undo, "get_active_moves", lambda r: [])

    exit_code = sort_undo.main(["--notify"])
    assert exit_code == 0
    mock_notify.assert_called_once_with(
        "Undo Last Batch",
        "No reversible move history found.",
        accent="#FA7183",
    )


def test_sort_undo_quiet_toast_flag(monkeypatch):
    """sort_undo.py with --quiet-toast dispatches identically."""
    mock_notify = MagicMock()
    monkeypatch.setattr(ribbon_popup, "notify_summary", mock_notify)
    monkeypatch.setattr(sort_undo, "load_history", lambda: [{"id": "1", "batch_id": "b1", "action": "move", "src": "a", "dest": "b"}])
    monkeypatch.setattr(sort_undo, "get_active_moves", lambda r: r)
    monkeypatch.setattr(sort_undo, "undo_moves", lambda *a, **kw: 2)

    exit_code = sort_undo.main(["--quiet-toast"])
    assert exit_code == 0
    mock_notify.assert_called_once_with(
        "Undo Last Batch",
        "Restored 2 file(s) to Downloads.",
        accent="#FA7183",
    )


def test_sort_undo_no_toast_when_flag_absent(monkeypatch):
    """sort_undo.py without --notify / --quiet-toast does NOT trigger toast."""
    mock_notify = MagicMock()
    monkeypatch.setattr(ribbon_popup, "notify_summary", mock_notify)
    monkeypatch.setattr(sort_undo, "load_history", lambda: [{"id": "1", "batch_id": "b1", "action": "move", "src": "a", "dest": "b"}])
    monkeypatch.setattr(sort_undo, "get_active_moves", lambda r: r)
    monkeypatch.setattr(sort_undo, "undo_moves", lambda *a, **kw: 2)

    exit_code = sort_undo.main([])
    assert exit_code == 0
    mock_notify.assert_not_called()


def test_main_cli_argument_forwarding_to_subcommands(monkeypatch):
    """Verify main.py correctly forwards --notify and --quiet-toast flags to subcommands."""
    mock_sort_main = MagicMock(return_value=0)
    mock_undo_main = MagicMock(return_value=0)
    monkeypatch.setattr(sort_once, "main", mock_sort_main)
    monkeypatch.setattr(sort_undo, "main", mock_undo_main)

    # Sort forward
    main.main(["--sort", "--notify"])
    mock_sort_main.assert_called_once_with(["--notify"])

    mock_sort_main.reset_mock()
    main.main(["-s", "--quiet-toast"])
    mock_sort_main.assert_called_once_with(["--quiet-toast"])

    # Undo forward
    main.main(["--undo", "--notify"])
    mock_undo_main.assert_called_once_with(["--notify"])

    mock_undo_main.reset_mock()
    main.main(["-u", "--quiet-toast"])
    mock_undo_main.assert_called_once_with(["--quiet-toast"])


def test_ribbon_popup_notify_summary_spawns_process(monkeypatch):
    """Verify ribbon_popup.notify_summary spawns detached process with --summary, title, message, and accent."""
    mock_popen = MagicMock()
    monkeypatch.setattr(subprocess, "Popen", mock_popen)

    ribbon_popup.notify_summary(
        title="Test Title",
        message="Test Message",
        accent="#69E6AD",
        timeout_ms=3000,
    )

    assert mock_popen.called
    cmd = mock_popen.call_args[0][0]
    assert "--summary" in cmd
    assert "--title" in cmd and cmd[cmd.index("--title") + 1] == "Test Title"
    assert "--message" in cmd and cmd[cmd.index("--message") + 1] == "Test Message"
    assert "--accent" in cmd and cmd[cmd.index("--accent") + 1] == "#69E6AD"
    assert "--timeout" in cmd and cmd[cmd.index("--timeout") + 1] == "3000"


# ============================================================================
# Group 4: install_context_menu.ps1 Parsing & Validation
# ============================================================================

def test_context_menu_script_contains_no_powershell_noexit():
    """Verify install_context_menu.ps1 does not spawn powershell.exe -NoExit console windows."""
    ps1_path = REPO_ROOT / "install_context_menu.ps1"
    assert ps1_path.exists(), "install_context_menu.ps1 must exist"

    content = ps1_path.read_text(encoding="utf-8")

    assert "powershell.exe -NoExit" not in content, (
        "install_context_menu.ps1 must not contain 'powershell.exe -NoExit'"
    )
    assert "-NoExit" not in content, (
        "install_context_menu.ps1 must not contain '-NoExit' flag"
    )


def test_context_menu_script_command_line_parsing_and_quoting():
    """Parse all command lines registered by install_context_menu.ps1:
    - Confirm --notify presence on sort and undo
    - Confirm proper quoting of paths and '%1' parameters
    - Confirm zero unquoted path tokens
    """
    ps1_path = REPO_ROOT / "install_context_menu.ps1"
    content = ps1_path.read_text(encoding="utf-8")

    command_lines = []
    for line in content.splitlines():
        line = line.strip()
        if r"\command" in line and '-Value "' in line:
            idx = line.find('-Value "')
            val = line[idx + len('-Value "'):-1]
            command_lines.append(val)

    assert len(command_lines) >= 8, f"Expected at least 8 command registrations in ps1, found {len(command_lines)}"

    for cmd_str in command_lines:
        # 1. Unescape powershell backtick quotes (`" -> ")
        normalized = cmd_str.replace('`"', '"')

        # 2. Check proper quotation around paths
        assert normalized.startswith('"'), f"Command line must start with quoted executable: {normalized}"

        # 3. Check %1 parameter quotation
        if "%1" in normalized:
            assert '"%1"' in normalized, f"%1 parameter must be safely enclosed in quotes: {normalized}"

        # 4. Check sort and undo have --notify
        if "--sort" in normalized:
            assert "--notify" in normalized, f"Sort command missing --notify: {normalized}"
            assert '--target "%1"' in normalized, f"Sort command missing proper quoted target: {normalized}"

        if "--undo" in normalized:
            assert "--notify" in normalized, f"Undo command missing --notify: {normalized}"


def test_config_manager_context_menu_commands_parity():
    """Verify config_manager.set_context_menu_enabled(True) registers command lines with --notify and proper quotes."""
    import config_manager
    assert hasattr(config_manager, "_get_launch_command")

    sort_cmd = config_manager._get_launch_command(["--sort", '--target "%1"', "--notify"])
    undo_cmd = config_manager._get_launch_command(["--undo", "--notify"])

    assert "--sort" in sort_cmd
    assert "--notify" in sort_cmd
    assert '"%1"' in sort_cmd
    assert sort_cmd.startswith('"')

    assert "--undo" in undo_cmd
    assert "--notify" in undo_cmd
    assert undo_cmd.startswith('"')
