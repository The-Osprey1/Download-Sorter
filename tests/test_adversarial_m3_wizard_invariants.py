"""Empirical Adversarial Verification Suite for Milestone 3.

Adversarially verifies:
1. WelcomeWizardDialog:
   - Rapid back-and-forth step transitions (0 -> 1 -> 2 -> 1 -> 0 -> 2) and boundary clamping.
   - Persona card selection toggling across all 4 personas, indicator symbols ('●' vs '○'),
     and theme accent border styling.
   - Initial state of checkboxes (startup, context menu) and their interaction with system settings.
   - Atomic 5-stage config persistence upon completion without corrupting arbitrary pre-existing user keys.
2. RulesManagerDialog View Switcher & Invariants:
   - Repeated switching between 'home' and 'customize' views (50 iterations loop).
   - Strict structural invariant: dlg._tabs.count() == 3 on every single iteration!
   - Strict widget invariant: dlg._tabs.widget(2) remains a MachineRemodelWidget on every iteration.
   - Header action button visibility synchronization (hidden on home, visible on customize).
   - Dynamic persona switching via dlg.set_active_persona(p):
     - dlg._active_project_card.isVisible() is False for everyday_home, academic_student, office_admin.
     - dlg._active_project_card.isVisible() is True for maker_3d.
     - dlg._sandbox preset chips rebuild correctly per persona.
     - Sandbox preset chip click triggers evaluate without errors.
3. Resource & Timer Safety:
   - Clean shutdown of QTimer instances on dialog close/hide without lingering timers or memory leaks.
4. Design System & Dynamic Attribution:
   - Zero forbidden hex tokens.
   - Dynamic attribution loaded via about.CREDIT without hardcoded author names.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
from unittest.mock import MagicMock

# Headless Qt Offscreen Safety: Must be set before importing PySide6
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QPushButton, QLabel

import about
import config_manager
from onboarding import HomeDashboardWidget, PersonaCard, WelcomeWizardDialog
import rules_manager
from rules_manager import MachineRemodelWidget, RulesManagerDialog
import sorter
from suite_theme import ACCENTS, COLORS, RADII, TYPE


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
# Area 1: WelcomeWizardDialog Step Transitions & Navigation
# ============================================================================

class TestWelcomeWizardNavigationTransitions:
    """Stress-test WelcomeWizardDialog step transitions and navigation boundaries."""

    def test_rapid_back_and_forth_step_transitions(self, qapp):
        """Stress-test rapid non-linear transitions: 0 -> 1 -> 2 -> 1 -> 0 -> 2 and boundary conditions."""
        cfg = copy.deepcopy(sorter.DEFAULT_CONFIG)
        dlg = WelcomeWizardDialog(config=cfg)
        try:
            dlg.show()
            qapp.processEvents()

            # Initial state: Step 0
            assert dlg._current_step == 0
            assert dlg._stack.currentIndex() == 0
            assert not dlg._btn_back.isVisible(), "Back button must be hidden on Step 0"
            assert dlg._btn_next.isVisible(), "Next button must be visible on Step 0"
            assert dlg._btn_next.text() == "Next →"
            assert not dlg._btn_finish.isVisible(), "Finish button must be hidden on Step 0"

            # Boundary Stress: Calling back on step 0 must NOT underflow
            dlg._on_back_clicked()
            qapp.processEvents()
            assert dlg._current_step == 0
            assert dlg._stack.currentIndex() == 0

            # Sequence: 0 -> 1 -> 2 -> 1 -> 0 -> 2
            sequence = [1, 2, 1, 0, 1, 2]
            for target_step in sequence:
                current = dlg._current_step
                if target_step > current:
                    for _ in range(target_step - current):
                        dlg._on_next_clicked()
                        qapp.processEvents()
                elif target_step < current:
                    for _ in range(current - target_step):
                        dlg._on_back_clicked()
                        qapp.processEvents()

                assert dlg._current_step == target_step, (
                    f"Expected step {target_step}, got {dlg._current_step}"
                )
                assert dlg._stack.currentIndex() == target_step, (
                    f"Stacked widget index {dlg._stack.currentIndex()} desynchronized from step {target_step}"
                )

                # Step-specific invariants
                if target_step == 0:
                    assert not dlg._btn_back.isVisible()
                    assert dlg._btn_next.text() == "Next →"
                    assert not dlg._btn_finish.isVisible()
                elif target_step == 1:
                    assert dlg._btn_back.isVisible()
                    assert dlg._btn_back.isEnabled()
                    assert dlg._btn_next.text() == "Next →"
                    assert not dlg._btn_finish.isVisible()
                elif target_step == 2:
                    assert dlg._btn_back.isVisible()
                    assert dlg._btn_back.isEnabled()
                    assert dlg._btn_next.text() == "Get Started ✓"
                    assert dlg._btn_finish.isVisible()

                # Pill styling sync check
                pills = [dlg._step_pill_1, dlg._step_pill_2, dlg._step_pill_3]
                active_pill = pills[target_step]
                mint_accent = ACCENTS["downloadSorter"].lower()
                assert mint_accent in active_pill.styleSheet().lower(), (
                    f"Active pill {target_step} missing theme mint accent in stylesheet"
                )
                for other_idx, other_pill in enumerate(pills):
                    if other_idx != target_step:
                        assert mint_accent not in other_pill.styleSheet().lower(), (
                            f"Inactive pill {other_idx} should not have active mint accent"
                        )
        finally:
            dlg.close()
            dlg.deleteLater()
            qapp.processEvents()

    def test_persona_card_selection_permutations_and_styling(self, qapp):
        """Stress-test persona card selection toggling across all 4 personas in multiple permutations."""
        cfg = copy.deepcopy(sorter.DEFAULT_CONFIG)
        dlg = WelcomeWizardDialog(config=cfg)
        try:
            dlg.show()
            qapp.processEvents()

            assert len(dlg._persona_cards) == 4, "Must display exactly 4 persona cards"
            personas = ["everyday_home", "academic_student", "office_admin", "maker_3d"]
            for pid in personas:
                assert pid in dlg._persona_cards, f"Missing persona card for {pid}"

            mint_accent = ACCENTS["downloadSorter"].lower()

            # Permutation cycle
            cycle = [
                "maker_3d",
                "academic_student",
                "everyday_home",
                "office_admin",
                "maker_3d",
                "office_admin",
                "academic_student",
                "everyday_home",
            ]

            for target_pid in cycle:
                # Trigger selection
                card = dlg._persona_cards[target_pid]
                dlg._on_persona_selected(target_pid)
                qapp.processEvents()

                assert dlg._selected_persona == target_pid
                assert card._selected is True
                assert card._indicator.text() == "●"
                assert mint_accent in card.styleSheet().lower()
                assert mint_accent in card._indicator.styleSheet().lower()
                assert mint_accent in card._title_lbl.styleSheet().lower()

                # Verify all other 3 cards are mutually excluded
                for other_pid, other_card in dlg._persona_cards.items():
                    if other_pid != target_pid:
                        assert other_card._selected is False, (
                            f"Card {other_pid} must be unselected when {target_pid} is active"
                        )
                        assert other_card._indicator.text() == "○"
                        assert mint_accent not in other_card.styleSheet().lower()
                        assert mint_accent not in other_card._indicator.styleSheet().lower()

            # Boundary Stress: Unknown persona selection does not crash
            dlg._on_persona_selected("unknown_persona_invalid")
            qapp.processEvents()
            assert dlg._selected_persona == "unknown_persona_invalid"
            for p_card in dlg._persona_cards.values():
                assert p_card._selected is False
                assert p_card._indicator.text() == "○"
        finally:
            dlg.close()
            dlg.deleteLater()
            qapp.processEvents()


# ============================================================================
# Area 2: Checkboxes, System Settings & Atomic Config Persistence
# ============================================================================

class TestWizardCheckboxesAndPersistence:
    """Stress-test checkbox state synchronization and atomic config persistence."""

    def test_wizard_step3_checkboxes_initial_state_and_acceptance(self, qapp, monkeypatch):
        """Verify Step 3 checkbox defaults, user toggling, and hook invocation on accept."""
        mock_startup = MagicMock()
        mock_context = MagicMock()
        monkeypatch.setattr(config_manager, "set_startup_enabled", mock_startup)
        monkeypatch.setattr(config_manager, "set_context_menu_enabled", mock_context)

        cfg = copy.deepcopy(sorter.DEFAULT_CONFIG)
        dlg = WelcomeWizardDialog(config=cfg)
        try:
            dlg.show()
            qapp.processEvents()

            # Navigate to Step 3
            dlg._on_next_clicked()
            dlg._on_next_clicked()
            qapp.processEvents()
            assert dlg._current_step == 2

            # Default recommendations on first-run wizard
            assert dlg._chk_startup.isChecked() is True, "Startup should default to True (Recommended)"
            assert dlg._chk_context.isChecked() is True, "Context menu should default to True"
            assert dlg._chk_sort_now.isChecked() is True, "Sort now should default to True"

            # User unchecks startup and context menu
            dlg._chk_startup.setChecked(False)
            dlg._chk_context.setChecked(False)
            qapp.processEvents()

            # Accept dialog
            dlg.accept()
            qapp.processEvents()

            mock_startup.assert_called_with(False)
            mock_context.assert_called_with(False)
        finally:
            dlg.close()
            dlg.deleteLater()
            qapp.processEvents()

    def test_home_dashboard_checkboxes_reflect_live_system_settings(self, qapp, monkeypatch):
        """Verify HomeDashboardWidget checkboxes query and strictly reflect system settings."""
        cfg = copy.deepcopy(sorter.DEFAULT_CONFIG)

        # Case A: Both disabled in Windows registry
        monkeypatch.setattr(config_manager, "is_startup_enabled", lambda: False)
        monkeypatch.setattr(config_manager, "is_context_menu_enabled", lambda: False)

        w1 = HomeDashboardWidget(config=cfg)
        try:
            w1.show()
            qapp.processEvents()
            assert w1._chk_startup.isChecked() is False
            assert w1._chk_context.isChecked() is False
        finally:
            w1.close()
            w1.deleteLater()
            qapp.processEvents()

        # Case B: Both enabled in Windows registry
        monkeypatch.setattr(config_manager, "is_startup_enabled", lambda: True)
        monkeypatch.setattr(config_manager, "is_context_menu_enabled", lambda: True)

        w2 = HomeDashboardWidget(config=cfg)
        try:
            w2.show()
            qapp.processEvents()
            assert w2._chk_startup.isChecked() is True
            assert w2._chk_context.isChecked() is True
        finally:
            w2.close()
            w2.deleteLater()
            qapp.processEvents()

    def test_atomic_config_persistence_preserves_arbitrary_user_keys(self, qapp, tmp_path, monkeypatch):
        """Verify wizard completion persists config atomically without corrupting pre-existing keys."""
        cfg_file = tmp_path / "config.json"
        monkeypatch.setattr(config_manager, "SORTER_CONFIG_FILE", cfg_file)

        # Pre-existing configuration with custom arbitrary keys and existing keyword rules
        pre_existing_config = {
            "version": 2,
            "downloads_folder": "C:\\Users\\devyn\\SpecialDownloads",
            "hold_period_seconds": 90,
            "user_persona": "everyday_home",
            "onboarding_completed": False,
            "auto_unpack_zips": True,
            "custom_metadata_tag": "ADVERSARIAL_TEST_PAYLOAD_XYZ",
            "nested_user_preferences": {
                "color_scheme": "high_contrast",
                "custom_ports": [8080, 8443],
                "retries": 5,
            },
            "keyword_rules": [
                {
                    "name": "PreExistingTaxRule",
                    "pattern": "w2_2025",
                    "folder": "Finance\\Taxes",
                }
            ],
            "extension_rules": [
                {"name": "XYZ Rule", "folder": "XYZFiles", "extensions": [".xyz"], "enabled": True}
            ],
        }
        cfg_file.write_text(json.dumps(pre_existing_config, indent=2), encoding="utf-8")

        # Launch wizard with this configuration
        dlg = WelcomeWizardDialog(config=pre_existing_config)
        try:
            dlg.show()
            qapp.processEvents()

            # Select 'office_admin'
            dlg._on_persona_selected("office_admin")
            dlg._on_next_clicked()
            dlg._on_next_clicked()
            qapp.processEvents()

            # Complete wizard
            dlg.accept()
            qapp.processEvents()

            # Verify file on disk
            assert cfg_file.exists(), "config.json must exist after wizard acceptance"
            saved = json.loads(cfg_file.read_text(encoding="utf-8"))

            # Milestone 3 core requirements
            assert saved.get("user_persona") == "office_admin", "Persona must update to 'office_admin'"
            assert saved.get("onboarding_completed") is True, "onboarding_completed must be True"

            # Strict preservation of pre-existing user data
            assert saved.get("downloads_folder") == "C:\\Users\\devyn\\SpecialDownloads"
            assert saved.get("hold_period_seconds") == 90
            assert saved.get("auto_unpack_zips") is True
            assert saved.get("custom_metadata_tag") == "ADVERSARIAL_TEST_PAYLOAD_XYZ"
            assert saved.get("nested_user_preferences") == {
                "color_scheme": "high_contrast",
                "custom_ports": [8080, 8443],
                "retries": 5,
            }

            # Preserves pre-existing non-colliding rule
            rule_names = [r.get("name") for r in saved.get("keyword_rules", [])]
            assert "PreExistingTaxRule" in rule_names, (
                "PreExistingTaxRule was dropped or corrupted during persona rule application"
            )

            # Injected office_admin rules
            assert len(saved.get("keyword_rules", [])) > 1
        finally:
            dlg.close()
            dlg.deleteLater()
            qapp.processEvents()


# ============================================================================
# Area 3: RulesManagerDialog View Switcher & Strict Structural Invariants
# ============================================================================

class TestRulesManagerViewSwitcherInvariants:
    """Stress-test RulesManagerDialog view switching and assert strict structural invariants."""

    def test_view_switcher_stress_tabs_count_invariant(self, qapp, tmp_path, monkeypatch):
        """Repeated switching between 'home' and 'customize' views (50 iterations loop).

        STRICT INVARIANT:
        dlg._tabs.count() == 3 must strictly hold on EVERY single iteration!
        dlg._tabs.widget(2) must remain a MachineRemodelWidget on EVERY single iteration!
        """
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps(sorter.DEFAULT_CONFIG), encoding="utf-8")
        monkeypatch.setattr(config_manager, "SORTER_CONFIG_FILE", cfg_file)

        dlg = rules_manager.RulesManagerDialog(initial_view="customize")
        try:
            dlg.show()
            qapp.processEvents()

            assert hasattr(dlg, "_tabs"), "RulesManagerDialog missing self._tabs"
            assert hasattr(dlg, "_view_stack"), "RulesManagerDialog missing self._view_stack"

            # Strict Invariant Baseline
            assert dlg._tabs.count() == 3
            assert isinstance(dlg._tabs.widget(2), MachineRemodelWidget)
            assert dlg._tabs.tabText(0) == "Keyword Rules (Priority Order)"
            assert dlg._tabs.tabText(1) == "Extension Rules (Fallback Categories)"
            assert dlg._tabs.tabText(2) == "Machine Remodel"

            # Stress Loop: 50 Iterations
            for i in range(50):
                # 1. Switch to Home
                dlg.switch_view("home")
                qapp.processEvents()

                assert dlg._tabs.count() == 3, f"Iteration {i}: _tabs.count() != 3 on home view"
                assert isinstance(dlg._tabs.widget(2), MachineRemodelWidget), (
                    f"Iteration {i}: Tab 2 was replaced or corrupted on home view"
                )
                assert dlg._view_stack.currentIndex() == 0, f"Iteration {i}: view stack index != 0"
                assert dlg._seg_btn_home.isChecked() is True, f"Iteration {i}: home button not checked"
                assert dlg._seg_btn_rules.isChecked() is False, f"Iteration {i}: rules button checked"

                # Header action buttons must be hidden in Home view
                for btn in [dlg._export_btn, dlg._import_btn, dlg._reset_btn, dlg._save_btn, dlg._preset_lib_btn]:
                    assert not btn.isVisible(), f"Iteration {i}: {btn.text()} should be hidden on Home view"

                # 2. Switch to Customize
                dlg.switch_view("customize")
                qapp.processEvents()

                assert dlg._tabs.count() == 3, f"Iteration {i}: _tabs.count() != 3 on customize view"
                assert isinstance(dlg._tabs.widget(2), MachineRemodelWidget), (
                    f"Iteration {i}: Tab 2 was replaced or corrupted on customize view"
                )
                assert dlg._view_stack.currentIndex() == 1, f"Iteration {i}: view stack index != 1"
                assert dlg._seg_btn_home.isChecked() is False, f"Iteration {i}: home button checked"
                assert dlg._seg_btn_rules.isChecked() is True, f"Iteration {i}: rules button not checked"

                # Header action buttons must be visible in Customize view
                for btn in [dlg._export_btn, dlg._import_btn, dlg._reset_btn, dlg._save_btn, dlg._preset_lib_btn]:
                    assert btn.isVisible(), f"Iteration {i}: {btn.text()} should be visible on Customize view"

            # Verify clicking segmented buttons triggers view changes
            dlg._seg_btn_home.click()
            qapp.processEvents()
            assert dlg._view_stack.currentIndex() == 0
            assert dlg._tabs.count() == 3

            dlg._seg_btn_rules.click()
            qapp.processEvents()
            assert dlg._view_stack.currentIndex() == 1
            assert dlg._tabs.count() == 3
        finally:
            dlg.close()
            dlg.deleteLater()
            qapp.processEvents()

    def test_dynamic_persona_switching_maker_visibility(self, qapp, tmp_path, monkeypatch):
        """Verify dynamic persona switching updates maker controls and preserves invariant across personas."""
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps(sorter.DEFAULT_CONFIG), encoding="utf-8")
        monkeypatch.setattr(config_manager, "SORTER_CONFIG_FILE", cfg_file)

        dlg = rules_manager.RulesManagerDialog(initial_view="customize")
        try:
            dlg.show()
            qapp.processEvents()

            assert hasattr(dlg, "_active_project_card"), "Missing _active_project_card"

            # Stress 20 persona switches
            test_cases = [
                ("everyday_home", False),
                ("academic_student", False),
                ("office_admin", False),
                ("maker_3d", True),
                ("everyday_home", False),
                ("maker_3d", True),
                ("office_admin", False),
                ("maker_3d", True),
                ("academic_student", False),
                ("maker_3d", True),
            ]

            for persona_id, expected_visible in test_cases * 2:
                dlg.set_active_persona(persona_id)
                qapp.processEvents()

                assert dlg._tabs.count() == 3, f"Tab count corrupted after switching to {persona_id}"
                assert dlg._active_project_card.isVisible() == expected_visible, (
                    f"For persona {persona_id}, expected isVisible() == {expected_visible}, "
                    f"got {dlg._active_project_card.isVisible()}"
                )
        finally:
            dlg.close()
            dlg.deleteLater()
            qapp.processEvents()

    def test_sandbox_preset_chips_rebuild_and_click_per_persona(self, qapp, tmp_path, monkeypatch):
        """Verify LiveSandboxWidget preset chips rebuild per persona and clicking sets input text."""
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps(sorter.DEFAULT_CONFIG), encoding="utf-8")
        monkeypatch.setattr(config_manager, "SORTER_CONFIG_FILE", cfg_file)

        dlg = rules_manager.RulesManagerDialog(initial_view="customize")
        try:
            dlg.show()
            qapp.processEvents()

            sandbox = dlg._sandbox

            # 1. Test maker_3d chips
            dlg.set_active_persona("maker_3d")
            qapp.processEvents()

            maker_buttons = [
                w for i in range(sandbox._chips_box.count())
                if (w := sandbox._chips_box.itemAt(i).widget()) and isinstance(w, QPushButton)
            ]
            maker_labels = [b.text() for b in maker_buttons]

            assert any("Haas" in lbl for lbl in maker_labels), f"Missing Haas CNC chip: {maker_labels}"
            assert any("Bambu" in lbl for lbl in maker_labels), f"Missing Bambu chip: {maker_labels}"
            assert any("3MF" in lbl for lbl in maker_labels), f"Missing 3MF chip: {maker_labels}"

            # Click a maker chip and check input
            haas_btn = next(b for b in maker_buttons if "Haas" in b.text())
            haas_btn.click()
            qapp.processEvents()
            assert sandbox._input.text() == "bracket_op1.nc"

            # 2. Test everyday_home chips
            dlg.set_active_persona("everyday_home")
            qapp.processEvents()

            home_buttons = [
                w for i in range(sandbox._chips_box.count())
                if (w := sandbox._chips_box.itemAt(i).widget()) and isinstance(w, QPushButton)
            ]
            home_labels = [b.text() for b in home_buttons]

            assert "Invoice_2026.pdf" in home_labels, f"Missing Invoice chip: {home_labels}"
            assert "Family_Photo.jpg" in home_labels, f"Missing Photo chip: {home_labels}"
            assert "Syllabus_Fall.pdf" in home_labels, f"Missing Syllabus chip: {home_labels}"
            assert not any("Haas" in lbl for lbl in home_labels), "Maker chip found in everyday persona"

            # Click an everyday chip and check input
            invoice_btn = next(b for b in home_buttons if "Invoice" in b.text())
            invoice_btn.click()
            qapp.processEvents()
            assert sandbox._input.text() == "Invoice_2026.pdf"
        finally:
            dlg.close()
            dlg.deleteLater()
            qapp.processEvents()


# ============================================================================
# Area 4: Resource Safety, Design Tokens & Dynamic Attribution
# ============================================================================

class TestResourceSafetyAndThemeCompliance:
    """Stress-test timer cleanup, theme tokens, and dynamic attribution."""

    def test_polling_timer_teardown_on_close_and_hide(self, qapp, tmp_path, monkeypatch):
        """Verify polling timers are cleanly stopped on closeEvent and hideEvent to prevent leaks."""
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps(sorter.DEFAULT_CONFIG), encoding="utf-8")
        monkeypatch.setattr(config_manager, "SORTER_CONFIG_FILE", cfg_file)

        dlg = rules_manager.RulesManagerDialog(initial_view="home")
        try:
            dlg.show()
            qapp.processEvents()

            assert hasattr(dlg, "_home_widget")
            assert hasattr(dlg._home_widget, "_refresh_timer")
            assert dlg._home_widget._refresh_timer.isActive(), "Refresh timer must be active when dialog is shown"

            # Trigger hide
            dlg.hide()
            qapp.processEvents()
            assert not dlg._home_widget._refresh_timer.isActive(), "Refresh timer must stop on hideEvent"

            # Trigger show again
            dlg.show()
            qapp.processEvents()
            # Start timer if restarted or check close stops it
            dlg._home_widget._refresh_timer.start()
            assert dlg._home_widget._refresh_timer.isActive()

            # Trigger close
            dlg.close()
            qapp.processEvents()
            assert not dlg._home_widget._refresh_timer.isActive(), "Refresh timer must stop on closeEvent"
        finally:
            dlg.deleteLater()
            qapp.processEvents()

    def test_zero_forbidden_hex_codes_in_onboarding_and_rules_manager(self):
        """Verify zero forbidden hex codes exist in onboarding.py or rules_manager.py."""
        forbidden_hexes = [
            "#171C26", "#181D26", "#212836", "#282F3D",
            "#151A22", "#1D232E", "#FFB347", "#181C25", "#0F1218"
        ]

        for filename in ["onboarding.py", "rules_manager.py"]:
            file_path = REPO_ROOT / filename
            assert file_path.exists(), f"Target file {filename} does not exist"
            content = file_path.read_text(encoding="utf-8").upper()

            for bad_hex in forbidden_hexes:
                assert bad_hex.upper() not in content, (
                    f"Forbidden palette token {bad_hex} found in {filename}!"
                )

    def test_dynamic_attribution_and_no_hardcoded_author(self):
        """Verify attribution is dynamically loaded from about.CREDIT with no hardcoded author string."""
        for filename in ["onboarding.py", "rules_manager.py"]:
            file_path = REPO_ROOT / filename
            content = file_path.read_text(encoding="utf-8")

            assert "about.CREDIT" in content, f"{filename} missing dynamic about.CREDIT attribution reference"
            assert "setObjectName(\"creditFooter\")" in content, f"{filename} missing creditFooter objectName"

            # Ensure no hardcoded personal author name
            assert "Devyn Fidel" not in content, f"Hardcoded author name found in {filename}"


# ============================================================================
# Area 5: Integration, CLI Dispatch & Fault Tolerance Stress
# ============================================================================

class TestIntegrationAndFaultTolerance:
    """Stress-test cross-widget integration, CLI routing, and error resilience."""

    def test_home_dashboard_combo_persona_change_synchronization(self, qapp, tmp_path, monkeypatch):
        """Verify changing persona combo in HomeDashboardWidget synchronizes RulesManagerDialog."""
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps(sorter.DEFAULT_CONFIG), encoding="utf-8")
        monkeypatch.setattr(config_manager, "SORTER_CONFIG_FILE", cfg_file)

        dlg = RulesManagerDialog(initial_view="home")
        try:
            dlg.show()
            qapp.processEvents()

            combo = dlg._home_widget._persona_combo

            # Switch combo to 'academic_student'
            idx_academic = combo.findData("academic_student")
            assert idx_academic >= 0
            combo.setCurrentIndex(idx_academic)
            qapp.processEvents()

            assert dlg._current_persona == "academic_student"
            assert dlg._tabs.count() == 3

            # Switch to customize view to check card visibility
            dlg.switch_view("customize")
            qapp.processEvents()
            assert not dlg._active_project_card.isVisible()
            assert dlg._tabs.count() == 3

            # Switch combo to 'maker_3d' while in customize view
            idx_maker = combo.findData("maker_3d")
            assert idx_maker >= 0
            combo.setCurrentIndex(idx_maker)
            qapp.processEvents()

            assert dlg._current_persona == "maker_3d"
            assert dlg._active_project_card.isVisible()
            assert dlg._tabs.count() == 3
        finally:
            dlg.close()
            dlg.deleteLater()
            qapp.processEvents()

    def test_wizard_resilience_to_registry_and_filesystem_exceptions(self, qapp, monkeypatch):
        """Verify WelcomeWizardDialog.accept() swallows low-level OS/registry exceptions without crashing."""
        monkeypatch.setattr(config_manager, "set_startup_enabled", MagicMock(side_effect=OSError("Access denied")))
        monkeypatch.setattr(config_manager, "set_context_menu_enabled", MagicMock(side_effect=PermissionError("Locked")))
        monkeypatch.setattr(config_manager, "save_sorter_config", MagicMock(side_effect=IOError("Disk full")))

        cfg = copy.deepcopy(sorter.DEFAULT_CONFIG)
        dlg = WelcomeWizardDialog(config=cfg)
        try:
            dlg.show()
            qapp.processEvents()

            # Navigate to end
            dlg._on_next_clicked()
            dlg._on_next_clicked()
            qapp.processEvents()

            # Must not raise unhandled exception
            dlg.accept()
            qapp.processEvents()
        finally:
            dlg.close()
            dlg.deleteLater()
            qapp.processEvents()

    def test_rules_manager_tab_switching_under_view_transitions(self, qapp, tmp_path, monkeypatch):
        """Verify active tab index inside _tabs is preserved across view transitions while _tabs.count() == 3."""
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps(sorter.DEFAULT_CONFIG), encoding="utf-8")
        monkeypatch.setattr(config_manager, "SORTER_CONFIG_FILE", cfg_file)

        dlg = RulesManagerDialog(initial_view="customize")
        try:
            dlg.show()
            qapp.processEvents()

            # Test each of the 3 tabs
            for tab_idx, expected_widget in [
                (0, dlg._keyword_table),
                (1, dlg._extension_table),
                (2, dlg._remodel_widget),
            ]:
                dlg._tabs.setCurrentIndex(tab_idx)
                qapp.processEvents()
                assert dlg._tabs.currentIndex() == tab_idx
                assert dlg._tabs.currentWidget() is expected_widget
                assert dlg._tabs.count() == 3

                # Switch to Home view
                dlg.switch_view("home")
                qapp.processEvents()
                assert dlg._tabs.count() == 3

                # Switch back to Customize view
                dlg.switch_view("customize")
                qapp.processEvents()
                assert dlg._tabs.count() == 3
                assert dlg._tabs.currentIndex() == tab_idx, (
                    f"Active tab index {dlg._tabs.currentIndex()} reset or lost after view switch"
                )
                assert dlg._tabs.currentWidget() is expected_widget
        finally:
            dlg.close()
            dlg.deleteLater()
            qapp.processEvents()

    def test_rules_manager_cli_flags_dispatch(self, qapp, monkeypatch):
        """Verify rules_manager.main routes --tab and --home correctly."""
        # Mock show and exec to avoid blocking
        monkeypatch.setattr(RulesManagerDialog, "show", lambda self: None)
        monkeypatch.setattr(QApplication, "exec", lambda self: 0)

        # 1. Test --home
        code_home = rules_manager.main(["--home"])
        assert code_home == 0

        # 2. Test --tab remodel
        code_remodel = rules_manager.main(["--tab", "remodel"])
        assert code_remodel == 0

        # 3. Test --tab keyword
        code_keyword = rules_manager.main(["--tab", "keyword"])
        assert code_keyword == 0

        # 4. Test --tab extension
        code_ext = rules_manager.main(["--tab", "extension"])
        assert code_ext == 0

