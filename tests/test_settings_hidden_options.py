"""Settings shows the options that used to need the settings file.

The folder to sort, your own names for type folders, folders of yours that should not collect
files, MakerWorld folders by designer, the .3mf hold and its days, and the days of the Desktop sweep.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import config_manager
import sorter

onboarding = pytest.importorskip("onboarding")


@pytest.fixture
def downloads(tmp_path) -> Path:
    root = tmp_path / "Downloads"
    root.mkdir()
    return root.resolve()


@pytest.fixture
def widget(qapp, downloads):
    cfg = sorter.get_default_config()
    cfg["watch"] = {"folder": str(downloads)}
    assert config_manager.save_sorter_config(cfg)
    page = onboarding.SettingsWidget(config=cfg)
    yield page
    from PySide6.QtCore import QEvent
    page.close()
    page.deleteLater()
    qapp.processEvents()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)


def _saved() -> dict:
    return config_manager.load_sorter_config()


# ---- the folder to sort ----

def test_the_page_shows_the_folder_being_sorted(widget, downloads):
    assert widget._watch_folder_lbl.text() == str(downloads)
    assert widget._watch_folder_note.isHidden()


def test_changing_the_folder_saves_it_and_the_sorter_uses_it(widget, tmp_path):
    other = tmp_path / "Inbox"
    other.mkdir()

    assert widget.set_watch_folder(str(other)) is True

    assert sorter.same_folder(sorter.watch_root(_saved()), other)
    assert widget._watch_folder_lbl.text() == str(other.resolve())


def test_the_older_downloads_dir_key_does_not_shadow_the_new_folder(qapp, tmp_path, downloads):
    cfg = sorter.get_default_config()
    cfg["downloads_dir"] = str(downloads)
    cfg.pop("watch", None)
    assert config_manager.save_sorter_config(cfg)
    page = onboarding.SettingsWidget(config=cfg)
    other = tmp_path / "Inbox"
    other.mkdir()
    try:
        page.set_watch_folder(str(other))
        assert sorter.same_folder(sorter.watch_root(_saved()), other)
    finally:
        page.close()


def test_a_missing_folder_is_refused_and_nothing_is_saved(widget, tmp_path, downloads):
    assert widget.set_watch_folder(str(tmp_path / "nowhere")) is False

    assert "not found" in widget._watch_folder_note.text()
    assert not widget._watch_folder_note.isHidden()
    assert sorter.same_folder(sorter.watch_root(_saved()), downloads)


def test_a_whole_drive_and_the_profile_folder_are_refused(tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    (profile / "Downloads").mkdir(parents=True)
    monkeypatch.setenv("USERPROFILE", str(profile))

    assert "drive" in onboarding.watch_folder_problem(Path(tmp_path.anchor))
    assert onboarding.watch_folder_problem(profile) != ""
    assert onboarding.watch_folder_problem(profile / "Downloads") == ""


def test_a_folder_inside_windows_is_refused(tmp_path, monkeypatch):
    windows = tmp_path / "Windows"
    (windows / "Temp").mkdir(parents=True)
    monkeypatch.setenv("SystemRoot", str(windows))

    assert onboarding.watch_folder_problem(windows / "Temp") != ""


def test_going_back_to_downloads_clears_the_folder(widget):
    assert widget.set_watch_folder("") is True

    assert _saved()["watch"]["folder"] == ""
    assert widget._btn_watch_default.isHidden()


def test_a_folder_that_went_missing_is_said_on_the_page(qapp, tmp_path):
    cfg = sorter.get_default_config()
    cfg["watch"] = {"folder": str(tmp_path / "unplugged")}
    assert config_manager.save_sorter_config(cfg)
    page = onboarding.SettingsWidget(config=cfg)
    try:
        assert not page._watch_folder_note.isHidden()
        assert "not found" in page._watch_folder_note.text()
    finally:
        page.close()


# ---- your own names for type folders ----

def test_type_folder_names_are_read_from_plain_text():
    names, bad = onboarding.parse_type_names(".pdf = PDFs, STL=Prints; .f3d = Fusion files")

    assert names == {".pdf": "PDFs", ".stl": "Prints", ".f3d": "Fusion files"}
    assert bad == []


@pytest.mark.parametrize("text", ["pdf PDFs", ".pdf =", "= PDFs", ".pdf = a/b", ".p df = PDFs"])
def test_text_that_is_not_a_name_is_reported_not_saved(text):
    names, bad = onboarding.parse_type_names(text)

    assert names == {} and bad == [text.strip()]


def test_a_new_type_folder_name_is_saved_and_used_by_the_sorter(widget):
    widget._type_names_edit.setText("pdf = PDFs")
    widget._on_type_names_edited()

    assert _saved()["type_folders"]["names"] == {".pdf": "PDFs"}
    assert sorter.type_folder_for(".pdf", _saved()) == "PDFs"
    assert widget._type_names_edit.text() == ".pdf = PDFs"
    assert widget._type_names_note.isHidden()


def test_a_name_that_cannot_be_read_keeps_the_saved_names(widget):
    widget._type_names_edit.setText(".pdf = PDFs")
    widget._on_type_names_edited()
    widget._type_names_edit.setText(".pdf PDFs")
    widget._on_type_names_edited()

    assert _saved()["type_folders"]["names"] == {".pdf": "PDFs"}
    assert not widget._type_names_note.isHidden()


def test_saved_names_are_shown_when_the_page_opens(qapp):
    cfg = sorter.get_default_config()
    cfg["type_folders"] = {"enabled": True, "names": {".stl": "Prints"}}
    cfg["my_folders"] = {"enabled": True, "skip": ["3D Models\\Old"]}
    assert config_manager.save_sorter_config(cfg)
    page = onboarding.SettingsWidget(config=cfg)
    try:
        assert page._type_names_edit.text() == ".stl = Prints"
        assert page._skip_folders_edit.text() == "3D Models\\Old"
    finally:
        page.close()


# ---- folders left out ----

def test_folders_left_out_are_saved_as_a_list(widget):
    widget._skip_folders_edit.setText("3D Models\\Old, Reference\\ ;")
    widget._on_skip_folders_edited()

    assert _saved()["my_folders"]["skip"] == ["3D Models\\Old", "Reference"]
    assert _saved()["my_folders"]["enabled"] is True, "the switch next to it is not touched"


# ---- 3D printing ----

def test_makerworld_by_designer_is_saved(widget):
    assert widget._chk_by_designer.isChecked() is False

    widget._chk_by_designer.setChecked(True)

    assert _saved()["makerworld_by_designer"] is True


def test_the_3mf_hold_and_its_days_are_saved(widget):
    hold = widget._option_checks[("hold", "enabled")]
    assert hold.isChecked() is False, "off unless turned on"

    hold.setChecked(True)
    widget._hold_days_spin.setValue(3)

    saved = _saved()["hold"]
    assert saved["enabled"] is True and saved["hold_days"] == 3
    assert saved["max_held"] == 15, "the other hold settings stay"


# ---- Desktop sweep ----

def test_the_desktop_sweep_days_are_saved(widget):
    widget._option_checks[("desktop_sweep", "enabled")].setChecked(True)
    widget._sweep_days_spin.setValue(14)

    assert _saved()["desktop_sweep"] == {"enabled": True, "days": 14}


def test_days_outside_the_range_in_the_file_do_not_break_the_page(qapp):
    cfg = sorter.get_default_config()
    cfg["desktop_sweep"] = {"enabled": True, "days": "soon"}
    cfg["hold"]["hold_days"] = 9999
    assert config_manager.save_sorter_config(cfg)
    page = onboarding.SettingsWidget(config=cfg)
    try:
        assert page._sweep_days_spin.value() == 7
        assert page._hold_days_spin.value() == 60
    finally:
        page.close()


def test_the_new_boxes_have_names_for_screen_readers(widget):
    for box in (widget._type_names_edit, widget._skip_folders_edit, widget._hold_days_spin, widget._sweep_days_spin):
        assert box.accessibleName()
