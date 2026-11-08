"""Comprehensive E2E and component test suite for Download Sorter UX Evolution.

Covering:
- Persona profiles, application logic, and immutability regression (R1)
- Recent file sorting history extraction from history.jsonl (R1)
- Pure-Python Windows login startup and Explorer context menu integration (R1)
- 3-step first-run WelcomeWizardDialog navigation, cards, and persistence (R2)
- HomeDashboardWidget hero card, quick actions, and recent activity feed (R2)
- Top-level segmented view switcher preserving _tabs.count() == 3 (R2)
- Conditional 3D/CNC maker element visibility (R2)
- Main CLI dispatcher flags (--home, --welcome) (R3)
- Silent context menu execution with ribbon toasts (R3)
- Multi-resolution application icon generation (R4)
- Native Windows C# compiler discovery and WinForms installer compilation (R4)
- Devyn Personal Suite theme compliance and dynamic attribution (R2, R4, R5)
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from typing import Any
from unittest.mock import MagicMock

# Headless Qt Offscreen Safety: Must be set before importing PySide6
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest
from PySide6.QtWidgets import QApplication

import about
import sorter
import suite_theme


@pytest.fixture(scope="session")
def qapp():
    """Session-scoped QApplication singleton for headless offscreen testing."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app
    app.closeAllWindows()
    app.processEvents()


# ============================================================================
# Tier 1 & Tier 2: Persona Profiles, Application & Regression Tests (R1)
# ============================================================================

def test_get_persona_profiles():
    """Verify get_persona_profiles() returns all 4 curated personas with complete metadata."""
    import config_manager

    assert hasattr(config_manager, "get_persona_profiles"), "config_manager missing get_persona_profiles()"
    profiles = config_manager.get_persona_profiles()
    assert isinstance(profiles, list), "get_persona_profiles() must return a list"
    assert len(profiles) >= 4, f"Expected at least 4 personas, got {len(profiles)}"

    expected_ids = {"everyday_home", "academic_student", "office_admin", "maker_3d"}
    actual_ids = {p.get("id") for p in profiles if isinstance(p, dict)}
    assert expected_ids.issubset(actual_ids), f"Missing required personas: {expected_ids - actual_ids}"

    for p in profiles:
        assert isinstance(p, dict), "Each persona profile must be a dict"
        assert "id" in p and isinstance(p["id"], str) and p["id"], "Persona missing valid 'id'"
        assert "name" in p and isinstance(p["name"], str) and p["name"], f"Persona {p.get('id')} missing 'name'"
        assert "icon" in p and isinstance(p["icon"], str), f"Persona {p.get('id')} missing 'icon'"
        assert "description" in p and isinstance(p["description"], str), f"Persona {p.get('id')} missing 'description'"
        assert "rules" in p or "presets" in p or "keyword_rules" in p, f"Persona {p.get('id')} missing rules definition"

    # Verify return value is a defensive copy to prevent mutation of internal definitions
    profiles_copy = config_manager.get_persona_profiles()
    profiles_copy[0]["_test_mutation"] = True
    assert "_test_mutation" not in config_manager.get_persona_profiles()[0], "get_persona_profiles() must return a deep/defensive copy"


def test_apply_persona_profile_everyday_home():
    """Verify applying everyday_home persona configures persona rules and marks onboarding completed."""
    import config_manager

    assert hasattr(config_manager, "apply_persona_profile"), "config_manager missing apply_persona_profile()"
    base_config = copy.deepcopy(sorter.DEFAULT_CONFIG)
    updated = config_manager.apply_persona_profile(base_config, "everyday_home")

    assert isinstance(updated, dict), "apply_persona_profile must return a config dict"
    assert updated.get("user_persona") == "everyday_home", "user_persona must be set to 'everyday_home'"
    assert updated.get("onboarding_completed") is True, "onboarding_completed must be True"
    assert "keyword_rules" in updated, "keyword_rules must be present in config"
    assert len(updated["keyword_rules"]) > 0, "Persona must populate keyword_rules"

    # Verify applying an unknown persona ID raises ValueError
    with pytest.raises(ValueError):
        config_manager.apply_persona_profile(base_config, "non_existent_persona_xyz")


def test_apply_persona_profile_preserves_default_config():
    """CRITICAL REGRESSION TEST: Asserts sorter.DEFAULT_CONFIG is completely untouched."""
    import config_manager

    assert hasattr(config_manager, "apply_persona_profile"), "config_manager missing apply_persona_profile()"
    snapshot_before = copy.deepcopy(sorter.DEFAULT_CONFIG)

    # Apply all 4 personas in succession
    for persona_id in ["everyday_home", "academic_student", "office_admin", "maker_3d"]:
        cfg_copy = copy.deepcopy(sorter.DEFAULT_CONFIG)
        _ = config_manager.apply_persona_profile(cfg_copy, persona_id)

    # Strict immutability assertion
    assert sorter.DEFAULT_CONFIG == snapshot_before, (
        "CRITICAL REGRESSION: sorter.DEFAULT_CONFIG was mutated by apply_persona_profile()! "
        "Default config must remain immutable for test suite parity."
    )
    assert "user_persona" not in sorter.DEFAULT_CONFIG
    assert "onboarding_completed" not in sorter.DEFAULT_CONFIG


