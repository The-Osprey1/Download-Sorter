"""The tray acts on the settings as they are saved now, not as they were when the tray started.

What went wrong: ``SorterTray`` read the settings once, in its constructor. The tray starts with Windows
and runs for days, and the window saves changes to the file. After a change of folder, Sort Downloads now
and Open Downloads still used the folder the tray had started with. (The watcher reloads its settings on
its own; only the menu did not.)

The settings are read afresh when a menu item acts. When the file cannot be read right now, the tray keeps
the settings it has: ``sorter.load_config`` would answer with the default settings, which name another
folder and other rules.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

import sorter
import tray


@pytest.fixture
def folders(tmp_path) -> tuple[Path, Path]:
    first, second = tmp_path / "First", tmp_path / "Second"
    first.mkdir()
    second.mkdir()
    return first, second


@pytest.fixture
def save_settings():
    """Save settings the way the window does: a whole settings file that names the folder to sort."""

    def save(folder: Path, **changes) -> dict:
        config = sorter.get_default_config()
        config["downloads_dir"] = str(folder)
        config["watch"] = dict(config.get("watch", {}), folder=str(folder))
        config.update(changes)
        sorter.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        sorter.CONFIG_PATH.write_text(json.dumps(config), encoding="utf-8")
        return config

    return save


@pytest.fixture
def opened(monkeypatch) -> list:
    """What Open Downloads would have opened in File Explorer."""
    seen: list = []
    monkeypatch.setattr(tray, "open_in_explorer", seen.append)
    return seen


def test_open_downloads_follows_a_folder_changed_after_the_tray_started(folders, save_settings, opened):
    first, second = folders
    save_settings(first)
    icon = tray.SorterTray()

    icon.on_open_downloads()
    assert opened == [first.resolve()]

    save_settings(second)  # changed in Settings while the tray keeps running
    icon.on_open_downloads()

    assert opened == [first.resolve(), second.resolve()]


def test_sort_now_uses_the_settings_saved_after_the_tray_started(folders, save_settings, monkeypatch):
    first, second = folders
    save_settings(first)
    icon = tray.SorterTray()
    save_settings(second, default_folder="Elsewhere")
    seen, done = [], threading.Event()
    monkeypatch.setattr(tray, "sort_existing", lambda config, **kwargs: seen.append(config) or 0)
    monkeypatch.setattr(tray, "sort_existing_documents", lambda config, **kwargs: 0)
    icon.notify = lambda *args, **kwargs: done.set()

    icon.on_sort_now()

    assert done.wait(10)
    assert len(seen) == 1
    assert seen[0]["watch"]["folder"] == str(second), "the folder it was changed to"
    assert seen[0]["default_folder"] == "Elsewhere", "and the rest of the new settings"


def _half_written(path: Path) -> None:
    path.write_text('{"watch": {"folder": ', encoding="utf-8")


def _missing(path: Path) -> None:
    path.unlink()


def _a_list(path: Path) -> None:
    path.write_text("[1, 2, 3]", encoding="utf-8")


@pytest.mark.parametrize("spoil", [_half_written, _missing, _a_list], ids=["half written", "missing", "not settings"])
def test_settings_that_cannot_be_read_right_now_leave_the_tray_as_it_was(
    folders, save_settings, opened, monkeypatch, spoil
):
    first, _second = folders
    save_settings(first)
    icon = tray.SorterTray()
    spoil(sorter.CONFIG_PATH)

    icon.on_open_downloads()

    assert opened == [first.resolve()], "the folder it had, not the default Downloads folder"

    seen, done = [], threading.Event()
    monkeypatch.setattr(tray, "sort_existing", lambda config, **kwargs: seen.append(config) or 0)
    monkeypatch.setattr(tray, "sort_existing_documents", lambda config, **kwargs: 0)
    icon.notify = lambda *args, **kwargs: done.set()
    icon.on_sort_now()
    assert done.wait(10)
    assert seen[0]["watch"]["folder"] == str(first), "Sort now keeps it too"


def test_read_saved_config_gives_the_saved_settings_with_the_defaults_filled_in():
    sorter.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    sorter.CONFIG_PATH.write_text(json.dumps({"watch": {"folder": "Somewhere"}}), encoding="utf-8")

    config = sorter.read_saved_config()

    assert config["watch"]["folder"] == "Somewhere"
    assert config["schema_version"] == 2, "what a settings file that is missing blocks gets, as load_config gives it"


@pytest.mark.parametrize("spoil", [_half_written, _missing, _a_list], ids=["half written", "missing", "not settings"])
def test_read_saved_config_says_none_when_the_settings_cannot_be_read(spoil):
    sorter.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    sorter.CONFIG_PATH.write_text("{}", encoding="utf-8")
    spoil(sorter.CONFIG_PATH)

    assert sorter.read_saved_config() is None
