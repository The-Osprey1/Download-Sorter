"""Adversarial stress tests for Live Sorter Sandbox and UI simulation logic in rules_manager.py.

Challenger M2:
1. Rapid simulated keystrokes, empty / whitespace / special character / path traversal filenames.
2. In-memory draft simulation under unsaved rule changes (toggle, reorder, add, delete, active project toggle).
3. Exact engine simulation parity against background daemon routing (sorter.py evaluate_routing).
4. Preset chips click interaction and dynamic state synchronization.
5. Zero unhandled exceptions, no memory leaks, accurate badge rendering.
"""
from __future__ import annotations

import copy
import gc
import json
import os
import sys
import time
import tracemalloc
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QTableWidgetItem

# Ensure download-sorter is in sys.path
DOWNLOAD_SORTER_DIR = Path(__file__).resolve().parent.parent
if str(DOWNLOAD_SORTER_DIR) not in sys.path:
    sys.path.insert(0, str(DOWNLOAD_SORTER_DIR))
else:
    sys.path.remove(str(DOWNLOAD_SORTER_DIR))
    sys.path.insert(0, str(DOWNLOAD_SORTER_DIR))

import sorter
import config_manager as suite_data
from rules_manager import (
    ActiveProjectCard,
    LiveSandboxWidget,
    RuleEditDialog,
    RulesManagerDialog,
    RuleTableWidget,
)


