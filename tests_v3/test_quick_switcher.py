"""Comprehensive automated tests for Recency-Gated Quick Switcher / Recent File Insert."""

from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import sys
import threading
import time
from typing import Any
from unittest.mock import patch

import pytest

from PySide6.QtWidgets import QApplication

from download_sorter.config import Config, import_v2_config, load_config, save_config
from download_sorter.paths import AppPaths
from download_sorter.quick_switcher import (
    IDCANCEL,
    WM_COMMAND,
    QuickSwitcherMonitor,
    blocked_by_uipi,
    build_dropfiles,
    clear_recent_file,
    copy_files_to_clipboard,
    dismiss_dialog,
    find_dialog_edit_control,
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
    window_is_elevated,
)
from tests_v3.mock_win32 import FakeWin32Desktop
from download_sorter.service import SorterService
from download_sorter.ui.quick_switcher_pill import QuickSwitcherPill


# ---------------------------------------------------------------------------
# Recency Buffer & State Tests
# ---------------------------------------------------------------------------

def test_record_and_get_recent_file(tmp_path: Path) -> None:
    clear_recent_file(data_dir=tmp_path)
    sample = tmp_path / "Bracket_Mount.step"
    sample.write_text("solid CAD data", encoding="utf-8")

    record_recent_file(sample, downloads_root=tmp_path, data_dir=tmp_path)
    recent = get_recent_file(max_age_sec=300.0, data_dir=tmp_path)

    assert recent is not None
    assert recent["name"] == "Bracket_Mount.step"
    assert recent["category"] == "cad"
    assert recent["color"] == "#69E6AD"
    assert Path(recent["path"]).exists()
    assert "age_sec" in recent
    assert recent["age_sec"] < 5.0
    assert recent["batch_count"] == 1
    clear_recent_file(data_dir=tmp_path)


def test_recency_ttl_standard_and_expiration(tmp_path: Path) -> None:
    clear_recent_file(data_dir=tmp_path)
    doc_file = tmp_path / "Quarterly_Report.pdf"
    doc_file.write_text("dummy report content", encoding="utf-8")

    record_recent_file(doc_file, downloads_root=tmp_path, data_dir=tmp_path)
    # Available under standard 300s window
    assert get_recent_file(max_age_sec=300.0, data_dir=tmp_path) is not None
    # Expired under 0.0s window
    assert get_recent_file(max_age_sec=0.0, data_dir=tmp_path) is None
    clear_recent_file(data_dir=tmp_path)


def test_3d_model_recency_extension_12h(tmp_path: Path) -> None:
    clear_recent_file(data_dir=tmp_path)
    cad_file = tmp_path / "Tool_Assembly.3mf"
    cad_file.write_text("3mf zip model data", encoding="utf-8")

    now = time.time()
    # Recorded 2 hours ago (7200 seconds ago)
    record_recent_file(cad_file, downloads_root=tmp_path, now=now - 7200.0, data_dir=tmp_path)

    # Standard TTL is 300s, but 3D files stay offered up to 12 hours (43200s)
    recent = get_recent_file(max_age_sec=300.0, data_dir=tmp_path)
    assert recent is not None
    assert recent["name"] == "Tool_Assembly.3mf"
    assert recent["age_sec"] >= 7199.0

    # Beyond 12 hours (e.g. 13 hours ago) -> expires
    clear_recent_file(data_dir=tmp_path)
    record_recent_file(cad_file, downloads_root=tmp_path, now=now - (13 * 3600.0), data_dir=tmp_path)
    assert get_recent_file(max_age_sec=300.0, data_dir=tmp_path) is None
    clear_recent_file(data_dir=tmp_path)


def test_deleted_file_pruned(tmp_path: Path) -> None:
    clear_recent_file(data_dir=tmp_path)
    temp_file = tmp_path / "ephemeral.txt"
    temp_file.write_text("hello world", encoding="utf-8")

    record_recent_file(temp_file, downloads_root=tmp_path, data_dir=tmp_path)
    assert get_recent_file(data_dir=tmp_path) is not None

    temp_file.unlink()
    assert get_recent_file(data_dir=tmp_path) is None


