"""Unit tests for public presets library, rule export/import, CLI dispatcher, and release verification."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

DOWNLOAD_SORTER_DIR = Path(__file__).resolve().parent.parent
if str(DOWNLOAD_SORTER_DIR) not in sys.path:
    sys.path.insert(0, str(DOWNLOAD_SORTER_DIR))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

import config_manager as suite_data
import main as main_entry
import verify_release
from rules_manager import PresetLibraryDialog


# Uses session-scoped qapp fixture from conftest.py


# =====================================================================
# 1. Preset Library Tests
# =====================================================================

def test_get_available_presets():
    presets = suite_data.get_available_presets()
    assert isinstance(presets, list)
    assert len(presets) >= 5

    preset_ids = {p["id"] for p in presets}
    assert "office_admin" in preset_ids
    assert "academic_student" in preset_ids
    assert "maker_3d" in preset_ids
    assert "software_dev" in preset_ids
    assert "creative_media" in preset_ids

    for p in presets:
        assert "name" in p
        assert "category" in p
        assert "description" in p
        assert len(p["rules"]) > 0
        for r in p["rules"]:
            assert "name" in r
            assert "folder" in r
            assert "match" in r
            assert "extensions" in r
            assert r["enabled"] is True


def test_apply_preset_success():
    base_config = {
        "keyword_rules": [
            {"name": "Existing Rule", "folder": "Folder", "match": ["test"], "extensions": [".pdf"], "enabled": True}
        ],
        "extension_rules": []
    }

    updated, added_count = suite_data.apply_preset(base_config, "office_admin")
    assert added_count > 0
    assert len(updated["keyword_rules"]) == 1 + added_count
    rule_names = [r["name"] for r in updated["keyword_rules"]]
    assert "Invoices & Receipts" in rule_names
    assert "Financial & Taxes" in rule_names

    # Applying again should add 0 duplicates
    updated_again, second_added = suite_data.apply_preset(updated, "office_admin")
    assert second_added == 0
    assert len(updated_again["keyword_rules"]) == len(updated["keyword_rules"])


def test_apply_preset_unknown_id():
    with pytest.raises(ValueError, match="Unknown preset ID"):
        suite_data.apply_preset({"keyword_rules": []}, "non_existent_preset_123")


# =====================================================================
# 2. Export and Import Tests
# =====================================================================

def test_export_and_import_rules(tmp_path):
    export_file = tmp_path / "exported_rules.json"

    test_config = {
        "keyword_rules": [
            {"name": "Exported KW", "folder": "Folder/KW", "match": ["kw"], "extensions": [".txt"], "enabled": True}
        ],
        "extension_rules": [
            {"name": "Exported Ext", "folder": "Folder/Ext", "extensions": [".bin"], "enabled": True}
        ]
    }

    # 1. Export
    assert suite_data.export_rules_to_file(export_file, test_config)
    assert export_file.exists()

    raw = json.loads(export_file.read_text(encoding="utf-8"))
    assert "version" in raw
    assert "exported_at" in raw
    assert len(raw["keyword_rules"]) == 1
    assert raw["keyword_rules"][0]["name"] == "Exported KW"

    # 2. Import
    imported = suite_data.import_rules_from_file(export_file)
    assert "keyword_rules" in imported
    assert "extension_rules" in imported
    assert imported["keyword_rules"][0]["name"] == "Exported KW"
    assert imported["extension_rules"][0]["name"] == "Exported Ext"


def test_import_rules_error_handling(tmp_path):
    # Non-existent file
    with pytest.raises(FileNotFoundError):
        suite_data.import_rules_from_file(tmp_path / "missing.json")

    # Invalid JSON
    corrupt_file = tmp_path / "corrupt.json"
    corrupt_file.write_text("{bad json", encoding="utf-8")
    with pytest.raises(Exception):
        suite_data.import_rules_from_file(corrupt_file)

    # Missing required keys
    empty_dict_file = tmp_path / "empty.json"
    empty_dict_file.write_text("{\"foo\": \"bar\"}", encoding="utf-8")
    with pytest.raises(ValueError, match="missing 'keyword_rules' and 'extension_rules'"):
        suite_data.import_rules_from_file(empty_dict_file)


def test_reset_sorter_config_to_defaults(tmp_path):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"keyword_rules": [], "extension_rules": []}), encoding="utf-8")

    res = suite_data.reset_sorter_config_to_defaults(config_path=cfg_file)
    assert isinstance(res, dict)
    assert len(res["keyword_rules"]) > 0
    assert len(res["extension_rules"]) > 0

    reloaded = suite_data.load_sorter_config(cfg_file)
    assert len(reloaded["keyword_rules"]) == len(res["keyword_rules"])


# =====================================================================
# 3. CLI Dispatcher (main.py) Tests
# =====================================================================

def test_main_cli_help(capsys):
    ret = main_entry.main(["--help"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "Download Sorter" in captured.out
    assert "--gui" in captured.out
    assert "--sort" in captured.out
    assert "--undo" in captured.out


def test_main_cli_version(capsys):
    ret = main_entry.main(["--version"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "Download Sorter v1.0.0" in captured.out


def test_main_cli_unknown_command(capsys):
    ret = main_entry.main(["--unknown-option-xyz"])
    assert ret == 2
    captured = capsys.readouterr()
    assert "Unknown command" in captured.out


def test_main_cli_sort_dry_run(capsys):
    ret = main_entry.main(["--sort", "--dry-run"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "[DRY-RUN]" in captured.out


def test_main_cli_dialog_routing(monkeypatch):
    import quick_switcher
    called = []
    monkeypatch.setattr(quick_switcher, "main", lambda: (called.append(True) or 0))
    ret = main_entry.main(["--dialog", "12345"])
    assert ret == 0
    assert len(called) == 1


def test_import_rules_malformed_items(tmp_path):
    # Keyword rule missing folder
    bad_kw = tmp_path / "bad_kw.json"
    bad_kw.write_text(json.dumps({"keyword_rules": [{"name": "Only Name"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="Malformed keyword rule"):
        suite_data.import_rules_from_file(bad_kw)

    # Extension rule missing extensions
    bad_ext = tmp_path / "bad_ext.json"
    bad_ext.write_text(json.dumps({"extension_rules": [{"name": "Only Name"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="Malformed extension rule"):
        suite_data.import_rules_from_file(bad_ext)


def test_apply_preset_malformed_existing():
    # Existing rules with None name or non-dict items
    corrupted_config = {
        "keyword_rules": [
            None,
            {"name": None},
            {"name": "Invoices & Receipts", "folder": "Docs", "match": ["inv"]}
        ]
    }
    updated, added = suite_data.apply_preset(corrupted_config, "office_admin")
    assert added > 0
    # "Invoices & Receipts" was already present, so it shouldn't be re-added
    names = [r.get("name") for r in updated["keyword_rules"] if isinstance(r, dict)]
    assert names.count("Invoices & Receipts") == 1


# =====================================================================
# 4. Release Verification Tests
# =====================================================================

def test_verify_release_checks():
    missing = verify_release.check_files()
    assert missing == [], f"Missing files in repository: {missing}"

    ok, ver, versions = verify_release.check_version_alignment()
    assert ok is True
    assert ver == "1.0.0"

    cfg_errors = verify_release.check_config_and_presets()
    assert cfg_errors == []

    cli_errors = verify_release.check_cli_dispatcher()
    assert cli_errors == []


# =====================================================================
# 5. Preset Dialog UI Tests
# =====================================================================

def test_preset_library_dialog(qapp):
    dlg = PresetLibraryDialog()
    assert dlg.windowTitle() == "📚 Rule Preset Library"
    assert len(dlg._checkboxes) >= 5

    # Test select all
    dlg._select_all()
    for cb in dlg._checkboxes.values():
        assert cb.isChecked()

    # Test clear all
    dlg._clear_all()
    for cb in dlg._checkboxes.values():
        assert not cb.isChecked()

    # Check one and accept
    dlg._checkboxes["office_admin"].setChecked(True)
    dlg._on_accept()
    assert dlg.get_selected_preset_ids() == ["office_admin"]