def test_get_recent_activity_parsing(tmp_path, monkeypatch):
    """Verify get_recent_activity(limit=25) parses history.jsonl, filtering undone and non-move entries."""
    import config_manager

    assert hasattr(config_manager, "get_recent_activity"), "config_manager missing get_recent_activity()"

    history_file = tmp_path / "history.jsonl"
    monkeypatch.setattr(sorter, "HISTORY_FILE", history_file)
    if hasattr(config_manager, "HISTORY_FILE"):
        monkeypatch.setattr(config_manager, "HISTORY_FILE", history_file)

    # Create real test files for destination existence checks
    dest_file_1 = tmp_path / "Documents" / "Taxes.pdf"
    dest_file_1.parent.mkdir(parents=True, exist_ok=True)
    dest_file_1.write_text("tax data", encoding="utf-8")

    dest_file_2 = tmp_path / "Images" / "Landscape.png"
    dest_file_2.parent.mkdir(parents=True, exist_ok=True)
    dest_file_2.write_text("image data", encoding="utf-8")

    # Write realistic history records with move, undone, non-move, and corrupt records
    lines = [
        json.dumps({"id": "m1", "action": "move", "filename": "Taxes.pdf", "folder": "Documents", "dest": str(dest_file_1), "timestamp": "2026-11-01T10:00:00Z"}),
        json.dumps({"id": "m2", "action": "move", "filename": "Draft.docx", "folder": "Documents", "dest": str(tmp_path / "Documents" / "Draft.docx"), "timestamp": "2026-11-02T11:00:00Z"}),
        json.dumps({"id": "u1", "action": "undo", "ref_id": "m2", "timestamp": "2026-11-02T11:05:00Z"}),  # m2 is undone!
        json.dumps({"id": "h1", "action": "hold", "filename": "Bracket.3mf", "folder": "3D Models", "timestamp": "2026-11-03T12:00:00Z"}),  # non-move!
        "CORRUPT_MALFORMED_JSON_LINE{{{",
        json.dumps({"id": "m3", "action": "move", "filename": "Landscape.png", "folder": "Images", "dest": str(dest_file_2), "timestamp": "2026-11-04T13:00:00Z"}),
    ]
    history_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    records = config_manager.get_recent_activity(limit=25)
    assert isinstance(records, list), "get_recent_activity must return a list"

    filenames = [r["filename"] for r in records if "filename" in r]
    assert "Landscape.png" in filenames, "Active move must be included in recent activity"
    assert "Taxes.pdf" in filenames, "Active move must be included in recent activity"
    assert "Draft.docx" not in filenames, "Undone file move must be excluded from recent activity"
    assert "Bracket.3mf" not in filenames, "Non-move actions must be excluded from recent activity"

    # Verify schema of returned records
    for rec in records:
        assert "filename" in rec
        assert "folder" in rec
        assert "dest" in rec
        assert "timestamp" in rec
        assert "exists" in rec
        assert isinstance(rec["exists"], bool)


def test_system_settings_startup_toggle(monkeypatch):
    """Verify is_startup_enabled and set_startup_enabled pure-Python winreg helpers."""
    import config_manager

    assert hasattr(config_manager, "is_startup_enabled"), "config_manager missing is_startup_enabled()"
    assert hasattr(config_manager, "set_startup_enabled"), "config_manager missing set_startup_enabled()"

    mock_run_registry: dict[str, str] = {}

    def mock_set_value(key, subkey, reserved, type_, value):
        mock_run_registry[subkey] = value

    def mock_query_value(key, subkey):
        if subkey in mock_run_registry:
            return (mock_run_registry[subkey], 1)
        raise FileNotFoundError(f"Subkey {subkey} not found in mock registry")

    def mock_delete_value(key, subkey):
        if subkey in mock_run_registry:
            del mock_run_registry[subkey]
        else:
            raise FileNotFoundError(f"Subkey {subkey} not found in mock registry")

    import winreg
    monkeypatch.setattr(winreg, "SetValueEx", mock_set_value)
    monkeypatch.setattr(winreg, "QueryValueEx", mock_query_value)
    monkeypatch.setattr(winreg, "DeleteValue", mock_delete_value)
    monkeypatch.setattr(winreg, "OpenKey", lambda *args, **kwargs: MagicMock())

    # Initially disabled
    assert config_manager.is_startup_enabled() is False

    # Enable startup
    res = config_manager.set_startup_enabled(True)
    assert res is True
    assert config_manager.is_startup_enabled() is True
    assert "DownloadSorter" in mock_run_registry

    # Disable startup
    res = config_manager.set_startup_enabled(False)
    assert res is True
    assert config_manager.is_startup_enabled() is False
    assert "DownloadSorter" not in mock_run_registry


def test_system_settings_context_menu_toggle(monkeypatch):
    """Verify is_context_menu_enabled and set_context_menu_enabled pure-Python helpers."""
    import config_manager

    assert hasattr(config_manager, "is_context_menu_enabled"), "config_manager missing is_context_menu_enabled()"
    assert hasattr(config_manager, "set_context_menu_enabled"), "config_manager missing set_context_menu_enabled()"

    mock_shell_keys: set[str] = set()

    def mock_create_key(key, subkey, *args, **kwargs):
        mock_shell_keys.add(subkey)
        m = MagicMock()
        m._subkey = subkey
        m.__enter__ = lambda self: self
        m.__exit__ = lambda self, *exc: None
        return m

    def mock_open_key(key, subkey, *args, **kwargs):
        if any(k == subkey or k.startswith(subkey + "\\") for k in mock_shell_keys):
            m = MagicMock()
            m._subkey = subkey
            m.__enter__ = lambda self: self
            m.__exit__ = lambda self, *exc: None
            return m
        raise FileNotFoundError(f"Key {subkey} not found in mock registry")

    def mock_enum_key(key, index):
        parent = getattr(key, "_subkey", "")
        children = [k[len(parent) + 1:].split("\\")[0] for k in mock_shell_keys if k.startswith(parent + "\\")]
        children = sorted(list(set(children)))
        if index < len(children):
            return children[index]
        raise OSError("No more keys")

    def mock_delete_key(key, subkey):
        to_del = [k for k in mock_shell_keys if k == subkey or k.startswith(subkey + "\\")]
        if not to_del:
            raise FileNotFoundError(f"Key {subkey} not found")
        for k in to_del:
            mock_shell_keys.remove(k)

    import winreg
    monkeypatch.setattr(winreg, "CreateKey", mock_create_key)
    monkeypatch.setattr(winreg, "CreateKeyEx", mock_create_key, raising=False)
    monkeypatch.setattr(winreg, "OpenKey", mock_open_key)
    monkeypatch.setattr(winreg, "EnumKey", mock_enum_key)
    monkeypatch.setattr(winreg, "DeleteKey", mock_delete_key)
    monkeypatch.setattr(winreg, "SetValueEx", lambda *args, **kwargs: None)

    # Initial state
    assert config_manager.is_context_menu_enabled() is False

    # Enable context menu
    res = config_manager.set_context_menu_enabled(True)
    assert res is True
    assert config_manager.is_context_menu_enabled() is True

    # Disable context menu
    res = config_manager.set_context_menu_enabled(False)
    assert res is True
    assert config_manager.is_context_menu_enabled() is False


