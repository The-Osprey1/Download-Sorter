"""Edge-case safety tests for download-sorter.

Validates:
1. Read-only files are not falsely reported as locked by is_file_locked().
2. Actively locked read-only files ARE correctly detected as locked.
3. unique_destination bounds collision loop safely and falls back to UUID.
4. sort_undo dismisses active ribbons and clears recent_file_buffer for restored files.
5. watch._handle_post_move properly honors ribbon_enabled, notifications, and quick_switcher_enabled.
"""

from __future__ import annotations

import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from sorter import is_file_locked, unique_destination
from sort_undo import undo_moves
from watch import _handle_post_move
from quick_switcher import clear_recent_file, get_recent_file, record_recent_file


class TestEdgeCasesSafety(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp_dir.name)
        clear_recent_file()

    def tearDown(self) -> None:
        clear_recent_file()
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_read_only_file_not_falsely_locked(self) -> None:
        """A file with read-only permissions/attributes must NOT be reported as locked."""
        test_file = self.dir / "readonly_document.pdf"
        test_file.write_bytes(b"%PDF-1.4 test content")
        
        # Set read-only
        os.chmod(test_file, stat.S_IREAD)
        try:
            self.assertFalse(
                is_file_locked(test_file),
                "Read-only file was falsely reported as locked by is_file_locked()",
            )
        finally:
            os.chmod(test_file, stat.S_IWRITE | stat.S_IREAD)

    def test_actively_locked_read_only_file_is_detected_as_locked(self) -> None:
        """If a read-only file is genuinely held by another process without shared access, it must be locked."""
        test_file = self.dir / "locked_readonly.pdf"
        test_file.write_bytes(b"%PDF-1.4 locked content")
        os.chmod(test_file, stat.S_IREAD)

        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes
            GENERIC_READ = 0x80000000
            OPEN_EXISTING = 3
            FILE_SHARE_READ = 1
            kernel32 = ctypes.windll.kernel32
            kernel32.CreateFileW.argtypes = [
                wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE
            ]
            kernel32.CreateFileW.restype = wintypes.HANDLE
            # Open file with FILE_SHARE_READ; our checker requests FILE_SHARE_NONE so it must detect conflict
            h_proc = kernel32.CreateFileW(
                str(test_file), GENERIC_READ, FILE_SHARE_READ, None, OPEN_EXISTING, 0, None
            )
            try:
                self.assertTrue(
                    is_file_locked(test_file),
                    "Actively opened read-only file was not detected as locked",
                )
            finally:
                kernel32.CloseHandle(h_proc)
                os.chmod(test_file, stat.S_IWRITE | stat.S_IREAD)
        else:
            os.chmod(test_file, stat.S_IWRITE | stat.S_IREAD)

    def test_unique_destination_bounds_and_fallback(self) -> None:
        """unique_destination must generate indexed names and fall back to UUID rather than infinite loop."""
        base = self.dir / "test.txt"
        base.touch()
        
        # Next should be test (2).txt
        cand2 = unique_destination(self.dir, "test.txt")
        self.assertEqual(cand2.name, "test (2).txt")
        cand2.touch()

        # Next should be test (3).txt
        cand3 = unique_destination(self.dir, "test.txt")
        self.assertEqual(cand3.name, "test (3).txt")

        # Mock candidate.exists() to simulate extreme collision / broken filesystem
        with patch.object(Path, "exists", return_value=True):
            cand_fallback = unique_destination(self.dir, "test.txt")
            self.assertTrue(cand_fallback.name.startswith("test_"))
            self.assertTrue(cand_fallback.name.endswith(".txt"))

    def test_sort_undo_dismisses_ribbon_and_clears_recent_file(self) -> None:
        """When undoing a move, active ribbons must be dismissed and recent file cleared if matching."""
        src_file = self.dir / "Downloads" / "report.xlsx"
        src_file.parent.mkdir(parents=True, exist_ok=True)
        dest_file = self.dir / "Documents" / "Spreadsheets" / "report.xlsx"
        dest_file.parent.mkdir(parents=True, exist_ok=True)
        dest_file.write_text("dummy spreadsheet")

        # Simulate recent file record
        record_recent_file(dest_file, self.dir / "Downloads")
        rec = get_recent_file(max_age_sec=300)
        self.assertIsNotNone(rec)
        self.assertEqual(Path(rec["path"]).resolve(), dest_file.resolve())

        # Now undo the move
        records = [{
            "id": "rec_test_123",
            "src": str(src_file),
            "dest": str(dest_file),
        }]

        with patch("sort_undo.dismiss_active_ribbons") as mock_dismiss:
            restored = undo_moves(records, dry_run=False)
            self.assertEqual(restored, 1)
            self.assertTrue(src_file.exists())
            self.assertFalse(dest_file.exists())
            # Ribbon dismiss called with dest_path
            mock_dismiss.assert_called_once_with(dest_file)
            # Recent file buffer cleared
            self.assertIsNone(get_recent_file(max_age_sec=300))

    def test_watch_handle_post_move_gating(self) -> None:
        """_handle_post_move must respect config flags for ribbons, notifications, and quick switcher."""
        dest = self.dir / "file.txt"
        target_root = self.dir

        with patch("ribbon_popup.notify_file_moved") as mock_notify, \
             patch("quick_switcher.record_recent_file") as mock_record:
            
            # Case 1: All enabled (defaults)
            _handle_post_move(dest, target_root, {})
            mock_notify.assert_called_once_with(dest, target_root)
            mock_record.assert_called_once_with(dest, target_root)

            mock_notify.reset_mock()
            mock_record.reset_mock()

            # Case 2: Ribbon disabled via ribbon_enabled=False
            _handle_post_move(dest, target_root, {"ribbon_enabled": False})
            mock_notify.assert_not_called()
            mock_record.assert_called_once_with(dest, target_root)

            mock_notify.reset_mock()
            mock_record.reset_mock()

            # Case 3: Notifications disabled globally
            _handle_post_move(dest, target_root, {"notifications": False})
            mock_notify.assert_not_called()
            mock_record.assert_called_once_with(dest, target_root)

            mock_notify.reset_mock()
            mock_record.reset_mock()

            # Case 4: Quick switcher disabled
            _handle_post_move(dest, target_root, {"quick_switcher_enabled": False})
            mock_notify.assert_called_once_with(dest, target_root)
            mock_record.assert_not_called()


if __name__ == "__main__":
    unittest.main()
