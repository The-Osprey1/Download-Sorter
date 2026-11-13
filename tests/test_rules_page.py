"""The Rules page: making a rule, seeing which rule did what, and knowing what is unsaved.

Rule editor: it says what is missing next to the box instead of in a pop-up, and
keeps Save off until the rule could match something. Rules page: rules made for
you carry a badge, "Try a filename" shows the rule that won (and offers to make
one when none did), and the page says when there are unsaved changes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog

import config_manager
import sorter
import ui_widgets

KEYWORD_RULES = [
    {"name": "Invoices", "folder": "Documents\\Invoices", "match": ["invoice"], "extensions": [".pdf"],
     "enabled": True},
    {"name": "Taxes", "folder": "Documents\\Taxes", "match": ["tax", "w2"], "extensions": [],
     "enabled": True, "origin": "learned"},
    {"name": "Holiday photos", "folder": "Pictures\\Holiday", "match": ["holiday"], "extensions": [],
     "enabled": True, "origin": "scan"},
]
EXTENSION_RULES = [
    {"name": "Pictures", "folder": "Pictures", "extensions": [".jpg", ".png"], "enabled": True},
]


def _type(edit, text: str) -> None:
    """Type into a box the way a person does. (setText would not count as typing.)"""
    edit.setFocus()
    edit.selectAll()
    QTest.keyClicks(edit, text)


def _clear(edit) -> None:
    edit.setFocus()
    edit.selectAll()
    QTest.keyClick(edit, Qt.Key.Key_Backspace)


# ---------------------------------------------------------------------------
# the rule editor
# ---------------------------------------------------------------------------

@pytest.fixture
def make_editor(qapp, real_font):
    import rules_manager

    made = []

    def make(rule=None, rule_type="keyword", **kwargs):
        kwargs.setdefault("known_folders", [])  # don't look at the real Downloads folder
        dialog = rules_manager.RuleEditDialog(rule=rule, rule_type=rule_type, **kwargs)
        dialog.show()
        qapp.processEvents()
        made.append(dialog)
        return dialog

    yield make
    for dialog in made:
        dialog.close()
        dialog.deleteLater()
    qapp.processEvents()


def test_a_new_name_rule_cannot_be_saved_until_it_could_match_something(make_editor):
    dialog = make_editor()
    assert not dialog._save_btn.isEnabled()
    assert dialog._needs_note.text() == "Still needed: a name, a folder, a word to look for"

    _type(dialog._name_input, "Bank statements")
    _type(dialog._folder_input, "Documents\\Banking")
    _type(dialog._exts_input, ".pdf")
    assert not dialog._save_btn.isEnabled(), "a file type alone is not enough: a name rule needs a word"
    assert dialog._needs_note.text() == "Still needed: a word to look for"

    _type(dialog._keywords_input, "statement")
    assert dialog._save_btn.isEnabled()
    assert dialog._needs_note.text() == ""


def test_the_missing_word_is_explained_next_to_its_box(make_editor):
    dialog = make_editor(rule={"name": "Bank", "folder": "Banking", "match": [], "extensions": [".pdf"]})
    # It is the only thing missing, so it is explained straight away.
    assert dialog._words_error.isVisibleTo(dialog)
    assert "never matches" in dialog._words_error.text()
    assert dialog._keywords_input.property("invalid") == "true"
    assert not dialog._name_error.isVisibleTo(dialog)

    _type(dialog._keywords_input, "statement")
    assert not dialog._words_error.isVisibleTo(dialog)
    assert not dialog._keywords_input.property("invalid")


def test_errors_wait_until_you_have_typed_in_a_box(make_editor):
    dialog = make_editor()
    for label in (dialog._name_error, dialog._folder_error, dialog._words_error):
        assert not label.isVisibleTo(dialog), "nothing is flagged in a form that was only just opened"

    _type(dialog._name_input, "Bank")
    _clear(dialog._name_input)
    assert dialog._name_error.isVisibleTo(dialog)
    assert dialog._name_input.property("invalid") == "true"
    assert not dialog._folder_error.isVisibleTo(dialog), "a box you have not touched stays quiet"


def test_trying_to_save_an_unfinished_rule_shows_everything_that_is_missing(make_editor):
    dialog = make_editor()
    dialog._validate_and_accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    for label in (dialog._name_error, dialog._folder_error, dialog._words_error):
        assert label.isVisibleTo(dialog)
    assert dialog.focusWidget() is dialog._name_input, "the first thing to fix is ready to type in"


def test_a_rule_made_for_a_website_needs_no_word(make_editor):
    dialog = make_editor(rule={"name": "Shop", "folder": "Orders", "match": [], "extensions": [],
                               "sites": ["shop.example.com"], "origin": "learned"})
    assert dialog._save_btn.isEnabled()
    saved = dialog.get_rule_data()
    assert saved["sites"] == ["shop.example.com"]
    assert saved["origin"] == "user", "edited by hand, so it is the person's own rule now"


def test_a_file_type_rule_needs_a_file_type(make_editor):
    dialog = make_editor(rule_type="extension")
    assert not hasattr(dialog, "_keywords_input")
    _type(dialog._name_input, "Pictures")
    _type(dialog._folder_input, "Pictures")
    assert not dialog._save_btn.isEnabled()
    assert dialog._types_error.isVisibleTo(dialog)
    assert "file type" in dialog._needs_note.text()

    _type(dialog._exts_input, "PNG")
    assert dialog._save_btn.isEnabled()
    assert dialog.get_rule_data()["extensions"] == [".png"]


def test_file_types_are_tidied_into_the_form_the_sorter_uses(make_editor):
    import rules_manager

    assert rules_manager._parse_extensions(".PDF, docx; *.xlsx  .pdf") == [".pdf", ".docx", ".xlsx"]
    assert rules_manager._parse_extensions("") == []
    assert rules_manager._parse_extensions(" , ;; ") == []
    assert rules_manager._parse_words(" Invoice, , Statement of account ") == ["invoice", "statement of account"]

    dialog = make_editor()
    _type(dialog._exts_input, "PDF, .Docx")
    assert dialog._exts_note.text() == "Will use: .pdf, .docx"
    assert dialog.get_rule_data()["extensions"] == [".pdf", ".docx"]
    dialog._exts_input.clear()
    assert dialog._exts_note.text() == "Any file type"


def test_enter_saves_a_finished_rule_and_does_nothing_for_an_unfinished_one(make_editor):
    dialog = make_editor()
    _type(dialog._name_input, "Bank")
    QTest.keyClick(dialog._name_input, Qt.Key.Key_Return)
    assert dialog.result() != QDialog.DialogCode.Accepted

    _type(dialog._folder_input, "Banking")
    _type(dialog._keywords_input, "statement")
    QTest.keyClick(dialog._name_input, Qt.Key.Key_Return)
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_folder_suggestions_are_the_folders_already_inside_downloads(qapp, real_font, tmp_path, monkeypatch):
    import rules_manager

    downloads = tmp_path / "Downloads"
    for sub in ("Documents/PDF", "Documents/Banking", "Projects", "_Duplicates", ".batch_stage"):
        (downloads / sub).mkdir(parents=True)
    (downloads / "notes.txt").write_text("a file, not a folder", encoding="utf-8")
    monkeypatch.setattr(sorter, "watch_root", lambda config: downloads)

    assert rules_manager._known_folders() == ["Documents", "Documents\\Banking", "Documents\\PDF", "Projects"]

    dialog = rules_manager.RuleEditDialog(rule_type="keyword")
    try:
        qapp.processEvents()  # the folders are read just after the dialog opens
        completer = dialog._folder_input.completer()
        assert completer.model().stringList() == ["Documents", "Documents\\Banking", "Documents\\PDF", "Projects"]
        completer.setCompletionPrefix("bank")
        assert completer.completionCount() == 1
        assert completer.currentCompletion() == "Documents\\Banking"
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()


def test_the_editor_does_not_look_at_the_disk_when_given_its_folders(qapp, real_font, monkeypatch):
    import rules_manager

    monkeypatch.setattr(rules_manager, "_known_folders", lambda *a, **k: pytest.fail("should not scan"))
    dialog = rules_manager.RuleEditDialog(rule_type="keyword", known_folders=["Documents", "Pictures"])
    try:
        qapp.processEvents()
        assert dialog._folder_input.completer().model().stringList() == ["Documents", "Pictures"]
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()


# ---------------------------------------------------------------------------
# the Rules page
# ---------------------------------------------------------------------------

@pytest.fixture
def make_window(qapp, real_font, tmp_path, monkeypatch):
    """A factory for main windows holding the rules above, on throwaway settings."""
    import rules_manager

    downloads = tmp_path / "Downloads"
    downloads.mkdir(exist_ok=True)
    config = sorter.get_default_config()
    config["downloads_dir"] = str(downloads)
    config["watch"] = {"folder": str(downloads)}
    config["onboarding_completed"] = True
    config["keyword_rules"] = [dict(rule) for rule in KEYWORD_RULES]
    config["extension_rules"] = [dict(rule) for rule in EXTENSION_RULES]
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setattr(config_manager, "SORTER_CONFIG_FILE", config_file)
    monkeypatch.setattr(config_manager, "ACTIVE_PROJECT_FILE", tmp_path / "active_project.json")
    monkeypatch.setattr(config_manager, "is_sorter_running", lambda: True)
    monkeypatch.setattr(sorter, "is_paused", lambda: False)
    made = []

    def make(initial_view: str = "rules"):
        window = rules_manager.RulesManagerDialog(initial_view=initial_view)
        window.show()
        qapp.processEvents()
        made.append(window)
        return window

    yield make
    for window in made:
        window._set_dirty(False)
        window.close()
        window.deleteLater()
    qapp.processEvents()


@pytest.fixture
def window(make_window):
    return make_window()


def _row_texts(table, column: int) -> list[str]:
    return [table.item(row, column).text() for row in range(table.rowCount())]


def test_rules_made_for_you_carry_a_badge_and_the_others_do_not(window):
    table = window._keyword_table._table
    badges = {table.item(row, 1).text(): table.item(row, 1).data(ui_widgets.BADGE_ROLE)
              for row in range(table.rowCount())}
    assert badges == {"Invoices": None, "Taxes": ("Learned", "success"), "Holiday photos": ("From setup", "info")}
    # The cell's text is still just the name, so nothing that reads the table changes.
    assert _row_texts(table, 1) == ["Invoices", "Taxes", "Holiday photos"]
    assert "becomes your own rule" in table.item(1, 1).toolTip()


def test_the_badge_is_drawn_in_the_name_cell(window, qapp):
    table = window._keyword_table._table
    table.setCurrentCell(-1, -1)
    table.clearSelection()
    qapp.processEvents()

    def right_edge_colors(row: int) -> int:
        rect = table.visualRect(table.model().index(row, 1))
        image = table.viewport().grab(rect).toImage()
        colors = {image.pixel(x, y) for x in range(image.width() - 90, image.width() - 4)
                  for y in range(4, image.height() - 4)}
        return len(colors)

    assert right_edge_colors(1) > right_edge_colors(0) + 2, "the Learned rule has a badge; the plain rule has none"


def test_a_rule_edited_by_hand_loses_its_badge(window):
    table = window._keyword_table._table
    rules = window._keyword_table.get_rules()
    rules[1]["origin"] = "user"
    window._keyword_table.load_rules(rules)
    assert table.item(1, 1).data(ui_widgets.BADGE_ROLE) is None


def test_unsaved_changes_are_called_out_next_to_save(window):
    assert not window._unsaved_chip.isVisibleTo(window)
    assert not window._save_btn.isEnabled()

    window._keyword_table.insert_rule({"name": "Receipts", "folder": "Receipts", "match": ["receipt"],
                                       "extensions": [], "enabled": True}, 0)
    assert window._unsaved_chip.isVisibleTo(window)
    assert window._unsaved_chip.text() == "Unsaved changes"
    assert window._save_btn.isEnabled()

    assert window.save_changes()
    assert not window._unsaved_chip.isVisibleTo(window)


def test_tab_titles_stay_put_and_the_rule_counts_are_in_the_tooltips(window):
    assert [window._tabs.tabText(i) for i in range(2)] == ["Name rules", "File types"]
    assert window._tabs.tabToolTip(0) == "3 rules"
    assert window._tabs.tabToolTip(1) == "1 rule"
    window._keyword_table.insert_rule({"name": "Receipts", "folder": "Receipts", "match": ["receipt"],
                                       "extensions": [], "enabled": True}, 0)
    assert window._tabs.tabToolTip(0) == "4 rules"


# ---- Try a filename ties back to the table ---------------------------------

def test_try_a_filename_selects_the_rule_that_won_on_the_tab_it_belongs_to(window, qapp):
    window._tabs.setCurrentWidget(window._extension_table)
    window._sandbox._input.setText("Invoice_2026.pdf")
    qapp.processEvents()
    assert window._tabs.currentWidget() is window._keyword_table
    assert window._keyword_table._table.currentRow() == 0

    window._sandbox._input.setText("Return_2025_tax.docx")
    qapp.processEvents()
    assert window._keyword_table._table.currentRow() == 1

    window._sandbox._input.setText("beach.PNG")
    qapp.processEvents()
    assert window._tabs.currentWidget() is window._extension_table
    assert window._extension_table._table.currentRow() == 0


def test_the_row_is_not_grabbed_again_while_the_same_rule_keeps_winning(window, qapp):
    window._sandbox._input.setText("Invoice_2026.pdf")
    qapp.processEvents()
    window._keyword_table._table.setCurrentCell(2, 1)  # the person clicked another row
    window._sandbox._input.setText("Invoice_2027.pdf")
    qapp.processEvents()
    assert window._keyword_table._table.currentRow() == 2


def test_nothing_is_selected_when_no_rule_matches(window, qapp):
    window._tabs.setCurrentWidget(window._extension_table)
    window._sandbox._input.setText("zzz_report.unknown")
    qapp.processEvents()
    assert window._tabs.currentWidget() is window._extension_table, "no rule won, so the page stays where it was"


def test_try_a_filename_matches_the_page_by_place_not_only_by_name(window, qapp):
    """Two rules called the same: the one that won is the one that is shown."""
    rules = window._keyword_table.get_rules()
    twin = dict(rules[0], name="Taxes", match=["receipt"], extensions=[])
    rules.append(twin)
    window._keyword_table.load_rules(rules)
    window._on_rules_or_project_changed()
    window._sandbox._input.setText("receipt.txt")
    qapp.processEvents()
    assert window._keyword_table._table.currentRow() == 3


# ---- "Make a rule for this..." ----------------------------------------------

def test_make_a_rule_is_offered_only_when_no_name_rule_decided(window, qapp):
    sandbox = window._sandbox
    button = sandbox._make_rule_btn

    sandbox._input.setText("Invoice_2026.pdf")
    assert not button.isVisibleTo(sandbox), "a name rule matched"
    sandbox._input.setText("zzz_report.unknown")
    assert button.isVisibleTo(sandbox), "nothing matched"
    sandbox._input.setText("beach.PNG")
    assert button.isVisibleTo(sandbox), "only the file type matched, which a name rule could do better"
    sandbox._input.setText("")
    assert not button.isVisibleTo(sandbox)


def test_make_a_rule_adds_it_first_marks_it_the_persons_own_and_checks_the_name_again(window, qapp, monkeypatch):
    import rules_manager

    seen = {}

    def fake_exec(self):
        seen["title"] = self.windowTitle()
        seen["name"] = self._name_input.text()
        seen["words"] = self._keywords_input.text()
        seen["folder"] = self._folder_input.text()
        assert not self._save_btn.isEnabled(), "there is no folder to put it in yet"
        self._folder_input.setText("Reports")
        assert self._save_btn.isEnabled()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(rules_manager.RuleEditDialog, "exec", fake_exec)

    window._sandbox._input.setText("zzz_report.unknown")
    window._sandbox._make_rule_btn.click()
    qapp.processEvents()

    assert seen["title"] == "Make a rule for this file name"
    assert seen["words"] == "zzz" and seen["folder"] == "" and seen["name"]
    rules = window._keyword_table.get_rules()
    assert len(rules) == 4
    assert rules[0]["folder"] == "Reports"
    assert rules[0]["origin"] == "user"
    assert rules[0]["match"] == ["zzz"]
    assert window._tabs.currentWidget() is window._keyword_table
    assert window._unsaved_chip.isVisibleTo(window)
    assert "Save changes" in window._notice.banner.text()
    # The name is checked again at once, and the new rule is the one that wins.
    assert window._sandbox._dest_lbl.text() == "Reports"
    assert not window._sandbox._make_rule_btn.isVisibleTo(window._sandbox)
    assert window._keyword_table._table.currentRow() == 0


def test_cancelling_make_a_rule_changes_nothing(window, qapp, monkeypatch):
    import rules_manager

    monkeypatch.setattr(rules_manager.RuleEditDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    window._sandbox._input.setText("zzz_report.unknown")
    window._sandbox._make_rule_btn.click()
    assert window._keyword_table.rule_count() == 3
    assert not window._unsaved_chip.isVisibleTo(window)


# ---- the sandbox gives room to the table in a short window --------------------

def test_the_examples_and_details_hide_in_a_short_window(window, qapp, monkeypatch):
    import rules_manager

    sandbox = window._sandbox
    monkeypatch.setattr(rules_manager.RulesManagerDialog, "SANDBOX_COMPACT_BELOW", 100_000)
    window.resize(window.width(), window.height() + 1)
    qapp.processEvents()
    assert sandbox.is_compact()
    assert not sandbox._chips_widget.isVisibleTo(sandbox)
    assert not sandbox._detail_widget.isVisibleTo(sandbox)
    assert sandbox._input.isVisibleTo(sandbox), "the box to type in always stays"

    monkeypatch.setattr(rules_manager.RulesManagerDialog, "SANDBOX_COMPACT_BELOW", 1)
    window.resize(window.width(), window.height() + 1)
    qapp.processEvents()
    assert not sandbox.is_compact()
    assert sandbox._chips_widget.isVisibleTo(sandbox)


def test_a_choice_made_with_the_button_beats_the_window_height_and_is_remembered(make_window, qapp, monkeypatch):
    import rules_manager

    first = make_window()
    assert not first._sandbox.is_compact()
    first._sandbox._compact_btn.click()
    assert first._sandbox.is_compact()

    monkeypatch.setattr(rules_manager.RulesManagerDialog, "SANDBOX_COMPACT_BELOW", 1)  # "tall enough"
    first.resize(first.width(), first.height() + 1)
    qapp.processEvents()
    assert first._sandbox.is_compact(), "the person's choice wins over the window's height"

    first.close()
    second = make_window()
    assert second._sandbox.is_compact(), "and it is still their choice the next time"
    second._sandbox._compact_btn.click()
    assert not second._sandbox.is_compact()


def test_alt_up_and_alt_down_move_the_selected_rule(window, qapp):
    table = window._keyword_table._table
    window.activateWindow()
    table.setFocus()
    table.setCurrentCell(1, 1)
    qapp.processEvents()

    def names() -> list[str]:
        return [rule["name"] for rule in window._keyword_table.get_rules()]

    QTest.keyClick(table, Qt.Key.Key_Up, Qt.KeyboardModifier.AltModifier)
    assert names() == ["Taxes", "Invoices", "Holiday photos"]
    assert table.currentRow() == 0, "the rule keeps the selection, so the key can be pressed again"
    assert window._unsaved_chip.isVisibleTo(window)

    QTest.keyClick(table, Qt.Key.Key_Down, Qt.KeyboardModifier.AltModifier)
    QTest.keyClick(table, Qt.Key.Key_Down, Qt.KeyboardModifier.AltModifier)
    assert names() == ["Invoices", "Holiday photos", "Taxes"]
    QTest.keyClick(table, Qt.Key.Key_Down, Qt.KeyboardModifier.AltModifier)
    assert names() == ["Invoices", "Holiday photos", "Taxes"], "the last rule can't go lower"

    QTest.keyClick(table, Qt.Key.Key_Up, Qt.KeyboardModifier.NoModifier)
    assert names() == ["Invoices", "Holiday photos", "Taxes"], "the arrow key alone only moves the selection"


def test_example_chips_that_do_not_fit_are_left_out_instead_of_cut_off(qapp, real_font):
    import rules_manager

    sandbox = rules_manager.LiveSandboxWidget()
    sandbox.resize(380, 260)
    sandbox.show()
    qapp.processEvents()
    try:
        row = sandbox._chips_widget
        row.setFixedWidth(380)
        qapp.processEvents()
        layout = row.layout()
        chips = [layout.itemAt(i).widget() for i in range(1, layout.count()) if layout.itemAt(i).widget()]
        assert len(chips) == 5
        shown = [chip for chip in chips if chip.isVisibleTo(row)]
        assert 0 < len(shown) < len(chips), "some chips are left out of a narrow row"
        assert all(chip.geometry().right() <= row.width() for chip in shown), "and none that is shown is cut off"
        row.setFixedWidth(1600)
        sandbox.resize(1600, 260)
        qapp.processEvents()
        assert all(chip.isVisibleTo(row) for chip in chips), "a wide row shows them all"
    finally:
        sandbox.close()
        sandbox.deleteLater()
        qapp.processEvents()


def test_site_box_suggests_recent_downloaded_domains_after_commas(
    qapp, real_font, tmp_path, monkeypatch
):
    """Issue #25: RuleEditDialog._sites_input suggests learned, rule, and recent download domains."""
    import learning
    import rules_manager

    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    recent_model = downloads / "benchy.3mf"
    recent_model.write_text("x", encoding="utf-8")

    cfg = sorter.get_default_config()
    cfg["downloads_dir"] = str(downloads)
    cfg["watch"] = {"folder": str(downloads)}
    cfg["keyword_rules"] = [
        {"name": "Parts", "folder": "Hardware", "match": ["bolt"], "sites": ["mcmaster.com"], "enabled": True}
    ]
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setattr(config_manager, "SORTER_CONFIG_FILE", cfg_file)
    monkeypatch.setattr(learning, "report", lambda: {"sites": ["makerworld.com", "printables.com"]})
    monkeypatch.setattr(learning, "source_site", lambda path: "github.com" if Path(path).name == "benchy.3mf" else None)

    assert rules_manager._known_sites() == [
        "makerworld.com",
        "printables.com",
        "mcmaster.com",
        "github.com",
    ]

    dialog = rules_manager.RuleEditDialog(rule_type="keyword", known_folders=[])
    try:
        qapp.processEvents()
        completer = dialog._sites_input.completer()
        assert completer.model().stringList() == [
            "makerworld.com",
            "printables.com",
            "mcmaster.com",
            "github.com",
        ]

        # Completing a second domain preserves the existing comma-separated entry.
        dialog._sites_input.setText("makerworld.com, print")
        assert completer.splitPath(dialog._sites_input.text()) == ["print"]
        completer.setCompletionPrefix(dialog._sites_input.text())
        assert completer.completionCount() == 1
        assert completer.currentCompletion() == "makerworld.com, printables.com"
        source_index = completer.completionModel().mapToSource(completer.completionModel().index(0, 0))
        assert source_index.data() == "printables.com"
        assert completer.pathFromIndex(source_index) == "makerworld.com, printables.com"

        # Completing a single entry works directly without extra commas.
        dialog._sites_input.setText("github")
        completer.setCompletionPrefix(dialog._sites_input.text())
        assert completer.completionCount() == 1
        assert completer.currentCompletion() == "github.com"
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()

    monkeypatch.setattr(rules_manager, "_known_sites", lambda *a, **k: pytest.fail("should not scan"))
    preset_dialog = rules_manager.RuleEditDialog(
        rule_type="keyword", known_folders=["Documents"], known_sites=["instructure.com"]
    )
    try:
        qapp.processEvents()
        assert preset_dialog._sites_input.completer().model().stringList() == ["instructure.com"]
    finally:
        preset_dialog.close()
        preset_dialog.deleteLater()
        qapp.processEvents()
