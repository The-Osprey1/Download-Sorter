"""Tests for sorting misplaced files inside managed Tier 1 / Tier 2+ folders."""

from __future__ import annotations

from download_sorter.config import Config, save_config
from download_sorter.paths import AppPaths
from download_sorter.service import SorterService
from download_sorter.ui.main_window import MainWindow


def test_sort_managed_tiers_now_moves_misplaced_tier_files_and_supports_undo(
    app_paths: AppPaths,
) -> None:
    cfg = Config.with_starter_defaults()
    cfg.use_type_subfolders = True
    save_config(cfg, app_paths)

    dl = app_paths.downloads_dir
    assert dl is not None

    # A PDF sitting directly inside Tier 1 Documents instead of Documents\PDF
    misplaced = dl / "Documents" / "report.pdf"
    misplaced.parent.mkdir(parents=True, exist_ok=True)
    misplaced.write_text("pdf", encoding="utf-8")

    # An unrelated user folder must never be touched
    unmanaged = dl / "My Personal Folder" / "notes.pdf"
    unmanaged.parent.mkdir(parents=True, exist_ok=True)
    unmanaged.write_text("personal", encoding="utf-8")

    svc = SorterService(app_paths)
    res = svc.sort_managed_tiers_now()
    assert len(res["moved"]) == 1
    target = dl / "Documents" / "PDF" / "report.pdf"
    assert target.exists()
    assert not misplaced.exists()
    assert unmanaged.exists()

    undo_res = svc.undo_last_batch()
    assert undo_res["restored"] == 1
    assert misplaced.exists()
    assert not target.exists()


def test_main_window_sort_lower_tiers_button(v3_qapp, app_paths: AppPaths) -> None:
    cfg = Config.with_starter_defaults()
    cfg.tutorial_completed = True
    cfg.use_type_subfolders = True
    save_config(cfg, app_paths)

    dl = app_paths.downloads_dir
    assert dl is not None
    misplaced = dl / "Documents" / "manual.pdf"
    misplaced.parent.mkdir(parents=True, exist_ok=True)
    misplaced.write_text("pdf", encoding="utf-8")

    svc = SorterService(app_paths)
    win = MainWindow(svc)
    assert hasattr(win, "btn_sort_tiers")
    win.btn_sort_tiers.click()

    assert (dl / "Documents" / "PDF" / "manual.pdf").exists()
    assert not misplaced.exists()
    assert "Tidied files inside folders: moved 1 file(s)" in win.banner._text.text()
    win.close()