def test_clear_and_invalidate_recent_file(tmp_path: Path) -> None:
    clear_recent_file(data_dir=tmp_path)
    f1 = tmp_path / "item1.stl"
    f2 = tmp_path / "item2.stl"
    f1.write_text("stl1", encoding="utf-8")
    f2.write_text("stl2", encoding="utf-8")

    t0 = time.time()
    record_recent_file(f1, downloads_root=tmp_path, now=t0, data_dir=tmp_path)
    record_recent_file(f2, downloads_root=tmp_path, now=t0 + 1.0, data_dir=tmp_path)

    batch = get_recent_file(data_dir=tmp_path)
    assert batch is not None
    assert batch["batch_count"] == 2

    # Invalidate f2 -> only f1 remains
    invalidate_recent_file(f2, data_dir=tmp_path)
    rem = get_recent_file(data_dir=tmp_path)
    assert rem is not None
    assert rem["name"] == "item1.stl"
    assert rem["batch_count"] == 1

    # Invalidate f1 -> buffer cleared
    invalidate_recent_file(f1, data_dir=tmp_path)
    assert get_recent_file(data_dir=tmp_path) is None


def test_batch_coalescing_gap_span_and_cap(tmp_path: Path) -> None:
    clear_recent_file(data_dir=tmp_path)
    f1 = tmp_path / "batch1.step"
    f2 = tmp_path / "batch2.step"
    f3 = tmp_path / "batch3.step"
    for f in (f1, f2, f3):
        f.write_text("step", encoding="utf-8")

    base = time.time() - 20.0
    record_recent_file(f1, downloads_root=tmp_path, now=base, data_dir=tmp_path)
    record_recent_file(f2, downloads_root=tmp_path, now=base + 5.0, data_dir=tmp_path)
    record_recent_file(f3, downloads_root=tmp_path, now=base + 10.0, data_dir=tmp_path)

    data = get_recent_file(data_dir=tmp_path)
    assert data is not None
    assert data["batch_count"] == 3
    assert len(data["items"]) == 3

    # Gap > 25s starts a new batch
    f4 = tmp_path / "batch4.step"
    f4.write_text("step4", encoding="utf-8")
    record_recent_file(f4, downloads_root=tmp_path, now=base + 40.0, data_dir=tmp_path)

    data2 = get_recent_file(data_dir=tmp_path)
    assert data2 is not None
    assert data2["batch_count"] == 1
    assert data2["items"][0]["name"] == "batch4.step"

    # Enforce batch cap of 5 files
    settings = {"gap_seconds": 25.0, "max_span_seconds": 120.0, "max_files": 5}
    now = time.time()
    for i in range(8):
        p = tmp_path / f"cap_test_{i}.step"
        p.write_text("cad", encoding="utf-8")
        record_recent_file(p, downloads_root=tmp_path, settings=settings, now=now + i, data_dir=tmp_path)

    data_capped = get_recent_file(data_dir=tmp_path)
    assert data_capped is not None
    assert data_capped["batch_count"] == 5
    names = [i["name"] for i in data_capped["items"]]
    assert names == [f"cap_test_{i}.step" for i in range(3, 8)]
    clear_recent_file(data_dir=tmp_path)


# ---------------------------------------------------------------------------
# Staging & Dropfiles Tests
# ---------------------------------------------------------------------------

def test_build_dropfiles_payload(tmp_path: Path) -> None:
    p1 = tmp_path / "part_alpha.stl"
    p2 = tmp_path / "part_beta.stl"
    p1.touch()
    p2.touch()

    payload = build_dropfiles([p1, p2])
    assert isinstance(payload, bytes)
    assert len(payload) > 20
    assert "part_alpha.stl".encode("utf-16-le") in payload
    assert "part_beta.stl".encode("utf-16-le") in payload


