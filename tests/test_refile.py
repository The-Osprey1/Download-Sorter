"""Fixing a misfiled file: put it back, move it to another folder, or turn it into a rule.

These are the actions behind the "..." menu in Home's "Recently sorted" list.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

import config_manager
import learning
import refile
import safe_ops
import sort_undo
import sorter


@pytest.fixture(autouse=True)
def _samples_are_finished_downloading(finished_downloads):
    """These tests are about fixing a filed file, not about waiting for downloads to finish."""


def _config(downloads: Path) -> dict:
    cfg = sorter.get_default_config()
    cfg["downloads_dir"] = str(downloads)
    cfg["watch"] = {"folder": str(downloads), "settle_seconds": 0, "min_age_minutes": 0,
                    "auto_unzip": False, "auto_unpack_3d_zips": False}
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
def filed(downloads):
    """A report the sorter filed into Documents\\PDF: (journal record, config, path where it was filed)."""
    cfg = _config(downloads)
    dest = sorter.move_file(_old_file(downloads / "March invoice.pdf", "the invoice"), cfg)
    assert dest is not None and dest.parent == downloads / "Documents" / "PDF"
    record = sort_undo.get_active_moves(sort_undo.load_history())[-1]
    return record, cfg, dest


# ---------------------------------------------------------------------------
# put back
# ---------------------------------------------------------------------------

def test_put_back_returns_the_file_and_marks_the_move_undone(filed, downloads):
    record, _cfg, dest = filed

    outcome = refile.put_back(record, downloads)

    assert outcome.ok
    assert outcome.message == "Put March invoice.pdf back in Downloads."
    assert (downloads / "March invoice.pdf").read_text(encoding="utf-8") == "the invoice"
    assert not dest.exists()
    assert refile.active_record(record["id"]) is None, "the move shows as undone"


def test_put_back_says_so_when_the_file_is_gone(filed, downloads):
    record, _cfg, dest = filed
    dest.unlink()

    outcome = refile.put_back(record, downloads)

    assert not outcome.ok
    assert "isn't where it was filed" in outcome.message


def test_put_back_leaves_an_unpacked_zip_alone(tmp_path):
    unpacked = tmp_path / "Extracted" / "pack"
    unpacked.mkdir(parents=True)
    record = {"id": "x", "src": str(tmp_path / "pack.zip"), "dest": str(unpacked)}

    outcome = refile.put_back(record, tmp_path)

    assert not outcome.ok
    assert "unpacked from a zip" in outcome.message
    assert unpacked.exists()


def test_active_record_ignores_unknown_and_undone_moves(filed, downloads):
    record, _cfg, _dest = filed
    assert refile.active_record("") is None
    assert refile.active_record("no-such-id") is None
    assert refile.active_record(record["id"]) == record
    refile.put_back(record, downloads)
    assert refile.active_record(record["id"]) is None


# ---------------------------------------------------------------------------
# move to another folder
# ---------------------------------------------------------------------------

def test_move_is_journaled_and_never_replaces_a_file(filed, downloads, monkeypatch):
    record, cfg, dest = filed
    target = downloads / "Invoices"
    target.mkdir()
    (target / "March invoice.pdf").write_text("someone else's file", encoding="utf-8")
    seen = []
    monkeypatch.setattr(learning, "observe_user_move", lambda path, root, config: seen.append((path, root)))

    outcome = refile.move_to_folder(record, target, cfg, downloads)

    assert outcome.ok
    assert outcome.path == target / "March invoice (2).pdf"
    assert (target / "March invoice.pdf").read_text(encoding="utf-8") == "someone else's file"
    assert outcome.path.read_text(encoding="utf-8") == "the invoice"
    assert not dest.exists()
    assert outcome.message == "Moved March invoice (2).pdf to Invoices."

    newest = sort_undo.get_active_moves(sort_undo.load_history())[-1]
    assert newest["batch_id"].startswith("manual-")
    assert Path(newest["dest"]) == outcome.path and Path(newest["src"]) == dest

    assert sorter.is_restored_by_undo(outcome.path), "the sorter must leave a file you placed alone"
    assert seen == [(outcome.path, downloads)], "the learner is told you chose this folder"


def test_a_move_can_be_undone_like_any_other(filed, downloads):
    record, cfg, dest = filed
    outcome = refile.move_to_folder(record, downloads / "Invoices", cfg, downloads)
    assert outcome.ok

    moved_record = sort_undo.get_active_moves(sort_undo.load_history())[-1]
    assert refile.put_back(moved_record, downloads).ok
    assert (downloads / "Documents" / "PDF" / "March invoice.pdf").exists()


def test_moving_to_the_folder_it_is_already_in_does_nothing(filed, downloads):
    record, cfg, dest = filed

    outcome = refile.move_to_folder(record, dest.parent, cfg, downloads)

    assert not outcome.ok
    assert outcome.message == "March invoice.pdf is already in Documents\\PDF."
    assert dest.exists()


def test_move_is_refused_while_the_file_is_open_elsewhere(filed, downloads, monkeypatch):
    record, cfg, dest = filed
    monkeypatch.setattr(sorter, "is_file_locked", lambda path: True)

    outcome = refile.move_to_folder(record, downloads / "Invoices", cfg, downloads)

    assert not outcome.ok
    assert "open in another program" in outcome.message
    assert dest.exists()


def test_move_says_so_when_the_file_is_gone(filed, downloads):
    record, cfg, dest = filed
    dest.unlink()

    outcome = refile.move_to_folder(record, downloads / "Invoices", cfg, downloads)

    assert not outcome.ok and "isn't where it was filed" in outcome.message


def test_learner_suggestion_is_passed_on(filed, downloads, monkeypatch):
    record, cfg, _dest = filed
    monkeypatch.setattr(learning, "observe_user_move",
                        lambda path, root, config: {"suggestion": {"id": "abc123"}})

    outcome = refile.move_to_folder(record, downloads / "Invoices", cfg, downloads)

    assert outcome.ok and outcome.suggestion == "abc123"


def test_moving_outside_downloads_is_not_learned_from(filed, downloads, tmp_path, monkeypatch):
    record, cfg, _dest = filed
    monkeypatch.setattr(learning, "observe_user_move",
                        lambda *a: pytest.fail("a move outside Downloads must not teach the learner"))

    outcome = refile.move_to_folder(record, tmp_path / "Elsewhere", cfg, downloads)

    assert outcome.ok and outcome.path.parent == tmp_path / "Elsewhere"
    assert outcome.message.endswith(f"to {tmp_path / 'Elsewhere'}.")


def test_a_failing_learner_never_blocks_the_move(filed, downloads, monkeypatch):
    record, cfg, _dest = filed

    def broken(*args):
        raise RuntimeError("learning is broken")

    monkeypatch.setattr(learning, "observe_user_move", broken)

    outcome = refile.move_to_folder(record, downloads / "Invoices", cfg, downloads)

    assert outcome.ok and outcome.suggestion is None


def test_suggestion_ribbon_follows_the_notification_settings(monkeypatch):
    import ribbon_popup

    shown = []
    monkeypatch.setattr(ribbon_popup, "notify_suggestion", lambda suggestion_id: shown.append(suggestion_id))

    refile.ask_about_suggestion("s1", {"ribbon_enabled": False})
    refile.ask_about_suggestion("s2", {"notifications": False})
    assert shown == []
    refile.ask_about_suggestion("s3", {"ribbon_enabled": True, "notifications": True})
    refile.ask_about_suggestion("s4", None)
    assert shown == ["s3", "s4"]


# ---------------------------------------------------------------------------
# a rule from a file
# ---------------------------------------------------------------------------

def test_rule_draft_uses_the_files_word_type_and_folder(downloads):
    folder = downloads / "Documents" / "Invoices & Receipts"

    draft = refile.rule_draft_for("Invoice_2026.pdf", folder, downloads)

    assert draft == {
        "name": "Invoice files",
        "folder": "Documents\\Invoices & Receipts",
        "match": ["invoice"],
        "extensions": [".pdf"],
        "enabled": True,
    }


def test_rule_draft_for_a_file_with_no_words_falls_back_to_its_type(downloads):
    draft = refile.rule_draft_for("2041.jpg", downloads / "Images", downloads)

    assert draft["match"] == [] and draft["extensions"] == [".jpg"]
    assert draft["name"] == "JPG files" and draft["folder"] == "Images"


def test_added_rule_goes_first_so_it_wins(tmp_path):
    config_file = tmp_path / "config.json"
    base = sorter.get_default_config()
    base["keyword_rules"] = [{"name": "Old rule", "folder": "Old", "match": ["old"], "enabled": True}]
    config_file.write_text(json.dumps(base), encoding="utf-8")

    stored = refile.add_rule_first(
        {"name": "Invoice files", "folder": "Invoices", "match": ["invoice"], "extensions": [".pdf"],
         "enabled": True, "origin": "user"}, config_file)

    saved = config_manager.load_sorter_config(config_file)
    assert [r["name"] for r in saved["keyword_rules"]] == ["Invoice files", "Old rule"]
    assert saved["keyword_rules"][0]["origin"] == "user"
    assert stored["folder"] == "Invoices"


def test_the_same_rule_is_not_added_twice(tmp_path):
    config_file = tmp_path / "config.json"
    base = sorter.get_default_config()
    base["keyword_rules"] = [
        {"name": "Other", "folder": "Other", "match": ["x"], "enabled": True},
        {"name": "Invoice files", "folder": "Invoices", "match": ["invoice"], "extensions": [".pdf"],
         "enabled": False},
    ]
    config_file.write_text(json.dumps(base), encoding="utf-8")

    refile.add_rule_first({"name": "Invoice files again", "folder": "Invoices", "match": ["invoice"],
                           "extensions": [".pdf"], "enabled": True}, config_file)

    rules = config_manager.load_sorter_config(config_file)["keyword_rules"]
    assert [r["name"] for r in rules] == ["Invoice files", "Other"]
    assert rules[0]["enabled"] is True, "an existing copy is switched back on and moved to the top"


def test_saving_the_rule_failing_is_reported(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(sorter.get_default_config()), encoding="utf-8")
    monkeypatch.setattr(config_manager, "save_sorter_config", lambda *a, **k: False)

    with pytest.raises(OSError):
        refile.add_rule_first({"name": "X", "folder": "X", "match": ["x"], "extensions": [], "enabled": True},
                              config_file)


# ---------------------------------------------------------------------------
# the "Recently sorted" list
# ---------------------------------------------------------------------------

def test_a_file_moved_on_shows_once_where_it_is_now(filed, downloads, monkeypatch):
    record, cfg, dest = filed
    monkeypatch.setattr(learning, "observe_user_move", lambda *a: None)
    outcome = refile.move_to_folder(record, downloads / "Invoices", cfg, downloads)
    assert outcome.ok

    rows = config_manager.get_recent_activity(limit=25)
    assert [Path(r["dest"]) for r in rows] == [outcome.path], "the old place is not listed as well"

    # Undoing that move brings the file, and its first row, back.
    moved = sort_undo.get_active_moves(sort_undo.load_history())[-1]
    assert refile.put_back(moved, downloads).ok
    rows = config_manager.get_recent_activity(limit=25)
    assert [Path(r["dest"]) for r in rows] == [dest]