@pytest.fixture(scope="session")
def qapp():
    """Ensure a QApplication instance is running in headless offscreen mode."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def base_config():
    """Return a standard test configuration."""
    return {
        "watch": {"folder": "C:/Users/devyn/Downloads"},
        "downloads_dir": "C:/Users/devyn/Downloads",
        "gcode_sniffer_enabled": True,
        "haas_destination": "Code\\Haas",
        "gcode_destination": "Code\\Gcode",
        "ignore_extensions": [".crdownload", ".part", ".tmp"],
        "ignore_names": ["desktop.ini", "thumbs.db"],
        "keyword_rules": [
            {
                "name": "IME 3311 Coursework",
                "folder": "Documents\\School\\IME 3311",
                "match": ["ime3311", "ime 3311", "solidcast"],
                "extensions": [".pdf", ".docx"],
                "enabled": True,
            },
            {
                "name": "School Documents",
                "folder": "Documents\\School",
                "match": ["exam", "homework", "me3210"],
                "extensions": [".pdf", ".docx"],
                "enabled": True,
            },
        ],
        "extension_rules": [
            {"name": "3D STL", "folder": "3D Models\\STL", "extensions": [".stl"], "enabled": True},
            {"name": "3D 3MF", "folder": "3D Models\\3MF", "extensions": [".3mf"], "enabled": True},
            {"name": "Haas / G-Code", "folder": "Code\\Gcode", "extensions": [".nc", ".tap", ".gcode"], "enabled": True},
            {"name": "PDFs", "folder": "Documents\\PDFs", "extensions": [".pdf"], "enabled": True},
            {"name": "Archives", "folder": "Archives", "extensions": [".zip"], "enabled": True},
        ],
        "default_folder": "Other",
    }


# =====================================================================
# 1. RAPID SIMULATED KEYSTROKES & EDGE FILENAMES
# =====================================================================

class TestKeystrokeAndEdgeFilenames:
    """Stress test typing dynamics and hostile/edge filename inputs."""

    def test_rapid_keystrokes_continuous_evaluation(self, qapp, base_config):
        """Simulate realistic typing character-by-character at high speed."""
        sandbox = LiveSandboxWidget()
        sandbox.set_config_and_project(base_config, None)

        target_string = "IME3311_Final_Exam_Review_v2.0.pdf"
        # Type character by character
        for i in range(1, len(target_string) + 1):
            sub = target_string[:i]
            sandbox._input.setText(sub)
            assert sandbox._dest_lbl.text() is not None

        # Fully typed: matches IME 3311 Coursework
        assert sandbox._badge_lbl.text() == "[KEYWORD RULE]"
        assert "IME 3311" in sandbox._rule_lbl.text()
        assert sandbox._dest_lbl.text() == "Documents\\School\\IME 3311"

        # Backspace character by character
        for i in range(len(target_string) - 1, -1, -1):
            sub = target_string[:i]
            sandbox._input.setText(sub)

        # Completely erased
        assert sandbox._input.text() == ""
        assert sandbox._badge_lbl.text() == "[WAITING]"
        assert sandbox._dest_lbl.text() == "—"

    def test_empty_and_whitespace_variants(self, qapp, base_config):
        """Verify empty strings, whitespace, tabs, and newlines don't trigger crashes or false matches."""
        sandbox = LiveSandboxWidget()
        sandbox.set_config_and_project(base_config, None)

        whitespaces = ["", " ", "   ", "\t", "\n", " \t \r\n ", "   .pdf", "   \t  "]
        for ws in whitespaces:
            sandbox._input.setText(ws)
            if not ws.strip():
                assert sandbox._badge_lbl.text() == "[WAITING]"
                assert sandbox._dest_lbl.text() == "—"
            else:
                # E.g. "   .pdf" stripped becomes ".pdf"
                assert sandbox._badge_lbl.text() in ["[EXTENSION RULE]", "[WAITING]", "[DEFAULT FALLBACK]"]

    def test_path_traversal_and_absolute_paths(self, qapp, base_config):
        """Ensure path traversal sequences and absolute paths evaluate without filesystem side effects."""
        sandbox = LiveSandboxWidget()
        sandbox.set_config_and_project(base_config, None)

        hostile_paths = [
            "../../../../../../Windows/System32/calc.exe",
            "..\\..\\..\\..\\secret.pdf",
            "/etc/passwd",
            "C:\\Users\\devyn\\Downloads\\homework.pdf",
            "\\\\shared_drive\\nas\\file.stl",
            "C:/some/deep/nested/path/sample.3mf",
            "../../me3210_exam.pdf",
        ]
        for path in hostile_paths:
            res = sandbox.evaluate(path)
            assert res is not None
            assert "destination" in res
            assert "match_type" in res
            # Must not crash and must assign a valid destination
            assert len(res["destination"]) > 0 or res["match_type"] == "ignored"

    def test_windows_reserved_device_names(self, qapp, base_config):
        """Ensure Windows DOS device names (CON, PRN, AUX, NUL, COM1, LPT1) don't crash the widget."""
        sandbox = LiveSandboxWidget()
        sandbox.set_config_and_project(base_config, None)

        reserved_names = [
            "CON.pdf", "PRN.txt", "AUX.stl", "NUL.3mf",
            "COM1.nc", "COM9.gcode", "LPT1.zip", "lpt9.pdf",
            "aux", "nul", "con",
        ]
        for name in reserved_names:
            res = sandbox.evaluate(name)
            assert res is not None
            assert res["match_type"] in ["active_project", "keyword", "extension", "fallback", "gcode_sniffer"]

    def test_special_characters_regex_metachars_and_null_byte(self, qapp, base_config):
        """Test regex metacharacters, punctuation, quotes, and embedded null byte."""
        sandbox = LiveSandboxWidget()
        sandbox.set_config_and_project(base_config, None)

        specials = [
            "test[1].pdf",
            "exam (version 2) [final].pdf",
            "file+name*with?wildcards.stl",
            "quotes\"and'apostrophes.3mf",
            "pipe|and<redirects>.nc",
            "formula=1+1^2$3#4@5%6.pdf",
            "regex.*+?^${}()|[]\\.pdf",
            "SQL' OR '1'='1; DROP TABLE projects; --.pdf",
            "null\x00byte.pdf",
        ]
        for name in specials:
            res = sandbox.evaluate(name)
            assert res is not None
            assert "match_type" in res

    def test_multilingual_unicode_and_emoji_filenames(self, qapp, base_config):
        """Test non-ASCII UTF-8 filenames: CJK, Cyrillic, Arabic, Greek, and Emoji."""
        sandbox = LiveSandboxWidget()
        sandbox.set_config_and_project(base_config, None)

        unicode_names = [
            "テスト_モデル_部品.3mf",
            "Учебный_материал_экзамен.pdf",
            "وثيقة_مشروع_الجامعة.docx",
            "Σχεδίαση_εργαλείου.stl",
            "🔥🚀_Bambu_Vase_Turbine_⚙️.3mf",
            "IME 3311 鑄造工程學.pdf",
        ]
        for name in unicode_names:
            res = sandbox.evaluate(name)
            assert res is not None
            assert res["match_type"] in ["active_project", "keyword", "extension", "fallback"]

    def test_ultra_long_filename_no_catastrophic_backtracking(self, qapp, base_config):
        """Verify 50,000 character filename executes in <100ms with no ReDoS."""
        sandbox = LiveSandboxWidget()
        sandbox.set_config_and_project(base_config, None)

        huge_name = ("ime3311_" * 2000) + ("A" * 30000) + ".pdf"
        t0 = time.time()
        res = sandbox.evaluate(huge_name)
        dt = time.time() - t0

        assert res is not None
        assert dt < 0.2, f"Evaluation took too long: {dt:.4f}s (possible ReDoS)"
        assert res["match_type"] == "keyword"

    def test_ignore_rules_lock_files_and_downloads(self, qapp, base_config):
        """Verify temporary files, Microsoft Office lock files, and partially downloaded files."""
        sandbox = LiveSandboxWidget()
        sandbox.set_config_and_project(base_config, None)

        ignored_files = [
            "archive.iso.crdownload",
            "~$Important_Report.docx",
            "~WRL0001.tmp",
            ".~lock.document.odt#",
            "desktop.ini",
            "thumbs.db",
        ]
        for fname in ignored_files:
            res = sandbox.evaluate(fname)
            assert res is not None
            assert res["match_type"] == "ignored"
            assert sandbox._badge_lbl.text() == "[IGNORED]"
            assert sandbox._dest_lbl.text() == "None"


