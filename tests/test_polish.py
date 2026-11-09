"""Small things that make the app easier to use.

- Home: when sorting is off or paused, "Start/Resume sorting" is the green button.
- Rules: the empty list fills the page; cut-off cells show their full text on hover.
- Closing the window with unsaved rule changes asks first.
- Settings shows "Just my folders" for people set up from their own folders.
- Tray: "1 file filed today", not "1 file(s)"; the held-3MF line only when some are held.
- Keyboard: Ctrl+1..4 pages, Ctrl+S save, Ctrl+Z (Home) undo last sort, F5 refresh.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("PySide6")

import config_manager
import sorter


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    import rules_manager

    cfg = sorter.get_default_config()
    cfg["downloads_dir"] = str(tmp_path / "Downloads")
    cfg["watch"] = {"folder": str(tmp_path / "Downloads")}
    cfg["onboarding_completed"] = True
    cfg["keyword_rules"] = [{"name": "School websites", "folder": "Documents\\School", "match": [],
                             "sites": ["instructure.com", "blackboard.com"], "enabled": True}]
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setattr(config_manager, "SORTER_CONFIG_FILE", cfg_file)
    monkeypatch.setattr(config_manager, "ACTIVE_PROJECT_FILE", tmp_path / "active_project.json")
    monkeypatch.setattr(config_manager, "is_sorter_running", lambda: False)
    dlg = rules_manager.RulesManagerDialog(initial_view="home")
    dlg.resize(1000, 760)
    dlg.show()
    qapp.processEvents()
    yield dlg
    dlg._set_dirty(False)
    dlg.close()
    dlg.deleteLater()
    qapp.processEvents()


def test_start_sorting_is_the_main_button_when_sorting_is_off(window, monkeypatch, qapp):
    home = window._home_widget
    assert home._btn_pause_resume.text() == "Start sorting"
    assert home._btn_pause_resume.property("role") == "primary"
    assert home._btn_sort_now.property("role") != "primary"

    monkeypatch.setattr(config_manager, "is_sorter_running", lambda: True)
    monkeypatch.setattr(sorter, "is_paused", lambda: False)
    home.refresh_status()
    assert home._btn_sort_now.property("role") == "primary"
    assert home._btn_pause_resume.property("role") != "primary"


def test_empty_rule_list_fills_the_page(qapp):
    import rules_manager

    table = rules_manager.RuleTableWidget("keyword")
    table.resize(800, 600)
    table.load_rules([])
    table.show()
    qapp.processEvents()
    try:
        assert table._empty_lbl.isVisible()
        assert table._empty_lbl.height() > 300, "the message fills the space instead of floating mid-page"
    finally:
        table.close()


def test_cut_off_cells_show_their_full_text(qapp):
    import rules_manager

    table = rules_manager.RuleTableWidget("keyword")
    table.load_rules([{"name": "School websites", "folder": "Documents\\School", "match": ["syllabus"],
                       "sites": ["instructure.com"], "extensions": [".pdf", ".docx"]}])
    assert table._table.item(0, 2).toolTip() == "Documents\\School"
    assert table._table.item(0, 3).toolTip() == "syllabus (downloaded from instructure.com)"
    assert table._table.item(0, 4).toolTip() == ".pdf, .docx"
    table.close()


@pytest.mark.parametrize("answer, closes, saved", [
    ("cancel", False, False),
    ("discard", True, False),
    ("save", True, True),
])
def test_closing_with_unsaved_rules_asks_first(window, monkeypatch, answer, closes, saved):
    saves = []
    monkeypatch.setattr(window, "ask_to_save_changes", lambda: answer)
    monkeypatch.setattr(window, "save_changes", lambda: saves.append(1) or window._set_dirty(False) or True)
    window._set_dirty(True)

    assert window.close() is closes
    assert bool(saves) is saved


def test_closing_without_changes_does_not_ask(window, monkeypatch):
    monkeypatch.setattr(window, "ask_to_save_changes", lambda: pytest.fail("should not ask"))
    assert window.close()


def test_keyboard_shortcuts(window, monkeypatch, qapp):
    from PySide6.QtGui import QKeySequence, QShortcut

    keys = {s.key().toString() for s in window.findChildren(QShortcut)}
    assert {"Ctrl+1", "Ctrl+2", "Ctrl+3", "Ctrl+4"} <= keys
    assert QKeySequence(QKeySequence.StandardKey.Save).toString() in keys
    assert window._nav_buttons["rules"].toolTip() == "Rules (Ctrl+2)"

    window.switch_view("settings")
    assert window.current_page() == "settings"
    undone = []
    monkeypatch.setattr(window._home_widget, "undo_last", lambda: undone.append(1) or 0)
    window._undo_shortcut()
    assert undone == [], "Ctrl+Z only undoes a sort on the Home page"
    window.switch_view("home")
    window._undo_shortcut()
    assert undone == [1]

    saves = []
    monkeypatch.setattr(window, "save_changes", lambda: saves.append(1) or True)
    window._save_shortcut()
    assert saves == [], "nothing to save"
    window._set_dirty(True)
    window._save_shortcut()
    assert saves == [1]


def test_settings_shows_just_my_folders_by_default(qapp):
    import onboarding

    widget = onboarding.SettingsWidget(config=sorter.get_default_config())
    try:
        assert widget._persona_combo.currentData() == "custom"
        assert widget._persona_combo.currentText() == "Just my folders"
    finally:
        widget.close()


@pytest.mark.parametrize("count, noun, text", [
    (0, "file", "No files"), (1, "file", "1 file"), (5, "file", "5 files"), (1, "3MF file", "1 3MF file"),
])
def test_tray_counts_read_naturally(count, noun, text):
    tray = pytest.importorskip("tray")
    assert tray._count_text(count, noun) == text