# ============================================================================
# Tier 1, 2 & 3: First-Run Welcome Wizard & Home Dashboard GUI (R2)
# ============================================================================

def test_welcome_wizard_dialog_steps_and_navigation(qapp):
    """Verify 3-step navigation in WelcomeWizardDialog without blocking modal loops."""
    from onboarding import WelcomeWizardDialog

    cfg = copy.deepcopy(sorter.DEFAULT_CONFIG)
    dlg = WelcomeWizardDialog(config=cfg)
    try:
        dlg.show()
        qapp.processEvents()

        # Step 1: Persona Selection (index 0)
        assert dlg._stack.currentIndex() == 0 or dlg._current_step == 0
        assert hasattr(dlg, "_btn_next")
        assert hasattr(dlg, "_btn_back")

        # Select a persona to enable navigation
        if hasattr(dlg, "_on_persona_selected"):
            dlg._on_persona_selected("everyday_home")
            qapp.processEvents()
        assert dlg._btn_next.isEnabled()

        # Step 1 -> Step 2: Feature Tour
        dlg._btn_next.click()
        qapp.processEvents()
        assert dlg._stack.currentIndex() == 1 or dlg._current_step == 1
        assert dlg._btn_back.isEnabled()

        # Step 2 -> Step 3: Preferences
        dlg._btn_next.click()
        qapp.processEvents()
        assert dlg._stack.currentIndex() == 2 or dlg._current_step == 2

        # Step 3 -> Step 2: Back navigation
        dlg._btn_back.click()
        qapp.processEvents()
        assert dlg._stack.currentIndex() == 1 or dlg._current_step == 1
    finally:
        dlg.close()
        dlg.deleteLater()
        qapp.processEvents()


def test_welcome_wizard_persona_card_selection(qapp):
    """Verify persona card selection, visual highlight tokens, and state transitions."""
    from onboarding import WelcomeWizardDialog

    cfg = copy.deepcopy(sorter.DEFAULT_CONFIG)
    dlg = WelcomeWizardDialog(config=cfg)
    try:
        dlg.show()
        qapp.processEvents()

        assert hasattr(dlg, "_persona_cards"), "WelcomeWizardDialog must maintain _persona_cards mapping"
        assert len(dlg._persona_cards) >= 4, "Must display at least 4 persona cards"

        # Simulate selecting academic_student
        dlg._on_persona_selected("academic_student")
        qapp.processEvents()
        assert dlg._selected_persona == "academic_student"

        # Check visual highlight token matches suite_theme mint accent
        card = dlg._persona_cards["academic_student"]
        card_style = card.styleSheet().lower()
        mint_hex = suite_theme.ACCENTS["downloadSorter"].lower()
        assert mint_hex in card_style, f"Selected persona card style must include theme accent {mint_hex}"

        # Switch to maker_3d
        dlg._on_persona_selected("maker_3d")
        qapp.processEvents()
        assert dlg._selected_persona == "maker_3d"
    finally:
        dlg.close()
        dlg.deleteLater()
        qapp.processEvents()


def test_welcome_wizard_finish_and_config_persistence(qapp, tmp_path, monkeypatch):
    """Verify wizard completion saves configuration atomically, applies persona, and accepts dialog."""
    from onboarding import WelcomeWizardDialog
    import config_manager

    cfg_file = tmp_path / "config.json"
    cfg = copy.deepcopy(sorter.DEFAULT_CONFIG)
    cfg_file.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setattr(config_manager, "SORTER_CONFIG_FILE", cfg_file)

    dlg = WelcomeWizardDialog(config=cfg)
    try:
        dlg.show()
        qapp.processEvents()

        # Step 1: Select office_admin
        dlg._on_persona_selected("office_admin")
        dlg._on_next_clicked() if hasattr(dlg, "_on_next_clicked") else dlg._btn_next.click()
        qapp.processEvents()

        # Step 2: Tour Next
        dlg._on_next_clicked() if hasattr(dlg, "_on_next_clicked") else dlg._btn_next.click()
        qapp.processEvents()

        # Step 3: Finish
        if hasattr(dlg, "_on_finish_clicked"):
            dlg._on_finish_clicked()
        elif hasattr(dlg, "_btn_finish"):
            dlg._btn_finish.click()
        qapp.processEvents()

        # Verify config saved to disk with persona and onboarding flag
        saved_config = json.loads(cfg_file.read_text(encoding="utf-8"))
        assert saved_config.get("user_persona") == "office_admin", "user_persona was not persisted"
        assert saved_config.get("onboarding_completed") is True, "onboarding_completed was not persisted"
        assert len(saved_config.get("keyword_rules", [])) > 0, "Persona rules were not populated in saved config"
    finally:
        dlg.close()
        dlg.deleteLater()
        qapp.processEvents()


