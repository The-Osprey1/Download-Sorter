"""The default watched folder is wherever Windows says Downloads is, not always
``%USERPROFILE%\\Downloads`` (people move it to another drive, and OneDrive can too)."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

from download_sorter import paths as paths_module
from download_sorter.config import Config, display_watch_folder, stored_watch_folder
from download_sorter.paths import DEFAULT_WATCH_FOLDER


@pytest.fixture
def moved_downloads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    moved = tmp_path / "D_drive" / "Downloads"
    moved.mkdir(parents=True)
    monkeypatch.setattr(paths_module, "known_downloads_dir", lambda: moved)
    return moved


def test_default_watch_folder_follows_the_moved_downloads_folder(moved_downloads: Path) -> None:
    cfg = Config()
    assert cfg.watch_folder == DEFAULT_WATCH_FOLDER
    assert cfg.resolved_watch_folder() == moved_downloads.resolve()


def test_a_folder_the_user_chose_is_kept(moved_downloads: Path, tmp_path: Path) -> None:
    chosen = tmp_path / "Inbox"
    chosen.mkdir()
    cfg = Config(watch_folder=str(chosen))
    assert cfg.resolved_watch_folder() == chosen.resolve()


def test_settings_show_the_real_downloads_folder(moved_downloads: Path) -> None:
    assert display_watch_folder(DEFAULT_WATCH_FOLDER) == str(moved_downloads)


def test_saving_the_shown_downloads_folder_keeps_following_windows(moved_downloads: Path) -> None:
    # Saving Settings without touching the folder must not pin today's location.
    assert stored_watch_folder(str(moved_downloads)) == DEFAULT_WATCH_FOLDER
    assert stored_watch_folder("") == DEFAULT_WATCH_FOLDER


def test_saving_a_different_folder_stores_that_folder(moved_downloads: Path, tmp_path: Path) -> None:
    other = tmp_path / "Elsewhere"
    assert stored_watch_folder(str(other)) != DEFAULT_WATCH_FOLDER


@pytest.mark.skipif(sys.platform != "win32", reason="asks Windows for its Downloads folder")
def test_windows_reports_an_existing_downloads_folder() -> None:
    found = paths_module.known_downloads_dir()
    assert found is not None
    assert found.is_dir()


def test_no_known_folder_outside_windows() -> None:
    if sys.platform == "win32":
        pytest.skip("Windows always has one")
    assert paths_module.known_downloads_dir() is None
