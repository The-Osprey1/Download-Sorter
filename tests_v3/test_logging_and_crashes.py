"""The log stays a fixed size, and an unexpected error is written to it instead of
vanishing, so a bug report can include what went wrong."""

from __future__ import annotations

import logging
from pathlib import Path
import sys
import threading

import pytest

from download_sorter import diagnostics


@pytest.fixture
def log_file(tmp_path: Path):
    path = tmp_path / "sorter_v3.log"
    yield path
    diagnostics.remove_file_logging()


def _read(path: Path) -> str:
    for handler in logging.getLogger().handlers:
        handler.flush()
    return path.read_text(encoding="utf-8") if path.exists() else ""


def test_log_rolls_over_instead_of_growing_forever(log_file: Path) -> None:
    diagnostics.configure_file_logging(log_file, max_bytes=2_000, backups=2)
    for i in range(400):
        logging.info("Moved file number %d into Documents", i)
    files = sorted(p.name for p in log_file.parent.iterdir() if p.name.startswith("sorter_v3.log"))
    assert files == ["sorter_v3.log", "sorter_v3.log.1", "sorter_v3.log.2"]
    assert all((log_file.parent / name).stat().st_size <= 2_200 for name in files)


def test_setting_up_logging_twice_keeps_one_log_handler(log_file: Path) -> None:
    diagnostics.configure_file_logging(log_file)
    diagnostics.configure_file_logging(log_file)
    ours = [h for h in logging.getLogger().handlers if getattr(h, diagnostics.HANDLER_TAG, False)]
    assert len(ours) == 1


def test_an_unexpected_error_is_written_to_the_log(log_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[type] = []
    monkeypatch.setattr(sys, "excepthook", lambda t, v, tb: seen.append(t))
    monkeypatch.setattr(threading, "excepthook", lambda args: None)
    diagnostics.configure_file_logging(log_file)
    diagnostics.install_crash_logging()
    try:
        raise ValueError("rule list was empty")
    except ValueError:
        sys.excepthook(*sys.exc_info())
    text = _read(log_file)
    assert "Unexpected error" in text
    assert "ValueError: rule list was empty" in text
    assert seen == [ValueError]  # the previous handler still runs


def test_an_error_in_a_background_thread_is_written_to_the_log(
    log_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "excepthook", lambda t, v, tb: None)
    monkeypatch.setattr(threading, "excepthook", lambda args: None)
    diagnostics.configure_file_logging(log_file)
    diagnostics.install_crash_logging()

    def boom() -> None:
        raise RuntimeError("watcher stopped")

    worker = threading.Thread(target=boom, name="sorter-watch")
    worker.start()
    worker.join()
    text = _read(log_file)
    assert "sorter-watch" in text
    assert "RuntimeError: watcher stopped" in text