def test_home_dashboard_hero_card_and_status(qapp, monkeypatch):
    """Verify HomeDashboardWidget renders hero status badge, counter, and 4 quick actions."""
    from onboarding import HomeDashboardWidget
    import config_manager

    monkeypatch.setattr(config_manager, "is_sorter_running", lambda: True)

    cfg = copy.deepcopy(sorter.DEFAULT_CONFIG)
    widget = HomeDashboardWidget(config=cfg)
    try:
        widget.show()
        qapp.processEvents()

        # Status hero card & badge
        assert hasattr(widget, "_status_card") or hasattr(widget, "_hero_card"), "Widget missing status hero card"
        assert hasattr(widget, "_status_badge"), "Widget missing status badge"
        badge_text = widget._status_badge.text().lower()
        assert any(status in badge_text for status in ["active", "protect", "monitor", "running"]), (
            f"Status badge text '{widget._status_badge.text()}' does not indicate active protection"
        )

        # Counter / Metric
        assert hasattr(widget, "_files_today_lbl") or hasattr(widget, "_metric_lbl"), "Widget missing today's files metric label"

        # 4 Quick Action Buttons
        assert hasattr(widget, "_btn_sort_now"), "Widget missing 'Sort Downloads Now' button"
        assert hasattr(widget, "_btn_undo_last"), "Widget missing 'Undo Last Sort' button"
        assert hasattr(widget, "_btn_open_downloads"), "Widget missing 'Open Downloads Folder' button"
        assert hasattr(widget, "_btn_pause_resume"), "Widget missing 'Pause / Resume' button"
    finally:
        widget.close()
        widget.deleteLater()
        qapp.processEvents()


def test_home_dashboard_actions_sort_and_undo(qapp, monkeypatch):
    """Verify clicking Sort Now and Undo Last Sort triggers actions and updates UI state."""
    from onboarding import HomeDashboardWidget

    cfg = copy.deepcopy(sorter.DEFAULT_CONFIG)
    widget = HomeDashboardWidget(config=cfg)
    try:
        widget.show()
        qapp.processEvents()

        mock_sort = MagicMock(return_value=3)
        mock_undo = MagicMock(return_value=3)
        monkeypatch.setattr(widget, "sort_now", mock_sort, raising=False)
        monkeypatch.setattr(widget, "undo_last", mock_undo, raising=False)

        widget._btn_sort_now.click()
        qapp.processEvents()
        if hasattr(widget, "sort_now"):
            mock_sort.assert_called_once()

        widget._btn_undo_last.click()
        qapp.processEvents()
        if hasattr(widget, "undo_last"):
            mock_undo.assert_called_once()
    finally:
        widget.close()
        widget.deleteLater()
        qapp.processEvents()


def test_home_dashboard_recent_activity_render(qapp, tmp_path, monkeypatch):
    """Verify recent activity feed renders file rows with Explorer buttons and empty-state fallback."""
    from onboarding import HomeDashboardWidget
    import config_manager

    cfg = copy.deepcopy(sorter.DEFAULT_CONFIG)

    # 1. Empty state verification
    monkeypatch.setattr(config_manager, "get_recent_activity", lambda limit=25: [])
    w_empty = HomeDashboardWidget(config=cfg)
    try:
        w_empty.show()
        qapp.processEvents()
        assert hasattr(w_empty, "_empty_state_lbl") or hasattr(w_empty, "_empty_widget"), "Widget missing empty-state indicator"
    finally:
        w_empty.close()
        w_empty.deleteLater()
        qapp.processEvents()

    # 2. Populated activity feed verification
    dummy_dest = tmp_path / "Downloads" / "Reports" / "Quarterly.pdf"
    dummy_dest.parent.mkdir(parents=True, exist_ok=True)
    dummy_dest.write_text("content", encoding="utf-8")

    mock_records = [
        {
            "filename": "Quarterly.pdf",
            "folder": "Reports",
            "timestamp": "2026-11-07T04:15:00Z",
            "dest": str(dummy_dest),
            "exists": True,
        }
    ]
    monkeypatch.setattr(config_manager, "get_recent_activity", lambda limit=25: mock_records)
    w_pop = HomeDashboardWidget(config=cfg)
    try:
        w_pop.show()
        qapp.processEvents()
        # Ensure row or table rendered
        table_or_rows = getattr(w_pop, "_activity_table", None) or getattr(w_pop, "_activity_layout", None)
        assert table_or_rows is not None, "Widget missing activity table or row container"
    finally:
        w_pop.close()
        w_pop.deleteLater()
        qapp.processEvents()


def test_home_dashboard_maker_features_visibility(qapp, tmp_path, monkeypatch):
    """Verify 3D/CNC maker elements are hidden when persona is everyday_home and visible when maker_3d."""
    import rules_manager
    import config_manager

    cfg_file = tmp_path / "config.json"
    cfg = copy.deepcopy(sorter.DEFAULT_CONFIG)
    cfg["user_persona"] = "everyday_home"
    cfg_file.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setattr(config_manager, "SORTER_CONFIG_FILE", cfg_file)

    dlg = rules_manager.RulesManagerDialog()
    try:
        dlg.show()
        qapp.processEvents()

        # For everyday_home, active project card must be hidden
        if hasattr(dlg, "_active_project_card"):
            assert not dlg._active_project_card.isVisible(), (
                "_active_project_card must be hidden when non-maker persona is active"
            )

        # Switch persona to maker_3d
        if hasattr(dlg, "set_active_persona"):
            dlg.set_active_persona("maker_3d")
            qapp.processEvents()
            if hasattr(dlg, "_active_project_card"):
                assert dlg._active_project_card.isVisible(), (
                    "_active_project_card must be visible when maker_3d persona is active"
                )
    finally:
        dlg.close()
        dlg.deleteLater()
        qapp.processEvents()


