"""The windows must fit the sizes they promise.

The main window says it can be 900x560 and the welcome wizard 720x640. When a
page asks for more room than that (a longer list, one more row), the page is
squeezed: the last button in a row is cut off at the edge, or ends up below it.
These checks measure with a real font (Qt's offscreen platform has none, which
would draw every letter as a wide box) and fail when a page outgrows its window.
(The ``real_font`` fixture is in conftest.py.)

There are two kinds of check. The first asks each page how much room it needs
and compares that with the window's own smallest size. The second resizes the
window and looks at where the buttons really ended up, which is what a person
sees: a size that adds up on paper can still leave a button too narrow for its
words.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, QRect  # noqa: E402
from PySide6.QtWidgets import QAbstractButton, QPushButton, QWidget  # noqa: E402

import config_manager  # noqa: E402
import onboarding  # noqa: E402
import sorter  # noqa: E402
import ui_style  # noqa: E402
import ui_widgets  # noqa: E402

# The smallest size the main window lets itself be dragged to. It must stay the
# same as RulesManagerDialog's setMinimumSize (a test below compares the two):
# when this was larger than the window's real minimum, pages that needed up to
# 957 px passed here and were cut off in a 900 px window.
MAIN_LIMIT = (900, 560)
# Sizes to look at the buttons in: the smallest, a little taller, and the size the window opens at.
MAIN_SIZES = (MAIN_LIMIT, (900, 640), (1040, 760))
WIZARD_LIMIT = (720, 640)
SIDEBAR_WIDTH = 216


def _config(downloads: Path) -> dict:
    cfg = sorter.get_default_config()
    cfg["downloads_dir"] = str(downloads)
    cfg["watch"] = {"folder": str(downloads)}
    cfg["onboarding_completed"] = True
    return cfg


@pytest.fixture
def window(qapp, real_font, tmp_path, monkeypatch):
    import rules_manager

    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(_config(downloads)), encoding="utf-8")
    monkeypatch.setattr(config_manager, "SORTER_CONFIG_FILE", cfg_file)
    monkeypatch.setattr(config_manager, "ACTIVE_PROJECT_FILE", tmp_path / "active_project.json")
    monkeypatch.setattr(config_manager, "is_sorter_running", lambda: True)
    dlg = rules_manager.RulesManagerDialog(initial_view="home")
    dlg.show()
    qapp.processEvents()
    yield dlg
    dlg._set_dirty(False)
    dlg.close()
    dlg.deleteLater()
    qapp.processEvents()


def test_the_limit_checked_here_is_the_windows_real_minimum(window):
    assert (window.minimumWidth(), window.minimumHeight()) == MAIN_LIMIT, (
        "MAIN_LIMIT and the window's setMinimumSize must be the same size, or pages are checked against "
        "a window larger than the smallest one people can have")


def test_every_main_window_page_fits_the_promised_size(window):
    problems = []
    for index, page in enumerate(window.PAGES):
        hint = window._view_stack.widget(index).minimumSizeHint()
        if hint.height() > MAIN_LIMIT[1] or hint.width() > MAIN_LIMIT[0] - SIDEBAR_WIDTH:
            problems.append(f"{page} needs {hint.width()}x{hint.height()}")
    assert not problems, "pages outgrew the window: " + "; ".join(problems)

    whole = window.minimumSizeHint()
    assert whole.height() <= MAIN_LIMIT[1], f"the window needs {whole.height()} px of height"
    assert whole.width() <= MAIN_LIMIT[0], f"the window needs {whole.width()} px of width"


@pytest.mark.parametrize("own_folders", [True, False], ids=["with-folders", "no-folders"])
def test_welcome_wizard_fits_the_promised_size(qapp, real_font, tmp_path, own_folders):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    if own_folders:
        for folder, names in {"Invoices": ["invoice_march.pdf"], "IME 4030": ["lecture 3.pdf"],
                              "Music": ["a.mp3"], "Banking": ["statement_may.pdf"]}.items():
            (downloads / folder).mkdir()
            for name in names:
                (downloads / folder / name).write_text("x", encoding="utf-8")
    wizard = onboarding.WelcomeWizardDialog(config=_config(downloads))
    try:
        wizard.show()
        qapp.processEvents()
        hint = wizard.minimumSizeHint()
        assert hint.height() <= WIZARD_LIMIT[1], f"the wizard needs {hint.height()} px of height"
        assert hint.width() <= WIZARD_LIMIT[0], f"the wizard needs {hint.width()} px of width"
    finally:
        wizard.close()
        wizard.deleteLater()
        qapp.processEvents()


def test_clean_up_after_a_scan_of_many_long_folders_still_fits_the_promised_size(window, qapp, tmp_path):
    import remodel

    long_name = "A folder with a very long name that goes on and on "
    cards = [
        remodel.FolderCard(tmp_path / f"{long_name * 3}{index}", "keep_intact", 5, 1.2,
                           remodel.ProtectionCheck(True, "Python virtual environment detected " * 3), {})
        for index in range(12)
    ]
    plan = remodel.RemodelPlan(
        remodel_id="fit", target_root=tmp_path, created_at="2026-01-01T00:00:00Z", cards=cards,
        loose_files_to_organize=[], duplicates_to_quarantine=[], protected_items=[],
        total_files_scanned=60, reclaimable_duplicate_bytes=0,
    )
    window.switch_view("cleanup")
    window._remodel_widget._on_scan_finished(plan)
    qapp.processEvents()

    page = window._view_stack.widget(window.PAGES.index("cleanup"))
    hint = page.minimumSizeHint()
    assert hint.height() <= MAIN_LIMIT[1], f"the Clean up page needs {hint.height()} px of height after a scan"
    assert hint.width() <= MAIN_LIMIT[0] - SIDEBAR_WIDTH, f"the Clean up page needs {hint.width()} px of width"
    assert window.minimumSizeHint().height() <= MAIN_LIMIT[1]


def test_rules_page_with_many_long_rules_still_fits_the_promised_size(window, qapp):
    rules = [
        {"name": f"A rule with a rather long name number {index}", "folder": "Documents\\" + "Deeply\\Nested\\" * 3,
         "match": ["alpha", "beta", "gamma", "delta"] * 3, "extensions": [".pdf", ".docx", ".xlsx", ".pptx"],
         "enabled": True, "origin": "learned" if index % 2 else "scan"}
        for index in range(30)
    ]
    window._keyword_table.load_rules(rules)
    window.switch_view("rules")
    window.resize(window.minimumWidth(), window.height())  # as narrow as the window can be
    qapp.processEvents()

    page = window._view_stack.widget(window.PAGES.index("rules"))
    hint = page.minimumSizeHint()
    assert hint.height() <= MAIN_LIMIT[1], f"the Rules page needs {hint.height()} px of height"
    assert hint.width() <= MAIN_LIMIT[0] - SIDEBAR_WIDTH, f"the Rules page needs {hint.width()} px of width"
    table = window._keyword_table._table
    assert table.columnWidth(3) >= 80, "the words column keeps room to read, even in the narrowest window"
    assert not table.horizontalScrollBar().isVisible(), "the table fits its page without a sideways scroll bar"


# ---------------------------------------------------------------------------
# where the buttons really end up
# ---------------------------------------------------------------------------

def _in_window(widget: QWidget, window: QWidget) -> QRect:
    return QRect(widget.mapTo(window, QPoint(0, 0)), widget.size())


def _button_problems(window: QWidget, page: QWidget, whole: tuple[QAbstractButton, ...] = ()) -> list[str]:
    """Every button on ``page`` that is too narrow for its words and icon, or sticks out sideways.

    The buttons in ``whole`` must also be inside the window from top to bottom
    (other buttons may sit in a list that scrolls, so only their sides are checked).
    """
    problems = []
    page_box = _in_window(page, window)
    for button in page.findChildren(QAbstractButton):
        if not button.isVisibleTo(window):
            continue
        name = button.text() or button.toolTip() or button.accessibleName() or type(button).__name__
        box = _in_window(button, window)
        # Icon-only buttons have a fixed size of their own; the ones with words must have room for them.
        needs = button.minimumSizeHint().width() if isinstance(button, QPushButton) and button.text() else 0
        if button.width() < needs:
            problems.append(f"'{name}' is {button.width()} px wide and needs {needs}")
        if box.left() < page_box.left() or box.right() > page_box.right():
            problems.append(f"'{name}' sticks out of the page sideways ({box.left()}..{box.right()} "
                            f"in {page_box.left()}..{page_box.right()})")
        parent_box = _in_window(button.parentWidget(), window)
        if box.left() < parent_box.left() or box.right() > parent_box.right():
            problems.append(f"'{name}' sticks out of the box it is in")
        if button in whole:
            # Top to bottom too, and in every box around it: a card that was squeezed
            # shorter than its rows cuts the lowest button off at its own edge.
            around = button.parentWidget()
            while around is not None:
                if not _in_window(around, window).contains(box):
                    problems.append(f"'{name}' is cut off by the {type(around).__name__} around it "
                                    f"({box.top()}..{box.bottom()} in "
                                    f"{_in_window(around, window).top()}..{_in_window(around, window).bottom()})")
                    break
                around = None if around is window else around.parentWidget()
    for index, first in enumerate(whole):
        for second in whole[index + 1:]:
            if _in_window(first, window).intersects(_in_window(second, window)):
                problems.append(f"'{first.text()}' and '{second.text()}' overlap")
    return problems


HOME_STATES = {
    # name: (sorter running, paused, files waiting)
    "running": (True, False, 0),
    "paused": (True, True, 0),
    "stopped": (False, False, 0),
    "2-files-waiting": (True, False, 2),
    "12345-files-waiting": (True, False, 12345),
    "stopped-with-files-waiting": (False, False, 128),
}


@pytest.mark.parametrize("size", MAIN_SIZES, ids=lambda size: f"{size[0]}x{size[1]}")
@pytest.mark.parametrize("state", HOME_STATES)
def test_home_action_buttons_are_whole_in_every_state(window, qapp, monkeypatch, size, state):
    running, paused, waiting = HOME_STATES[state]
    monkeypatch.setattr(config_manager, "is_sorter_running", lambda: running)
    monkeypatch.setattr(sorter, "is_paused", lambda: paused)
    home = window._home_widget
    qapp.processEvents()
    home.refresh_status()
    home._preview_generation += 1  # a count still on its way from the background is dropped
    home._on_preview_ready(waiting, home._preview_generation)
    window.resize(*size)
    for _ in range(3):
        qapp.processEvents()

    assert (window.width(), window.height()) == size, "the window was pushed larger than asked"
    assert home._btn_pause_resume.text() == {"paused": "Resume sorting", "stopped": "Start sorting"}.get(
        state.split("-")[0], "Pause sorting")
    assert (str(waiting) in home._btn_sort_now.text()) == (waiting > 0)
    assert home._waiting_lbl.isVisible() == (waiting > 0)

    actions = (home._btn_sort_now, home._btn_undo_last, home._btn_pause_resume, home._btn_open_downloads)
    assert all(button.isVisibleTo(window) for button in actions)
    page = window._view_stack.currentWidget()
    problems = _button_problems(window, page, whole=actions)
    assert not problems, f"Home at {size[0]}x{size[1]}, {state}: " + "; ".join(problems)
    for button in actions:  # the words themselves, not only the button's own idea of its size
        words = button.fontMetrics().horizontalAdvance(button.text()) + button.iconSize().width()
        assert button.width() >= words, f"'{button.text()}' is cut off"
    # In the window's opening size everything is on one row, with Open Downloads at the right edge.
    if size[0] >= 1040:
        assert len({button.y() for button in actions}) == 1


PAGES = ("home", "rules", "cleanup", "settings", "projects")
# Settings lists its options as check boxes with long labels, and a check box cannot wrap its
# words. In a 900 px window the longest ones push the whole page about 60 px past the right
# edge (it scrolls sideways). That was there before these checks and needs a check box that
# wraps, which is its own change: see SETTINGS_ISSUE. strict=True makes this fail the day it
# is fixed, so the mark gets removed.
SETTINGS_ISSUE = "https://github.com/The-Osprey1/Download-Sorter/issues/54"


def _page_cases():
    for size in MAIN_SIZES:
        for page in PAGES:
            known = page == "settings" and size[0] < 1040
            marks = [pytest.mark.xfail(strict=True, reason=f"Settings is wider than a 900 px window: {SETTINGS_ISSUE}")]
            yield pytest.param(size, page, id=f"{page}-{size[0]}x{size[1]}", marks=marks if known else [])


def test_the_pages_checked_here_are_all_the_pages(window):
    assert window.PAGES == PAGES


@pytest.mark.parametrize("size, page", _page_cases())
def test_no_page_cuts_a_button_off_sideways(window, qapp, size, page):
    window.resize(*size)
    window.switch_view(page)
    for _ in range(3):
        qapp.processEvents()
    assert (window.width(), window.height()) == size, "the window was pushed larger than asked"
    problems = _button_problems(window, window._view_stack.currentWidget())
    assert not problems, f"{page} at {size[0]}x{size[1]}: " + "; ".join(problems)


def test_clean_up_with_another_folder_chosen_still_fits(window, qapp):
    """Choosing "Another folder..." adds a path box and a Browse button to the row."""
    window.switch_view("cleanup")
    cleanup = window._remodel_widget
    cleanup._scope_combo.setCurrentIndex(cleanup._scope_combo.count() - 1)
    window.resize(*MAIN_LIMIT)
    for _ in range(3):
        qapp.processEvents()
    assert cleanup._browse_btn.isVisibleTo(window)
    page = window._view_stack.currentWidget()
    assert page.minimumSizeHint().width() <= MAIN_LIMIT[0] - SIDEBAR_WIDTH
    problems = _button_problems(window, page, whole=(cleanup._browse_btn, cleanup._scan_btn))
    assert not problems, "; ".join(problems)


# ---------------------------------------------------------------------------
# the row that wraps
# ---------------------------------------------------------------------------

@pytest.fixture
def flow_row(qapp, real_font):
    host = QWidget()
    host.setStyleSheet(ui_style.stylesheet())
    row = ui_widgets.FlowLayout(host, spacing=8, push_last=True)
    buttons = [ui_style.button(text)
               for text in ("Sort Downloads now", "Undo last sort", "Pause sorting", "Open Downloads")]
    for button in buttons:
        row.addWidget(button)
    host.show()
    qapp.processEvents()
    yield host, row, buttons
    host.close()
    host.deleteLater()
    qapp.processEvents()


def test_flow_row_keeps_one_row_while_it_fits_and_pushes_the_last_widget_right(flow_row, qapp):
    host, row, buttons = flow_row
    one_row = row.sizeHint()
    host.resize(one_row.width() + 120, one_row.height())
    qapp.processEvents()
    assert len({button.y() for button in buttons}) == 1
    assert buttons[-1].geometry().right() == host.width() - 1, "the last widget sits at the right edge"
    assert buttons[2].geometry().right() < buttons[-1].x() - 100, "the spare room is before the last widget"
    assert row.heightForWidth(host.width()) == one_row.height()


def test_flow_row_wraps_instead_of_squeezing(flow_row, qapp):
    host, row, buttons = flow_row
    one_row = row.sizeHint()
    narrow = one_row.width() - 20  # one button too many
    assert row.minimumSize().width() <= max(button.sizeHint().width() for button in buttons)
    assert row.heightForWidth(narrow) == 2 * one_row.height() + row.spacing()
    host.resize(narrow, row.heightForWidth(narrow))
    qapp.processEvents()
    assert [button.y() for button in buttons[:3]] == [buttons[0].y()] * 3
    assert buttons[3].y() > buttons[0].y() and buttons[3].x() == buttons[0].x(), "the last one starts a second row"
    assert all(button.width() == button.sizeHint().width() for button in buttons), "no button was made narrower"
    assert [row.itemAt(index).widget() for index in range(row.count())] == buttons, "the order is kept"


def test_flow_row_gives_a_hidden_widget_no_room(flow_row, qapp):
    host, row, buttons = flow_row
    full = row.sizeHint().width()
    buttons[1].hide()
    qapp.processEvents()
    assert row.sizeHint().width() == full - buttons[1].sizeHint().width() - row.spacing()
