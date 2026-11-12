"""A test run leaves the Download Sorter the person is using, their Windows settings and their
Downloads folder alone.

Before tests/conftest.py gave each test a quit event of its own, a scratch area in the registry and
a home folder of its own, a full run closed the tray that was open, replaced the "start with
Windows" and right-click entries (they ended up pointing at the source folder), overwrote the
"Installed apps" entry with a fake 2.1.0 whose Uninstall pointed at a deleted temp folder, and
left empty XYZFiles and Finance\\Taxes folders in Downloads. These tests fail if that comes back.
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import pytest

import config_manager as cm
import installer_builder
import quit_signal
import sorter

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="named events and the registry are Windows features")

RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
UNINSTALL = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\DownloadSorter"
SORT_MENU = r"Software\Classes\Directory\shell\DownloadSorter"
UNDO_MENU = r"Software\Classes\Directory\Background\shell\DownloadSorterUndo"


# ---------------------------------------------------------------------------
# The quit signal
# ---------------------------------------------------------------------------

def test_the_real_quit_event_is_the_one_the_app_listens_on(own_quit_event):
    assert own_quit_event == "Local\\DownloadSorter_Quit"


def test_a_test_never_uses_the_real_quit_event(own_quit_event):
    assert quit_signal.EVENT_NAME != own_quit_event
    assert quit_signal.EVENT_NAME.startswith(own_quit_event + "_pytest_")


_names_seen: set[str] = set()


@pytest.mark.parametrize("run", [1, 2, 3])
def test_every_test_has_a_quit_event_of_its_own(run):
    assert quit_signal.EVENT_NAME not in _names_seen
    _names_seen.add(quit_signal.EVENT_NAME)


@windows_only
def test_a_quit_in_a_test_reaches_only_that_tests_own_listeners(own_quit_event):
    assert quit_signal.EVENT_NAME != own_quit_event, "stop here: this would close the app the person is running"
    assert quit_signal.request_quit() is False, "nobody listens on this test's own event yet"

    listener = quit_signal.QuitListener(reset=True)
    try:
        fired = threading.Event()
        listener.watch(fired.set, threading.Event())
        assert quit_signal.request_quit() is True
        assert fired.wait(5)
    finally:
        listener.close()


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------

@windows_only
def test_start_with_windows_and_the_right_click_menu_never_touch_the_real_entries(registry_scratch):
    watched = (UNINSTALL, SORT_MENU, SORT_MENU + r"\command", UNDO_MENU, UNDO_MENU + r"\command")
    real_before = {key: registry_scratch.real(key) for key in watched}
    real_run_before = (registry_scratch.real(RUN) or {}).get("DownloadSorter")

    assert cm.set_startup_enabled(True) is True
    assert cm.set_context_menu_enabled(True) is True

    # They happened, in this test's scratch area ...
    assert "--tray" in registry_scratch.scratch(RUN)["DownloadSorter"]
    assert registry_scratch.scratch(SORT_MENU)[""] == "Sort with DownloadSorter"
    assert "--undo" in registry_scratch.scratch(UNDO_MENU + r"\command")[""]
    # ... and the person's own entries are exactly as they were.
    assert (registry_scratch.real(RUN) or {}).get("DownloadSorter") == real_run_before
    assert {key: registry_scratch.real(key) for key in watched} == real_before


@windows_only
def test_turning_them_off_again_only_reaches_the_scratch_area(registry_scratch):
    assert cm.set_startup_enabled(True) and cm.set_context_menu_enabled(True)
    real_run_before = (registry_scratch.real(RUN) or {}).get("DownloadSorter")
    real_menu_before = registry_scratch.real(SORT_MENU)

    assert cm.set_startup_enabled(False) and cm.set_context_menu_enabled(False)

    assert cm.is_startup_enabled() is False and cm.is_context_menu_enabled() is False
    assert registry_scratch.scratch(SORT_MENU) is None
    assert (registry_scratch.real(RUN) or {}).get("DownloadSorter") == real_run_before
    assert registry_scratch.real(SORT_MENU) == real_menu_before


# Harmless stand-ins, one under each registry area the app writes. A subkey of Run or Uninstall
# does nothing, so even if the redirect were ever lost these could not start or list anything.
PROBES = [
    r"Software\DownloadSorter_Isolation_Probe\Child",
    r"Software\Classes\DownloadSorterIsolationProbe\Child",
    r"Software\Microsoft\Windows\CurrentVersion\Run\DownloadSorterIsolationProbe",
    r"Software\Microsoft\Windows\CurrentVersion\Uninstall\DownloadSorterIsolationProbe",
]


@windows_only
@pytest.mark.parametrize("path", PROBES)
def test_every_registry_area_the_app_writes_goes_to_the_scratch_area(registry_scratch, path):
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as key:
        winreg.SetValueEx(key, "Probe", 0, winreg.REG_SZ, "x")

    assert registry_scratch.scratch(path) == {"Probe": "x"}
    assert registry_scratch.real(path) is None, "the write reached the real registry"


@windows_only
def test_other_registry_reads_still_see_the_real_registry(registry_scratch):
    import winreg

    shell_folders = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, shell_folders) as key:  # not redirected
        assert winreg.QueryValueEx(key, "Desktop")[0]


# ---------------------------------------------------------------------------
# The Downloads folder
# ---------------------------------------------------------------------------

def test_a_test_has_a_home_folder_of_its_own(own_home_folder):
    assert Path.home() == own_home_folder
    assert os.path.expandvars("${USERPROFILE}") == str(own_home_folder)
    assert sorter.watch_root({}) == (own_home_folder / "Downloads").resolve()


@windows_only
def test_a_missing_downloads_folder_never_turns_into_the_real_one(tmp_path, registry_scratch):
    # watch_root falls back to the Downloads folder the registry knows when the one in the
    # settings does not exist (yet). In a test that was the Downloads folder of whoever ran it.
    missing = tmp_path / "not made yet"

    assert sorter.watch_root({"watch": {"folder": str(missing)}}) == missing.resolve()


def test_the_rule_folders_a_wizard_makes_stay_in_the_tests_own_downloads(own_home_folder):
    # What tests/test_adversarial_m3_wizard_invariants.py did: settings with rules but no
    # Downloads folder. ensure_tree made XYZFiles and Finance\Taxes in the real one, every run.
    config = {
        "keyword_rules": [{"name": "PreExistingTaxRule", "pattern": "w2_2025", "folder": "Finance\\Taxes"}],
        "extension_rules": [{"name": "XYZ Rule", "folder": "XYZFiles", "extensions": [".xyz"], "enabled": True}],
    }
    downloads = (own_home_folder / "Downloads").resolve()
    assert sorter.watch_root(config) == downloads, "stop here: this would create folders in the real Downloads"

    assert sorter.ensure_tree(config) == downloads

    assert (downloads / "XYZFiles").is_dir() and (downloads / "Finance" / "Taxes").is_dir()


# ---------------------------------------------------------------------------
# The setup program
# ---------------------------------------------------------------------------

def test_the_setup_program_can_skip_the_installed_apps_entry():
    source = installer_builder._generate_csharp_source("2.3.0", "a", "c", "https://example.invalid", "Download Sorter")

    assert '"/NO-REGISTRY"' in source and "registerUninstall" in source
    assert "PerformInstall(_targetDir, _chkDesktop.Checked, _chkStartMenu.Checked)" in source, "the window still registers"
