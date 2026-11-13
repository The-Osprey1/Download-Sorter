"""The shelf as a search box: type to find any file in the sorted folder, then show, open,
copy, move, put back or send it to a project."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent

import config_manager
import file_index
import learning
import projects_hub
import shelf
import sort_undo
import sorter
import tray
import tray_qt


@pytest.fixture(autouse=True)
def _samples_are_finished_downloading(finished_downloads):
    """These tests are about finding files, not about waiting for downloads to finish."""


@pytest.fixture
def downloads(tmp_path) -> Path:
    root = tmp_path / "Downloads"
    root.mkdir()
    return root.resolve()


@pytest.fixture
def config(downloads, tmp_path, monkeypatch) -> dict:
    cfg = sorter.get_default_config()
    cfg["downloads_dir"] = str(downloads)
    cfg["watch"] = {"folder": str(downloads), "settle_seconds": 0, "min_age_minutes": 0,
                    "auto_unzip": False, "auto_unpack_3d_zips": False}
    cfg["notifications"] = False
    cfg["ribbon_enabled"] = False
    cfg["quick_switcher_enabled"] = False
    cfg["hold"]["enabled"] = False
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setattr(config_manager, "SORTER_CONFIG_FILE", cfg_file)
    monkeypatch.setattr(shelf, "three_d_app_open", lambda: False)
    return cfg


def _file(path: Path, text: str = "data") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    past = time.time() - 120
    os.utime(path, (past, past))
    return path


@pytest.fixture
def window(qapp, downloads, config):
    """A shelf over a small sorted folder: one file the sorter filed, three that were just there."""
    filed = sorter.move_file(_file(downloads / "Quarterly Report.pdf"), config)
    _file(downloads / "Banking" / "chase statement october.pdf")
    _file(downloads / "Banking" / "chase statement september.pdf")
    _file(downloads / "3D Models" / "STL" / "bracket.stl")
    file_index.rebuild(downloads)
    win = shelf._build_shelf_window()
    win.filed = filed
    yield win
    win.close()
    win.deleteLater()
    qapp.processEvents()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)


def _names(win) -> list[str]:
    return [rec["filename"] for rec in win._files]


def _type(win, text: str) -> None:
    win.search_box.setText(text)
    win.search_now()          # what the short typing pause does


# ---------------------------------------------------------------------------
# finding
# ---------------------------------------------------------------------------

def test_an_empty_box_lists_what_was_filed_lately(window):
    assert _names(window) == ["Quarterly Report.pdf"]
    assert window.listbox.item(0).text() == "Quarterly Report.pdf\nPDF · just now"


def test_typing_finds_files_the_sorter_never_touched(window):
    _type(window, "chase")
    assert set(_names(window)) == {"chase statement october.pdf", "chase statement september.pdf"}
    assert "Banking" in window.listbox.item(0).text()
    assert not window.listbox.item(0).icon().isNull()

    _type(window, "chase oct")
    assert _names(window) == ["chase statement october.pdf"]
    assert window.chosen()["dest"].endswith("chase statement october.pdf")

    _type(window, "type:stl")
    assert _names(window) == ["bracket.stl"]


def test_typing_waits_for_a_pause(window, qapp):
    window.search_box.setText("chase")
    assert window._search_timer.isActive()
    assert _names(window) == ["Quarterly Report.pdf"]      # not yet


def test_nothing_found_says_so_and_switches_the_buttons_off(window):
    _type(window, "zebra")
    assert window.listbox.item(0).text() == "No file matches “zebra”."
    assert window.chosen() is None
    assert not window.show_btn.isEnabled() and not window.copy_btn.isEnabled() and not window.more_btn.isEnabled()

    _type(window, "")
    assert _names(window) == ["Quarterly Report.pdf"] and window.show_btn.isEnabled()


def test_arrow_keys_in_the_box_move_through_the_list(window, qapp):
    _type(window, "chase")
    assert window.listbox.currentRow() == 0
    down = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier)
    qapp.sendEvent(window.search_box, down)
    assert window.listbox.currentRow() == 1
    qapp.sendEvent(window.search_box, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier))
    assert window.listbox.currentRow() == 1                # stops at the end
    qapp.sendEvent(window.search_box, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Up, Qt.KeyboardModifier.NoModifier))
    assert window.listbox.currentRow() == 0


def test_enter_in_the_box_shows_the_first_match(window, monkeypatch):
    shown: list[str] = []
    monkeypatch.setattr(shelf, "open_in_explorer", lambda dest: shown.append(Path(dest).name))
    _type(window, "bracket")
    window.search_box.returnPressed.emit()
    assert shown == ["bracket.stl"]


def test_times_read_naturally():
    now = 1_800_000_000.0
    ago = lambda seconds: shelf.how_long_ago(now - seconds, now)  # noqa: E731
    assert [ago(5), ago(300), ago(3 * 3600), ago(30 * 3600), ago(12 * 86400)] == [
        "just now", "5 min ago", "3 h ago", "yesterday", "12 days ago"]
    assert ago(400 * 86400)[-4:].isdigit()
    assert shelf.how_long_ago(None) == "" and shelf.how_long_ago("not a time") == ""


# ---------------------------------------------------------------------------
# doing something with what was found
# ---------------------------------------------------------------------------

def _menu(win) -> dict[str, bool]:
    return {entry[0]: entry[3] for entry in win.menu_entries() if entry}


def test_the_menu_offers_what_fits_the_file(window):
    assert _menu(window) == {"Open": True, "Show in Explorer": True, "Copy file": True, "Copy path": True,
                             "Move to...": True, "Put back where it was": True}
    _type(window, "bracket")       # never filed by the sorter: there is nowhere to put it back
    assert _menu(window)["Put back where it was"] is False


def test_put_back_from_the_shelf(window, downloads):
    result = shelf.put_back(window.chosen())
    assert result[0] is True
    assert (downloads / "Quarterly Report.pdf").exists() and not window.filed.exists()
    window.search_now()
    assert _names(window) == []
    _type(window, "quarterly")     # still findable, where it is now
    assert window.chosen()["folder"] == ""


def test_move_to_from_the_shelf_teaches_the_learner_and_the_list(window, downloads):
    ok, message = shelf.move_to(window.chosen(), downloads / "Work")
    assert ok and message == "Moved Quarterly Report.pdf to Work."
    assert (downloads / "Work" / "Quarterly Report.pdf").exists()
    assert learning.load_model().folders["work"]["examples"] == 1
    _type(window, "quarterly")
    assert [rec["folder"] for rec in window._files] == ["Work"]


def test_a_file_the_sorter_never_filed_can_still_be_moved(window, downloads):
    _type(window, "bracket")
    ok, _message = shelf.move_to(window.chosen(), downloads / "Parts")
    assert ok and (downloads / "Parts" / "bracket.stl").exists()
    assert sort_undo.find_active_move(downloads / "Parts" / "bracket.stl") is not None   # so Undo works


def test_send_to_a_project(window, downloads, tmp_path, monkeypatch):
    project_dir = tmp_path / "Robot Arm"
    project_dir.mkdir()
    project = {"name": "Robot Arm", "path": str(project_dir), "kind": "cad"}
    monkeypatch.setattr(projects_hub, "route_targets", lambda: [project])

    _type(window, "bracket")
    assert _menu(window)["Send to project: Robot Arm"] is True
    window.send_selected(project)

    assert (project_dir / "bracket.stl").exists()
    assert window.heading.text() == "Sent bracket.stl to Robot Arm."
    assert _names(window) == []


def test_a_project_folder_is_found_but_not_moved(window, downloads):
    _file(downloads / "my-tool" / ".git" / "config")
    file_index.rebuild(downloads)
    _type(window, "tool")
    assert "project folder" in window.listbox.item(0).text()
    menu = _menu(window)
    assert menu["Show in Explorer"] is True and menu["Move to..."] is False and menu["Copy file"] is False


# ---------------------------------------------------------------------------
# opened by the tray, in the tray
# ---------------------------------------------------------------------------

def test_the_tray_shows_the_shelf_itself_and_reuses_one_window(window, qapp, monkeypatch):
    monkeypatch.setattr(shelf, "_WINDOW", None)
    shelf.show_in_process()
    first = shelf._WINDOW
    try:
        first.search_box.setText("chase")
        first.search_now()
        shelf.show_in_process()
        assert shelf._WINDOW is first and first.isVisible()
        assert first.search_box.text() == "" and _names(first) == ["Quarterly Report.pdf"]
    finally:
        first.close()
        first.deleteLater()
        monkeypatch.setattr(shelf, "_WINDOW", None)
        qapp.processEvents()


def test_the_shortcut_falls_back_to_its_own_program(monkeypatch):
    spawned: list[tuple] = []
    monkeypatch.setattr(tray, "spawn_window", lambda *args: spawned.append(args))

    tray.open_shelf(None)                                  # no tray icon yet
    tray.open_shelf(tray_qt.Icon("x"))                     # an icon that is not running
    assert spawned == [("--shelf",), ("--shelf",)]

    class Running:
        def run_on_ui(self, work):
            self.work = work
            return True

    running = Running()
    tray.open_shelf(running)
    assert spawned == [("--shelf",), ("--shelf",)] and running.work is tray._show_shelf_here
