"""Unit and integration tests for the Recency-Gated Quick Switcher."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

if sys.platform == "win32":
    import ctypes
    import tkinter as tk
    from tkinter import filedialog

from quick_switcher import (
    clear_recent_file,
    find_dialog_edit_control,
    get_recent_file,
    inject_file_into_dialog,
    is_file_dialog,
    record_recent_file,
)


class QuickSwitcherStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_root = Path(self.temp_dir.name)
        clear_recent_file()

    def tearDown(self) -> None:
        clear_recent_file()
        self.temp_dir.cleanup()

    def test_record_and_get_recent_file(self) -> None:
        sample_file = self.test_root / "Spindle_Adapter.step"
        sample_file.write_text("dummy 3d content")

        record_recent_file(sample_file, downloads_root=self.test_root)
        recent = get_recent_file(max_age_sec=300.0)

        self.assertIsNotNone(recent)
        self.assertEqual(recent["name"], "Spindle_Adapter.step")
        self.assertEqual(recent["category"], "cad")
        self.assertEqual(recent["color"], "#69E6AD")
        self.assertTrue(Path(recent["path"]).exists())
        self.assertIn("age_sec", recent)
        self.assertLess(recent["age_sec"], 5.0)

    def test_special_characters_and_spaces_in_path(self) -> None:
        sub_dir = self.test_root / "Project, Part #1 & Drafts"
        sub_dir.mkdir(parents=True, exist_ok=True)
        complex_file = sub_dir / "Complex (Model) [v2.0] + test.step"
        complex_file.write_text("complex 3d data")

        record_recent_file(complex_file, downloads_root=self.test_root)
        recent = get_recent_file(max_age_sec=300.0)

        self.assertIsNotNone(recent)
        self.assertEqual(recent["name"], "Complex (Model) [v2.0] + test.step")
        self.assertEqual(recent["path"], str(complex_file.resolve()))
        self.assertTrue(Path(recent["path"]).exists())

    def test_recency_timeout_expiration(self) -> None:
        sample_file = self.test_root / "test_notes.pdf"
        sample_file.write_text("dummy pdf content")

        record_recent_file(sample_file, downloads_root=self.test_root)
        # Should return None if max_age_sec is 0 or negative
        self.assertIsNone(get_recent_file(max_age_sec=0.0))

    def test_deleted_file_returns_none(self) -> None:
        sample_file = self.test_root / "temp_to_delete.txt"
        sample_file.write_text("hello")

        record_recent_file(sample_file, downloads_root=self.test_root)
        self.assertIsNotNone(get_recent_file())

        # Delete file from disk
        sample_file.unlink()
        self.assertIsNone(get_recent_file())

    def test_clear_recent_file(self) -> None:
        sample_file = self.test_root / "archive.zip"
        sample_file.write_text("dummy archive")

        record_recent_file(sample_file, downloads_root=self.test_root)
        self.assertIsNotNone(get_recent_file())

        clear_recent_file()
        self.assertIsNone(get_recent_file())


class QuickSwitcherWin32SafetyTests(unittest.TestCase):
    def test_invalid_dialog_hwnd_safety(self) -> None:
        # Must return False safely and not raise an exception
        self.assertFalse(is_file_dialog(0))
        self.assertFalse(is_file_dialog(-1))
        self.assertFalse(is_file_dialog(999999999))

    def test_find_edit_control_safety(self) -> None:
        self.assertIsNone(find_dialog_edit_control(0))
        self.assertIsNone(find_dialog_edit_control(-1))

    def test_inject_file_safety(self) -> None:
        self.assertFalse(inject_file_into_dialog(0, "C:/fake/path.step"))
        self.assertFalse(inject_file_into_dialog(-1, "C:/fake/path.step"))

    def test_inject_nonexistent_file_returns_false(self) -> None:
        # Must verify file exists before attempting to inject
        self.assertFalse(inject_file_into_dialog(12345, "C:/totally_nonexistent_file.xyz"))

    @unittest.skipUnless(sys.platform == "win32", "Windows Win32 dialog integration test")
    def test_live_dialog_injection_e2e(self) -> None:
        user32 = ctypes.windll.user32
        with tempfile.TemporaryDirectory() as td:
            sample_file = Path(td) / "E2E_Test_Model.step"
            sample_file.write_text("solid model data")

            chosen_result: list[str] = []

            def background_agent() -> None:
                deadline = time.time() + 4.0
                while time.time() < deadline:
                    time.sleep(0.05)
                    found_hwnd = 0

                    def enum_wnd(h, _l):
                        nonlocal found_hwnd
                        buf = (ctypes.c_wchar * 256)()
                        user32.GetWindowTextW(h, buf, 256)
                        if "Live E2E Test Dialog" in buf.value:
                            found_hwnd = h
                            return False
                        return True

                    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.wintypes.BOOL, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
                    user32.EnumWindows(WNDENUMPROC(enum_wnd), 0)

                    if found_hwnd and is_file_dialog(found_hwnd):
                        time.sleep(0.08)
                        if inject_file_into_dialog(found_hwnd, sample_file):
                            return

                # Safety fallback: dismiss dialog if test timed out
                if found_hwnd:
                    user32.PostMessageW(found_hwnd, 0x0111, 2, 0)  # IDCANCEL = 2

            t = threading.Thread(target=background_agent, daemon=True)
            t.start()

            root = tk.Tk()
            root.withdraw()
            res = filedialog.askopenfilename(title="Live E2E Test Dialog")
            if res:
                chosen_result.append(res)
            root.destroy()

            self.assertTrue(len(chosen_result) > 0)
            self.assertEqual(Path(chosen_result[0]).resolve(), sample_file.resolve())

    def test_pill_select_file_success_lifecycle(self) -> None:
        from unittest.mock import MagicMock, patch
        from quick_switcher import QuickSwitcherPill
        from ribbon_popup import RIBBON_DISMISS_FILE

        with tempfile.TemporaryDirectory() as td:
            sample_file = Path(td) / "Lifecycle_Part.step"
            sample_file.write_text("step data")
            record_recent_file(sample_file)
            recent = get_recent_file()
            self.assertIsNotNone(recent)

            mock_tk_inst = MagicMock()
            mock_tk_inst.winfo_id.return_value = 0
            mock_tk_inst.winfo_fpixels.return_value = 96.0

            with patch("quick_switcher.tk.Tk", return_value=mock_tk_inst), \
                 patch.object(QuickSwitcherPill, "_build_ui"), \
                 patch.object(QuickSwitcherPill, "_position_pill"), \
                 patch.object(QuickSwitcherPill, "_register_hotkey"):
                pill = QuickSwitcherPill(dialog_hwnd=12345, recent_data=recent)
                pill.action_btn = MagicMock()
                self.assertFalse(pill.is_closed)
                self.assertFalse(pill._is_injecting)

                with patch("quick_switcher.inject_file_into_dialog", return_value=True):
                    # Trigger select_file
                    pill.select_file()
                    self.assertTrue(pill._is_injecting)
                    # Successful injection must clear recent file state
                    self.assertIsNone(get_recent_file())
                    # Must signal ribbon popups to dismiss
                    self.assertTrue(RIBBON_DISMISS_FILE.exists())

                # Now close pill
                pill.close()
                self.assertTrue(pill.is_closed)

                # Window property cleanup verification on Win32
                if sys.platform == "win32":
                    user32 = ctypes.windll.user32
                    prop_val = user32.GetPropW(12345, "DownloadSorterQuickSwitcher")
                    self.assertFalse(bool(prop_val))

    def test_pill_select_file_failure_preserves_buffer(self) -> None:
        from unittest.mock import MagicMock, patch
        from quick_switcher import QuickSwitcherPill

        with tempfile.TemporaryDirectory() as td:
            sample_file = Path(td) / "Preserve_Part.step"
            sample_file.write_text("step data")
            record_recent_file(sample_file)

            mock_tk_inst = MagicMock()
            mock_tk_inst.winfo_id.return_value = 0
            mock_tk_inst.winfo_fpixels.return_value = 96.0

            with patch("quick_switcher.tk.Tk", return_value=mock_tk_inst), \
                 patch.object(QuickSwitcherPill, "_build_ui"), \
                 patch.object(QuickSwitcherPill, "_position_pill"), \
                 patch.object(QuickSwitcherPill, "_register_hotkey"):
                pill = QuickSwitcherPill(dialog_hwnd=12345, recent_data=get_recent_file())
                pill.action_btn = MagicMock()
                with patch("quick_switcher.inject_file_into_dialog", return_value=False):
                    pill.select_file()
                    # Failed injection must preserve recent buffer for retry
                    self.assertIsNotNone(get_recent_file())

                pill.close()


class QuickSwitcherBatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_root = Path(self.temp_dir.name)
        clear_recent_file()

    def tearDown(self) -> None:
        clear_recent_file()
        self.temp_dir.cleanup()

    def test_batch_coalescing_and_limits(self) -> None:
        import batch_handoff
        # Record 3 files within 10 seconds of each other
        f1 = self.test_root / "part1.stl"
        f2 = self.test_root / "part2.stl"
        f3 = self.test_root / "part3.stl"
        for f in (f1, f2, f3):
            f.write_text("mesh")

        t0 = time.time() - 20.0
        batch_handoff.record_recent_file(f1, downloads_root=self.test_root, now=t0)
        batch_handoff.record_recent_file(f2, downloads_root=self.test_root, now=t0 + 5.0)
        batch_handoff.record_recent_file(f3, downloads_root=self.test_root, now=t0 + 10.0)

        data = get_recent_file()
        self.assertIsNotNone(data)
        self.assertEqual(data["batch_count"], 3)
        self.assertEqual(len(data["items"]), 3)
        self.assertEqual(data["items"][-1]["name"], "part3.stl")

        # Gap > 25s starts a new batch
        f4 = self.test_root / "part4.stl"
        f4.write_text("mesh")
        batch_handoff.record_recent_file(f4, downloads_root=self.test_root, now=t0 + 40.0)
        data2 = get_recent_file(max_age_sec=300.0)
        self.assertIsNotNone(data2)
        self.assertEqual(data2["batch_count"], 1)
        self.assertEqual(data2["items"][0]["name"], "part4.stl")

    def test_batch_cap_enforced(self) -> None:
        import batch_handoff
        settings = batch_handoff.batch_settings()
        settings["max_files"] = 5
        files = []
        base = time.time() - 10.0
        for i in range(8):
            p = self.test_root / f"model_{i}.stl"
            p.write_text("mesh")
            files.append(p)
            batch_handoff.record_recent_file(p, downloads_root=self.test_root, settings=settings, now=base + i)

        data = get_recent_file()
        self.assertIsNotNone(data)
        self.assertEqual(data["batch_count"], 5)
        # Oldest 3 should have been dropped (0, 1, 2)
        names = [item["name"] for item in data["items"]]
        self.assertEqual(names, ["model_3.stl", "model_4.stl", "model_5.stl", "model_6.stl", "model_7.stl"])

    def test_batch_select_latest_leaves_remaining(self) -> None:
        from unittest.mock import MagicMock, patch
        from quick_switcher import QuickSwitcherPill

        f1 = self.test_root / "batch1.step"
        f2 = self.test_root / "batch2.step"
        f1.write_text("step1")
        f2.write_text("step2")
        record_recent_file(f1, downloads_root=self.test_root)
        record_recent_file(f2, downloads_root=self.test_root)

        recent = get_recent_file()
        self.assertIsNotNone(recent)
        self.assertTrue(len(recent["items"]) == 2)

        mock_tk = MagicMock()
        mock_tk.winfo_id.return_value = 0
        mock_tk.winfo_fpixels.return_value = 96.0

        with patch("quick_switcher.tk.Tk", return_value=mock_tk), \
             patch.object(QuickSwitcherPill, "_build_ui"), \
             patch.object(QuickSwitcherPill, "_position_pill"), \
             patch.object(QuickSwitcherPill, "_register_hotkey"):
            pill = QuickSwitcherPill(dialog_hwnd=12345, recent_data=recent)
            pill.action_btn = MagicMock()

            with patch("quick_switcher.inject_file_into_dialog", return_value=True):
                pill.select_file()
                # f2 was consumed, f1 must remain in the batch
                rem = get_recent_file()
                self.assertIsNotNone(rem)
                self.assertEqual(rem["name"], "batch1.step")

            pill.close()

    def test_batch_copy_files(self) -> None:
        import batch_handoff
        f1 = self.test_root / "clip1.stl"
        f2 = self.test_root / "clip2.stl"
        f1.write_text("data")
        f2.write_text("data")

        # Test build_dropfiles directly
        drop_bytes = batch_handoff.build_dropfiles([f1, f2])
        self.assertIsNotNone(drop_bytes)
        self.assertTrue(len(drop_bytes) > 20)
        # Verify wide char null-terminated structure contains both file names
        self.assertIn("clip1.stl".encode("utf-16-le"), drop_bytes)
        self.assertIn("clip2.stl".encode("utf-16-le"), drop_bytes)

    def test_staging_fallback_and_cleanup(self) -> None:
        import batch_handoff
        d1 = self.test_root / "FolderA"
        d2 = self.test_root / "FolderB"
        d1.mkdir()
        d2.mkdir()
        f1 = d1 / "fileA.stl"
        f2 = d2 / "fileB.stl"
        f1.write_text("meshA")
        f2.write_text("meshB")

        # Files in different folders trigger staging
        plan = batch_handoff.plan_injection([f1, f2], downloads_root=self.test_root)
        self.assertEqual(plan.kind, "staged")
        self.assertEqual(len(plan.names), 2)

        # Stage batch creates stage folder and hardlinks/copies
        stage, staged = batch_handoff.stage_batch(plan.paths, "test_b", self.test_root)
        self.assertTrue(stage.exists())
        self.assertEqual(len(staged), 2)

        # Prune stages removes stage folders
        # Force mtime into the past
        old_time = time.time() - 7200.0
        os.utime(stage, (old_time, old_time))
        batch_handoff.prune_stages(self.test_root, max_age_hours=1.0)
        self.assertFalse(stage.exists())


class QuickSwitcherFixesTests(unittest.TestCase):
    """Regression tests for Milestone 2 Quick Switcher fixes and theme alignment."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_root = Path(self.temp_dir.name)
        clear_recent_file()

    def tearDown(self) -> None:
        clear_recent_file()
        self.temp_dir.cleanup()

    def test_action_color_defaults_to_mint_accent(self) -> None:
        import quick_switcher
        from suite_theme import ACCENTS
        self.assertEqual(quick_switcher.ACTION_COLOR, ACCENTS["downloadSorter"])
        self.assertEqual(quick_switcher.ACTION_COLOR, "#69E6AD")

    def test_no_forbidden_hex_codes_in_quick_switcher(self) -> None:
        import re
        qs_file = REPO_ROOT / "quick_switcher.py"
        content = qs_file.read_text(encoding="utf-8")
        forbidden = ["#171C26", "#212836", "#282F3D"]
        for code in forbidden:
            matches = re.findall(re.escape(code), content, re.IGNORECASE)
            self.assertEqual(matches, [], f"Forbidden off-palette hex code {code} found in quick_switcher.py")

    def test_empty_selection_guards(self) -> None:
        """select_all and copy_files must guard when 0 files are checked and alert user."""
        import quick_switcher
        f1 = self.test_root / "part1.stl"
        f1.write_text("mesh")
        recent = {
            "name": f1.name,
            "path": str(f1),
            "is_batch": True,
            "items": [{"name": f1.name, "path": str(f1)}],
            "batch_id": "b_test",
        }
        mock_tk_inst = MagicMock()
        mock_tk_inst.winfo_id.return_value = 0
        mock_tk_inst.winfo_fpixels.return_value = 96.0

        with patch("quick_switcher.tk.Tk", return_value=mock_tk_inst), \
             patch.object(quick_switcher.QuickSwitcherPill, "_build_ui"), \
             patch.object(quick_switcher.QuickSwitcherPill, "_position_pill"), \
             patch.object(quick_switcher.QuickSwitcherPill, "_register_hotkey"):
            pill = quick_switcher.QuickSwitcherPill(dialog_hwnd=123, recent_data=recent)
            pill.all_btn = MagicMock()
            pill.copy_btn = MagicMock()
            pill._flash = MagicMock()

            # Empty selection: untick f1
            pill.checked[str(f1)] = False
            self.assertEqual(len(pill._checked_paths()), 0)

            # select_all guard
            with patch("batch_handoff.inject_files_into_dialog") as mock_inject:
                pill.select_all()
                mock_inject.assert_not_called()
                pill._flash.assert_called_with(pill.all_btn, "✕ No files selected", False)

            pill._flash.reset_mock()

            # copy_files guard
            with patch("batch_handoff.copy_files_to_clipboard") as mock_copy:
                pill.copy_files()
                mock_copy.assert_not_called()
                pill._flash.assert_called_with(pill.copy_btn, "✕ No files selected", False)

            pill.close()

    def test_checklist_collapse_mousewheel_cleanup(self) -> None:
        """_collapse_checklist must call unbind_all('<MouseWheel>') on canvas."""
        import quick_switcher
        f1 = self.test_root / "part1.stl"
        f1.write_text("mesh")
        recent = {
            "name": f1.name,
            "path": str(f1),
            "is_batch": True,
            "items": [{"name": f1.name, "path": str(f1)}],
        }
        mock_tk_inst = MagicMock()
        mock_tk_inst.winfo_id.return_value = 0
        mock_tk_inst.winfo_fpixels.return_value = 96.0

        with patch("quick_switcher.tk.Tk", return_value=mock_tk_inst), \
             patch.object(quick_switcher.QuickSwitcherPill, "_build_ui"), \
             patch.object(quick_switcher.QuickSwitcherPill, "_position_pill"), \
             patch.object(quick_switcher.QuickSwitcherPill, "_register_hotkey"):
            pill = quick_switcher.QuickSwitcherPill(dialog_hwnd=0, recent_data=recent)
            mock_canvas = MagicMock()
            mock_win = MagicMock()
            pill.checklist_canvas = mock_canvas
            pill.checklist_win = mock_win

            pill._collapse_checklist()
            mock_canvas.unbind_all.assert_called_once_with("<MouseWheel>")
            mock_win.destroy.assert_called_once()
            self.assertIsNone(pill.checklist_canvas)
            self.assertIsNone(pill.checklist_win)

            pill.close()


if __name__ == "__main__":
    unittest.main()