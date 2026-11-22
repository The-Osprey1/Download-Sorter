"""Besides Downloads, people can have other folders tidied (Desktop, Screenshots, a
scanner folder). Each one is sorted into folders inside itself, with the same rules;
files nothing matches stay where they are."""

from __future__ import annotations

from pathlib import Path
import time

import pytest

from download_sorter.config import Config, save_config
from download_sorter.paths import AppPaths
from download_sorter.service import SorterService


def _found_under(root: Path, name: str) -> list[Path]:
    return [p for p in root.rglob(name) if p.is_file()]


@pytest.fixture
def desktop(tmp_path: Path) -> Path:
    folder = tmp_path / "Desktop"
    folder.mkdir()
    return folder


def _service(app_paths: AppPaths, *extra: Path, fallback: str = "other") -> SorterService:
    cfg = Config(extra_watch_folders=[str(p) for p in extra], fallback_mode=fallback, settle_seconds=0.0)
    cfg.tutorial_completed = True
    save_config(cfg, app_paths)
    return SorterService(app_paths)


def test_extra_folders_are_saved() -> None:
    cfg = Config(extra_watch_folders=[r"C:\Users\me\Desktop", r"C:\Users\me\Desktop"])
    assert cfg.extra_watch_folders == [r"C:\Users\me\Desktop"]
    assert Config.from_dict(cfg.to_dict()).extra_watch_folders == [r"C:\Users\me\Desktop"]
    assert Config.from_dict({}).extra_watch_folders == []


def test_watched_folders_are_downloads_then_the_extras(app_paths: AppPaths, desktop: Path) -> None:
    svc = _service(app_paths, desktop)
    assert svc.watch_roots() == [app_paths.downloads_dir.resolve(), desktop.resolve()]


def test_missing_or_overlapping_folders_are_left_out(app_paths: AppPaths, desktop: Path, tmp_path: Path) -> None:
    inside_downloads = app_paths.downloads_dir / "Inbox"
    inside_downloads.mkdir()
    inside_desktop = desktop / "Scans"
    inside_desktop.mkdir()
    svc = _service(
        app_paths,
        desktop,
        tmp_path / "missing",
        inside_downloads,  # its sorted files could be picked up again by Downloads
        app_paths.downloads_dir.parent,  # contains Downloads
        inside_desktop,  # inside another watched folder
    )
    assert svc.watch_roots() == [app_paths.downloads_dir.resolve(), desktop.resolve()]


def test_files_in_an_extra_folder_are_sorted_inside_it(app_paths: AppPaths, desktop: Path) -> None:
    (desktop / "invoice_march.pdf").write_bytes(b"%PDF-1.4 bill")
    (app_paths.downloads_dir / "photo.jpg").write_bytes(b"jpg")
    svc = _service(app_paths, desktop)
    res = svc.scan_top_level_now(settle_seconds=0.0)
    assert len(res["moved"]) == 2
    assert not (desktop / "invoice_march.pdf").exists()
    moved = _found_under(desktop, "invoice_march.pdf")
    assert len(moved) == 1 and moved[0].parent != desktop
    assert _found_under(app_paths.downloads_dir, "photo.jpg")


def test_unmatched_files_stay_in_an_extra_folder(app_paths: AppPaths, desktop: Path) -> None:
    (desktop / "Notepad.lnk").write_bytes(b"shortcut")
    (app_paths.downloads_dir / "mystery.qqq").write_bytes(b"?")
    svc = _service(app_paths, desktop, fallback="other")
    svc.scan_top_level_now(settle_seconds=0.0)
    assert (desktop / "Notepad.lnk").exists()  # never moved into Desktop\Other
    assert not (app_paths.downloads_dir / "mystery.qqq").exists()  # Downloads keeps its fallback


def test_undo_puts_extra_folder_files_back(app_paths: AppPaths, desktop: Path) -> None:
    original = desktop / "notes.txt"
    original.write_text("hello", encoding="utf-8")
    svc = _service(app_paths, desktop)
    svc.scan_top_level_now(settle_seconds=0.0)
    assert not original.exists()
    svc.undo_last_batch()
    assert original.read_text(encoding="utf-8") == "hello"


def test_subfolders_of_an_extra_folder_are_never_touched(app_paths: AppPaths, desktop: Path) -> None:
    project = desktop / "Project"
    project.mkdir()
    (project / "plan.pdf").write_bytes(b"%PDF")
    svc = _service(app_paths, desktop)
    svc.scan_top_level_now(settle_seconds=0.0)
    assert (project / "plan.pdf").exists()


def test_new_files_in_an_extra_folder_are_sorted_as_they_arrive(app_paths: AppPaths, desktop: Path) -> None:
    svc = _service(app_paths, desktop)
    svc.start()
    try:
        time.sleep(0.5)
        target = desktop / "arrived.pdf"
        target.write_bytes(b"%PDF-1.4 new")
        deadline = time.time() + 10
        while target.exists() and time.time() < deadline:
            time.sleep(0.2)
        assert not target.exists()
        assert _found_under(desktop, "arrived.pdf")
    finally:
        svc.stop()


def test_settings_page_adds_and_removes_extra_folders(service_instance, v3_qapp, desktop: Path) -> None:
    from download_sorter.ui.main_window import MainWindow

    win = MainWindow(service_instance)
    assert win.list_extra_folders.count() == 0
    win._add_extra_folder(str(desktop))
    win._save_settings_form()
    assert service_instance.config.extra_watch_folders == [str(desktop)]
    win.list_extra_folders.setCurrentRow(0)
    win._remove_extra_folder()
    win._save_settings_form()
    assert service_instance.config.extra_watch_folders == []
    win.close()
