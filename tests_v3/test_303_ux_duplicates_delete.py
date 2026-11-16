"""3.0.3 UX, progress animation HUD, native duplicate deletion, and preview selection deletion tests."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase, QPixmap

from download_sorter import fs_ops
from download_sorter.config import Config, save_config
from download_sorter.paths import AppPaths
from download_sorter.service import SorterService
from download_sorter.ui.main_window import DuplicateManagerDialog, MainWindow
from download_sorter.ui.motion import SortingProgressHUD, SortyMascotWidget


def _fake_recycle_unlink(paths: list[str]) -> bool:
    """Test double simulating Windows SHFileOperationW deleting files to Recycle Bin."""
    for p in paths:
        Path(p).unlink()
    return True


def _make_window(v3_qapp, app_paths: AppPaths) -> MainWindow:  # noqa: ANN001
    fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    if (fonts / "segoeui.ttf").exists():
        for name in ("segoeui.ttf", "segoeuib.ttf", "seguisb.ttf"):
            if (fonts / name).exists():
                QFontDatabase.addApplicationFont(str(fonts / name))
        v3_qapp.setFont(QFont("Segoe UI", 10))
    cfg = Config.with_starter_defaults()
    cfg.tutorial_completed = True
    save_config(cfg, app_paths)
    svc = SorterService(app_paths)
    return MainWindow(svc)


# ---------------------------------------------------------------------------
# 1. Progress Callbacks, SortingProgressHUD & Sorty Working Animation
# ---------------------------------------------------------------------------
def test_service_sort_and_lower_tier_progress_callbacks(app_paths: AppPaths) -> None:
    cfg = Config.with_starter_defaults()
    cfg.hold_3mf_enabled = False
    save_config(cfg, app_paths)
    (app_paths.downloads_dir / "alpha.pdf").write_bytes(b"pdf-data")
    (app_paths.downloads_dir / "bravo.zip").write_bytes(b"zip-data")

    svc = SorterService(app_paths)
    events: list[tuple[int, int, str]] = []
    res = svc.scan_top_level_now(settle_seconds=0.0, on_progress=lambda d, t, s: events.append((d, t, s)))
    assert len(res["moved"]) == 2
    assert events[0] == (0, 0, "Scanning Downloads...")
    assert any(t == 2 and "alpha.pdf" in s for _d, t, s in events)
    assert events[-1] == (2, 2, "Complete")

    # Place a wrongly-filed document inside top-level Documents/ and verify lower-tier sort emits progress
    wrong_tier = app_paths.downloads_dir / "Documents" / "report.pdf"
    wrong_tier.parent.mkdir(parents=True, exist_ok=True)
    wrong_tier.write_bytes(b"tier-pdf")
    tier_events: list[tuple[int, int, str]] = []
    tier_res = svc.sort_managed_tiers_now(on_progress=lambda d, t, s: tier_events.append((d, t, s)))
    assert len(tier_res["moved"]) == 1
    assert tier_events[0] == (0, 0, "Scanning managed folders...")
    assert tier_events[-1][0] == tier_events[-1][1] and tier_events[-1][2] == "Complete"
    svc.stop(timeout=1.0)


def test_sorting_progress_hud_and_mascot_working_render(v3_qapp) -> None:  # noqa: ANN001
    hud = SortingProgressHUD()
    hud.resize(540, 68)
    hud.start("Sorting Downloads...", detail="Scanning files...", total=0)
    assert hud.isVisible()
    assert hud.mode == "indeterminate"
    assert hud._pct_pill.text() == "···"

    # Paint indeterminate state cleanly
    px1 = QPixmap(hud.size())
    hud.render(px1)

    hud.update_progress(3, 10, "very_long_archive_filename_backup_2026_11.tar.gz")
    assert hud.mode == "determinate"
    assert hud._count_pill.text() == "3 / 10"
    assert hud._pct_pill.text() == "30%"
    px2 = QPixmap(hud.size())
    hud.render(px2)

    hud.finish("Cleaned up Downloads", detail="Moved 10 file(s)", kind="success")
    assert hud.mode == "success"
    assert hud._pct_pill.text() == "100%"
    px3 = QPixmap(hud.size())
    hud.render(px3)

    hud.collapse()
    assert not hud.isVisible()

    mascot = SortyMascotWidget(size=48)
    mascot.set_working(True, mode="scanning")
    assert mascot.pose == "scanning"
    assert not mascot.to_pixmap(48, 48).isNull()

    mascot.set_working(True, mode="working")
    assert mascot.pose == "working"
    assert not mascot.to_pixmap(48, 48).isNull()

    mascot.set_working(False)
    assert mascot.pose == "smart"


# ---------------------------------------------------------------------------
# 2. Native Duplicate File Listing, Security Boundaries & Dialog UX
# ---------------------------------------------------------------------------
def test_duplicate_listing_boundary_and_manager_dialog(v3_qapp, app_paths: AppPaths, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(fs_ops, "_send_to_recycle_bin_win32", _fake_recycle_unlink)

    dup_dir = app_paths.downloads_dir / "_Duplicates" / "2026-11"
    dup_dir.mkdir(parents=True, exist_ok=True)
    d1 = dup_dir / "driver_setup.exe"
    d2 = dup_dir / "driver_setup (2).exe"
    d3 = dup_dir / "invoice.pdf"
    d1.write_bytes(b"A" * 2048)
    d2.write_bytes(b"A" * 2048)
    d3.write_bytes(b"B" * 512)
    # Give d2 a clearly newer mtime than d1
    os.utime(d1, (1_700_000_000, 1_700_000_000))
    os.utime(d2, (1_700_000_500, 1_700_000_500))

    outside_file = app_paths.data_dir / "do_not_delete.txt"
    outside_file.write_text("keep me", encoding="utf-8")
    sorted_original = app_paths.downloads_dir / "Installers" / "driver_setup.exe"
    sorted_original.parent.mkdir(parents=True, exist_ok=True)
    sorted_original.write_bytes(b"A" * 2048)

    win = _make_window(v3_qapp, app_paths)
    try:
        assert "Duplicates (3)" in win.btn_duplicates.text()

        # Security boundary: delete_duplicate_files must refuse non-_Duplicates paths
        refused = win.service.delete_duplicate_files([outside_file, sorted_original])
        assert len(refused["deleted"]) == 0
        assert len(refused["failed"]) == 2
        assert outside_file.exists() and sorted_original.exists()

        dlg = DuplicateManagerDialog(win.service, win)
        try:
            assert dlg.tbl_dups.rowCount() == 3
            assert "Quarantined" in dlg.lbl_reclaim_pill.text()

            # Smart selection leaves newest copy of driver_setup (d2) and standalone invoice.pdf unchecked,
            # selecting older duplicate d1
            dlg.select_all_except_newest()
            checked = dlg.checked_paths()
            assert checked == [d1]
            assert "1 selected" in dlg.lbl_reclaim_pill.text()

            res = dlg.delete_checked_files(confirm=False)
            assert res["deleted"] == [str(d1)]
            assert not d1.exists()
            assert d2.exists() and d3.exists()

            # Select all remaining quarantined copies and delete them too
            dlg.select_all()
            res2 = dlg.delete_checked_files(confirm=False)
            assert len(res2["deleted"]) == 2
            assert not dup_dir.exists()  # empty monthly subfolder cleaned up
            assert dlg.tbl_dups.rowCount() == 0
            assert dlg.lbl_empty.isVisibleTo(dlg) or not dlg.lbl_empty.isHidden()
        finally:
            dlg.close()

        win.refresh_all()
        assert win.btn_duplicates.text() == "Duplicates"
    finally:
        win.close()
        win.service.stop(timeout=1.0)


# ---------------------------------------------------------------------------
# 3. Direct Selection & Deletion Inside Downloads Folder Preview (tbl_sim)
# ---------------------------------------------------------------------------
def test_select_and_delete_files_in_preview_table(v3_qapp, app_paths: AppPaths, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(fs_ops, "_send_to_recycle_bin_win32", _fake_recycle_unlink)

    f_pdf = app_paths.downloads_dir / "bank_october.pdf"
    f_zip = app_paths.downloads_dir / "mod_pack.zip"
    f_keep = app_paths.downloads_dir / "notes.txt"
    for f in (f_pdf, f_zip, f_keep):
        f.write_bytes(b"hello-world")

    # Add a quarantined duplicate too so we test 900x560 layout with BOTH badges visible!
    dup_file = app_paths.downloads_dir / "_Duplicates" / "2026-11" / "extra.zip"
    dup_file.parent.mkdir(parents=True, exist_ok=True)
    dup_file.write_bytes(b"dup")

    win = _make_window(v3_qapp, app_paths)
    try:
        win.show()
        assert win.tbl_sim.rowCount() == 3
        assert not win.btn_delete_selected.isVisible()

        # Select two rows cleanly in tbl_sim
        win.tbl_sim.selectRow(0)
        from PySide6.QtCore import QItemSelectionModel

        row1_idx = win.tbl_sim.model().index(1, 0)
        win.tbl_sim.selectionModel().select(
            row1_idx,
            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
        )
        v3_qapp.processEvents()

        selected_paths = win._selected_sim_paths()
        assert len(selected_paths) == 2
        assert win.btn_delete_selected.isVisible()
        assert win.btn_delete_selected.text() == "Delete (2)"

        # Verify 900x560 layout still has zero clipped buttons even with Delete (2) & Duplicates (1) active
        win.resize(900, 560)
        v3_qapp.processEvents()
        from tests_v3.test_301_fixes import _clipped_buttons

        assert _clipped_buttons(win) == []

        # Delete only the 2 selected rows
        deleted_names = {p.name for p in selected_paths}
        win._delete_selected_sim_files(confirm=False)
        for p in selected_paths:
            assert not p.exists()
        assert win.tbl_sim.rowCount() == 1
        remaining = {win.tbl_sim.item(0, 0).text()}
        assert remaining == ({f.name for f in (f_pdf, f_zip, f_keep)} - deleted_names)
        assert not win.btn_delete_selected.isVisible()
        assert win.progress_hud.mode == "success"
    finally:
        win.close()
        win.service.stop(timeout=1.0)
