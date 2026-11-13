"""Home's "Recently sorted" list, and fixing a misfiled file from it.

The list shows what kind of file each one is, when it was filed in plain words,
and a "..." menu: put it back, move it to another folder, or make a rule from it.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QPushButton

import config_manager
import learning
import onboarding
import refile
import sorter
import ui_widgets


def _config(downloads: Path) -> dict:
    cfg = sorter.get_default_config()
    cfg["downloads_dir"] = str(downloads)
    cfg["watch"] = {"folder": str(downloads), "settle_seconds": 0, "min_age_minutes": 0,
                    "auto_unzip": False, "auto_unpack_3d_zips": False}
    cfg["onboarding_completed"] = True
    cfg["notifications"] = False
    cfg["ribbon_enabled"] = False
    return cfg


def _old_file(path: Path, text: str = "data") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    past = time.time() - 120
    os.utime(path, (past, past))
    return path


@pytest.fixture
def downloads(tmp_path) -> Path:
    folder = tmp_path / "Downloads"
    folder.mkdir()
    return folder


@pytest.fixture
def home(qapp, real_font, downloads, tmp_path, monkeypatch, finished_downloads):
    cfg = _config(downloads)
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setattr(config_manager, "SORTER_CONFIG_FILE", cfg_file)
    monkeypatch.setattr(config_manager, "ACTIVE_PROJECT_FILE", tmp_path / "active_project.json")
    monkeypatch.setattr(config_manager, "is_sorter_running", lambda: True)
    monkeypatch.setattr(sorter, "is_paused", lambda: False)
    widget = onboarding.HomeDashboardWidget(config=cfg)
    widget.sorter_config = cfg
    widget.show()
    qapp.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()
    qapp.processEvents()


def _file_it(home, downloads: Path, name: str, text: str = "data") -> Path:
    """Have the sorter file ``name`` (as the background sorter would) and show the result on Home."""
    dest = sorter.move_file(_old_file(downloads / name, text), home.sorter_config)
    assert dest is not None
    home.refresh_data()
    return dest


def _entries(home, row: int) -> dict[str, bool]:
    entries = (e for e in home._row_entries(home._row_record(row)) if e)
    return {text: enabled for text, _icon, _run, enabled in entries}


# ---------------------------------------------------------------------------
# times
# ---------------------------------------------------------------------------

def test_times_read_naturally():
    now = datetime(2026, 11, 10, 15, 0).astimezone()

    def ago(**delta) -> str:
        return (now - timedelta(**delta)).isoformat()

    assert onboarding._relative_time(ago(seconds=10), now) == "Just now"
    assert onboarding._relative_time(ago(seconds=-30), now) == "Just now", "another clock a little ahead"
    assert onboarding._relative_time(ago(minutes=1, seconds=10), now) == "1 min ago"
    assert onboarding._relative_time(ago(minutes=59), now) == "59 min ago"
    assert onboarding._relative_time(ago(hours=3), now) == "3 h ago"
    assert onboarding._relative_time(ago(hours=20), now).startswith("Yesterday, ")
    assert onboarding._relative_time(ago(days=4), now).startswith("Nov 6, ")
    assert onboarding._relative_time("not a time", now) == "not a time"
    full = onboarding._full_time(now.isoformat())
    assert "2026" in full and "Tuesday" in full


# ---------------------------------------------------------------------------
# the list
# ---------------------------------------------------------------------------

def test_rows_show_a_kind_icon_and_a_time_that_reads_naturally(home, downloads):
    _file_it(home, downloads, "report.pdf")

    table = home._activity_table
    assert not table.item(0, 0).icon().isNull()
    assert table.item(0, 2).text() == "Just now"
    assert str(datetime.now().year) in table.item(0, 2).toolTip()
    assert home._last_filed_lbl.isVisible()
    assert home._last_filed_lbl.text() == "Last filed: report.pdf in Documents\\PDF, just now"


def test_the_last_filed_line_is_hidden_until_something_was_filed(home):
    assert not home._last_filed_lbl.isVisible()


def test_relative_times_keep_counting_while_the_window_is_open(home, downloads):
    _file_it(home, downloads, "report.pdf")
    item = home._activity_table.item(0, 2)
    assert item.text() == "Just now"
    stamp = (datetime.now().astimezone() - timedelta(minutes=7)).isoformat()
    item.setData(Qt.ItemDataRole.UserRole, stamp)
    home._last_filed_raw = stamp

    home._refresh_relative_times()

    assert item.text() == "7 min ago"
    assert home._last_filed_lbl.text().endswith("7 min ago")


def test_a_file_that_is_gone_is_dimmed_and_cannot_be_fixed(home, downloads):
    dest = _file_it(home, downloads, "report.pdf")
    assert _entries(home, 0) == {"Show in folder": True, "Put back where it was": True,
                                 "Move to...": True, "Always file like this...": True}
    dest.unlink()
    home.refresh_data()

    assert "moved or deleted" in home._activity_table.item(0, 0).toolTip()
    assert _entries(home, 0) == {"Show in folder": True, "Put back where it was": False,
                                 "Move to...": False, "Always file like this...": False}


def test_filter_narrows_the_list_and_keeps_all_files(home, downloads):
    for name in ("report.pdf", "notes.pdf", "photo.jpg"):
        _file_it(home, downloads, name)
    table = home._activity_table
    assert table.rowCount() == 3

    home._filter_edit.setText("photo")
    assert table.rowCount() == 1 and table.item(0, 0).text() == "photo.jpg"
    home._filter_edit.setText("PDF")
    assert table.rowCount() == 2, "matches the folder name too, whatever the capitals"

    home._filter_edit.setText("zzz")
    assert not table.isVisible()
    assert home._empty_state_lbl.text() == "No matches" and "zzz" in home._empty_hint_lbl.text()

    home._filter_edit.setText("")
    assert table.isVisible() and table.rowCount() == 3
    assert len(home._records) == 3


def test_filter_only_shows_when_there_is_something_to_filter(home, downloads):
    assert not home._filter_edit.isVisible()
    _file_it(home, downloads, "report.pdf")
    assert home._filter_edit.isVisible()


def test_a_new_file_is_still_announced_while_a_filter_hides_it(home, downloads):
    _file_it(home, downloads, "report.pdf")
    home._filter_edit.setText("report")
    dest = sorter.move_file(_old_file(downloads / "photo.jpg"), home.sorter_config)
    assert dest is not None

    home.refresh_data(announce=True)

    assert home._activity_table.rowCount() == 1, "the filter still applies"
    assert home._banner.text() == "Filed photo.jpg in Images\\JPG."


def test_show_more_loads_up_to_a_hundred(home, monkeypatch, tmp_path):
    records = [{"id": f"r{i}", "filename": f"f{i}.pdf", "folder": "Documents", "dest": str(tmp_path / f"f{i}.pdf"),
                "timestamp": "2026-11-08T00:00:00Z", "exists": False} for i in range(150)]
    monkeypatch.setattr(config_manager, "get_recent_activity", lambda limit=25: records[:limit])

    home.refresh_data()
    assert home._activity_table.rowCount() == 25 and home._more_btn.isVisible()

    home._more_btn.click()
    assert home._activity_table.rowCount() == 100 and not home._more_btn.isVisible()


def test_empty_state_says_what_to_do_next(home, downloads, monkeypatch, qapp):
    assert home._empty_state_lbl.text() == "Nothing sorted yet"
    assert "show up here" in home._empty_hint_lbl.text()

    _old_file(downloads / "report.pdf")
    _old_file(downloads / "photo.jpg")
    home.refresh_waiting()
    deadline = time.monotonic() + 10
    while home._waiting != 2 and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert "2 files are waiting" in home._empty_hint_lbl.text()
    assert "Sort 2 files now" in home._empty_hint_lbl.text()

    monkeypatch.setattr(config_manager, "is_sorter_running", lambda: False)
    home.refresh_status()
    assert "Sorting is off" in home._empty_hint_lbl.text()


def test_the_status_card_is_tinted_by_the_sorters_state(home, monkeypatch):
    assert home._status_card.property("hero") == "true"
    assert home._status_card.property("tone") == "success"

    monkeypatch.setattr(sorter, "is_paused", lambda: True)
    home.refresh_status()
    assert home._status_card.property("tone") == "warning"

    monkeypatch.setattr(sorter, "is_paused", lambda: False)
    monkeypatch.setattr(config_manager, "is_sorter_running", lambda: False)
    home.refresh_status()
    assert not home._status_card.property("tone")


def test_home_announces_a_change_of_state_once(home, monkeypatch):
    seen = []
    home.status_changed.connect(seen.append)

    home.refresh_status()
    assert seen == [], "nothing changed"
    monkeypatch.setattr(sorter, "is_paused", lambda: True)
    home.refresh_status()
    home.refresh_status()
    assert seen == ["paused"]


def test_double_clicking_a_row_shows_the_file(home, downloads, monkeypatch):
    dest = _file_it(home, downloads, "report.pdf")
    shown = []
    monkeypatch.setattr(home, "_show_in_folder", lambda path: shown.append(path))

    home._on_row_double_clicked(0, 0)
    home._on_row_double_clicked(0, 3)

    assert shown == [str(dest)], "the button column does its own thing"


def test_the_row_menu_lists_the_ways_to_fix_a_file(home, downloads, monkeypatch):
    _file_it(home, downloads, "report.pdf")
    cell = home._activity_table.cellWidget(0, 3)
    assert cell.findChild(QPushButton).toolTip() == "Show in folder"
    menu_button = cell.findChild(ui_widgets.RowMenuButton)
    actions = [a for a in menu_button.menu().actions() if not a.isSeparator()]
    assert [a.text() for a in actions] == [
        "Show in folder", "Put back where it was", "Move to...", "Always file like this..."]

    called = []
    monkeypatch.setattr(home, "put_back", lambda rec: called.append(rec["filename"]))
    actions[1].trigger()
    assert called == ["report.pdf"]


# ---------------------------------------------------------------------------
# fixing a misfile
# ---------------------------------------------------------------------------

def test_put_back_from_home(home, downloads):
    _file_it(home, downloads, "report.pdf", "the report")
    rec = home._row_record(0)

    assert home.put_back(rec) is True

    assert (downloads / "report.pdf").read_text(encoding="utf-8") == "the report"
    assert home._banner.text() == "Put report.pdf back in Downloads."
    assert home._banner.kind() == "info"
    assert home._activity_table.rowCount() == 0, "an undone move leaves the list"

    assert home.put_back(rec) is False, "a second try finds nothing to undo"
    assert home._banner.kind() == "warning" and "already undone" in home._banner.text()


def test_move_to_from_home_files_it_there_and_lists_it_once(home, downloads, monkeypatch):
    _file_it(home, downloads, "report.pdf")
    rec = home._row_record(0)
    asked = []
    monkeypatch.setattr(learning, "observe_user_move", lambda *a: {"suggestion": {"id": "s9"}})
    monkeypatch.setattr(refile, "ask_about_suggestion", lambda suggestion_id, cfg=None: asked.append(suggestion_id))

    assert home.move_to(rec, folder=downloads / "Invoices") is True

    assert (downloads / "Invoices" / "report.pdf").exists()
    assert home._banner.text() == "Moved report.pdf to Invoices."
    assert home._banner.kind() == "success"
    assert asked == ["s9"], "the learner's question is shown"
    assert home._activity_table.rowCount() == 1
    assert home._activity_table.item(0, 1).text() == "Invoices"


def test_move_to_that_goes_wrong_says_why(home, downloads, monkeypatch):
    dest = _file_it(home, downloads, "report.pdf")
    monkeypatch.setattr(sorter, "is_file_locked", lambda path: True)

    assert home.move_to(home._row_record(0), folder=downloads / "Invoices") is False

    assert home._banner.kind() == "warning" and "open in another program" in home._banner.text()
    assert dest.exists()


def test_cancelling_the_folder_picker_does_nothing(home, downloads, monkeypatch):
    dest = _file_it(home, downloads, "report.pdf")
    monkeypatch.setattr(onboarding.QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: ""))

    assert home.move_to(home._row_record(0)) is False
    assert dest.exists()


class _FakeRuleDialog:
    """Stands in for the rule editor: "the person" clicks Save, or Cancel."""

    result = QDialog.DialogCode.Accepted
    created_with: dict = {}

    def __init__(self, rule=None, rule_type="keyword", parent=None, title=None):
        type(self).created_with = {"rule": rule, "rule_type": rule_type, "title": title}
        self._rule = dict(rule or {})

    def exec(self):
        return type(self).result

    def get_rule_data(self):
        return dict(self._rule, name="Report files")


def test_always_file_like_this_adds_a_winning_rule_and_offers_the_rules_page(home, downloads, monkeypatch):
    import rules_manager

    _file_it(home, downloads, "report.pdf")
    monkeypatch.setattr(rules_manager, "RuleEditDialog", _FakeRuleDialog)
    _FakeRuleDialog.result = QDialog.DialogCode.Accepted
    opened = []
    home.navigate_requested.connect(opened.append)

    assert home.always_file_like(home._row_record(0)) is True

    draft = _FakeRuleDialog.created_with
    assert draft["title"] == "Always file like this"
    assert draft["rule"]["match"] == ["report"] and draft["rule"]["extensions"] == [".pdf"]
    assert draft["rule"]["folder"] == "Documents\\PDF"

    first = config_manager.load_sorter_config()["keyword_rules"][0]
    assert first["name"] == "Report files" and first["origin"] == "user"
    assert home._banner.action_text() == "Open Rules"
    home._banner._action_btn.click()
    assert opened == ["rules"]


def test_cancelling_the_rule_editor_adds_nothing(home, downloads, monkeypatch):
    import rules_manager

    _file_it(home, downloads, "report.pdf")
    before = config_manager.load_sorter_config()["keyword_rules"]
    monkeypatch.setattr(rules_manager, "RuleEditDialog", _FakeRuleDialog)
    _FakeRuleDialog.result = QDialog.DialogCode.Rejected

    assert home.always_file_like(home._row_record(0)) is False
    assert config_manager.load_sorter_config()["keyword_rules"] == before


# ---------------------------------------------------------------------------
# why a file was filed
# ---------------------------------------------------------------------------

def test_a_row_says_why_the_file_was_filed(home, downloads):
    _file_it(home, downloads, "report.pdf")

    record = home._row_record(0)
    assert record["why"] == "extension"
    tip = home._activity_table.item(0, 0).toolTip()
    assert tip.splitlines()[0].endswith("report.pdf")
    assert "Filed by its file type" in tip


def test_why_reads_in_plain_words():
    describe = config_manager.describe_why
    assert describe({"why": "keyword", "rule": "Banking", "site": "yourbank.com"}) == (
        'Filed by your rule "Banking"\nDownloaded from yourbank.com')
    assert describe({"why": "fallback", "rule": "Default Fallback"}).startswith("No rule matched")
    assert describe({}) == ""   # a move from before this was recorded
    assert describe({"why": "something new"}) == ""