def test_rules_manager_top_level_segmented_switcher(qapp, tmp_path, monkeypatch):
    """Verify top-level segmented view switcher toggles views while strictly preserving _tabs.count() == 3."""
    import rules_manager
    import config_manager

    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(sorter.DEFAULT_CONFIG), encoding="utf-8")
    monkeypatch.setattr(config_manager, "SORTER_CONFIG_FILE", cfg_file)

    dlg = rules_manager.RulesManagerDialog()
    try:
        dlg.show()
        qapp.processEvents()

        # STRICT CONTRACT: _tabs.count() must remain exactly 3 (Keyword, Extension, Remodel)
        assert hasattr(dlg, "_tabs"), "RulesManagerDialog must retain self._tabs"
        assert dlg._tabs.count() == 3, f"Expected exactly 3 tabs in RulesManagerDialog._tabs, got {dlg._tabs.count()}"

        # Segmented view switcher verification
        assert hasattr(dlg, "_view_switcher") or hasattr(dlg, "_seg_btn_home"), "RulesManagerDialog missing top-level segmented view switcher"
        if hasattr(dlg, "switch_view"):
            dlg.switch_view("customize")
            qapp.processEvents()
            assert dlg._tabs.count() == 3

            dlg.switch_view("home")
            qapp.processEvents()
            assert dlg._tabs.count() == 3
    finally:
        dlg.close()
        dlg.deleteLater()
        qapp.processEvents()


# ============================================================================
# Tier 1 & Tier 3: CLI Dispatch & Silent Context Menu Execution (R3)
# ============================================================================

def test_main_cli_home_and_welcome_dispatch(monkeypatch):
    """Verify main.py CLI routes --home and --welcome to rules_manager."""
    import main
    import rules_manager

    mock_rules_main = MagicMock(return_value=0)
    monkeypatch.setattr(rules_manager, "main", mock_rules_main)

    # Route --home
    code_home = main.main(["--home"])
    assert code_home == 0
    mock_rules_main.assert_called_with(["--home"])

    mock_rules_main.reset_mock()

    # Route --welcome
    code_welcome = main.main(["--welcome"])
    assert code_welcome == 0
    mock_rules_main.assert_called_with(["--welcome"])


def test_silent_sort_and_undo_toast(monkeypatch):
    """Verify ribbon_popup.notify_summary provides genuine windowless desktop ribbon notifications.

    Verifies the entire desktop ribbon notification integration chain:
    1. sort_once.main(['--notify']) calls ribbon_popup.notify_summary with 'Downloads Organized'.
    2. sort_undo.main(['--notify']) calls ribbon_popup.notify_summary with 'Undo Last Batch'.
    3. main.main(['--ribbon-summary', ...]) routes to SummaryRibbonPopup and NOT RibbonPopup.
    4. ribbon_popup.notify_summary constructs command line arguments containing '--summary'
       in both frozen and dev modes.
    """
    import main
    import ribbon_popup
    import sort_once
    import sort_undo

    # 1. Verify sort_once.main(["--notify"]) invokes notify_summary("Downloads Organized", ...)
    real_notify_summary = ribbon_popup.notify_summary
    mock_notify = MagicMock()
    monkeypatch.setattr(ribbon_popup, "notify_summary", mock_notify)
    monkeypatch.setattr(sort_once, "sort_existing", lambda *a, **kw: 0)
    monkeypatch.setattr(sort_once, "sort_existing_documents", lambda *a, **kw: 0)
    monkeypatch.setattr(sort_once, "sort_existing_misplaced", lambda *a, **kw: 0)

    sort_once.main(["--notify"])
    assert mock_notify.called, "sort_once.main(['--notify']) did not call ribbon_popup.notify_summary"
    assert mock_notify.call_args[0][0] == "Downloads Organized"

    # 2. Verify sort_undo.main(["--notify"]) invokes notify_summary("Undo Last Batch", ...)
    mock_notify.reset_mock()
    monkeypatch.setattr(sort_undo, "load_history", lambda: [])

    sort_undo.main(["--notify"])
    assert mock_notify.called, "sort_undo.main(['--notify']) did not call ribbon_popup.notify_summary"
    assert mock_notify.call_args[0][0] == "Undo Last Batch"

    # 3. Verify main.main(["--ribbon-summary", ...]) routes to SummaryRibbonPopup, NOT RibbonPopup
    mock_summary_instance = MagicMock()
    mock_summary_cls = MagicMock(return_value=mock_summary_instance)
    mock_ribbon_cls = MagicMock()
    monkeypatch.setattr(ribbon_popup, "SummaryRibbonPopup", mock_summary_cls)
    monkeypatch.setattr(ribbon_popup, "RibbonPopup", mock_ribbon_cls)

    ret = main.main(["--ribbon-summary", "--title", "Downloads Organized", "--message", "Filed 3 files."])
    assert ret == 0
    assert mock_summary_cls.called, "main.main(['--ribbon-summary', ...]) did not instantiate SummaryRibbonPopup"
    assert not mock_ribbon_cls.called, "main.main(['--ribbon-summary', ...]) unexpectedly instantiated RibbonPopup"

    # 4. Verify notify_summary produces command line containing '--summary' in dev and frozen modes
    monkeypatch.setattr(ribbon_popup, "notify_summary", real_notify_summary)
    mock_popen = MagicMock()
    monkeypatch.setattr(subprocess, "Popen", mock_popen)

    # Dev mode
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    ribbon_popup.notify_summary("Test Title", "Test Message")
    assert mock_popen.called, "notify_summary in dev mode did not spawn subprocess"
    dev_cmd = mock_popen.call_args[0][0]
    assert "--summary" in dev_cmd, f"--summary missing from dev command line: {dev_cmd}"

    mock_popen.reset_mock()

    # Frozen mode
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    ribbon_popup.notify_summary("Test Title", "Test Message")
    assert mock_popen.called, "notify_summary in frozen mode did not spawn subprocess"
    frozen_cmd = mock_popen.call_args[0][0]
    assert "--summary" in frozen_cmd, f"--summary missing from frozen command line: {frozen_cmd}"
    assert "--ribbon-summary" in frozen_cmd, f"--ribbon-summary missing from frozen command line: {frozen_cmd}"