def test_batch_staging_and_pruning(tmp_path: Path) -> None:
    d1 = tmp_path / "Folder1"
    d2 = tmp_path / "Folder2"
    d1.mkdir()
    d2.mkdir()
    f1 = d1 / "file1.stl"
    f2 = d2 / "file2.stl"
    f1.write_text("stl1", encoding="utf-8")
    f2.write_text("stl2", encoding="utf-8")

    plan = plan_injection([f1, f2], downloads_root=tmp_path)
    assert plan.kind == "staged"
    assert len(plan.names) == 2

    stage_dir, staged = stage_batch(plan.paths, "batch_test_stage", tmp_path)
    assert stage_dir.exists()
    assert len(staged) == 2

    # Prune stages older than 1 hour
    old_mtime = time.time() - 7200.0
    os.utime(stage_dir, (old_mtime, old_mtime))
    removed = prune_stages(downloads_root=tmp_path, max_age_hours=1.0)
    assert removed >= 1
    assert not stage_dir.exists()


# ---------------------------------------------------------------------------
# Win32 Dialog Discovery & Injection Safety Tests
# ---------------------------------------------------------------------------

def test_win32_invalid_hwnd_safety() -> None:
    """Verify functions safely return booleans / None on invalid HWNDs without raising."""
    assert is_file_dialog(0) is False
    assert is_file_dialog(-1) is False
    assert is_file_dialog(999999999) is False

    assert find_dialog_edit_control(0) is None
    assert find_dialog_edit_control(-1) is None
    assert find_dialog_edit_control(999999999) is None

    assert inject_file_into_dialog(0, "C:/fake/path.step") is False
    assert inject_file_into_dialog(-1, "C:/fake/path.step") is False
    assert inject_file_into_dialog(12345, "C:/totally_nonexistent_file.xyz") is False


def test_is_file_dialog_mock_classification(monkeypatch: pytest.MonkeyPatch) -> None:
    if sys.platform != "win32":
        pytest.skip("Win32 ctypes test")

    class MockUser32:
        def __init__(self, class_name: str, title: str, has_edit: bool = True, button_text: str = "&Open") -> None:
            self._class_name = class_name
            self._title = title
            self._has_edit = has_edit
            self._button_text = button_text

        def IsWindow(self, hwnd: int) -> int:
            return 1 if hwnd > 0 else 0

        def IsWindowVisible(self, hwnd: int) -> int:
            return 1 if hwnd > 0 else 0

        def GetClassNameW(self, hwnd: int, buf: Any, max_len: int) -> int:
            buf.value = self._class_name
            return len(buf.value)

        def GetWindowTextW(self, hwnd: int, buf: Any, max_len: int) -> int:
            buf.value = self._title
            return len(buf.value)

        def EnumChildWindows(self, hwnd: int, proc: Any, lparam: int) -> int:
            if self._has_edit:
                # Call proc with fake Edit control handle
                h_edit = 2001
                def fake_get_class(h, b, m):
                    b.value = "ComboBoxEx32"
                    return 12
                self.GetClassNameW = fake_get_class
                proc(h_edit, lparam)
            # Call proc with fake Button control handle
            h_btn = 2002
            def fake_btn_class(h, b, m):
                b.value = "Button"
                return 6
            def fake_btn_text(h, b, m):
                b.value = self._button_text
                return len(self._button_text)
            self.GetClassNameW = fake_btn_class
            self.GetWindowTextW = fake_btn_text
            proc(h_btn, lparam)
            return 1

    # Valid Open dialog
    mock_u32 = MockUser32("#32770", "Open File", has_edit=True, button_text="&Open")
    monkeypatch.setattr(ctypes.windll, "user32", mock_u32)
    assert is_file_dialog(1001) is True

    # Save As title rejection
    mock_u32_save = MockUser32("#32770", "Save As", has_edit=True, button_text="&Save")
    monkeypatch.setattr(ctypes.windll, "user32", mock_u32_save)
    assert is_file_dialog(1002) is False

    # Foreign language Save title rejection ("Speichern unter")
    mock_u32_de = MockUser32("#32770", "Speichern unter", has_edit=True, button_text="&Speichern")
    monkeypatch.setattr(ctypes.windll, "user32", mock_u32_de)
    assert is_file_dialog(1003) is False

    # Non-#32770 window rejection (e.g. Chrome window)
    mock_u32_chrome = MockUser32("Chrome_WidgetWin_1", "Open File", has_edit=True)
    monkeypatch.setattr(ctypes.windll, "user32", mock_u32_chrome)
    assert is_file_dialog(1004) is False


