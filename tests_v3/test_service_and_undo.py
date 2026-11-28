"""Tests for top-level-only watcher, duplicate isolation, and SQLite batch/single undo."""

from __future__ import annotations

import os
from pathlib import Path
import time

from download_sorter.config import Config, save_config
from download_sorter.paths import AppPaths
from download_sorter.service import SorterService


def test_top_level_only_never_touches_subfolders(app_paths: AppPaths) -> None:
    cfg = Config.with_starter_defaults()
    cfg.settle_seconds = 0.0
    save_config(cfg, app_paths)

    dl = app_paths.downloads_dir
    assert dl is not None
    # Top-level file SHOULD move
    top_pdf = dl / "notes_report.pdf"
    top_pdf.write_text("top-level pdf", encoding="utf-8")

    # Misplaced file inside a user subfolder MUST NEVER be touched by v3
    sub_dir = dl / "Documents" / "Career"
    sub_dir.mkdir(parents=True, exist_ok=True)
    nested_file = sub_dir / "FEF CIC 2025 - Career Statement.pdf"
    nested_file.write_text("leave me alone", encoding="utf-8")

    svc = SorterService(app_paths)
    res = svc.scan_top_level_now(settle_seconds=0.0)

    assert len(res["moved"]) == 1
    assert not top_pdf.exists()
    assert (dl / "Documents" / "PDF" / "notes_report.pdf").exists()
    assert nested_file.exists(), "Subfolder file must NEVER be moved or reclassified!"


def test_duplicate_isolation_and_batch_and_single_undo(app_paths: AppPaths) -> None:
    cfg = Config.with_starter_defaults()
    cfg.settle_seconds = 0.0
    save_config(cfg, app_paths)

    dl = app_paths.downloads_dir
    assert dl is not None
    existing_dir = dl / "Documents" / "PDF"
    existing_dir.mkdir(parents=True, exist_ok=True)
    (existing_dir / "dup.pdf").write_bytes(b"identical-bytes")
    (existing_dir / "diff.pdf").write_bytes(b"original-version")

    incoming_dup = dl / "dup.pdf"
    incoming_dup.write_bytes(b"identical-bytes")
    incoming_diff = dl / "diff.pdf"
    incoming_diff.write_bytes(b"newer-version")

    svc = SorterService(app_paths)
    res = svc.scan_top_level_now(settle_seconds=0.0)
    assert len(res["moved"]) == 2

    moved_by_name = {m["filename"]: Path(m["dest_path"]) for m in res["moved"]}
    assert "Duplicates" in moved_by_name["dup.pdf"].parts
    assert moved_by_name["diff.pdf"].name == "diff (2).pdf"

    # Single-move undo restores just one file
    single = svc.undo_single_move(res["moved"][0]["id"])
    assert single["restored"] == 1
    assert (dl / res["moved"][0]["filename"]).exists()

    # Batch undo restores remaining active file in that batch
    batch_res = svc.undo_last_batch()
    assert batch_res["restored"] == 1
    assert incoming_dup.exists() and incoming_diff.exists()
    assert svc.db.stats()["active_moves"] == 0


def test_live_non_recursive_watchdog_worker(app_paths: AppPaths) -> None:
    cfg = Config.with_starter_defaults()
    cfg.settle_seconds = 0.05
    cfg.tutorial_completed = True  # a set-up user: automatic sorting waits for the welcome steps
    save_config(cfg, app_paths)

    dl = app_paths.downloads_dir
    assert dl is not None
    nested = dl / "ManualFolder"
    nested.mkdir()
    nested_keep = nested / "ignored_inside_subfolder.pdf"
    nested_keep.write_text("stays put", encoding="utf-8")

    completed: list[str] = []
    svc = SorterService(app_paths, on_moves_completed=lambda b, _m: completed.append(b))
    svc.start()
    try:
        dropped = dl / "live_homework.pdf"
        dropped.write_text("homework pdf", encoding="utf-8")
        past = time.time() - 1.0
        os.utime(dropped, (past, past))

        target = dl / "Documents" / "School" / "live_homework.pdf"
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and (not target.exists() or not completed):
            time.sleep(0.05)

        assert target.exists()
        assert nested_keep.exists()
        assert len(completed) >= 1
    finally:
        svc.stop()