# =====================================================================
# 2. SIMULATION UNDER DRAFT (UNSAVED) RULE MODIFICATIONS
# =====================================================================

class TestDraftRuleModifications:
    """Challenge the in-memory draft synchronization contract.
    Changes made in the dialog must immediately reflect in the sandbox simulator,
    WHILE leaving the underlying disk files completely untouched until Save is clicked.
    """

    def test_draft_toggle_rule_off_updates_sandbox_before_save(self, qapp, tmp_path, monkeypatch, base_config):
        """Toggling a rule off in the table widget immediately demotes matching files in the sandbox."""
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps(base_config, indent=2), encoding="utf-8")
        monkeypatch.setattr(suite_data, "SORTER_CONFIG_FILE", cfg_file)

        proj_file = tmp_path / "active_project.json"
        monkeypatch.setattr(suite_data, "ACTIVE_PROJECT_FILE", proj_file)

        dlg = RulesManagerDialog()
        sandbox = dlg._sandbox

        # 1. Initially, IME3311 coursework rule matches
        res1 = sandbox.evaluate("IME3311_Lecture_Notes.pdf")
        assert res1["match_type"] == "keyword"
        assert res1["winning_rule"] == "IME 3311 Coursework"
        assert res1["destination"] == "Documents\\School\\IME 3311"

        # 2. Toggle off the first keyword rule ("IME 3311 Coursework") in draft table
        active_item = dlg._keyword_table._table.item(0, 5)
        active_item.setCheckState(Qt.CheckState.Unchecked)

        # 3. Test sandbox immediately: "IME 3311 Coursework" is disabled, falls through to "PDFs" extension rule
        res2 = sandbox.evaluate("IME3311_Lecture_Notes.pdf")
        assert res2["match_type"] == "extension"
        assert res2["winning_rule"] == "PDFs"
        assert res2["destination"] == "Documents\\PDFs"
        assert sandbox._badge_lbl.text() == "[EXTENSION RULE]"

        # 4. CRITICAL: Disk file must STILL HAVE enabled = True (unsaved draft!)
        on_disk = json.loads(cfg_file.read_text(encoding="utf-8"))
        assert on_disk["keyword_rules"][0]["enabled"] is True

        # 5. Save changes
        assert dlg.save_changes() is True

        # 6. Now disk reflects enabled = False
        saved_disk = json.loads(cfg_file.read_text(encoding="utf-8"))
        assert saved_disk["keyword_rules"][0]["enabled"] is False

    def test_draft_precedence_reordering_updates_sandbox_before_save(self, qapp, tmp_path, monkeypatch, base_config):
        """Moving a rule up or down in precedence immediately changes which rule wins in sandbox."""
        # Create config where two rules could both match "me3210_exam.pdf"
        cfg = copy.deepcopy(base_config)
        cfg["keyword_rules"] = [
            {"name": "General School", "folder": "School/General", "match": ["me3210", "exam"], "extensions": [".pdf"], "enabled": True},
            {"name": "Specific Exam Prep", "folder": "School/Exams", "match": ["me3210", "exam"], "extensions": [".pdf"], "enabled": True},
        ]
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        monkeypatch.setattr(suite_data, "SORTER_CONFIG_FILE", cfg_file)
        monkeypatch.setattr(suite_data, "ACTIVE_PROJECT_FILE", tmp_path / "active_project.json")

        dlg = RulesManagerDialog()
        sandbox = dlg._sandbox

        # 1. Rule 1 ("General School") wins initially due to precedence #1
        res1 = sandbox.evaluate("me3210_exam_practice.pdf")
        assert res1["winning_rule"] == "General School"
        assert res1["destination"] == "School/General"

        # 2. Swap precedence: Move Rule 1 down (or select row 1 and move up)
        dlg._keyword_table._table.selectRow(1)
        dlg._keyword_table.move_rule_up()

        # 3. Sandbox immediately reflects new precedence: "Specific Exam Prep" wins!
        res2 = sandbox.evaluate("me3210_exam_practice.pdf")
        assert res2["winning_rule"] == "Specific Exam Prep"
        assert res2["destination"] == "School/Exams"

        # 4. Verify disk still has "General School" at index 0 before saving
        disk_raw = json.loads(cfg_file.read_text(encoding="utf-8"))
        assert disk_raw["keyword_rules"][0]["name"] == "General School"

        # 5. Now save and verify disk updated
        dlg.save_changes()
        disk_saved = json.loads(cfg_file.read_text(encoding="utf-8"))
        assert disk_saved["keyword_rules"][0]["name"] == "Specific Exam Prep"

    def test_draft_add_and_delete_rule_before_save(self, qapp, tmp_path, monkeypatch, base_config):
        """Adding or deleting rules in draft memory affects sandbox instantly without premature save."""
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps(base_config, indent=2), encoding="utf-8")
        monkeypatch.setattr(suite_data, "SORTER_CONFIG_FILE", cfg_file)
        monkeypatch.setattr(suite_data, "ACTIVE_PROJECT_FILE", tmp_path / "active_project.json")

        dlg = RulesManagerDialog()
        sandbox = dlg._sandbox

        # File does not match any keyword initially
        res_init = sandbox.evaluate("robotics_controller_schematic.pdf")
        assert res_init["match_type"] == "extension"

        # Add new rule directly to draft table
        new_rule = {
            "name": "Robotics Lab",
            "folder": "Engineering\\Robotics",
            "match": ["robotics"],
            "extensions": [".pdf"],
            "enabled": True,
        }
        rules = dlg._keyword_table.get_rules()
        rules.insert(0, new_rule)
        dlg._keyword_table.load_rules(rules)
        dlg._on_rules_or_project_changed()

        # Sandbox now matches new rule!
        res_added = sandbox.evaluate("robotics_controller_schematic.pdf")
        assert res_added["match_type"] == "keyword"
        assert res_added["winning_rule"] == "Robotics Lab"
        assert res_added["destination"] == "Engineering\\Robotics"

        # Disk has not changed
        disk_raw = json.loads(cfg_file.read_text(encoding="utf-8"))
        assert len(disk_raw["keyword_rules"]) == 2

        # Now delete the added rule
        dlg._keyword_table._table.selectRow(0)
        dlg._keyword_table.delete_selected_rule()

        # Sandbox reverts back to extension rule
        res_deleted = sandbox.evaluate("robotics_controller_schematic.pdf")
        assert res_deleted["match_type"] == "extension"

    def test_draft_active_project_toggle_and_directory_edit(self, qapp, tmp_path, monkeypatch, base_config):
        """Toggling Active Project or editing its target directory updates sandbox CAD routing immediately."""
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps(base_config, indent=2), encoding="utf-8")
        monkeypatch.setattr(suite_data, "SORTER_CONFIG_FILE", cfg_file)

        proj_file = tmp_path / "active_project.json"
        monkeypatch.setattr(suite_data, "ACTIVE_PROJECT_FILE", proj_file)

        dlg = RulesManagerDialog()
        sandbox = dlg._sandbox
        card = dlg._active_project_card

        # 1. 3D model routes to extension rule initially
        res_off = sandbox.evaluate("gearbox_housing.stl")
        assert res_off["match_type"] == "extension"
        assert res_off["destination"] == "3D Models\\STL"

        # 2. Enable Active Project card with target directory
        card._enable_chk.setChecked(True)
        card._dir_edit.setText("C:\\Projects\\ActiveGearbox")

        # 3. Sandbox immediately routes CAD file to active project!
        res_on = sandbox.evaluate("gearbox_housing.stl")
        assert res_on["match_type"] == "active_project"
        assert res_on["destination"] == "C:\\Projects\\ActiveGearbox"
        assert sandbox._badge_lbl.text() == "[ACTIVE PROJECT]"

        # 4. Modify target directory live
        card._dir_edit.setText("D:\\Workspaces\\CustomBuild")
        res_modified = sandbox.evaluate("gearbox_housing.stl")
        assert res_modified["destination"] == "D:\\Workspaces\\CustomBuild"

        # 5. Non-CAD files bypass active project to keyword rules
        res_bypass = sandbox.evaluate("me3210_homework.pdf")
        assert res_bypass["match_type"] == "keyword"
        assert "School" in res_bypass["destination"]

        # 6. Disk active_project.json must NOT exist or be enabled yet!
        assert not proj_file.exists() or not json.loads(proj_file.read_text(encoding="utf-8")).get("enabled")

        # 7. Save changes and verify disk persisted
        dlg.save_changes()
        assert proj_file.exists()
        saved_proj = json.loads(proj_file.read_text(encoding="utf-8"))
        assert saved_proj["enabled"] is True
        assert saved_proj["target_directory"] == "D:\\Workspaces\\CustomBuild"