def test_find_dialog_edit_control_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    if sys.platform != "win32":
        pytest.skip("Win32 ctypes test")

    class MockUser32Edit:
        def IsWindow(self, hwnd: int) -> int:
            return 1

        def IsWindowVisible(self, hwnd: int) -> int:
            return 1

        def IsWindowEnabled(self, hwnd: int) -> int:
            return 1

        def GetClassNameW(self, hwnd: int, buf: Any, max_len: int) -> int:
            buf.value = "Edit"
            return 4

        def GetDlgCtrlID(self, hwnd: int) -> int:
            return 1148 if hwnd == 5001 else 9999

        def GetParent(self, hwnd: int) -> int:
            return 0

        def EnumChildWindows(self, hwnd: int, proc: Any, lparam: int) -> int:
            # Emit child handle 5001 (ctrl_id 1148)
            proc(5001, lparam)
            return 1

    monkeypatch.setattr(ctypes.windll, "user32", MockUser32Edit())
    edit_hwnd = find_dialog_edit_control(1001)
    assert edit_hwnd == 5001


def test_inject_file_into_dialog_mock(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    if sys.platform != "win32":
        pytest.skip("Win32 ctypes test")

    sample_file = tmp_path / "mock_inject.step"
    sample_file.write_text("solid CAD", encoding="utf-8")

    sent_messages: list[tuple[int, int, int, Any]] = []
    posted_messages: list[tuple[int, int, int, int]] = []

    class MockUser32Inject:
        def IsWindow(self, hwnd: int) -> int:
            return 1

        def IsWindowVisible(self, hwnd: int) -> int:
            return 1

        def IsWindowEnabled(self, hwnd: int) -> int:
            return 1

        def SetForegroundWindow(self, hwnd: int) -> int:
            return 1

        def SendMessageW(self, hwnd: int, msg: int, wparam: int, lparam: Any) -> int:
            sent_messages.append((hwnd, msg, wparam, lparam))
            return 0

        def PostMessageW(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
            posted_messages.append((hwnd, msg, wparam, lparam))
            return 1

    monkeypatch.setattr(ctypes.windll, "user32", MockUser32Inject())
    monkeypatch.setattr("download_sorter.quick_switcher.find_dialog_edit_control", lambda _h: 7001)

    ok = inject_file_into_dialog(6001, sample_file)
    assert ok is True
    # Verify WM_SETTEXT (0x000C) was sent with file path
    assert any(msg == 0x000C and str(sample_file.resolve()) in str(lparam) for _h, msg, _w, lparam in sent_messages)
    # Verify VK_RETURN (0x0D) and IDOK (1) were posted
    assert any(wparam == 0x0D for _h, _msg, wparam, _l in posted_messages)
    assert any(wparam == 1 for _h, _msg, wparam, _l in posted_messages)


# ---------------------------------------------------------------------------
# Configuration Persistence & Importer Tests
# ---------------------------------------------------------------------------

def test_quick_switcher_config_fields_and_persistence(app_paths: AppPaths) -> None:
    cfg = Config(
        quick_switcher_enabled=True,
        quick_switcher_timeout_seconds=420.0,
        quick_switcher_3d_timeout_hours=16.0,
        quick_switcher_hotkey="Ctrl+Shift+F",
    )
    save_config(cfg, app_paths)
    loaded = load_config(app_paths)

    assert loaded.quick_switcher_enabled is True
    assert loaded.quick_switcher_timeout_seconds == 420.0
    assert loaded.quick_switcher_3d_timeout_hours == 16.0
    assert loaded.quick_switcher_hotkey == "Ctrl+Shift+F"


def test_import_v2_config_quick_switcher(tmp_path: Path) -> None:
    v2_json = tmp_path / "v2_config.json"
    v2_json.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "quick_switcher_enabled": True,
                "batch_handoff": {
                    "enabled": True,
                    "timeout_seconds": 250.0,
                    "3d_timeout_hours": 8.0,
                    "hotkey_latest": "Ctrl+Shift+K",
                    "hotkey_all": "Ctrl+Alt+A",
                    "hotkey_copy": "Ctrl+Alt+C",
                },
            }
        ),
        encoding="utf-8",
    )
    imported = import_v2_config(v2_json)
    assert imported.quick_switcher_enabled is True
    assert imported.quick_switcher_timeout_seconds == 250.0
    assert imported.quick_switcher_3d_timeout_hours == 8.0
    assert imported.quick_switcher_hotkey == "Ctrl+Shift+K"
    assert imported.quick_switcher_hotkey_all == "Ctrl+Alt+A"
    assert imported.quick_switcher_hotkey_copy == "Ctrl+Alt+C"