def test_3mf_grace_hold_expired_and_file_held_now_undo(app_paths: AppPaths) -> None:
    cfg = Config.with_starter_defaults()
    cfg.settle_seconds = 0.0
    cfg.hold_3mf_enabled = True
    cfg.hold_3mf_days = 7
    save_config(cfg, app_paths)

    dl = app_paths.downloads_dir
    assert dl is not None

    fresh_3mf = dl / "active_print.3mf"
    fresh_3mf.write_text("fresh 3mf", encoding="utf-8")

    old_3mf = dl / "expired_print.3mf"
    old_3mf.write_text("old 3mf", encoding="utf-8")
    ten_days_ago = time.time() - (10 * 86400)
    os.utime(old_3mf, (ten_days_ago, ten_days_ago))

    svc = SorterService(app_paths)

    # Normal scan holds the fresh .3mf, but sorts the expired one
    res = svc.scan_top_level_now(settle_seconds=0.0)
    assert len(res["moved"]) == 1
    assert res["held"] == 1
    assert fresh_3mf.exists()
    assert not old_3mf.exists()
    assert (dl / "3D Models" / "3MF" / "expired_print.3mf").exists()

    held = svc.list_held_3mf()
    assert held == [fresh_3mf]

    # Clicking "File Held .3MF Now" moves it immediately and supports undo
    res_held = svc.file_held_3mf_now()
    assert len(res_held["moved"]) == 1
    assert not fresh_3mf.exists()
    assert (dl / "3D Models" / "3MF" / "active_print.3mf").exists()
    assert svc.list_held_3mf() == []

    undo_res = svc.undo_last_batch()
    assert undo_res["restored"] == 1
    assert fresh_3mf.exists()


def test_old_underscore_duplicates_folder_is_renamed_and_history_follows(app_paths) -> None:  # noqa: ANN001
    from download_sorter.db import StateDB
    from download_sorter.fs_ops import migrate_duplicates_folder

    dl = app_paths.downloads_dir
    old = dl / "_Duplicates" / "2026-11"
    old.mkdir(parents=True)
    (old / "report (1).pdf").write_bytes(b"x")
    db = StateDB(app_paths.data_dir / "t.db")
    db.record_move(
        batch_id="b1",
        filename="report (1).pdf",
        src_path=dl / "report (1).pdf",
        dest_path=old / "report (1).pdf",
        reason="dup",
        matched_rule="",
        match_type="duplicate",
        is_duplicate=True,
    )
    assert migrate_duplicates_folder(dl, db) == 1
    assert (dl / "Duplicates" / "2026-11" / "report (1).pdf").is_file()
    assert not (dl / "_Duplicates").exists()
    rows = db.get_batch_moves("b1")
    assert Path(rows[0]["dest_path"]) == dl / "Duplicates" / "2026-11" / "report (1).pdf"
    # Running again is harmless
    assert migrate_duplicates_folder(dl, db) == 0
    db.close()


def test_new_file_in_a_subfolder_is_rerouted_but_hand_moves_and_own_folders_stay(app_paths) -> None:  # noqa: ANN001
    from types import SimpleNamespace

    from download_sorter.config import Config, save_config
    from download_sorter.service import SorterService, _TopLevelEventHandler

    cfg = Config.with_starter_defaults()
    cfg.settle_seconds = 0.0
    save_config(cfg, app_paths)
    svc = SorterService(app_paths)
    dl = app_paths.downloads_dir
    (dl / "Images").mkdir()
    stray = dl / "Images" / "bracket.step"
    stray.write_bytes(b"ISO-10303-21;")
    (dl / "My Project").mkdir()
    mine = dl / "My Project" / "bolt.step"
    mine.write_bytes(b"ISO-10303-21;")
    (dl / "Documents" / "Career").mkdir(parents=True)
    chosen = dl / "Documents" / "Career" / "cover_letter.pdf"
    chosen.write_bytes(b"%PDF-1.4")

    res = svc._process_paths([stray, mine, chosen], settle_seconds=0.0)
    assert len(res["moved"]) == 1
    assert not stray.exists()
    assert mine.exists()  # a folder of your own: left alone
    assert chosen.exists()  # same kind of folder: you put it there on purpose
    dest = Path(res["moved"][0]["dest_path"].replace("\\", "/"))
    assert dest.name == "bracket.step" and "Images" not in dest.parts

    # Turned off: nothing in subfolders moves.
    off = Config.with_starter_defaults()
    off.sort_new_files_in_subfolders = False
    svc.update_config(off)
    again = dl / "Images" / "again.step"
    again.write_bytes(b"ISO-10303-21;")
    assert svc._process_paths([again], settle_seconds=0.0)["moved"] == []

    # The watcher queues files created or renamed in place, never files moved between folders.
    top, nested = [], []
    handler = _TopLevelEventHandler(dl, top.append, nested.append)
    handler.on_created(SimpleNamespace(is_directory=False, src_path=str(dl / "Images" / "new.step")))
    handler.on_moved(
        SimpleNamespace(
            is_directory=False, src_path=str(dl / "Images" / "a.crdownload"), dest_path=str(dl / "Images" / "a.step")
        )
    )
    handler.on_moved(
        SimpleNamespace(is_directory=False, src_path=str(dl / "x.step"), dest_path=str(dl / "Images" / "x.step"))
    )
    assert [p.name for p in nested] == ["new.step", "a.step"]
    assert top == []