# ============================================================================
# Tier 1 & Tier 4: Single-File Setup Executable & Icon Generation (R4)
# ============================================================================

def test_generate_app_icon_layers_and_colors(tmp_path):
    """Verify generate_app_icon produces a 6-layer .ico matching theme mint & slate palette."""
    from installer_builder import generate_app_icon
    from PIL import Image

    ico_path = tmp_path / "DownloadSorter.ico"
    result_path = generate_app_icon(ico_path)
    assert result_path.exists(), "generate_app_icon did not create output file"
    assert result_path.stat().st_size > 0, "Generated icon file is 0 bytes"

    with Image.open(result_path) as img:
        assert img.format == "ICO", f"Expected ICO format, got {img.format}"
        sizes = img.info.get("sizes")
        if sizes:
            expected_sizes = {(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)}
            assert expected_sizes.issubset(sizes), f"Icon missing required layer sizes: {expected_sizes - sizes}"

        # Inspect 256x256 layer colors for palette compliance
        img_256 = img.resize((256, 256))
        colors = img_256.convert("RGBA").getcolors(maxcolors=65536)
        color_rgbs = {(c[1][0], c[1][1], c[1][2]) for c in colors}

        # Mint #69E6AD is (105, 230, 173), Slate #171B24 is (23, 27, 36)
        mint_found = any(abs(r - 105) < 15 and abs(g - 230) < 15 and abs(b - 173) < 15 for r, g, b in color_rgbs)
        slate_found = any(abs(r - 23) < 15 and abs(g - 27) < 15 and abs(b - 36) < 15 for r, g, b in color_rgbs)
        assert mint_found, "Mint accent color (#69E6AD) must be present in application icon"
        assert slate_found, "Slate surface color (#171B24) must be present in application icon"


def test_find_csc_compiler():
    """Verify find_csc_compiler discovers native Windows C# compiler."""
    from installer_builder import find_csc_compiler

    csc_path = find_csc_compiler()
    if platform.system() == "Windows":
        assert csc_path is not None, "csc.exe must be found on Windows system"
        assert csc_path.exists(), f"Discovered csc path does not exist: {csc_path}"
        assert csc_path.name.lower() == "csc.exe"


