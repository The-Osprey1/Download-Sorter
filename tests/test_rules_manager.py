"""Unit tests for RulesManagerDialog, ActiveProjectCard, LiveSandboxWidget, and suite_data rules operations."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

DOWNLOAD_SORTER_DIR = Path(__file__).resolve().parent.parent
if str(DOWNLOAD_SORTER_DIR) not in sys.path:
    sys.path.insert(0, str(DOWNLOAD_SORTER_DIR))
else:
    sys.path.remove(str(DOWNLOAD_SORTER_DIR))
    sys.path.insert(0, str(DOWNLOAD_SORTER_DIR))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QTableWidgetItem, QCheckBox

import config_manager as suite_data
from rules_manager import (
    RuleEditDialog,
    RuleTableWidget,
    ActiveProjectCard,
    LiveSandboxWidget,
    RulesManagerDialog,
)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


# ---- Test Suite Data: Sorter Config & Active Project Persistence ----

def test_load_sorter_config_missing_file(tmp_path):
    missing_file = tmp_path / "nonexistent_config.json"
    cfg = suite_data.load_sorter_config(missing_file)
    assert isinstance(cfg, dict)
    assert "keyword_rules" in cfg
    assert "extension_rules" in cfg


def test_load_sorter_config_valid_and_bak_recovery(tmp_path):
    config_file = tmp_path / "config.json"
    bak_file = tmp_path / "config.json.bak"

    # Valid config
    initial_data = {
        "watch_directory": str(tmp_path / "Downloads"),
        "keyword_rules": [{"name": "School", "keywords": ["ME3210"], "folder": "School", "active": True}],
        "extension_rules": [{"name": "Images", "extensions": [".png", ".jpg"], "folder": "Images", "active": True}]
    }
    config_file.write_text(json.dumps(initial_data), encoding="utf-8")

    loaded = suite_data.load_sorter_config(config_file)
    assert loaded["keyword_rules"][0]["name"] == "School"

    # Corrupt config with healthy .bak
    bak_file.write_text(json.dumps(initial_data), encoding="utf-8")
    config_file.write_text("{corrupt: json!!!", encoding="utf-8")

    recovered = suite_data.load_sorter_config(config_file)
    assert recovered["keyword_rules"][0]["name"] == "School"


def test_save_sorter_config_atomic_5_stage(tmp_path):
    config_file = tmp_path / "config.json"
    bak_file = tmp_path / "config.json.bak"

    cfg1 = {
        "watch_directory": str(tmp_path / "Downloads"),
        "keyword_rules": [{"name": "Initial", "keywords": ["init"], "folder": "Init", "active": True}],
        "extension_rules": []
    }
    assert suite_data.save_sorter_config(cfg1, config_file)
    assert config_file.exists()

    cfg2 = {
        "watch_directory": str(tmp_path / "Downloads"),
        "keyword_rules": [{"name": "Updated", "keywords": ["upd"], "folder": "Upd", "active": True}],
        "extension_rules": []
    }
    assert suite_data.save_sorter_config(cfg2, config_file)

    # .bak must hold cfg1 and config.json must hold cfg2
    assert bak_file.exists()
    assert json.loads(bak_file.read_text(encoding="utf-8"))["keyword_rules"][0]["name"] == "Initial"
    assert json.loads(config_file.read_text(encoding="utf-8"))["keyword_rules"][0]["name"] == "Updated"


def test_save_sorter_config_validation_rejection(tmp_path):
    config_file = tmp_path / "config.json"
    with pytest.raises(TypeError):
        suite_data.save_sorter_config(["not", "a", "dict"], config_file)
    with pytest.raises(ValueError):
        suite_data.save_sorter_config({"keyword_rules": "invalid"}, config_file)
    with pytest.raises(ValueError):
        suite_data.save_sorter_config({"keyword_rules": [], "extension_rules": 123}, config_file)


def test_active_project_save_and_expiry(tmp_path):
    proj_file = tmp_path / "active_project.json"

    # Active and not expired
    data = {
        "enabled": True,
        "name": "Haas Rotary Fixture",
        "folder": str(tmp_path / "Projects" / "Haas_Fixture"),
        "expires_at": time.time() + 3600
    }
    assert suite_data.save_active_project(data, proj_file)
    assert proj_file.exists()

    loaded = suite_data.get_active_project(proj_file)
    assert loaded is not None
    assert loaded["name"] == "Haas Rotary Fixture"
    assert loaded["enabled"] is True

    # Expired
    expired_data = {
        "enabled": True,
        "name": "Old Project",
        "folder": str(tmp_path / "Projects" / "Old"),
        "expires_at": time.time() - 100
    }
    assert suite_data.save_active_project(expired_data, proj_file)
    loaded_expired = suite_data.get_active_project(proj_file)
    assert loaded_expired is None or loaded_expired.get("enabled") is False


def test_get_maker_projects_sqlite(tmp_path):
    db_file = tmp_path / "library.db"
    # If DB doesn't exist
    assert suite_data.get_maker_projects(db_file) == []

    # If DB exists
    with sqlite3.connect(db_file) as conn:
        conn.execute("""
            CREATE TABLE projects (
                id INTEGER PRIMARY KEY, name TEXT, description TEXT, notes TEXT,
                stage TEXT, machine_type TEXT, next_action TEXT, blocker TEXT,
                updated_at REAL, archived_at REAL, managed_folder TEXT
            )
        """)
        conn.execute("INSERT INTO projects VALUES (1, 'Vise Jaw Set', '', '', 'Ready', 'CNC', '', '', 100, NULL, '')")
        conn.execute("INSERT INTO projects VALUES (2, 'Archived Spool', '', '', 'Done', '3D', '', '', 50, 999, '')")

    projs = suite_data.get_maker_projects(db_file)
    assert len(projs) == 1
    assert projs[0]["name"] == "Vise Jaw Set"
    assert projs[0]["machine_type"] == "CNC"


# ---- UI Component Tests: RuleEditDialog ----

def test_rule_edit_dialog_keyword_rule(qapp):
    initial = {
        "name": "Exam Prep",
        "folder": "School/Exams",
        "match": ["Exam", "Midterm"],
        "extensions": [".pdf"],
        "enabled": True
    }
    dlg = RuleEditDialog(rule=initial, rule_type="keyword")
    assert dlg._name_input.text() == "Exam Prep"
    assert dlg._folder_input.text() == "School/Exams"
    assert "Exam" in dlg._keywords_input.text()
    assert ".pdf" in dlg._exts_input.text()
    assert dlg._active_chk.isChecked()

    dlg._name_input.setText("Exam Prep Updated")
    res = dlg.get_rule_data()
    assert res["name"] == "Exam Prep Updated"
    assert res["folder"] == "School/Exams"
    assert "exam" in res["match"]
    assert res["enabled"] is True


def test_rule_edit_dialog_extension_rule(qapp):
    initial = {
        "name": "GCode",
        "folder": "3D_Prints/GCode",
        "extensions": [".gcode", ".nc"],
        "enabled": True
    }
    dlg = RuleEditDialog(rule=initial, rule_type="extension")
    assert dlg._name_input.text() == "GCode"
    assert ".gcode" in dlg._exts_input.text()
    assert not hasattr(dlg, "_keywords_input")

    res = dlg.get_rule_data()
    assert res["name"] == "GCode"
    assert ".gcode" in res["extensions"]


# ---- UI Component Tests: RuleTableWidget ----

def test_rule_table_widget_operations(qapp):
    rules = [
        {"name": "Rule 1", "folder": "Folder1", "match": ["k1"], "extensions": [".pdf"], "enabled": True},
        {"name": "Rule 2", "folder": "Folder2", "match": ["k2"], "extensions": [".docx"], "enabled": False},
        {"name": "Rule 3", "folder": "Folder3", "match": ["k3"], "extensions": [".xlsx"], "enabled": True},
    ]
    table_widget = RuleTableWidget(rule_type="keyword")
    table_widget.load_rules(rules)
    assert table_widget._table.rowCount() == 3

    # Check precedence numbers
    assert table_widget._table.item(0, 0).text() == "#1"
    assert table_widget._table.item(1, 0).text() == "#2"
    assert table_widget._table.item(2, 0).text() == "#3"

    # Toggle active checkbox
    item = table_widget._table.item(1, 5)
    assert item.checkState() == Qt.CheckState.Unchecked
    item.setCheckState(Qt.CheckState.Checked)
    out_rules = table_widget.get_rules()
    assert out_rules[1]["enabled"] is True

    # Move down Rule 1 -> precedence #2
    table_widget._table.selectRow(0)
    table_widget.move_rule_down()
    new_rules = table_widget.get_rules()
    assert new_rules[0]["name"] == "Rule 2"
    assert new_rules[1]["name"] == "Rule 1"

    # Move up Rule 1 -> back to precedence #1
    table_widget._table.selectRow(1)
    table_widget.move_rule_up()
    back_rules = table_widget.get_rules()
    assert back_rules[0]["name"] == "Rule 1"

    # Delete Rule 3
    table_widget._table.selectRow(2)
    table_widget.delete_selected_rule()
    assert table_widget._table.rowCount() == 2
    del_rules = table_widget.get_rules()
    assert len(del_rules) == 2
    assert del_rules[0]["name"] == "Rule 1"
    assert del_rules[1]["name"] == "Rule 2"


# ---- UI Component Tests: ActiveProjectCard ----

def test_active_project_card_toggle_and_expiry(qapp, tmp_path):
    card = ActiveProjectCard()

    # Initial state disabled
    assert not card._enable_chk.isChecked()
    state = card.get_state()
    assert state["enabled"] is False

    # Enable and choose custom directory
    card._enable_chk.setChecked(True)
    custom_dir = str(tmp_path / "MyProject")
    card._dir_edit.setText(custom_dir)

    # Select 4 Hours expiry
    card._expiry_combo.setCurrentText("4 Hours")

    active_data = card.get_state()
    assert active_data["enabled"] is True
    assert active_data["target_directory"] == custom_dir
    assert active_data["expires_at"] > time.time() + 3500


# ---- UI Component Tests: LiveSandboxWidget ----

def test_live_sandbox_evaluation(qapp):
    sandbox = LiveSandboxWidget()

    # Empty filename
    res_empty = sandbox.evaluate("")
    assert res_empty is None
    assert sandbox._badge_lbl.text() == "[WAITING]"

    # Keyword rule test
    cfg = {
        "watch": {"folder": "C:/Users/devyn/Downloads"},
        "keyword_rules": [
            {"name": "University Exam", "folder": "School/Exams", "match": ["me3210"], "extensions": [".pdf"], "enabled": True},
            {"name": "CNC Toolpath", "folder": "CNC/GCode", "match": ["haas"], "extensions": [".nc"], "enabled": True}
        ],
        "extension_rules": [],
        "default_folder": "Other"
    }
    sandbox.set_config_and_project(config=cfg, active_project=None)

    # Test ME3210
    res_exam = sandbox.evaluate("ME3210_Midterm_Study_Guide.pdf")
    assert res_exam is not None
    assert res_exam["match_type"] == "keyword"
    assert res_exam["winning_rule"] == "University Exam"
    assert res_exam["destination"] == "School/Exams"
    assert "[KEYWORD RULE]" in sandbox._badge_lbl.text()

    # Test Active Project mode
    active_proj = {
        "enabled": True,
        "name": "Bambu Dual Gear Extruder",
        "target_directory": "C:/Projects/Extruder",
        "folder": "C:/Projects/Extruder",
        "expires_at": time.time() + 3600
    }
    sandbox.set_config_and_project(config=cfg, active_project=active_proj)

    # 3D model should route to active project
    res_model = sandbox.evaluate("gears_plate1.3mf")
    assert res_model is not None
    assert res_model["match_type"] == "active_project"
    assert res_model["destination"] == "C:/Projects/Extruder"
    assert "[ACTIVE PROJECT]" in sandbox._badge_lbl.text()

    # School document still takes precedence over active project catch-all
    res_exam_ap = sandbox.evaluate("ME3210_Homework.pdf")
    assert res_exam_ap is not None
    assert res_exam_ap["match_type"] == "keyword"
    assert res_exam_ap["winning_rule"] == "University Exam"


def test_live_sandbox_preset_chips(qapp):
    sandbox = LiveSandboxWidget()
    # Click chip Haas CNC .nc
    sandbox._set_input("bracket_op1.nc")
    assert sandbox._input.text() == "bracket_op1.nc"
    assert sandbox._dest_lbl.text() != "—"

    # Click chip 3MF Model
    sandbox._set_input("turbine_bracket.3mf")
    assert sandbox._input.text() == "turbine_bracket.3mf"


# ---- Comprehensive Dialog Integration Test ----

def test_rules_manager_dialog_draft_and_persistence(qapp, tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    initial_cfg = {
        "watch": {"folder": str(tmp_path / "Downloads")},
        "keyword_rules": [
            {"name": "ME3210", "folder": "Academics/ME3210", "match": ["me3210"], "extensions": [".pdf"], "enabled": True}
        ],
        "extension_rules": [
            {"name": "Images", "folder": "Media/Images", "extensions": [".png", ".jpg"], "enabled": True}
        ],
        "default_folder": "Other"
    }
    cfg_file.write_text(json.dumps(initial_cfg), encoding="utf-8")
    monkeypatch.setattr(suite_data, "SORTER_CONFIG_FILE", cfg_file)

    proj_file = tmp_path / "active_project.json"
    monkeypatch.setattr(suite_data, "ACTIVE_PROJECT_FILE", proj_file)

    dlg = RulesManagerDialog()
    assert dlg._tabs.count() == 2
    assert dlg._keyword_table._table.rowCount() == 1
    assert dlg._extension_table._table.rowCount() == 1

    # Add a new extension rule to draft
    new_ext_rule = {"name": "CAD Models", "folder": "Engineering/CAD", "extensions": [".step", ".stp"], "enabled": True}
    current_ext_rules = dlg._extension_table.get_rules()
    current_ext_rules.append(new_ext_rule)
    dlg._extension_table.load_rules(current_ext_rules)
    assert dlg._extension_table._table.rowCount() == 2

    # Save changes
    dlg.save_changes()

    # Verify saved file
    saved_cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert len(saved_cfg["extension_rules"]) == 2
    assert saved_cfg["extension_rules"][1]["name"] == "CAD Models"
