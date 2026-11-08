"""Empirical adversarial test suite for Milestone 2:
- CLI dispatch in main.py: --home, --welcome, invalid commands, --sort --notify, --undo --notify, --ribbon-summary
- System tray menu in tray.py: SorterTray().build_menu(), item hierarchy, default action, pause toggle
- Ribbon notifications in ribbon_popup.py: notify_summary and SummaryRibbonPopup stress testing
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tkinter as tk
from unittest.mock import MagicMock, call, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import about
import main
import ribbon_popup
import rules_manager
import sort_once
import sort_undo
import sorter
import tray
import watch


# ============================================================================
# Section 1: CLI Dispatch in main.py
# ============================================================================

def test_main_cli_dispatch_home(monkeypatch):
    """Verify main.py correctly dispatches --home to rules_manager.main."""
    mock_rules_main = MagicMock(return_value=0)
    monkeypatch.setattr(rules_manager, "main", mock_rules_main)

    exit_code = main.main(["--home"])

    assert exit_code == 0
    mock_rules_main.assert_called_once_with(["--home"])


def test_main_cli_dispatch_welcome(monkeypatch):
    """Verify main.py correctly dispatches --welcome to rules_manager.main."""
    mock_rules_main = MagicMock(return_value=0)
    monkeypatch.setattr(rules_manager, "main", mock_rules_main)

    exit_code = main.main(["--welcome"])

    assert exit_code == 0
    mock_rules_main.assert_called_once_with(["--welcome"])


@pytest.mark.parametrize("invalid_cmd", [
    "--invalid-cmd",
    "--unknown",
    "-z",
    "foobar",
    "--help-me",
])
def test_main_cli_dispatch_invalid_commands(capsys, invalid_cmd):
    """Verify unknown commands return exit code 2 and display helpful guidance."""
    exit_code = main.main([invalid_cmd])

    assert exit_code == 2
    captured = capsys.readouterr()
    assert f"Unknown command: {invalid_cmd}" in captured.out
    assert "Run with --help to see available commands." in captured.out


def test_main_cli_dispatch_help_and_version(capsys):
    """Verify --help and --version return exit code 0 and emit expected info."""
    code_help = main.main(["--help"])
    assert code_help == 0
    out_help = capsys.readouterr().out
    assert "Download Sorter" in out_help
    assert "--home" in out_help
    assert "--welcome" in out_help

    code_ver = main.main(["--version"])
    assert code_ver == 0
    out_ver = capsys.readouterr().out
    assert about.__version__ in out_ver


def test_main_cli_dispatch_sort_with_notify(monkeypatch):
    """Verify main.py dispatches --sort --notify by forwarding arguments to sort_once.main."""
    mock_sort_main = MagicMock(return_value=0)
    monkeypatch.setattr(sort_once, "main", mock_sort_main)

    code = main.main(["--sort", "--notify"])
    assert code == 0
    mock_sort_main.assert_called_once_with(["--notify"])


def test_main_cli_dispatch_undo_with_notify(monkeypatch):
    """Verify main.py dispatches --undo --notify by forwarding arguments to sort_undo.main."""
    mock_undo_main = MagicMock(return_value=0)
    monkeypatch.setattr(sort_undo, "main", mock_undo_main)

    code = main.main(["--undo", "--notify"])
    assert code == 0
    mock_undo_main.assert_called_once_with(["--notify"])


def test_main_cli_dispatch_ribbon_summary_routing_bug(monkeypatch):
    """Verify main.py dispatches --ribbon-summary to instantiate SummaryRibbonPopup.
    
    Adversarial Challenge:
    When main.main(['--ribbon-summary', ...]) is called, it passes args[1:] to ribbon_popup.main.
    Because '--summary' is not forwarded, ribbon_popup.main sees args.summary=False.
    It therefore treats the call as an unconfigured file popup and opens RibbonPopup
    with the hardcoded sample 'Flanged Mace Iteration 1 STEP.step' instead of SummaryRibbonPopup!
    """
    mock_summary_instance = MagicMock()
    mock_summary_cls = MagicMock(return_value=mock_summary_instance)
    mock_ribbon_cls = MagicMock()

    monkeypatch.setattr(ribbon_popup, "SummaryRibbonPopup", mock_summary_cls)
    monkeypatch.setattr(ribbon_popup, "RibbonPopup", mock_ribbon_cls)

    code = main.main([
        "--ribbon-summary",
        "--title", "Downloads Organized",
        "--message", "Filed 4 files into categories.",
        "--accent", "#69E6AD",
        "--timeout", "5000",
    ])

    assert code == 0
    # Must instantiate SummaryRibbonPopup, NOT RibbonPopup!
    assert mock_summary_cls.called, (
        "BUG CONFIRMED: main.py --ribbon-summary failed to invoke SummaryRibbonPopup! "
        f"RibbonPopup called: {mock_ribbon_cls.called}. "
        "Root cause: main.py passes args[1:] to ribbon_popup.main without '--summary', "
        "causing ribbon_popup.main to treat args.summary as False and fall back to RibbonPopup."
    )
    assert not mock_ribbon_cls.called, "RibbonPopup should not be invoked when --ribbon-summary is requested"


# ============================================================================
# Section 2: System Tray Menu in tray.py
# ============================================================================

def test_tray_menu_hierarchy_and_default_action():
    """Verify SorterTray.build_menu() hierarchy:
    - Item 0 is 'Open Download Sorter' with default=True
    - Pause toggle does NOT have default=True (preventing accidental pausing)
    - Menu structure conforms to UI specifications
    """
    st = tray.SorterTray()
    menu = st.build_menu()
    items = list(menu.items)

    assert len(items) >= 15, f"Expected full tray menu, got {len(items)} items"

    # Item 0: Default double-click action
    item_0 = items[0]
    assert item_0.text == "Open Download Sorter"
    assert getattr(item_0, "default", False) is True, "Open Download Sorter must be the default menu item"

    # Pause action: must NOT be default
    pause_item = items[7]
    assert "pause" in str(pause_item.text).lower() or "resume" in str(pause_item.text).lower()
    assert getattr(pause_item, "default", False) is False, "Pause action must not have default=True"

    # Attribution item
    credit_item = items[16]
    assert credit_item.text == about.CREDIT
    assert getattr(credit_item, "enabled", True) is False, "Attribution item should be disabled"


def test_tray_pause_sorting_toggle_behavior(monkeypatch):
    """Verify toggling pause in SorterTray updates sorter.is_paused() and the menu text."""
    # Ensure clean starting state
    sorter.set_paused(False)
    assert not sorter.is_paused()

    st = tray.SorterTray()
    menu = st.build_menu()
    pause_item = menu.items[7]

    assert pause_item.text == "Pause sorting"

    # Toggle to paused
    st.on_toggle_pause()
    assert sorter.is_paused() is True
    assert pause_item.text == "Resume sorting"

    # Toggle back to active
    st.on_toggle_pause()
    assert sorter.is_paused() is False
    assert pause_item.text == "Pause sorting"


def test_tray_smart_second_instance_launch(monkeypatch):
    """Verify tray.main() executes spawn_window('--home') and exits 0 when lock is already held."""
    monkeypatch.setattr(watch, "acquire_single_instance_lock", lambda: None)
    mock_spawn = MagicMock()
    mock_tray_cls = MagicMock()
    monkeypatch.setattr(tray, "spawn_window", mock_spawn)
    monkeypatch.setattr(tray, "SorterTray", mock_tray_cls)

    code = tray.main()

    assert code == 0
    mock_spawn.assert_called_once_with("--home")
    mock_tray_cls.assert_not_called()


def test_tray_first_run_auto_welcome_wizard(monkeypatch):
    """Verify tray.main() triggers spawn_window('--welcome') on first run."""
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


# ============================================================================
# Section 3: Ribbon Notifications in ribbon_popup.py
# ============================================================================

def test_notify_summary_dev_and_frozen_spawn_flags(monkeypatch):
    """Verify notify_summary constructs windowless detached process calls in dev and frozen modes."""
    mock_popen = MagicMock()
    monkeypatch.setattr(subprocess, "Popen", mock_popen)

    # 1. Dev mode
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    ribbon_popup.notify_summary(
        title="Batch Completed",
        message="5 files sorted into category folders.",
        accent="#69E6AD",
        timeout_ms=3500,
    )
    assert mock_popen.called
    cmd_dev = mock_popen.call_args[0][0]
    assert "--summary" in cmd_dev
    assert "--title" in cmd_dev and cmd_dev[cmd_dev.index("--title") + 1] == "Batch Completed"
    assert "--message" in cmd_dev and cmd_dev[cmd_dev.index("--message") + 1] == "5 files sorted into category folders."
    assert "--accent" in cmd_dev and cmd_dev[cmd_dev.index("--accent") + 1] == "#69E6AD"
    assert "--timeout" in cmd_dev and cmd_dev[cmd_dev.index("--timeout") + 1] == "3500"
    if sys.platform == "win32":
        assert mock_popen.call_args[1].get("creationflags") == subprocess.CREATE_NO_WINDOW

    mock_popen.reset_mock()

    # 2. Frozen mode
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    ribbon_popup.notify_summary(
        title="Undo Batch",
        message="Restored 2 files.",
        accent="#FA7183",
        timeout_ms=4000,
    )
    assert mock_popen.called
    cmd_frozen = mock_popen.call_args[0][0]
    assert cmd_frozen[0] == sys.executable
    assert "--ribbon-summary" in cmd_frozen
    if sys.platform == "win32":
        assert mock_popen.call_args[1].get("creationflags") == subprocess.CREATE_NO_WINDOW


def test_notify_summary_stress_no_crashes():
    """Verify notify_summary safely handles custom titles, long messages, accent overrides, and timeout values without crashing."""
    test_cases = [
        ("Custom Title", "Normal message", "#69E6AD", 5000),
        ("Very Long Title " * 10, "Very Long Message " * 50, "#FA7183", 10000),
        ("Emoji Title 🚀", "Unicode message 🎉\nLine 2", "#61A5FF", 2500),
        ("", "", "#A855F7", 1000),
        ("Accent Override", "Testing gold accent", "#F5AD62", 3000),
        ("Negative Timeout", "Timeout -100", "#69E6AD", -100),
    ]

    for title, msg, accent, timeout in test_cases:
        # notify_summary spawns a background process and should never throw
        ribbon_popup.notify_summary(
            title=title,
            message=msg,
            accent=accent,
            timeout_ms=timeout,
        )


def test_summary_ribbon_popup_stress_parameters_no_crash():
    """Stress test SummaryRibbonPopup instantiation with extreme inputs:
    - Long titles (up to 200 chars)
    - Long messages (up to 1,000 chars)
    - Negative and large timeouts
    - Empty strings
    - Unicode characters and emojis
    - Accent overrides (#FA7183, #61A5FF, #D74A4A, #A855F7, #69E6AD)
    Ensure no crashes or Tkinter geometry exceptions occur.
    """
    test_cases = [
        ("Normal Title", "Regular message", "#69E6AD", 5000),
        ("A" * 200, "B" * 1000, "#FA7183", 3000),
        ("", "", "#61A5FF", 1000),
        ("🚀 Files Sorted!", "🎉 Folder: Downloads/3D Models\nLine 2 info", "#D74A4A", 4000),
        ("Negative Timeout", "Timeout test", "#A855F7", -500),
        ("Huge Timeout", "Timeout test", "#F5AD62", 100000),
    ]

    for title, message, accent, timeout in test_cases:
        popup = ribbon_popup.SummaryRibbonPopup(
            title=title,
            message=message,
            accent=accent,
            timeout_ms=timeout,
            enable_animation=False,
        )
        try:
            # Check title truncation
            if len(title) > 36:
                assert popup.title_lbl.cget("text").endswith("...")
            # Check message truncation
            if len(message) > 48:
                assert popup.msg_lbl.cget("text").endswith("…")
            # Check timeout clamping
            assert popup.timeout_ms >= 500
        finally:
            popup.close()
            ribbon_popup.release_slot(popup.slot, popup.pid)