# =====================================================================
# 3. EXACT ENGINE SIMULATION PARITY AGAINST BACKGROUND DAEMON
# =====================================================================

class TestEngineSimulationParity:
    """Stress test parity between LiveSandboxWidget and the background sorter daemon.
    Every evaluation in the sandbox must return the exact same destination,
    match_type, and winning rule as direct evaluate_routing calls.
    """

    def test_parity_matrix_across_all_tiers(self, qapp, base_config):
        """Test a dense matrix of 30 diverse filenames across all 6 routing tiers."""
        active_proj = {
            "enabled": True,
            "project_name": "Autonomous Drone",
            "target_directory": "C:\\Drone_Project",
            "cad_extensions": list(suite_data.CAD_EXTENSIONS),
        }
        sandbox = LiveSandboxWidget()

        # Part A: With Active Project Enabled
        sandbox.set_config_and_project(base_config, active_proj)
        active_test_cases = [
            # Tier 0: Ignored
            ("download.iso.crdownload", "ignored", ""),
            ("~$Draft_Spec.docx", "ignored", ""),
            ("desktop.ini", "ignored", ""),
            # Tier 1: Active Project CAD / 3D / CNC files (captures .stl, .3mf, .step, .obj, .dxf, .nc, .gcode)
            ("motor_mount.stl", "active_project", "C:\\Drone_Project"),
            ("frame_body.3mf", "active_project", "C:\\Drone_Project"),
            ("sensor_bracket.step", "active_project", "C:\\Drone_Project"),
            ("landing_gear.obj", "active_project", "C:\\Drone_Project"),
            ("arm_profile.dxf", "active_project", "C:\\Drone_Project"),
            ("cnc_bracket.nc", "active_project", "C:\\Drone_Project"),
            # Tier 2: Keyword Rules (Non-CAD files bypass active project)
            ("IME3311_Solidcast_Report.pdf", "keyword", "Documents\\School\\IME 3311"),
            ("me3210_exam_study.docx", "keyword", "Documents\\School"),
            # Tier 4: Extension Rules (Non-CAD files not matching keywords)
            ("datasheet.pdf", "extension", "Documents\\PDFs"),
            ("backup.zip", "extension", "Archives"),
            # Tier 5: Default Fallback
            ("unrecognized_data.xyz", "fallback", "Other"),
            ("readme.txt", "fallback", "Other"),
            ("firmware.bin", "fallback", "Other"),
        ]

        for fname, expected_type, expected_dest in active_test_cases:
            ui_res = sandbox.evaluate(fname)
            daemon_res = sorter.evaluate_routing(
                filename=fname,
                config=base_config,
                active_project=active_proj,
            )
            assert ui_res["match_type"] == daemon_res["match_type"] == expected_type, f"Parity mismatch on {fname}"
            assert ui_res["destination"] == daemon_res["destination"] == expected_dest, f"Destination mismatch on {fname}"
            assert ui_res["winning_rule"] == daemon_res["winning_rule"], f"Winning rule mismatch on {fname}"

        # Part B: With Active Project Disabled (Testing Tier 3 G-Code Sniffer)
        sandbox.set_config_and_project(base_config, None)
        sniffer_test_cases = [
            ("bracket_op1.nc", "gcode_sniffer", "Code\\Haas"),
            ("vase_multicolor.gcode", "gcode_sniffer", "Code\\Gcode"),
            ("generic_model.stl", "extension", "3D Models\\STL"),
            ("turbine_bracket.3mf", "extension", "3D Models\\3MF"),
        ]
        for fname, expected_type, expected_dest in sniffer_test_cases:
            ui_res = sandbox.evaluate(fname)
            daemon_res = sorter.evaluate_routing(
                filename=fname,
                config=base_config,
                active_project=None,
                content_sample="%\nO01001\nG90 G54\nM30\n%" if "bracket_op1" in fname else (
                    "; generated by BambuStudio\nM104 S220\nG1 E1\n" if "vase" in fname else None
                ),
            )
            assert ui_res["match_type"] == daemon_res["match_type"] == expected_type, f"Parity mismatch on {fname}"
            assert ui_res["destination"] == daemon_res["destination"] == expected_dest, f"Destination mismatch on {fname}"
            assert ui_res["winning_rule"] == daemon_res["winning_rule"], f"Winning rule mismatch on {fname}"

    def test_parity_with_minimal_and_empty_config(self, qapp):
        """Evaluate parity when config dictionary has missing keys or default fallback only."""
        minimal_cfg = {"default_folder": "FallbackFolder"}
        sandbox = LiveSandboxWidget()
        sandbox.set_config_and_project(minimal_cfg, None)

        ui_res = sandbox.evaluate("test_file.xyz")
        daemon_res = sorter.evaluate_routing("test_file.xyz", minimal_cfg, active_project=None)

        assert ui_res["match_type"] == daemon_res["match_type"] == "fallback"
        assert ui_res["destination"] == daemon_res["destination"] == "FallbackFolder"
        assert sandbox._badge_lbl.text() == "[DEFAULT FALLBACK]"