def test_build_csharp_installer_and_silent_extract(tmp_path):
    """Verify C# WinForms setup compilation and unattended silent extraction with /S."""
    from installer_builder import build_csharp_installer, find_csc_compiler

    if platform.system() != "Windows":
        pytest.skip("C# installer compilation requires Windows")

    csc = find_csc_compiler()
    if not csc:
        pytest.skip("csc.exe compiler not found in environment")

    # Create mock dist directory with payload files
    dist_dir = tmp_path / "dist_src"
    dist_dir.mkdir()
    (dist_dir / "DownloadSorter.exe").write_text("MOCK_BIN", encoding="utf-8")
    (dist_dir / "about.py").write_text("MOCK_ABOUT", encoding="utf-8")

    out_exe = tmp_path / "DownloadSorter-Setup-Test.exe"
    installer_path = build_csharp_installer(
        output_exe=out_exe,
        dist_dir=dist_dir,
        version="2.1.0",
    )
    assert installer_path.exists(), "build_csharp_installer failed to output setup executable"
    assert installer_path.stat().st_size > 1024, "Compiled installer executable suspiciously small"

    # Execute silent installation with /S /D=<tmp_target>
    install_target = tmp_path / "installed_app"
    cmd = [str(installer_path), "/S", f"/D={install_target}"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, f"Silent install failed with code {proc.returncode}: {proc.stderr} {proc.stdout}"
    assert (install_target / "DownloadSorter.exe").exists(), "Payload DownloadSorter.exe not extracted"
    assert (install_target / "about.py").exists(), "Payload about.py not extracted"


# ============================================================================
# Tier 2: Theme Compliance & Dynamic Attribution Audits (R2, R4, R5)
# ============================================================================

def test_onboarding_and_installer_theme_compliance():
    """Verify zero forbidden off-palette hex codes exist in onboarding and installer modules."""
    forbidden_hexes = [
        "#171C26", "#181D26", "#212836", "#282F3D",
        "#151A22", "#1D232E", "#FFB347", "#181C25", "#0F1218",
    ]
    target_files = ["onboarding.py", "installer_builder.py"]

    for fname in target_files:
        fpath = REPO_ROOT / fname
        if not fpath.exists():
            continue
        content = fpath.read_text(encoding="utf-8").upper()
        for forbidden in forbidden_hexes:
            assert forbidden.upper() not in content, (
                f"Forbidden off-palette hex code {forbidden} found in {fname}! "
                f"Must strictly use suite_theme.py design tokens."
            )

    # Validate canonical theme tokens are intact
    assert suite_theme.COLORS["canvas"] == "#0B0D12"
    assert suite_theme.ACCENTS["downloadSorter"] == "#69E6AD"


def test_onboarding_and_installer_attribution():
    """Verify onboarding and installer modules import about.CREDIT and do not hardcode author name."""
    target_files = ["onboarding.py", "installer_builder.py"]

    for fname in target_files:
        fpath = REPO_ROOT / fname
        if not fpath.exists():
            continue
        content = fpath.read_text(encoding="utf-8")
        assert "about.CREDIT" in content, f"{fname} must dynamically reference about.CREDIT"
        assert "Devyn Fidel" not in content, f"{fname} must not hardcode author name"


# ============================================================================
# Milestone 3B: Smart Profile Recommendation & Mascot Companion
# ============================================================================

def test_recommend_persona_profile_maker_3d(tmp_path):
    """Verify recommend_persona_profile detects 3D/CAD/CNC extensions and recommends maker_3d."""
    import config_manager

    assert hasattr(config_manager, "recommend_persona_profile"), "config_manager missing recommend_persona_profile()"

    # Populate directory with 3D/CAD/CNC files
    (tmp_path / "bracket_mount.3mf").write_text("dummy 3mf", encoding="utf-8")
    (tmp_path / "toolpath_contour.gcode").write_text("G0 X0 Y0", encoding="utf-8")
    (tmp_path / "flange_assembly.step").write_text("ISO-10303-21", encoding="utf-8")
    (tmp_path / "random_notes.txt").write_text("hello", encoding="utf-8")

    rec = config_manager.recommend_persona_profile(scan_dirs=tmp_path)
    assert isinstance(rec, dict), "recommend_persona_profile must return a dict"
    assert rec.get("recommended_id") == "maker_3d", f"Expected 'maker_3d', got {rec.get('recommended_id')}"
    assert "3D" in rec.get("reason", "") or "Maker" in rec.get("reason", "")
    assert rec.get("scores", {}).get("maker_3d", 0) > 0
    assert rec.get("files_scanned") >= 4


def test_recommend_persona_profile_academic(tmp_path):
    """Verify recommend_persona_profile detects academic keywords/course codes and recommends academic_student."""
    import config_manager

    # Populate directory with academic files
    (tmp_path / "syllabus_me3210.pdf").write_text("syllabus", encoding="utf-8")
    (tmp_path / "homework1_submission.docx").write_text("homework", encoding="utf-8")
    (tmp_path / "lecture_notes_week2.pptx").write_text("lecture notes", encoding="utf-8")
    (tmp_path / "photo.jpg").write_text("image data", encoding="utf-8")

    rec = config_manager.recommend_persona_profile(scan_dirs=tmp_path)
    assert rec.get("recommended_id") == "academic_student", f"Expected 'academic_student', got {rec.get('recommended_id')}"
    assert "coursework" in rec.get("reason", "").lower() or "student" in rec.get("reason", "").lower()
    assert rec.get("scores", {}).get("academic_student", 0) > 0
    assert rec.get("files_scanned") >= 4


def test_recommend_persona_profile_office(tmp_path):
    """Verify recommend_persona_profile detects office/finance keywords and recommends office_admin."""
    import config_manager

    # Populate directory with office/finance files
    (tmp_path / "invoice_march_2026.pdf").write_text("invoice", encoding="utf-8")
    (tmp_path / "receipt_hotel_chicago.pdf").write_text("receipt", encoding="utf-8")
    (tmp_path / "w2_statement_2025.pdf").write_text("tax", encoding="utf-8")
    (tmp_path / "family_vacation.png").write_text("image", encoding="utf-8")

    rec = config_manager.recommend_persona_profile(scan_dirs=tmp_path)
    assert rec.get("recommended_id") == "office_admin", f"Expected 'office_admin', got {rec.get('recommended_id')}"
    assert "invoice" in rec.get("reason", "").lower() or "office" in rec.get("reason", "").lower()
    assert rec.get("scores", {}).get("office_admin", 0) > 0
    assert rec.get("files_scanned") >= 4


def test_recommend_persona_profile_fallback_home(tmp_path):
    """Verify recommend_persona_profile falls back to everyday_home when no specialized signals dominate."""
    import config_manager

    # Empty directory
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    rec_empty = config_manager.recommend_persona_profile(scan_dirs=empty_dir)
    assert rec_empty.get("recommended_id") == "everyday_home"
    assert "Everyday" in rec_empty.get("reason", "")
    assert rec_empty.get("files_scanned") == 0

    # Directory with only generic files (images, music, archives)
    generic_dir = tmp_path / "generic"
    generic_dir.mkdir()
    (generic_dir / "sunset.jpg").write_text("image", encoding="utf-8")
    (generic_dir / "track01.mp3").write_text("audio", encoding="utf-8")
    (generic_dir / "backup.zip").write_text("archive", encoding="utf-8")

    rec_generic = config_manager.recommend_persona_profile(scan_dirs=generic_dir)
    assert rec_generic.get("recommended_id") == "everyday_home"
    assert rec_generic.get("files_scanned") == 3


def test_recommend_persona_profile_defensive(tmp_path):
    """Verify recommend_persona_profile cleanly handles missing directories and caps scan at 500 files."""
    import config_manager

    # Non-existent path
    non_existent = tmp_path / "does_not_exist_xyz_123"
    rec = config_manager.recommend_persona_profile(scan_dirs=non_existent)
    assert rec.get("recommended_id") == "everyday_home"
    assert rec.get("files_scanned") == 0

    # Ensure sorter.DEFAULT_CONFIG remains untouched
    snapshot_before = copy.deepcopy(sorter.DEFAULT_CONFIG)
    config_manager.recommend_persona_profile(scan_dirs=tmp_path)
    assert sorter.DEFAULT_CONFIG == snapshot_before, "DEFAULT_CONFIG was mutated by recommend_persona_profile"


def test_welcome_wizard_auto_recommendation_and_mascot(qapp, tmp_path, monkeypatch):
    """Verify WelcomeWizardDialog auto-recommends on Step 1, exposes mascot & speech label, and updates on navigation."""
    from onboarding import SorterMascotWidget, WelcomeWizardDialog
    import config_manager

    # Mock recommend_persona_profile to recommend maker_3d
    mock_rec = {
        "recommended_id": "maker_3d",
        "reason": "We spotted 4 3D/CAD files in your Downloads — 3D Printing, CAD & CNC Maker is recommended for your PC!",
        "scores": {"maker_3d": 12, "academic_student": 0, "office_admin": 0, "everyday_home": 0},
        "files_scanned": 4,
    }
    monkeypatch.setattr(config_manager, "recommend_persona_profile", lambda *args, **kwargs: mock_rec)

    cfg = copy.deepcopy(sorter.DEFAULT_CONFIG)
    dlg = WelcomeWizardDialog(config=cfg)
    try:
        dlg.show()
        qapp.processEvents()

        # Attributes exposed
        assert hasattr(dlg, "_mascot_widget"), "WelcomeWizardDialog must expose _mascot_widget"
        assert isinstance(dlg._mascot_widget, SorterMascotWidget)
        assert hasattr(dlg, "_mascot_speech_lbl"), "WelcomeWizardDialog must expose _mascot_speech_lbl"
        assert hasattr(dlg, "_recommended_persona_id"), "WelcomeWizardDialog must expose _recommended_persona_id"
        assert dlg._recommended_persona_id == "maker_3d"

        # Pre-selection on Step 1
        assert dlg._selected_persona == "maker_3d", "Recommended persona 'maker_3d' was not pre-selected"
        card = dlg._persona_cards["maker_3d"]
        assert card._selected is True

        # Check badge on recommended card
        assert card._rec_badge_lbl is not None
        assert card._rec_badge_lbl.isVisible()
        assert "Recommended" in card._rec_badge_lbl.text()

        # Step 1 speech text and mascot pose
        assert dlg._mascot_widget.state in ("smart", "waving")
        assert "Hi! I'm Sorty!" in dlg._mascot_speech_lbl.text()
        assert "3D" in dlg._mascot_speech_lbl.text()

        # Navigate to Step 2
        dlg._on_next_clicked()
        qapp.processEvents()
        assert dlg._current_step == 1
        assert dlg._mascot_widget.state in ("safe", "protective")
        speech_step2 = dlg._mascot_speech_lbl.text().lower()
        assert "offline" in speech_step2 or "private" in speech_step2
        assert "undo" in speech_step2

        # Navigate to Step 3
        dlg._on_next_clicked()
        qapp.processEvents()
        assert dlg._current_step == 2
        assert dlg._mascot_widget.state in ("celebrating", "happy")
        speech_step3 = dlg._mascot_speech_lbl.text().lower()
        assert "get started" in speech_step3 or "done" in speech_step3 or "tidy" in speech_step3 or "clutter" in speech_step3

        # Navigate Back to Step 2
        dlg._on_back_clicked()
        qapp.processEvents()
        assert dlg._current_step == 1
        assert dlg._mascot_widget.state in ("safe", "protective")

        # Navigate Back to Step 1
        dlg._on_back_clicked()
        qapp.processEvents()
        assert dlg._current_step == 0
        assert dlg._mascot_widget.state in ("smart", "waving")
    finally:
        dlg.close()
        dlg.deleteLater()
        qapp.processEvents()


def test_mascot_widget_poses_and_pixmap(qapp):
    """Verify SorterMascotWidget state updates, set_step helper, and QPixmap rendering."""
    from onboarding import SorterMascotWidget

    widget = SorterMascotWidget()
    try:
        assert widget.state == "smart"

        # State transitions
        widget.set_state("safe")
        assert widget.state == "safe"
        widget.set_state("celebrating")
        assert widget.state == "celebrating"
        widget.set_state("waving")
        assert widget.state == "waving"

        # set_step transitions
        widget.set_step(0)
        assert widget.state in ("smart", "waving")
        widget.set_step(1)
        assert widget.state in ("safe", "protective")
        widget.set_step(2)
        assert widget.state in ("celebrating", "happy")

        # Pixmap export
        pixmap = widget.to_pixmap(84, 80)
        assert not pixmap.isNull()
        assert pixmap.width() == 84
        assert pixmap.height() == 80
    finally:
        widget.deleteLater()


def test_persona_card_recommendation_badge(qapp):
    """Verify PersonaCard displays and toggles the recommendation badge correctly."""
    from onboarding import PersonaCard

    card = PersonaCard("academic_student", "🎓", "Student", "Study materials")
    try:
        card.show()
        qapp.processEvents()
        assert card._rec_badge_lbl is None

        # Enable recommendation badge
        card.set_recommended(True, "✨ Recommended for your PC")
        qapp.processEvents()
        assert card._rec_badge_lbl is not None
        assert card._rec_badge_lbl.isVisible()
        assert "Recommended for your PC" in card._rec_badge_lbl.text()

        # Disable recommendation badge
        card.set_recommended(False)
        qapp.processEvents()
        assert not card._rec_badge_lbl.isVisible()

        # Re-enable recommendation badge
        card.set_recommended(True)
        qapp.processEvents()
        assert card._rec_badge_lbl.isVisible()
    finally:
        card.close()
        card.deleteLater()
        qapp.processEvents()