# ---------------------------------------------------------------------------
# Monitor Daemon Lifecycle Tests
# ---------------------------------------------------------------------------

def test_quick_switcher_monitor_lifecycle(tmp_path: Path) -> None:
    stop_event = threading.Event()
    detected_events: list[tuple[int, dict]] = []

    def on_detected(hwnd: int, data: dict) -> None:
        detected_events.append((hwnd, data))

    monitor = QuickSwitcherMonitor(
        on_dialog_detected=on_detected,
        check_interval=0.05,
        stop_event=stop_event,
        data_dir=tmp_path,
    )
    monitor.start()
    assert monitor.is_alive()

    # Clean stop
    monitor.stop()
    monitor.join(timeout=2.0)
    assert not monitor.is_alive()


# ---------------------------------------------------------------------------
# SorterService & Undo Integration Tests
# ---------------------------------------------------------------------------

def test_service_records_recent_file_and_undo_invalidates(app_paths: AppPaths) -> None:
    clear_recent_file(data_dir=app_paths.data_dir)
    cfg = Config.with_starter_defaults()
    cfg.settle_seconds = 0.0
    save_config(cfg, app_paths)

    service = SorterService(app_paths)
    watch_root = service.watch_root

    test_file = watch_root / "Design_Prototype.step"
    test_file.write_text("cad prototype model", encoding="utf-8")

    # Scan top-level and execute move with settle_seconds=0.0
    res = service.scan_top_level_now(settle_seconds=0.0)
    assert len(res["moved"]) == 1
    dest = Path(res["moved"][0]["dest_path"])
    assert dest.exists()

    # Recent file was recorded
    recent = get_recent_file(data_dir=app_paths.data_dir)
    assert recent is not None
    assert recent["name"] == "Design_Prototype.step"

    # Undo batch restores file and invalidates recent buffer
    undo_res = service.undo_last_batch()
    assert undo_res["restored"] == 1
    assert not dest.exists()
    assert (watch_root / "Design_Prototype.step").exists()

    # Recent file buffer was invalidated
    assert get_recent_file(data_dir=app_paths.data_dir) is None
    clear_recent_file(data_dir=app_paths.data_dir)


# ---------------------------------------------------------------------------
# PySide6 Floating Pill UI Tests (Offscreen)
# ---------------------------------------------------------------------------

def test_quick_switcher_pill_single_file_ui(v3_qapp: QApplication, tmp_path: Path) -> None:
    f = tmp_path / "Impeller.stl"
    f.write_text("mesh data", encoding="utf-8")

    recent_data = {
        "path": str(f.resolve()),
        "name": f.name,
        "category": "cad",
        "color": "#69E6AD",
        "timestamp": time.time(),
        "items": [{"path": str(f.resolve()), "name": f.name, "category": "cad", "color": "#69E6AD"}],
    }

    with patch.object(QuickSwitcherPill, "_position_pill"), \
         patch.object(QuickSwitcherPill, "_apply_pill_style"):
        pill = QuickSwitcherPill(dialog_hwnd=12345, recent_data=recent_data)
        assert pill.root is not None
        assert not pill.is_batch
        assert pill.action_btn is not None
        assert "Select" in pill.action_btn.text()
        assert pill.all_btn is None

        pill.close()
        assert pill.is_closed is True