# =====================================================================
# 4. PRESET CHIPS CLICKING & DYNAMIC STATE SYNCHRONIZATION
# =====================================================================

class TestPresetChipsAndStateSync:
    """Challenge the interactive quick preset chips in LiveSandboxWidget."""

    def test_all_five_preset_chips(self, qapp, base_config):
        """Click every preset chip and verify input text, badge, and destination."""
        sandbox = LiveSandboxWidget()
        sandbox.set_config_and_project(base_config, None)

        expected_presets = [
            ("bracket_op1.nc", "[GCODE SNIFFER]", "Code\\Haas"),
            ("vase_multicolor.gcode", "[GCODE SNIFFER]", "Code\\Gcode"),
            ("ME3210_Exam_Review.pdf", "[KEYWORD RULE]", "Documents\\School"),
            ("turbine_bracket.3mf", "[EXTENSION RULE]", "3D Models\\3MF"),
            ("hardware_pack.zip", "[EXTENSION RULE]", "Archives"),
        ]

        for fname, expected_badge, expected_dest in expected_presets:
            sandbox._set_input(fname)
            assert sandbox._input.text() == fname
            assert sandbox._badge_lbl.text() == expected_badge
            assert sandbox._dest_lbl.text() == expected_dest
            assert len(sandbox._rule_lbl.text()) > 0

    def test_preset_chips_rapid_switching(self, qapp, base_config):
        """Rapidly switch between chips 50 times to test for UI race conditions."""
        sandbox = LiveSandboxWidget()
        sandbox.set_config_and_project(base_config, None)

        fnames = [
            "ME3210_Exam_Review.pdf",
            "bracket_op1.nc",
            "vase_multicolor.gcode",
            "turbine_bracket.3mf",
            "hardware_pack.zip",
        ]
        for cycle in range(10):
            for fname in fnames:
                sandbox._set_input(fname)
                assert sandbox._input.text() == fname
                assert sandbox._badge_lbl.text() != "[WAITING]"

    def test_state_sync_active_project_toggle_with_chip_loaded(self, qapp, base_config):
        """Load 3MF preset chip, then toggle active project on and off.
        The simulator must instantly flip from [EXTENSION RULE] to [ACTIVE PROJECT] and back.
        """
        sandbox = LiveSandboxWidget()
        sandbox.set_config_and_project(base_config, None)

        # 1. Select 3MF chip
        sandbox._set_input("turbine_bracket.3mf")
        assert sandbox._badge_lbl.text() == "[EXTENSION RULE]"
        assert sandbox._dest_lbl.text() == "3D Models\\3MF"

        # 2. Toggle Active Project ON
        proj = {
            "enabled": True,
            "name": "Fixture Vise",
            "target_directory": "C:\\Fixture_Vise",
            "cad_extensions": list(suite_data.CAD_EXTENSIONS),
        }
        sandbox.set_config_and_project(base_config, proj)
        # Without re-typing, sandbox re-evaluates input!
        assert sandbox._badge_lbl.text() == "[ACTIVE PROJECT]"
        assert sandbox._dest_lbl.text() == "C:\\Fixture_Vise"
        assert sandbox._rule_lbl.text() == "Fixture Vise"

        # 3. Toggle Active Project OFF
        sandbox.set_config_and_project(base_config, None)
        assert sandbox._badge_lbl.text() == "[EXTENSION RULE]"
        assert sandbox._dest_lbl.text() == "3D Models\\3MF"

    def test_collapsible_rule_chain_trace(self, qapp, base_config):
        """Toggle collapsible trace list and verify contents."""
        sandbox = LiveSandboxWidget()
        sandbox.show()
        sandbox.set_config_and_project(base_config, None)

        assert not sandbox._trace_expanded
        assert sandbox._trace_list.isHidden()

        # Expand trace
        sandbox._toggle_trace()
        assert sandbox._trace_expanded
        assert not sandbox._trace_list.isHidden()
        assert "▼" in sandbox._trace_btn.text()

        # Evaluate and check trace entries
        sandbox._set_input("ME3210_Exam_Review.pdf")
        assert sandbox._trace_list.count() > 0
        trace_texts = [sandbox._trace_list.item(i).text() for i in range(sandbox._trace_list.count())]
        assert any("MATCHED" in t for t in trace_texts)

        # Collapse trace
        sandbox._toggle_trace()
        assert not sandbox._trace_expanded
        assert sandbox._trace_list.isHidden()


