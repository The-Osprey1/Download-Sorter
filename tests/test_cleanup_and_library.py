"""The Clean up page and the rule library.

Clean up: the numbers after a scan are shown at a glance and follow your choices,
each folder's choice is a segmented button, long reasons stay inside their badge,
plurals read right, and results go to the window's message bar instead of a
pop-up. Rule library: sets you already have are marked, and can't be added twice.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QLabel, QMessageBox

import remodel
import ui_widgets


@pytest.fixture
def widget(qapp, real_font, tmp_path):
    import rules_manager

    page = rules_manager.MachineRemodelWidget()
    page._engine.config = {"downloads_dir": str(tmp_path)}
    page.resize(760, 640)
    page.show()
    qapp.processEvents()
    yield page
    page.close()
    page.deleteLater()
    qapp.processEvents()


def _card(tmp_path, name: str, action: str, files: int, reason: str = "") -> remodel.FolderCard:
    folder = tmp_path / name
    folder.mkdir(exist_ok=True)
    return remodel.FolderCard(folder, action, files, 1.5, remodel.ProtectionCheck(bool(reason), reason), {})


def _plan(tmp_path, cards, loose=0, dupes=0, protected=0, scanned=20, reclaim=0) -> remodel.RemodelPlan:
    return remodel.RemodelPlan(
        remodel_id="test",
        target_root=tmp_path,
        created_at="2026-01-01T00:00:00Z",
        cards=list(cards),
        loose_files_to_organize=[(tmp_path / f"loose{i}.txt", tmp_path / "Docs" / f"loose{i}.txt") for i in range(loose)],
        duplicates_to_quarantine=[(tmp_path / f"copy{i}.txt", tmp_path / "_Duplicates" / f"copy{i}.txt",
                                   str(tmp_path / "original.txt")) for i in range(dupes)],
        protected_items=[(tmp_path / f"keep{i}", "kept") for i in range(protected)],
        total_files_scanned=scanned,
        reclaimable_duplicate_bytes=reclaim,
    )


def _numbers(page) -> dict[str, str]:
    return {key: label.text() for key, label in page._stat_values.items()}


@pytest.fixture
def no_popups(monkeypatch):
    """Good news and mild problems are not allowed to open a dialog."""
    def forbidden(*args, **kwargs):
        pytest.fail("a pop-up was opened")

    for name in ("information", "warning"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(forbidden))


# ---------------------------------------------------------------------------
# after a scan
# ---------------------------------------------------------------------------

def test_a_scan_shows_its_numbers_at_a_glance(widget, tmp_path):
    assert not widget._stats_strip.isVisibleTo(widget), "there is nothing to count before a scan"
    cards = [_card(tmp_path, "Old stuff", "organize", 3), _card(tmp_path, "My app", "keep_intact", 4, "Git repository")]
    widget._on_scan_finished(_plan(tmp_path, cards, loose=2, dupes=1, protected=1, scanned=12, reclaim=2048))

    assert widget._stats_strip.isVisibleTo(widget)
    assert _numbers(widget) == {"looked": "12", "move": "5", "alone": "1", "dupes": "1"}
    assert widget._stat_captions["dupes"].text() == "Duplicates · 2 KB"


def test_would_move_follows_the_choice_made_for_each_folder(widget, tmp_path):
    old = _card(tmp_path, "Old stuff", "organize", 3)
    app = _card(tmp_path, "My app", "keep_intact", 4, "Git repository")
    widget._on_scan_finished(_plan(tmp_path, [old, app], loose=2))
    assert _numbers(widget)["move"] == "5"

    widget._card_buttons[str(old.folder_path)].button(3).click()  # Skip for now
    assert _numbers(widget)["move"] == "2"
    widget._card_buttons[str(app.folder_path)].button(1).click()  # Organize by file type
    assert _numbers(widget)["move"] == "6"
    widget._card_buttons[str(app.folder_path)].button(2).click()  # Keep intact
    assert _numbers(widget)["move"] == "2"


def test_each_folders_choice_is_a_segmented_button_with_the_same_ids_as_before(widget, tmp_path):
    cards = [_card(tmp_path, "Old stuff", "organize", 3), _card(tmp_path, "My app", "keep_intact", 4, "Git repository"),
             _card(tmp_path, "Later", "skip", 1)]
    widget._on_scan_finished(_plan(tmp_path, cards))
    checked = {}
    for card in cards:
        group = widget._card_buttons[str(card.folder_path)]
        assert [(group.id(b), b.text()) for b in group.buttons()] == [
            (1, "Organize by file type"), (2, "Keep intact"), (3, "Skip for now")]
        assert all(b.property("role") == "segment" for b in group.buttons())
        checked[card.folder_path.name] = group.checkedId()
    assert checked == {"Old stuff": 1, "My app": 2, "Later": 3}


def test_a_new_scan_replaces_the_cards_of_the_last_one(widget, tmp_path):
    widget._on_scan_finished(_plan(tmp_path, [_card(tmp_path, "One", "organize", 1), _card(tmp_path, "Two", "organize", 1)]))
    assert len(widget._card_buttons) == 2 and len(widget._card_frames) == 2
    widget._on_scan_finished(_plan(tmp_path, [_card(tmp_path, "Three", "organize", 1)]))
    assert len(widget._card_buttons) == 1 and len(widget._card_frames) == 1
    assert widget._cards_layout.count() == 3, "one card, the empty-state label and the stretch"


def test_a_long_reason_stays_inside_its_badge_and_its_card(widget, tmp_path, qapp):
    reason = "Python virtual environment detected " + "(a very long folder name) " * 8
    widget._on_scan_finished(_plan(tmp_path, [_card(tmp_path, "Project", "keep_intact", 4, reason)]))
    qapp.processEvents()
    frame = widget._card_frames[0]
    badge = next(b for b in frame.findChildren(ui_widgets.ElidedLabel) if b.property("role") == "badge")
    assert badge.full_text() == f"Left alone: {reason}"
    assert badge.text().endswith("…") and badge.text() != badge.full_text()
    assert badge.toolTip() == badge.full_text(), "hover shows all of it"
    assert badge.geometry().right() <= frame.width(), "the badge does not push the card wider"
    assert widget._cards_scroll.horizontalScrollBar().maximum() == 0


# ---------------------------------------------------------------------------
# the sentence and the empty states
# ---------------------------------------------------------------------------

def test_the_summary_reads_right_for_one_and_for_many(widget, tmp_path):
    widget._on_scan_finished(_plan(tmp_path, [_card(tmp_path, "Only", "organize", 1)], dupes=1, scanned=1, reclaim=300))
    assert widget._summary_lbl.text() == (
        "Looked at 1 file in 1 folder. Found 1 duplicate file (under 1 KB); it'd be set aside for 30 days, "
        "not deleted. Check each folder below, then click Apply.")

    widget._on_scan_finished(_plan(tmp_path, [_card(tmp_path, "A", "organize", 1), _card(tmp_path, "B", "organize", 2)],
                                   dupes=3, scanned=9, reclaim=5 * 1024 * 1024))
    assert widget._summary_lbl.text() == (
        "Looked at 9 files in 2 folders. Found 3 duplicate files (5.0 MB); they'd be set aside for 30 days, "
        "not deleted. Check each folder below, then click Apply.")

    widget._on_scan_finished(_plan(tmp_path, [_card(tmp_path, "A", "organize", 1)], scanned=4))
    assert "No duplicates found." in widget._summary_lbl.text()


def test_the_page_says_what_to_do_before_a_scan_and_why_when_there_is_nothing(widget, tmp_path):
    assert widget._cards_empty.isVisibleTo(widget)
    assert widget._cards_empty.text().startswith("Nothing to show yet.")

    widget._on_scan_finished(_plan(tmp_path, [_card(tmp_path, "A", "organize", 1)]))
    assert not widget._cards_empty.isVisibleTo(widget), "the folders take its place"

    widget._on_scan_finished(_plan(tmp_path, [], loose=2))
    assert widget._cards_empty.text() == "No folders inside this one.\n\n2 loose files would be sorted into folders by type."
    assert widget._summary_lbl.text().endswith("Click Apply to go ahead.")

    widget._on_scan_finished(_plan(tmp_path, [], scanned=0))
    assert widget._cards_empty.text() == "Nothing to clean up here."
    assert widget._summary_lbl.text().endswith("There is nothing to move.")
    assert not widget._apply_btn.isEnabled()


# ---------------------------------------------------------------------------
# results go to the window's message bar
# ---------------------------------------------------------------------------

def test_a_finished_clean_up_is_a_message_with_a_way_to_the_list_of_moves(widget, tmp_path, no_popups):
    heard = []
    widget.notice.connect(lambda *args: heard.append(args))
    widget._on_scan_finished(_plan(tmp_path, [_card(tmp_path, "A", "organize", 3)], loose=1))
    widget._on_apply_finished({"executed_moves": [{}, {}, {}], "skipped_items": [{}]})

    assert len(heard) == 1
    text, kind, action_text, action = heard[0]
    assert text == "Clean-up finished. Moved 3 files and left 1 item alone."
    assert (kind, action_text) == ("success", "Where did my files go?")
    assert action == widget._open_receipt
    assert widget._receipt_btn.isEnabled()
    assert not widget._stats_strip.isVisibleTo(widget), "'would move' is no longer true once the files moved"


def test_undo_is_a_message_too(widget, monkeypatch, no_popups):
    heard = []
    widget.notice.connect(lambda *args: heard.append(args))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(widget._engine, "undo", lambda: {"ok": True, "message": "Put 4 files back where they were."})
    widget._on_undo()
    assert heard == [("Put 4 files back where they were.", "success", "", None)]
    assert widget._summary_lbl.text() == "Put 4 files back where they were."


def test_a_missing_folder_is_a_warning_message_not_a_dialog(widget, tmp_path, no_popups):
    heard = []
    widget.notice.connect(lambda *args: heard.append(args))
    widget._scope_combo.setCurrentIndex(3)
    widget._custom_path_edit.setText(str(tmp_path / "not there"))
    widget._start_scan()
    assert len(heard) == 1 and heard[0][1] == "warning" and "doesn't exist" in heard[0][0]
    assert widget._scan_btn.isEnabled(), "nothing started, so there is nothing to wait for"


def test_the_window_shows_the_clean_up_messages_in_its_bar(qapp, real_font, tmp_path, monkeypatch):
    import json

    import config_manager
    import rules_manager
    import sorter

    config = sorter.get_default_config()
    config["downloads_dir"] = str(tmp_path)
    config["watch"] = {"folder": str(tmp_path)}
    config["onboarding_completed"] = True
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setattr(config_manager, "SORTER_CONFIG_FILE", config_file)
    monkeypatch.setattr(config_manager, "is_sorter_running", lambda: True)
    monkeypatch.setattr(sorter, "is_paused", lambda: False)

    window = rules_manager.RulesManagerDialog(initial_view="cleanup")
    window.show()
    qapp.processEvents()
    try:
        opened = []
        window._remodel_widget._notify("Clean-up finished.", "success", "Where did my files go?", lambda: opened.append(1))
        assert window._notice.banner.text() == "Clean-up finished."
        assert window._notice.banner.action_text() == "Where did my files go?"
        window._notice.banner._action_btn.click()
        assert opened == [1]
    finally:
        window._set_dirty(False)
        window.close()
        window.deleteLater()
        qapp.processEvents()


# ---------------------------------------------------------------------------
# the rule library
# ---------------------------------------------------------------------------

def _library(qapp, real_font, existing=()):
    import rules_manager

    dialog = rules_manager.PresetLibraryDialog(existing_names=existing)
    dialog.show()
    qapp.processEvents()
    return dialog


def _close(dialog, qapp) -> None:
    dialog.close()
    dialog.deleteLater()
    qapp.processEvents()


def _badges(dialog) -> list[str]:
    return [label.text() for label in dialog.findChildren(QLabel) if label.property("role") == "badge"]


def test_the_library_adds_nothing_until_a_set_is_ticked(qapp, real_font, monkeypatch):
    dialog = _library(qapp, real_font)
    try:
        monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: pytest.fail("a pop-up was opened")))
        assert not dialog._add_btn.isEnabled()
        assert "Tick at least one" in dialog._add_btn.toolTip()
        dialog._on_accept()
        assert dialog.result() == 0 and dialog.get_selected_preset_ids() == []

        next(iter(dialog._checkboxes.values())).setChecked(True)
        assert dialog._add_btn.isEnabled()
        dialog._clear_all()
        assert not dialog._add_btn.isEnabled()
    finally:
        _close(dialog, qapp)


def test_a_set_you_already_have_is_marked_and_cannot_be_ticked(qapp, real_font):
    import config_manager

    presets = config_manager.get_available_presets()
    whole = presets[0]
    partial = presets[1]
    have = [rule["name"].upper() for rule in whole["rules"]] + [partial["rules"][0]["name"]]  # names ignore case
    dialog = _library(qapp, real_font, existing=have)
    try:
        assert not dialog._checkboxes[whole["id"]].isEnabled()
        assert dialog._checkboxes[partial["id"]].isEnabled()
        assert "Already added" in _badges(dialog)
        assert f"1 of {len(partial['rules'])} already added" in _badges(dialog)

        dialog._select_all()
        assert not dialog._checkboxes[whole["id"]].isChecked(), "Select all leaves out what you already have"
        assert dialog._checkboxes[partial["id"]].isChecked()
    finally:
        _close(dialog, qapp)


def test_the_library_marks_nothing_for_someone_with_no_rules(qapp, real_font):
    dialog = _library(qapp, real_font)
    try:
        assert all(cb.isEnabled() for cb in dialog._checkboxes.values())
        assert not any("already added" in text.lower() for text in _badges(dialog))
    finally:
        _close(dialog, qapp)


def test_the_window_tells_the_library_which_rules_are_in_the_list(qapp, real_font, tmp_path, monkeypatch):
    import json

    import config_manager
    import rules_manager
    import sorter

    preset = config_manager.get_available_presets()[0]
    config = sorter.get_default_config()
    config["downloads_dir"] = str(tmp_path)
    config["watch"] = {"folder": str(tmp_path)}
    config["onboarding_completed"] = True
    config["keyword_rules"] = [dict(rule) for rule in preset["rules"]]
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setattr(config_manager, "SORTER_CONFIG_FILE", config_file)
    monkeypatch.setattr(config_manager, "is_sorter_running", lambda: True)
    monkeypatch.setattr(sorter, "is_paused", lambda: False)

    seen = {}

    def fake_exec(self):
        seen["enabled"] = {pid: box.isEnabled() for pid, box in self._checkboxes.items()}
        return rules_manager.QDialog.DialogCode.Rejected

    monkeypatch.setattr(rules_manager.PresetLibraryDialog, "exec", fake_exec)
    window = rules_manager.RulesManagerDialog(initial_view="rules")
    window.show()
    qapp.processEvents()
    try:
        window.open_preset_library()
        assert seen["enabled"][preset["id"]] is False
    finally:
        window._set_dirty(False)
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_a_set_name_with_an_ampersand_is_shown_as_written(qapp, real_font):
    import config_manager

    named = [p for p in config_manager.get_available_presets() if "&" in p["name"]]
    assert named, "some built-in sets have an ampersand in their name"
    dialog = _library(qapp, real_font)
    try:
        for preset in named:
            text = dialog._checkboxes[preset["id"]].text()
            assert text.replace("&&", "&") == preset["name"], "a lone & would turn into a keyboard shortcut mark"
    finally:
        _close(dialog, qapp)