def test_quick_switcher_pill_batch_and_empty_selection_guard(v3_qapp: QApplication, tmp_path: Path) -> None:
    f1 = tmp_path / "part_1.step"
    f2 = tmp_path / "part_2.step"
    f1.write_text("cad1", encoding="utf-8")
    f2.write_text("cad2", encoding="utf-8")

    recent_data = {
        "path": str(f2.resolve()),
        "name": f2.name,
        "category": "cad",
        "color": "#69E6AD",
        "timestamp": time.time(),
        "batch_id": "test_batch_123",
        "items": [
            {"path": str(f1.resolve()), "name": f1.name, "category": "cad", "color": "#69E6AD"},
            {"path": str(f2.resolve()), "name": f2.name, "category": "cad", "color": "#69E6AD"},
        ],
    }

    with patch.object(QuickSwitcherPill, "_position_pill"), \
         patch.object(QuickSwitcherPill, "_apply_pill_style"):
        pill = QuickSwitcherPill(dialog_hwnd=12345, recent_data=recent_data)
        assert pill.is_batch is True
        assert pill.all_btn is not None
        assert "All 2" in pill.all_btn.text()
        assert pill.copy_btn is not None

        # Untick both files to test empty selection guard
        pill.checked[str(f1.resolve())] = False
        pill.checked[str(f2.resolve())] = False
        assert len(pill._checked_paths()) == 0

        # select_all guard test
        with patch("download_sorter.ui.quick_switcher_pill.inject_files_into_dialog") as mock_inject:
            pill.select_all()
            mock_inject.assert_not_called()

        # copy_files guard test
        with patch("download_sorter.ui.quick_switcher_pill.copy_files_to_clipboard") as mock_copy:
            pill.copy_files()
            mock_copy.assert_not_called()

        pill.close()
        assert pill.is_closed is True


# ---------------------------------------------------------------------------
# Live Desktop Common Dialog Interactive Test
# ---------------------------------------------------------------------------

@pytest.mark.desktop
def test_live_dialog_injection_desktop(tmp_path: Path) -> None:
    if sys.platform != "win32":
        pytest.skip("Windows Win32 live dialog test")

    import tkinter as tk
    from tkinter import filedialog

    user32 = ctypes.windll.user32
    sample_file = tmp_path / "Live_E2E_Model.step"
    sample_file.write_text("solid CAD geometry", encoding="utf-8")

    dialog_closed = threading.Event()
    results: list[str] = []

    def find_dialog() -> int:
        target_hwnd = 0
        def enum_wnd(h: int, _: int) -> bool:
            nonlocal target_hwnd
            buf = (ctypes.c_wchar * 256)()
            user32.GetWindowTextW(h, buf, 256)
            if "DSv3 Live Test Dialog" in buf.value:
                target_hwnd = h
                return False
            return True
        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.wintypes.BOOL, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
        user32.EnumWindows(WNDENUMPROC(enum_wnd), 0)
        return target_hwnd

    def injector_worker() -> None:
        start_time = time.time()
        injected = False
        while not dialog_closed.is_set():
            time.sleep(0.05)
            dlg = find_dialog()
            if not dlg:
                continue
            if not injected and is_file_dialog(dlg):
                time.sleep(0.08)
                injected = inject_file_into_dialog(dlg, sample_file)
            if time.time() - start_time > 15.0:
                user32.PostMessageW(dlg, WM_COMMAND, IDCANCEL, 0)
                break

    worker = threading.Thread(target=injector_worker, daemon=True)
    worker.start()

    tk_root = tk.Tk()
    tk_root.withdraw()
    try:
        res = filedialog.askopenfilename(title="DSv3 Live Test Dialog")
        if res:
            results.append(res)
    finally:
        dialog_closed.set()
        tk_root.destroy()

    worker.join(timeout=3.0)
    assert len(results) > 0
    assert Path(results[0]).resolve() == sample_file.resolve()


# ---------------------------------------------------------------------------
# Remediation Tests (64-bit Clipboard, HWND Safety, Multilingual Save As)
# ---------------------------------------------------------------------------