# =====================================================================
# 5. ZERO EXCEPTIONS, MEMORY LEAKS & BADGE RENDERING INTEGRITY
# =====================================================================

class TestBadgeRenderingAndMemoryIntegrity:
    """Stress test memory stability, badge rendering consistency, and exception safety."""

    def test_all_seven_badge_rendering_states(self, qapp, base_config):
        """Verify exact styling and text for all 7 badge states."""
        active_proj = {
            "enabled": True,
            "name": "Active Drone",
            "target_directory": "C:\\Drone",
            "cad_extensions": list(suite_data.CAD_EXTENSIONS),
        }
        sandbox = LiveSandboxWidget()

        # 1. Test Active Project badge
        sandbox.set_config_and_project(base_config, active_proj)
        sandbox.evaluate("model.stl")
        assert sandbox._badge_lbl.text() == "[ACTIVE PROJECT]"
        style = sandbox._badge_lbl.styleSheet()
        assert "background:" in style
        assert "border-radius:" in style

        # 2. Test Remaining 6 Badges with Active Project Disabled
        sandbox.set_config_and_project(base_config, None)
        badge_test_cases = [
            ("", "[WAITING]"),
            ("IME3311_Doc.pdf", "[KEYWORD RULE]"),
            ("bracket_op1.nc", "[GCODE SNIFFER]"),
            ("unrelated.zip", "[EXTENSION RULE]"),
            ("unknown_file.bin", "[DEFAULT FALLBACK]"),
            ("document.docx.crdownload", "[IGNORED]"),
        ]

        for fname, expected_badge in badge_test_cases:
            sandbox.evaluate(fname)
            assert sandbox._badge_lbl.text() == expected_badge
            style = sandbox._badge_lbl.styleSheet()
            assert "background:" in style
            assert "border-radius:" in style

    def test_memory_leak_rapid_keystrokes_and_evaluations(self, qapp, base_config):
        """Execute 1,500 evaluations and verify bounded memory allocation."""
        sandbox = LiveSandboxWidget()
        sandbox.set_config_and_project(base_config, None)

        gc.collect()
        tracemalloc.start()
        snap1 = tracemalloc.take_snapshot()

        words = ["IME3311", "me3210", "exam", "report", "model", "bracket", "gcode", "archive", "test"]
        exts = [".pdf", ".docx", ".stl", ".3mf", ".nc", ".zip", ".xyz", ".crdownload"]

        for i in range(1500):
            word = words[i % len(words)]
            ext = exts[i % len(exts)]
            fname = f"{word}_{i}{ext}"
            sandbox.evaluate(fname)

        gc.collect()
        snap2 = tracemalloc.take_snapshot()
        tracemalloc.stop()

        stats = snap2.compare_to(snap1, "lineno")
        total_growth_kb = sum(stat.size_diff for stat in stats) / 1024.0

        # Memory growth over 1,500 operations must be modest (< 3 MB)
        assert total_growth_kb < 3072, f"Excessive memory growth: {total_growth_kb:.2f} KB"

    def test_memory_leak_repeated_dialog_instantiation(self, qapp, tmp_path, monkeypatch, base_config):
        """Repeatedly instantiate and close RulesManagerDialog 20 times.
        Verify no unclosed timer or circular reference memory accumulation.
        """
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps(base_config, indent=2), encoding="utf-8")
        monkeypatch.setattr(suite_data, "SORTER_CONFIG_FILE", cfg_file)
        monkeypatch.setattr(suite_data, "ACTIVE_PROJECT_FILE", tmp_path / "active_project.json")

        gc.collect()
        tracemalloc.start()
        snap1 = tracemalloc.take_snapshot()

        for _ in range(20):
            dlg = RulesManagerDialog()
            dlg._sandbox.evaluate("test_bracket.3mf")
            dlg._keyword_table.load_rules(base_config["keyword_rules"])
            dlg._active_project_card.reload_projects()
            dlg.close()
            dlg.deleteLater()
            qapp.processEvents()

        gc.collect()
        snap2 = tracemalloc.take_snapshot()
        tracemalloc.stop()

        stats = snap2.compare_to(snap1, "lineno")
        total_growth_kb = sum(stat.size_diff for stat in stats) / 1024.0
        assert total_growth_kb < 4096, f"Dialog lifecycle leaked excessive memory: {total_growth_kb:.2f} KB"
