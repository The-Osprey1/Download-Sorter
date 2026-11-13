"""The main window's shell and everyday workflows.

Shell: it is a real window (minimize, maximize, icon, remembers its size), Esc
asks before dropping unsaved rules, messages appear inline instead of in
pop-up boxes, and the sidebar shows the sorter's state and what needs attention.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

import about
import config_manager
import sorter


def _config(downloads: Path) -> dict:
    cfg = sorter.get_default_config()
    cfg["downloads_dir"] = str(downloads)
    cfg["watch"] = {"folder": str(downloads)}
    cfg["onboarding_completed"] = True
    return cfg


@pytest.fixture
def make_window(qapp, real_font, tmp_path, monkeypatch):
    """A factory for main windows on throwaway settings; every window it made is closed afterwards."""
    import rules_manager

    downloads = tmp_path / "Downloads"
    downloads.mkdir(exist_ok=True)
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(_config(downloads)), encoding="utf-8")
    monkeypatch.setattr(config_manager, "SORTER_CONFIG_FILE", cfg_file)
    monkeypatch.setattr(config_manager, "ACTIVE_PROJECT_FILE", tmp_path / "active_project.json")
    monkeypatch.setattr(config_manager, "is_sorter_running", lambda: True)
    monkeypatch.setattr(sorter, "is_paused", lambda: False)
    made = []

    def make(initial_view: str = "home"):
        dlg = rules_manager.RulesManagerDialog(initial_view=initial_view)
        dlg.show()
        qapp.processEvents()
        made.append(dlg)
        return dlg

    yield make
    for dlg in made:
        dlg._set_dirty(False)
        dlg.close()
        dlg.deleteLater()
    qapp.processEvents()


@pytest.fixture
def window(make_window):
    return make_window()


# ---------------------------------------------------------------------------
# it is a real window
# ---------------------------------------------------------------------------

def test_window_can_be_minimized_and_maximized_and_has_an_icon(window):
    flags = window.windowFlags()
    assert flags & Qt.WindowType.WindowMinimizeButtonHint
    assert flags & Qt.WindowType.WindowMaximizeButtonHint
    assert not window.windowIcon().isNull()


def test_window_is_allowed_to_be_short_enough_for_a_scaled_laptop_screen(window):
    assert window.minimumHeight() <= 580


def test_window_remembers_its_size(make_window):
    """Only the height is compared: the test screen is 800 px wide, and Qt keeps a restored window on screen."""
    first = make_window()
    first.resize(first.width(), 650)
    first.close()
    saved = (sorter.LOG_DIR / "window.ini").read_text(encoding="utf-8")
    assert "geometry" in saved

    second = make_window()
    assert second.height() == 650


def test_a_leftover_window_does_not_overwrite_the_saved_size(make_window):
    """A new window closes any leftover one; that must not replace the size you left the app at."""
    first = make_window()
    first.resize(first.width(), 650)
    first.close()
    leftover = make_window()  # starts at the remembered size...
    leftover.resize(leftover.width(), 700)  # ...and is then left open and forgotten

    third = make_window()

    assert not leftover.isVisible(), "opening a window tidies up the leftover one"
    assert third.height() == 650


def test_a_broken_window_file_falls_back_to_the_default_size(make_window):
    (sorter.LOG_DIR).mkdir(parents=True, exist_ok=True)
    (sorter.LOG_DIR / "window.ini").write_text("[General]\ngeometry=not-a-geometry\n", encoding="utf-8")
    window = make_window()
    assert window.isVisible() and window.width() > 0 and window.height() > 0


def test_escape_asks_before_dropping_unsaved_rules(window, monkeypatch):
    asked = []
    monkeypatch.setattr(window, "ask_to_save_changes", lambda: asked.append(1) or "cancel")
    window._set_dirty(True)

    QTest.keyClick(window, Qt.Key.Key_Escape)

    assert asked == [1], "Esc went around the unsaved-rules question"
    assert window.isVisible()


def test_escape_closes_the_window_when_nothing_is_unsaved(window, monkeypatch):
    monkeypatch.setattr(window, "ask_to_save_changes", lambda: pytest.fail("nothing to save"))
    window._set_dirty(False)

    QTest.keyClick(window, Qt.Key.Key_Escape)

    assert not window.isVisible()


def test_escape_saves_when_asked_to(window, monkeypatch):
    saved = []
    monkeypatch.setattr(window, "ask_to_save_changes", lambda: "save")
    monkeypatch.setattr(window, "save_changes", lambda: saved.append(1) or window._set_dirty(False) or True)
    window._set_dirty(True)

    QTest.keyClick(window, Qt.Key.Key_Escape)

    assert saved == [1] and not window.isVisible()


# ---------------------------------------------------------------------------
# messages appear inline
# ---------------------------------------------------------------------------

def test_notice_bar_shows_a_message_with_an_action_then_goes_away(window, qapp):
    assert not window._notice.isVisible()
    clicked = []
    window.notify("Rules added.", "success", "Open Rules", lambda: clicked.append(1))
    qapp.processEvents()

    assert window._notice.isVisible()
    assert window._notice.banner.text() == "Rules added."
    assert window._notice.banner.kind() == "success"
    assert window._notice.banner.action_text() == "Open Rules"

    window._notice.banner._action_btn.click()
    assert clicked == [1]
    assert not window._notice.isVisible(), "the bar takes no room once its message is gone"


def test_resetting_rules_reports_inline_instead_of_in_a_pop_up(window, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: pytest.fail("an information pop-up box appeared")))

    window.reset_to_defaults()

    assert "reset" in window._notice.banner.text().lower()
    assert "Save changes" in window._notice.banner.text()


# ---------------------------------------------------------------------------
# sidebar
# ---------------------------------------------------------------------------

def test_footer_credit_fits_on_two_lines_without_wrapping(window):
    label = window._credit_lbl
    assert not label.wordWrap()
    assert about.__version__ in label.text() and about.CREDIT in label.text()
    assert label.text().count("\n") == 1
    assert label.sizeHint().width() <= window._sidebar.width() - 28, "the credit line is wider than the sidebar"


def test_status_chip_follows_home_and_opens_it(window, monkeypatch, qapp):
    window.switch_view("settings")
    assert "on" in window._sorter_status_pill.text().lower()

    monkeypatch.setattr(sorter, "is_paused", lambda: True)
    window._home_widget.refresh_status()
    assert window._sorter_status_pill.text() == "Sorting is paused"

    window._status_chip.clicked.emit()
    assert window.current_page() == "home"


def test_nav_dot_on_rules_while_changes_are_unsaved(window, qapp):
    dot = window._nav_dots["rules"]
    assert not dot.isVisible()
    assert window._nav_buttons["rules"].toolTip() == "Rules (Ctrl+2)"

    window._set_dirty(True)
    qapp.processEvents()
    assert dot.isVisible()
    assert "unsaved" in window._nav_buttons["rules"].toolTip().lower()

    window._set_dirty(False)
    qapp.processEvents()
    assert not dot.isVisible()
    assert window._nav_buttons["rules"].toolTip() == "Rules (Ctrl+2)"


def test_nav_dot_on_home_when_an_update_is_waiting_and_you_are_elsewhere(window, qapp):
    dot = window._nav_dots["home"]
    window._home_widget.update_available.emit(True)
    qapp.processEvents()
    assert not dot.isVisible(), "you are looking at Home, where the update message is"

    window.switch_view("rules")
    qapp.processEvents()
    assert dot.isVisible()

    window.switch_view("home")
    qapp.processEvents()
    assert not dot.isVisible()


# ---------------------------------------------------------------------------
# a rule made on Home shows up on the Rules page
# ---------------------------------------------------------------------------

def _add_report_rule():
    import refile

    refile.add_rule_first({"name": "Report files", "folder": "Reports", "match": ["report"],
                           "extensions": [".pdf"], "enabled": True, "origin": "user"})


def test_a_rule_made_on_home_is_on_the_rules_page_when_you_open_it(window, qapp):
    window.switch_view("rules")
    window.switch_view("home")
    _add_report_rule()

    window._home_widget.navigate_requested.emit("rules")
    qapp.processEvents()

    assert window.current_page() == "rules"
    assert window._keyword_table.get_rules()[0]["name"] == "Report files"
    assert not window._dirty


def test_unsaved_rules_are_not_replaced_when_the_rules_page_opens(window):
    window.switch_view("rules")
    window._set_dirty(True)
    _add_report_rule()

    window.switch_view("home")
    window.switch_view("rules")

    assert "Report files" not in [r["name"] for r in window._keyword_table.get_rules()]
    assert window._dirty


def test_sidebar_selection_bar_and_projects_toggle(window, qapp):
    import ui_style

    window._show_projects_nav(True)
    qapp.processEvents()
    for target in ("home", "rules", "cleanup", "projects", "settings"):
        window.switch_view(target)
        qapp.processEvents()
        for page_id, bar in window._nav_bars.items():
            assert bar.isVisible() is (page_id == target)

    # Hiding Projects while it is open switches back to Home and keeps its indicator in sync.
    window.switch_view("projects")
    window._show_projects_nav(False)
    qapp.processEvents()
    assert window.current_page() == "home"
    assert window._nav_buttons["projects"].isHidden()
    assert window._nav_bars["home"].isVisible()
    assert not window._nav_bars["projects"].isVisible()

    # Check non-color-only, hover, focus, and 24 px title rules in the stylesheet.
    qss = ui_style.stylesheet()
    assert 'QLabel[role="title"] { font-size: 24px; font-weight: 600; }' in qss
    assert 'QPushButton[role="nav"]:checked {' in qss
    assert 'font-weight: 600;' in qss
    assert 'QPushButton[role="nav"]:focus {' in qss
    assert 'QPushButton[role="nav"]:checked:focus {' in qss
    assert 'QFrame[role="navBar"] {' in qss


def test_rules_header_with_24px_title_and_unsaved_chip_fits_at_900px(window, qapp):
    window.resize(900, 560)
    window.switch_view("rules")
    window._set_dirty(True)
    for _ in range(3):
        qapp.processEvents()

    page = window._view_stack.currentWidget()
    widgets = [window._preset_lib_btn, window._more_btn, window._unsaved_chip, window._save_btn]
    for widget in widgets:
        assert widget.isVisibleTo(page)
        top_left = widget.mapTo(page, widget.rect().topLeft())
        assert top_left.x() >= 0 and top_left.x() + widget.width() <= page.width()
    tops = [w.mapTo(page, w.rect().topLeft()).y() for w in widgets]
    assert max(tops) - min(tops) <= 10, f"header actions wrapped onto multiple rows: tops={tops}"
    # No overlap between consecutive action controls on the header row.
    rights = [w.mapTo(page, w.rect().topLeft()).x() + w.width() for w in widgets[:-1]]
    lefts = [w.mapTo(page, w.rect().topLeft()).x() for w in widgets[1:]]
    for r, l in zip(rights, lefts):
        assert r < l, f"adjacent header controls touched/overlapped: {r} >= {l}"
