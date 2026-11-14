"""Tier 4: Real-World Application Scenarios E2E Tests.

Exercises 5 multi-component end-to-end user journeys:
1. Scenario 1: Download 3D Model & Open Slicer Dialog (F1, F2, F3, F4, F5)
2. Scenario 2: Multi-File Batch Download & Multi-Select Upload (F1, F2, F3, F4, F5)
3. Scenario 3: Download -> Undo -> File Dialog Opened (F2, F5)
4. Scenario 4: High-Frequency Rapid Downloads Coalescing (F2, F5)
5. Scenario 5: Elevated Target Process UIPI Block Notification (F1, F3, F4)
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
import time
from typing import Any
from unittest.mock import patch

import pytest

from download_sorter.quick_switcher import (
    ADMIN_WARNING,
    DEFAULT_RECENCY_TTL,
    THREE_D_EXTENSIONS,
    THREE_D_MAX_AGE_SEC,
    TOO_MANY_WARNING,
    QuickSwitcherMonitor,
    blocked_by_uipi,
    build_dropfiles,
    clear_recent_file,
    consume_items,
    copy_files_to_clipboard,
    find_dialog_edit_control,
    get_recent_batch,
    get_recent_file,
    inject_file_into_dialog,
    inject_files_into_dialog,
    invalidate_recent_file,
    is_file_dialog,
    plan_injection,
    prune_stages,
    read_clipboard_files,
    record_recent_file,
    stage_batch,
    start_file_drag,
)

from download_sorter.ui.quick_switcher_pill import QuickSwitcherPill

from tests_v3.mock_win32 import FakeWin32Desktop, MockWindow


# ===========================================================================
# Scenario 1: Download 3D Model & Open Slicer Dialog (F1, F2, F3, F4, F5)
# ===========================================================================

def test_scenario_1_download_3d_model_and_open_slicer(
    service_instance: Any, fake_desktop: FakeWin32Desktop, create_test_file: Any, app_paths: Any, v3_qapp: Any
) -> None:
    """Scenario 1: User downloads 3mf model; 2 hours later opens slicer dialog and injects via Ctrl+G."""
    clear_recent_file(data_dir=app_paths.data_dir)
    t0 = 200000.0

    # 1. Download file placed in watch folder
    model = create_test_file("articulated_dragon.3mf", content=b"3MF ZIP PK HEADER DATA")

    # 2. SorterService sorts file and records into recency buffer
    with patch("time.time", return_value=t0):
        summary = service_instance.scan_top_level_now(settle_seconds=0.0, bypass_3mf_hold=True)
        assert len(summary["moved"]) == 1
        sorted_dest = Path(summary["moved"][0]["dest_path"])
        assert sorted_dest.exists()

    # 3. 2 hours later (7,200s), user opens OrcaSlicer and triggers File -> Open (#32770)
    t_slicer = t0 + 7200.0
    with patch("time.time", return_value=t_slicer):
        slicer_dlg = fake_desktop.create_dialog(title="Open Project", edit_type="ComboBoxEx32")
        assert is_file_dialog(slicer_dlg) is True

        # Query recent file: 3mf qualifies under 12-hour TTL
        recent = get_recent_file(max_age_sec=300.0, data_dir=app_paths.data_dir)
        assert recent is not None
        assert recent["name"] == "articulated_dragon.3mf"
        assert recent["age_sec"] >= 7199.0

        # Spawns QuickSwitcherPill docked to dialog
        pill = QuickSwitcherPill(slicer_dlg, recent)
        assert "articulated_dragon.3mf" in pill.filename
        assert pill.is_closed is False

        # Simulate user pressing Ctrl+G / clicking Select
        pill.select_file()
        assert pill.action_btn.text() == "✓ Injected!"

        # Assert Edit control received the full resolved path
        edit_w = fake_desktop.get_edit_control(slicer_dlg)
        assert edit_w is not None
        assert edit_w.text == str(sorted_dest.resolve())

        pill.close()

    clear_recent_file(data_dir=app_paths.data_dir)


# ===========================================================================
# Scenario 2: Multi-File Batch Download & Multi-Select Upload (F1, F2, F3, F4, F5)
# ===========================================================================

def test_scenario_2_multifile_batch_download_and_multiselect_upload(
    service_instance: Any, fake_desktop: FakeWin32Desktop, create_test_file: Any, app_paths: Any, v3_qapp: Any
) -> None:
    """Scenario 2: User downloads 4 JPG photos; uploads all simultaneously via multi-file injection."""
    clear_recent_file(data_dir=app_paths.data_dir)
    now = time.time()

    # 1. 4 photos downloaded in rapid succession (every 2 seconds)
    photos = [create_test_file(f"photo_{i}.jpg", content=b"JPG DATA") for i in range(1, 5)]

    # 2. Sorter sorts all 4 into category destination
    summary = service_instance.scan_top_level_now(settle_seconds=0.0)
    assert len(summary["moved"]) == 4

    # 3. Recency buffer coalesces into single batch
    recent = get_recent_file(data_dir=app_paths.data_dir)
    assert recent is not None
    assert recent["batch_count"] == 4
    batch_id = recent["batch_id"]

    # 4. Chrome file upload dialog opened
    browser_dlg = fake_desktop.create_dialog(title="Open", edit_type="ComboBoxEx32")
    assert is_file_dialog(browser_dlg) is True

    # 5. Pill docks in batch mode with "All 4" button
    pill = QuickSwitcherPill(browser_dlg, recent)
    assert pill.is_batch is True
    assert pill.all_btn is not None
    assert "All 4" in pill.all_btn.text()

    # 6. User clicks "All 4"
    pill.select_all()
    assert pill.all_btn.text() == "✓ 4 injected"

    # Consuming items clears batch buffer
    consume_items(pill._checked_paths(), data_dir=app_paths.data_dir)
    assert get_recent_file(data_dir=app_paths.data_dir) is None

    pill.close()
    clear_recent_file(data_dir=app_paths.data_dir)


# ===========================================================================
# Scenario 3: Download -> Undo -> File Dialog Opened (F2, F5)
# ===========================================================================

def test_scenario_3_download_undo_and_file_dialog_exclusion(
    service_instance: Any, fake_desktop: FakeWin32Desktop, create_test_file: Any, app_paths: Any
) -> None:
    """Scenario 3: User sorts file, clicks Undo on ribbon, then opens dialog: undone file is excluded."""
    clear_recent_file(data_dir=app_paths.data_dir)

    # 1. Download file and sort
    invoice = create_test_file("Client_Invoice_99.pdf")
    summary = service_instance.scan_top_level_now(settle_seconds=0.0)
    assert len(summary["moved"]) == 1
    move_id = summary["moved"][0]["id"]
    dest_path = summary["moved"][0]["dest_path"]

    # Verify offered in recent state
    recent = get_recent_file(data_dir=app_paths.data_dir)
    assert recent is not None
    assert recent["name"] == "Client_Invoice_99.pdf"

    # 2. User clicks Undo
    undo_result = service_instance.undo_single_move(move_id)
    assert undo_result["restored"] == 1
    assert invoice.exists(), "Invoice was not restored to download folder"

    # 3. Invalidation purged recency offering
    assert get_recent_file(data_dir=app_paths.data_dir) is None

    # 4. Dialog opens; no pill offered
    dlg = fake_desktop.create_dialog()
    assert is_file_dialog(dlg) is True
    assert get_recent_file(data_dir=app_paths.data_dir) is None  # Nothing to show

    clear_recent_file(data_dir=app_paths.data_dir)


# ===========================================================================
# Scenario 4: High-Frequency Rapid Downloads Coalescing (F2, F5)
# ===========================================================================

def test_scenario_4_high_frequency_rapid_downloads_coalescing(
    app_paths: Any, create_test_file: Any
) -> None:
    """Scenario 4: 55 rapid file downloads coalesce cleanly without race conditions or exceeding 50 cap."""
    clear_recent_file(data_dir=app_paths.data_dir)
    base_time = time.time()

    # Simulate 55 file exports finishing every 0.3 seconds
    for i in range(1, 56):
        f = create_test_file(f"batch_export_{i:02d}.csv")
        record_recent_file(
            f,
            downloads_root=app_paths.downloads_dir,
            now=base_time + (i * 0.3),
            data_dir=app_paths.data_dir,
        )

    # Validate state adherence
    recent = get_recent_file(data_dir=app_paths.data_dir)
    assert recent is not None
    # Maximum 50 items cap enforced
    assert recent["batch_count"] == 50
    assert len(recent["items"]) == 50

    # Oldest 5 items dropped; latest 50 present
    names = [item["name"] for item in recent["items"]]
    assert "batch_export_01.csv" not in names
    assert "batch_export_05.csv" not in names
    assert "batch_export_06.csv" in names
    assert "batch_export_55.csv" in names

    # All share single unified batch_id
    batch_ids = {item.get("source_batch", recent["batch_id"]) for item in recent["items"]}
    assert len(batch_ids) == 1

    clear_recent_file(data_dir=app_paths.data_dir)


# ===========================================================================
# Scenario 5: Elevated Target Process UIPI Block Notification (F1, F3, F4)
# ===========================================================================

def test_scenario_5_elevated_target_process_uipi_block_notification(
    fake_desktop: FakeWin32Desktop, create_test_file: Any, v3_qapp: Any
) -> None:
    """Scenario 5: Admin dialog triggers UIPI detection; blocks direct injection and offers OLE drag."""
    cad_drawing = create_test_file("chassis_blueprint.dxf")

    # 1. Elevated dialog opened by administrator process
    admin_dlg = fake_desktop.create_dialog(title="Open Drawing", elevated=True)

    # 2. Dialog classification succeeds
    assert is_file_dialog(admin_dlg) is True

    # 3. UIPI check detects elevation block
    assert blocked_by_uipi(admin_dlg) is True

    # 4. Attempting injection returns blocked result with ADMIN_WARNING
    res = inject_files_into_dialog(admin_dlg, [cad_drawing])
    assert res.ok is False
    assert res.mode == "blocked"
    assert res.message == ADMIN_WARNING

    # 5. Native OLE drag fallback payload buildable
    payload = build_dropfiles([cad_drawing])
    assert len(payload) > 20
    assert cad_drawing.name.encode("utf-16-le") in payload

    # 6. OLE drag loop executes cleanly with mock desktop
    effect = start_file_drag([cad_drawing], admin_dlg)
    assert effect == 1  # DROPEFFECT_COPY
