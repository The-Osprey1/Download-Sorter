"""Unit and integration tests for the Recency-Gated Quick Switcher."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import time
import unittest

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


if __name__ == "__main__":
    unittest.main()
