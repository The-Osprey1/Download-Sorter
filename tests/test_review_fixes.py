"""Fixes from the v2.3.0 review.

1. Saving rules kept the whole settings file as it was when the window
   opened, so it undid Settings options changed meanwhile and rules added
   from a learning ribbon. It now changes only the rules in the current file.
2. Settings toggles saved the page's own (possibly old) copy. They now change
   one option in the current file.
3. Bug reports opened on GitHub (public) could show the user name and file
   names: the error message was the issue title.
4. Closing for an update asked "Save changes?" while the installer waited.
5. The daily clean-up ignored a renamed Installers or Archives folder.
"""

from __future__ import annotations

import json
import sys
import types

import pytest

import clean_old
import config_manager
import problem_report
import sorter


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    cfg = sorter.get_default_config()
    cfg["downloads_dir"] = str(tmp_path / "Downloads")
    cfg["watch"] = {"folder": str(tmp_path / "Downloads")}
    cfg["onboarding_completed"] = True
    cfg["keyword_rules"] = [{"name": "Invoices", "folder": "Invoices", "match": ["invoice"], "enabled": True}]
    path = tmp_path / "config.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setattr(config_manager, "SORTER_CONFIG_FILE", path)
    monkeypatch.setattr(config_manager, "ACTIVE_PROJECT_FILE", tmp_path / "active_project.json")
    return path


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _change_on_disk(path, change):
    cfg = _read(path)
    change(cfg)
    path.write_text(json.dumps(cfg), encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Saving rules
# ---------------------------------------------------------------------------

@pytest.fixture
def window(qapp, settings_file, monkeypatch):
    import rules_manager

    monkeypatch.setattr(config_manager, "is_sorter_running", lambda: False)
    dlg = rules_manager.RulesManagerDialog(initial_view="rules")
    yield dlg
    dlg._set_dirty(False)
    dlg.close()
    dlg.deleteLater()
    qapp.processEvents()


def test_saving_rules_keeps_settings_changed_meanwhile(window, settings_file):
    _change_on_disk(settings_file, lambda cfg: cfg.setdefault("photos", {}).update(by_month=True))
    rules = window._keyword_table.get_rules()
    rules[0]["match"] = ["invoice", "bill"]
    window._keyword_table.load_rules(rules)
    window._set_dirty(True)

    assert window.save_changes()

    saved = _read(settings_file)
    assert saved["photos"]["by_month"] is True, "the Settings option is not undone"
    assert saved["keyword_rules"][-1]["match"] == ["invoice", "bill"]


def test_saving_rules_keeps_a_rule_learned_meanwhile(window, settings_file):
    learned = {"name": "Banking (learned)", "folder": "Banking", "match": ["statement"],
               "sites": ["yourbank.com"], "origin": "learned", "enabled": True}
    _change_on_disk(settings_file, lambda cfg: cfg["keyword_rules"].insert(0, learned))
    window._set_dirty(True)

    assert window.save_changes()

    names = [r["name"] for r in _read(settings_file)["keyword_rules"]]
    assert names == ["Banking (learned)", "Invoices"]
    assert [r["name"] for r in window._keyword_table.get_rules()] == names, "and the page shows it"


def test_a_rule_deleted_on_the_page_stays_deleted(window, settings_file):
    window._keyword_table.load_rules([])
    window._set_dirty(True)
    assert window.save_changes()
    assert _read(settings_file)["keyword_rules"] == []


# ---------------------------------------------------------------------------
# 2. Settings toggles
# ---------------------------------------------------------------------------

def test_settings_toggle_keeps_a_rule_added_meanwhile(qapp, settings_file):
    import onboarding

    widget = onboarding.SettingsWidget()
    try:
        _change_on_disk(settings_file, lambda cfg: cfg["keyword_rules"].insert(
            0, {"name": "Banking (learned)", "folder": "Banking", "match": ["statement"]}))
        widget._option_checks[("rename", "tidy")].setChecked(True)
        widget._chk_unpack.setChecked(True)

        saved = _read(settings_file)
        assert [r["name"] for r in saved["keyword_rules"]] == ["Banking (learned)", "Invoices"]
        assert saved["rename"]["tidy"] is True
        assert saved["watch"]["auto_unzip"] is True
    finally:
        widget.close()


# ---------------------------------------------------------------------------
# 3. Nothing private in a public bug report
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    (r"FileNotFoundError: [Errno 2] No such file or directory: 'C:\\Users\\Jane\\Downloads\\Tax Return 2025.pdf'",
     "FileNotFoundError: [Errno 2] No such file or directory: '<file>.pdf'"),
    ('File "C:\\Users\\Jane\\AppData\\Local\\Programs\\DownloadSorter\\_internal\\sorter.py", line 12',
     'File "sorter.py", line 12'),
    (r"PermissionError: D:\School\IME4030\notes.docx is locked", "PermissionError: <file>.docx is locked"),
    (r"OSError: cannot open C:\Users\Jane\Desktop\secret", "OSError: cannot open <folder>"),
    ("ValueError: bad value 3", "ValueError: bad value 3"),
])
def test_private_paths_are_hidden(monkeypatch, text, expected):
    monkeypatch.setenv("USERPROFILE", r"C:\Users\Jane")
    assert problem_report.scrub_private(text) == expected


