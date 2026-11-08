"""The Home page shows what the sorter is doing, as it happens.

Turning sorting on used to change one word and nothing else, so it didn't feel
like anything happened. Home now says what it is watching, counts files that
are still waiting, reports what each action did ("Sorted 3 files into 2
folders"), and picks up files the background sorter files without a click.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

import config_manager
import onboarding
import sorter


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


def _wait(qapp, condition, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()
    assert condition(), "timed out waiting for the Home page"


@pytest.fixture(autouse=True)
def _samples_are_finished_downloading(finished_downloads):
    """These tests are about what Home shows, not about waiting for downloads."""


@pytest.fixture
def downloads(tmp_path) -> Path:
    folder = tmp_path / "Downloads"
    folder.mkdir()
    return folder


@pytest.fixture
def home(qapp, downloads, monkeypatch):
    cfg = _config(downloads)
    monkeypatch.setattr(config_manager, "load_sorter_config", lambda *a, **k: cfg)
    monkeypatch.setattr(config_manager, "is_sorter_running", lambda: True)
    monkeypatch.setattr(sorter, "is_paused", lambda: False)
    widget = onboarding.HomeDashboardWidget(config=cfg)
    widget.show()
    qapp.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()
    qapp.processEvents()


# ---------------------------------------------------------------------------
# The quiet preview behind "N files are waiting"
# ---------------------------------------------------------------------------

def test_preview_lists_files_without_moving_or_logging(downloads, caplog):
    cfg = _config(downloads)
    report = _old_file(downloads / "report.pdf")
    photo = _old_file(downloads / "photo.jpg")
    _old_file(downloads / "movie.mp4.crdownload")

    with caplog.at_level(logging.INFO):
        preview = sorter.preview_existing(cfg)

    names = {src.name: dest for src, dest in preview}
    assert set(names) == {"report.pdf", "photo.jpg"}
    assert names["report.pdf"].parent == downloads / "Documents" / "PDFs"
    assert report.exists() and photo.exists(), "a preview must not move anything"
    assert not sorter.HISTORY_FILE.exists() or not sorter.HISTORY_FILE.read_text(encoding="utf-8").strip()
    assert not [r for r in caplog.records if str(r.msg).startswith("[DRY-RUN]")]


def test_preview_does_not_hide_other_threads_logs(downloads, caplog):
    """The filter only hides the preview's own lines."""
    import threading

    cfg = _config(downloads)
    _old_file(downloads / "report.pdf")
    other: list[str] = []

    def log_from_other_thread():
        logging.info("[DRY-RUN] from another thread")
        other.append("done")

    with caplog.at_level(logging.INFO):
        sorter.preview_existing(cfg)
        t = threading.Thread(target=log_from_other_thread)
        t.start()
        t.join()

    assert other == ["done"]
    assert any("from another thread" in str(r.msg) for r in caplog.records)


# ---------------------------------------------------------------------------
# Home page feedback
# ---------------------------------------------------------------------------

def test_status_says_what_is_being_watched(home, downloads):
    assert home._status_badge.text() == "Sorting is on"
    assert str(downloads) in home._status_desc_lbl.text()


def test_waiting_files_are_counted_and_offered(qapp, home, downloads):
    _old_file(downloads / "report.pdf")
    _old_file(downloads / "photo.jpg")

    home.refresh_waiting()
    _wait(qapp, lambda: home._waiting == 2)

    assert home._waiting_lbl.isVisible()
    assert "2 files are waiting" in home._waiting_lbl.text()
    assert home._btn_sort_now.text() == "Sort 2 files now"


def test_sort_now_reports_what_it_did(qapp, home, downloads):
    _old_file(downloads / "report.pdf")
    _old_file(downloads / "notes.pdf")
    _old_file(downloads / "photo.jpg")

    home._btn_sort_now.click()
    assert home._btn_sort_now.text() == "Sorting..."
    _wait(qapp, lambda: not home.is_sorting())

    assert (downloads / "Documents" / "PDFs" / "report.pdf").exists()
    banner = home._banner
    assert banner.isVisible()
    assert banner.text().startswith("Sorted 3 files into 2 folders:")
    assert "PDFs (2)" in banner.text()
    assert banner.action_text() == "Undo"
    assert home._activity_table.rowCount() == 3
    _wait(qapp, lambda: home._btn_sort_now.text() == "Sort Downloads now")


def test_sort_now_says_when_there_was_nothing_to_do(qapp, home):
    home._btn_sort_now.click()
    _wait(qapp, lambda: not home.is_sorting())

    assert "already tidy" in home._banner.text()


def test_undo_from_the_message_puts_files_back(qapp, home, downloads):
    _old_file(downloads / "report.pdf")
    home.sort_now()
    assert not (downloads / "report.pdf").exists()

    home._banner._action_btn.click()

    assert (downloads / "report.pdf").exists()
    assert home._banner.text() == "Put 1 file back where it was."


def test_files_filed_in_the_background_appear_without_a_click(qapp, home, downloads):
    assert home._activity_table.rowCount() == 0
    report = _old_file(downloads / "report.pdf")

    # The background sorter files a download...
    dest = sorter.move_file(report, _config(downloads))
    assert dest is not None

    # ...and the next tick of the Home page picks it up.
    home._tick()

    assert home._activity_table.rowCount() == 1
    assert home._activity_table.item(0, 0).text() == "report.pdf"
    assert home._banner.text() == "Filed report.pdf in PDFs."
    assert "1 file organized today" == home._files_today_lbl.text()


def test_pause_and_resume_say_what_happens_next(qapp, home, monkeypatch):
    paused = [False]
    monkeypatch.setattr(sorter, "is_paused", lambda: paused[0])
    monkeypatch.setattr(sorter, "set_paused", lambda value: paused.__setitem__(0, value))

    home.toggle_pause()
    assert home._status_badge.text() == "Sorting is paused"
    assert "stay in Downloads until you resume" in home._banner.text()

    home.toggle_pause()
    assert home._status_badge.text() == "Sorting is on"
    assert "Sorting is on again" in home._banner.text()


def test_starting_the_sorter_shows_progress_then_confirms(qapp, home, monkeypatch):
    running = [False]
    monkeypatch.setattr(config_manager, "is_sorter_running", lambda: running[0])
    monkeypatch.setattr(home, "_start_background_sorter", lambda: None)
    home.refresh_status()
    assert home._status_badge.text() == "Sorting is off"

    home.toggle_pause()
    assert home._status_badge.text() == "Starting..."
    assert not home._btn_pause_resume.isEnabled()

    running[0] = True
    home.refresh_status()
    assert home._status_badge.text() == "Sorting is on"
    assert home._banner.text().startswith("Sorting is on. Download Sorter is now watching")


def test_starting_the_sorter_reports_a_failure(qapp, home, monkeypatch):
    monkeypatch.setattr(config_manager, "is_sorter_running", lambda: False)
    monkeypatch.setattr(home, "_start_background_sorter", lambda: None)
    home.toggle_pause()
    home._starting_until = time.monotonic() - 1  # the wait ran out

    home.refresh_status()

    assert home._status_badge.text() == "Sorting is off"
    assert home._banner.kind() == "error"
    assert "didn't start" in home._banner.text()
