"""Unit and integration tests for the Recency-Gated Quick Switcher."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

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

    def test_pill_select_file_lifecycle_and_dismiss(self) -> None:
        from quick_switcher import QuickSwitcherPill
        from ribbon_popup import RIBBON_DISMISS_FILE

        with tempfile.TemporaryDirectory() as td:
            sample_file = Path(td) / "Lifecycle_Part.step"
            sample_file.write_text("step data")
            record_recent_file(sample_file)
            recent = get_recent_file()
            self.assertIsNotNone(recent)

            pill = QuickSwitcherPill(dialog_hwnd=12345, recent_data=recent)
            self.assertFalse(pill.is_closed)
            self.assertFalse(pill._is_injecting)

            # Trigger select_file
            pill.select_file()
            self.assertTrue(pill._is_injecting)
            # Must clear recent file state
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
                self.assertEqual(prop_val, 0)


if __name__ == "__main__":
    unittest.main()