def test_clipboard_64bit_allocation_and_roundtrip(tmp_path: Path) -> None:
    """Verify copy_files_to_clipboard handles 64-bit pointers without truncation and roundtrips."""
    if sys.platform != "win32":
        pytest.skip("Windows clipboard test")

    import ctypes
    k32 = ctypes.windll.kernel32

    # Verify ctypes configuration does not truncate 64-bit pointers
    h_test = k32.GlobalAlloc(0x0002 | 0x0040, 128)
    assert h_test != 0
    ptr = k32.GlobalLock(h_test)
    assert ptr != 0, f"Pointer truncation detected! LastError: {k32.GetLastError()}"
    k32.GlobalUnlock(h_test)
    k32.GlobalFree(h_test)

    # Test genuine file copy to clipboard
    f1 = tmp_path / "model_a.step"
    f2 = tmp_path / "model_b.stl"
    f1.write_text("cad1", encoding="utf-8")
    f2.write_text("cad2", encoding="utf-8")

    ok = copy_files_to_clipboard([f1, f2])
    assert ok is True

    # Read back from clipboard and verify roundtrip
    retrieved = read_clipboard_files()
    assert len(retrieved) == 2
    assert str(f1.resolve()) in retrieved
    assert str(f2.resolve()) in retrieved


def test_hwnd_type_and_range_validation_safety(tmp_path: Path) -> None:
    """Verify window_is_elevated and dismiss_dialog handle out-of-range and non-int HWNDs safely."""
    invalid_hwnds = [
        # Out-of-range integer handles (> 32-bit and > 64-bit)
        0x100000000,
        0x7FFFFFFFFFFFFFFF,
        0xFFFFFFFFFFFFFFFF,
        0x10000000000000000,
        # Negative and zero handles
        0,
        -1,
        -0x100000000,
        # Non-integer types
        None,
        3.14,
        "not_a_handle",
        [1234],
        {"hwnd": 1234},
        True,
        False,
    ]

    dummy_file = tmp_path / "dummy.txt"
    dummy_file.write_text("test", encoding="utf-8")

    for h in invalid_hwnds:
        # window_is_elevated must return None or False without crashing
        res = window_is_elevated(h)
        assert res in (None, False), f"Expected None/False for hwnd {h!r}, got {res!r}"

        # blocked_by_uipi must safely return False
        assert blocked_by_uipi(h) is False, f"Expected False for hwnd {h!r}"

        # dismiss_dialog must return None cleanly without crashing
        dismiss_dialog(h)

        # is_file_dialog must return False cleanly
        assert is_file_dialog(h) is False, f"Expected False for hwnd {h!r}"

        # find_dialog_edit_control must return None cleanly
        assert find_dialog_edit_control(h) is None, f"Expected None for hwnd {h!r}"

        # inject_file_into_dialog must return False cleanly
        assert inject_file_into_dialog(h, dummy_file) is False, f"Expected False for hwnd {h!r}"

        # inject_files_into_dialog must return ok=False cleanly
        plan_res = inject_files_into_dialog(h, [dummy_file])
        assert plan_res.ok is False, f"Expected ok=False for hwnd {h!r}"


def test_multilingual_save_as_rejection(fake_desktop: FakeWin32Desktop) -> None:
    """Verify is_file_dialog rejects multilingual Save As titles and buttons."""
    # 1. Single-word and multi-word Save titles across languages
    save_titles = [
        "Save",
        "Save As",
        "Save File",
        "Speichern",
        "Speichern unter",
        "Enregistrer",
        "Enregistrer sous",
        "Guardar",
        "Guardar como",
    ]
    for title in save_titles:
        dlg = fake_desktop.create_dialog(title=title, is_save=True)
        assert is_file_dialog(dlg) is False, f"Failed to reject save dialog title: {title}"

    # 2. Localized Save action buttons
    save_buttons = [
        "&Save", "Save", "Save &Now",
        "&Speichern", "Speichern", "Speichern &unter",
        "&Enregistrer", "Enregistrer", "Enregistrer &sous",
        "&Guardar", "Guardar", "Guardar &como",
    ]
    for btn in save_buttons:
        dlg = fake_desktop.create_dialog(title="Select Destination", button_text=btn)
        assert is_file_dialog(dlg) is False, f"Failed to reject save button: {btn}"

    # 3. Valid Open/Upload dialogs must NOT be rejected
    valid_dialogs = [
        ("Open", "&Open"),
        ("Select File", "&Open"),
        ("Choose File to Upload", "Upload"),
        ("Öffnen", "&Öffnen"),
    ]
    for title, btn in valid_dialogs:
        dlg = fake_desktop.create_dialog(title=title, button_text=btn)
        assert is_file_dialog(dlg) is True, f"Incorrectly rejected valid open dialog: {title} / {btn}"
