"""Bypassing the hold: file every held .3mf now, in one batch, from the command line,
the tray and Settings."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

import hold_manager
import safe_ops
import sort_undo
import sorter


@pytest.fixture(autouse=True)
def _samples_are_finished_downloading(finished_downloads):
    """These tests are about the hold, not about waiting for downloads to finish."""


@pytest.fixture
def downloads(tmp_path) -> Path:
    root = tmp_path / "Downloads"
    root.mkdir()
    return root.resolve()


@pytest.fixture
def config(downloads) -> dict:
    cfg = sorter.get_default_config()
    cfg["downloads_dir"] = str(downloads)
    cfg["watch"] = {"folder": str(downloads), "settle_seconds": 0, "min_age_minutes": 0,
                    "auto_unzip": False, "auto_unpack_3d_zips": False}
    cfg["notifications"] = False
    cfg["ribbon_enabled"] = False
    cfg["quick_switcher_enabled"] = False
    cfg["hold"] = {**cfg.get("hold", {}), "enabled": True, "hold_days": 7, "max_held": 15}
    return cfg


def _held_file(downloads: Path, config: dict, name: str) -> Path:
    """A .3mf the hold keeps in Downloads, as the sorter would."""
    path = downloads / name
    path.write_text("model data", encoding="utf-8")
    past = time.time() - 120
    os.utime(path, (past, past))
    assert sorter.move_file(path, config) is None          # held, so not moved
    assert hold_manager.is_held(path)
    return path


def test_file_all_now_files_every_held_file_in_one_batch(downloads, config):
    first = _held_file(downloads, config, "bracket.3mf")
    second = _held_file(downloads, config, "plate.3mf")

    result = hold_manager.file_all_now(config)

    assert (result["filed"], result["failed"], result["kept"]) == (2, 0, 0)
    assert not first.exists() and not second.exists()
    assert not hold_manager.get_all_held()
    filed = [r for r in sort_undo.get_active_moves(sort_undo.load_history()) if r.get("dest")]
    batches = {r["batch_id"] for r in filed if str(r["dest"]).lower().endswith(".3mf")}
    assert len(batches) == 1 and next(iter(batches)).startswith("file_now-")


def test_undo_puts_the_whole_bypass_back(downloads, config):
    first = _held_file(downloads, config, "bracket.3mf")
    second = _held_file(downloads, config, "plate.3mf")
    hold_manager.file_all_now(config)

    moves = [r for r in sort_undo.get_active_moves(sort_undo.load_history())
             if str(r.get("dest", "")).lower().endswith(".3mf")]
    assert sort_undo.undo_moves(moves) == 2
    assert first.exists() and second.exists()


def test_files_marked_keep_here_are_left_unless_asked(downloads, config):
    keeper = _held_file(downloads, config, "keeper.3mf")
    other = _held_file(downloads, config, "other.3mf")
    assert hold_manager.keep_here(keeper)

    result = hold_manager.file_all_now(config)
    assert (result["filed"], result["kept"]) == (1, 1)
    assert keeper.exists() and not other.exists()

    again = hold_manager.file_all_now(config, include_kept=True)
    assert again["filed"] == 1 and not keeper.exists()


def test_a_file_that_cannot_move_keeps_its_hold(downloads, config, monkeypatch):
    stuck = _held_file(downloads, config, "stuck.3mf")
    free = _held_file(downloads, config, "free.3mf")
    real_move = safe_ops.move

    def move(src, dst, **kwargs):
        if Path(src).name == "stuck.3mf":
            raise safe_ops.Refused("in-use", src)
        return real_move(src, dst, **kwargs)

    monkeypatch.setattr(safe_ops, "move", move)
    result = hold_manager.file_all_now(config)

    assert (result["filed"], result["failed"]) == (1, 1)
    assert stuck.exists() and hold_manager.is_held(stuck)      # nothing forgotten
    assert not free.exists()


def test_the_held_count_ignores_files_that_are_gone(downloads, config):
    gone = _held_file(downloads, config, "gone.3mf")
    _held_file(downloads, config, "here.3mf")
    gone.unlink()

    assert hold_manager.get_held_summary()["total_held"] == 1
    assert hold_manager.file_all_now(config)["filed"] == 1
    assert hold_manager.get_held_summary()["total_held"] == 0


def test_nothing_held_is_not_an_error(config):
    assert hold_manager.file_all_now(config) == {"filed": 0, "failed": 0, "kept": 0, "paths": []}


def test_the_file_held_command(downloads, config, monkeypatch, capsys):
    import main

    _held_file(downloads, config, "bracket.3mf")
    monkeypatch.setattr(sorter, "load_config", lambda: config)

    assert main.main(["--file-held"]) == 0
    assert capsys.readouterr().out.strip() == "Filed 1 held file(s)."
    assert not (downloads / "bracket.3mf").exists()
    assert "--file-held" in main.HELP_TEXT


def test_the_tray_offers_it_only_while_something_is_held(downloads, config, monkeypatch):
    import tray

    _held_file(downloads, config, "bracket.3mf")
    monkeypatch.setattr(tray.SorterTray, "__init__", lambda self, *a, **k: None)
    sorter_tray = tray.SorterTray()
    items = [i for i in sorter_tray.build_menu().items if getattr(i, "text", None) == "File held 3MF files now"]
    assert len(items) == 1 and items[0].visible is True

    hold_manager.file_all_now(config)
    assert items[0].visible is False


def test_settings_has_the_button(qapp, downloads, config, monkeypatch):
    import onboarding

    monkeypatch.setattr(onboarding.suite_data, "save_sorter_config", lambda *a, **k: None)
    _held_file(downloads, config, "bracket.3mf")
    widget = onboarding.SettingsWidget(config=config)
    try:
        widget._refresh_held_button()
        assert widget._btn_file_held.isEnabled()
        assert "1 held .3mf file waiting" == widget._file_held_note.text()

        widget._btn_file_held.click()

        assert widget._file_held_note.text() == "Filed 1 held file."
        assert not widget._btn_file_held.isEnabled()
        assert not (downloads / "bracket.3mf").exists()
    finally:
        widget.close()
        widget.deleteLater()