def test_issue_links_never_carry_paths(monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    monkeypatch.setenv("USERPROFILE", r"C:\Users\Jane")
    url = problem_report.issue_url(title=r"Crash in C:\Users\Jane\a.pdf", description=r"see C:\Users\Jane\x.txt",
                                   logs=r"Error at 'C:\Users\Jane\Downloads\Pay Stub.pdf'")
    assert "Jane" not in url and "Pay" not in url
    query = parse_qs(urlsplit(url).query)
    assert query["logs"] == ["Error at '<file>.pdf'"]


def test_crash_title_names_only_the_kind_of_error(monkeypatch):
    opened = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv(problem_report.NO_DIALOGS_ENV, raising=False)
    monkeypatch.setattr(problem_report, "_dialog_shown", False)
    monkeypatch.setattr(problem_report, "open_issue", lambda **kw: opened.append(kw) or True)
    fake_user32 = types.SimpleNamespace(MessageBoxW=lambda *a: 6)  # "Yes"
    import ctypes
    monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(user32=fake_user32), raising=False)
    try:
        raise FileNotFoundError(r"C:\Users\Jane\Downloads\Tax Return 2025.pdf")
    except FileNotFoundError as exc:
        problem_report._show_crash_dialog(None, type(exc), exc, exc.__traceback__, "test")

    assert opened[0]["title"] == "Crash: FileNotFoundError"


# ---------------------------------------------------------------------------
# 4. Closing for an update
# ---------------------------------------------------------------------------

def test_closing_for_an_update_saves_without_asking(window, qapp, monkeypatch):
    import rules_manager

    saves = []
    monkeypatch.setattr(window, "ask_to_save_changes", lambda: pytest.fail("must not ask during an update"))
    monkeypatch.setattr(window, "save_changes", lambda: saves.append(1) or window._set_dirty(False) or True)
    window._set_dirty(True)
    qapp.setProperty(rules_manager.QUITTING_FOR_UPDATE, True)
    try:
        assert window.close()
    finally:
        qapp.setProperty(rules_manager.QUITTING_FOR_UPDATE, False)
    assert saves == [1]


# ---------------------------------------------------------------------------
# 5. Clean-up follows renamed folders
# ---------------------------------------------------------------------------

def test_cleanup_follows_a_renamed_installers_folder(tmp_path, monkeypatch):
    import os
    import time

    monkeypatch.setattr(sys, "platform", "win32")
    binned = []
    monkeypatch.setattr(clean_old, "send_to_recycle_bin", lambda p: binned.append(p.name) or True)
    downloads = tmp_path / "Downloads"
    old = downloads / "Programs" / "old_setup.exe"
    old.parent.mkdir(parents=True)
    old.write_text("x", encoding="utf-8")
    past = time.time() - 60 * 86400
    os.utime(old, (past, past))

    # Personal mixed files in the same folder must NOT be recycled
    old_readme = downloads / "Programs" / "README.txt"
    old_readme.write_text("personal readme", encoding="utf-8")
    os.utime(old_readme, (past, past))
    old_pdf = downloads / "Programs" / "manual.pdf"
    old_pdf.write_text("personal manual", encoding="utf-8")
    os.utime(old_pdf, (past, past))

    cfg = sorter.get_default_config()
    cfg["downloads_dir"] = str(downloads)
    cfg["watch"] = {"folder": str(downloads)}
    cfg["cleanup"]["enabled"] = True
    next(r for r in cfg["extension_rules"] if r["name"] == "Installers")["folder"] = "Programs"

    clean_old.auto_cleanup(cfg)

    assert binned == ["old_setup.exe"]
    assert old_readme.exists()
    assert old_pdf.exists()
