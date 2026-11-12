"""Unit and integration tests for 3MF Grace-Period Hold & MakerWorld Overhaul."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch
import zipfile

from gcode_peek import parse_3mf
import hold_manager
import safe_ops
import sorter


def _ignore_download_readiness(test: unittest.TestCase) -> None:
    """Treat every existing file as finished downloading for the rest of ``test``.

    These tests are about the 3MF hold, not about waiting for downloads to finish.
    On Windows a file written a moment ago can briefly look unfinished (the
    antivirus still has it open, or its modified time is a clock tick ahead),
    which made the hold tests fail now and then on the Windows test machines.
    """
    patcher = patch("sorter.is_file_ready_to_move", side_effect=lambda path, settle: Path(path).is_file())
    patcher.start()
    test.addCleanup(patcher.stop)


class Test3MFHoldLifecycle(unittest.TestCase):
    def setUp(self) -> None:
        _ignore_download_readiness(self)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_root = Path(self.temp_dir.name)
        self.downloads = self.test_root / "Downloads"
        self.downloads.mkdir(parents=True, exist_ok=True)
        self.config = sorter.get_default_config()
        self.config["downloads_dir"] = str(self.downloads)
        self.config["watch"] = {"folder": str(self.downloads), "settle_seconds": 0, "min_age_minutes": 0}
        self.config["notifications"] = False
        self.config["ribbon_enabled"] = False
        self.config["hold"]["enabled"] = True
        self.config["hold"]["hold_days"] = 7
        self.config["hold"]["max_held"] = 15

        # Clear held state
        state_f = hold_manager.state_file()
        if state_f.exists():
            state_f.unlink()

    def tearDown(self) -> None:
        state_f = hold_manager.state_file()
        if state_f.exists():
            try:
                state_f.unlink()
            except Exception:
                pass
        self.temp_dir.cleanup()

    def test_hold_registration_and_deferral(self) -> None:
        sample = self.downloads / "Benchy.3mf"
        sample.write_text("dummy 3mf content")

        with patch("ribbon_popup.notify_file_held"):
            res = sorter.move_file(sample, self.config, source_root=self.downloads)
            # File should not be moved immediately; move_file returns None
            self.assertIsNone(res)
            self.assertTrue(sample.exists())
            self.assertTrue(hold_manager.is_held(sample))

            rec = hold_manager.get_held(sample)
            self.assertIsNotNone(rec)
            self.assertFalse(rec["keep_here"])
            self.assertIn("3D Models", rec["planned_dest"])
            # release_at must be ~7 days in the future
            self.assertGreater(rec["release_at"], time.time() + (6.9 * 86400))

    def test_keep_here_override(self) -> None:
        sample = self.downloads / "Favorite_Tool.3mf"
        sample.write_text("tool 3mf content")

        sorter.move_file(sample, self.config, source_root=self.downloads)
        self.assertTrue(hold_manager.is_held(sample))

        # User clicks 'Keep here'
        ok = hold_manager.keep_here(sample)
        self.assertTrue(ok)
        self.assertTrue(hold_manager.is_keep_here(sample))

        # check_releases 10 days in future should ignore this file
        future_time = time.time() + (10 * 86400)
        ready = hold_manager.check_releases(self.config, now=future_time)
        self.assertEqual(len(ready), 0)
        self.assertTrue(sample.exists())

    def test_file_now_override(self) -> None:
        sample = self.downloads / "Quick_Print.3mf"
        sample.write_text("quick print content")

        sorter.move_file(sample, self.config, source_root=self.downloads)
        self.assertTrue(hold_manager.is_held(sample))

        # User clicks 'File now'
        final_dest = hold_manager.file_now(sample, self.config)
        self.assertIsNotNone(final_dest)
        self.assertFalse(sample.exists())
        self.assertTrue(final_dest.exists())
        self.assertFalse(hold_manager.is_held(sample))

    def test_check_releases_idle_expiration(self) -> None:
        sample = self.downloads / "Matured_Part.3mf"
        sample.write_text("matured part content")

        sorter.move_file(sample, self.config, source_root=self.downloads)
        self.assertTrue(hold_manager.is_held(sample))

        rec = hold_manager.get_held(sample)
        rel_time = rec["release_at"]

        # 1. Before release_at: not ready
        ready_before = hold_manager.check_releases(self.config, now=rel_time - 100.0)
        self.assertEqual(len(ready_before), 0)

        # 2. At release_at: ready for release (no slicer running)
        with patch("hold_manager.is_slicer_running", return_value=False):
            ready_after = hold_manager.check_releases(self.config, now=rel_time + 10.0)
            self.assertEqual(len(ready_after), 1)
            src, planned = ready_after[0]
            self.assertEqual(src.resolve(), sample.resolve())
            self.assertIn("3D Models", str(planned))

    def test_slicer_lock_deferral(self) -> None:
        sample = self.downloads / "OpenInBambu.3mf"
        sample.write_text("open in bambu content")

        sorter.move_file(sample, self.config, source_root=self.downloads)
        rec = hold_manager.get_held(sample)
        rel_time = rec["release_at"]

        # Slicer running at day 8: deferred!
        with patch("hold_manager.is_slicer_running", return_value=True):
            ready = hold_manager.check_releases(self.config, now=rel_time + 86400.0)
            self.assertEqual(len(ready), 0)

        # After 15 days (rel_time + 8 days), overrides running slicer!
        with patch("hold_manager.is_slicer_running", return_value=True), \
             patch("safe_ops.can_open_exclusive", return_value=True):
            ready = hold_manager.check_releases(self.config, now=rel_time + (8 * 86400.0))
            self.assertEqual(len(ready), 1)

    def test_file_modification_resets_grace_period(self) -> None:
        sample = self.downloads / "EditedProject.3mf"
        sample.write_text("v1 content")

        sorter.move_file(sample, self.config, source_root=self.downloads)
        rec = hold_manager.get_held(sample)
        orig_release = rec["release_at"]

        # Simulate user editing the project in Bambu Studio 3 days later
        edit_time = time.time() + (3 * 86400.0)
        os.utime(sample, (edit_time, edit_time))

        with patch("hold_manager.is_slicer_running", return_value=False):
            hold_manager.check_releases(self.config, now=edit_time + 100.0)

        updated_rec = hold_manager.get_held(sample)
        self.assertIsNotNone(updated_rec)
        # release_at must now be edit_time + 7 days
        self.assertGreater(updated_rec["release_at"], orig_release)


class TestMakerWorldAndSecurity(unittest.TestCase):
    def setUp(self) -> None:
        self.fixtures_dir = Path("tests/fixtures/3mf")
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_root = Path(self.temp_dir.name)
        self.downloads = self.test_root / "Downloads"
        self.downloads.mkdir(parents=True, exist_ok=True)
        self.config = sorter.get_default_config()
        self.config["watch_directory"] = str(self.downloads)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_makerworld_routing_destination(self) -> None:
        mw_fixture = self.fixtures_dir / "makerworld_model.3mf"
        self.assertTrue(mw_fixture.exists())

        res = sorter.evaluate_routing(
            mw_fixture.name,
            self.config,
            file_path=mw_fixture,
        )
        self.assertEqual(res["match_type"], "makerworld")
        self.assertIn("MakerWorld", res["destination"])
        # The fixture's model header has no Title, so the file name names the folder.
        self.assertIn("makerworld_model", res["destination"])
        self.assertNotIn("DevynFidel", res["destination"])  # one folder per model by default

    def test_bambu_local_project_not_makerworld(self) -> None:
        local_fixture = self.fixtures_dir / "bambu_local_project.3mf"
        self.assertTrue(local_fixture.exists())

        res = sorter.evaluate_routing(
            local_fixture.name,
            self.config,
            file_path=local_fixture,
        )
        self.assertNotEqual(res["match_type"], "makerworld")
        self.assertIn("3D Models", res["destination"])

    def test_zip_bomb_rejection(self) -> None:
        bomb_fixture = self.fixtures_dir / "zip_bomb.3mf"
        self.assertTrue(bomb_fixture.exists())

        parsed = parse_3mf(bomb_fixture)
        self.assertIsNotNone(parsed.get("error"))
        self.assertIn("max uncompressed size", parsed["error"])

    def test_path_traversal_rejection(self) -> None:
        traversal_fixture = self.fixtures_dir / "traversal.3mf"
        self.assertTrue(traversal_fixture.exists())

        parsed = parse_3mf(traversal_fixture)
        self.assertIsNotNone(parsed.get("error"))
        self.assertIn("Path traversal", parsed["error"])

    def test_malformed_xml_safety(self) -> None:
        malformed_fixture = self.fixtures_dir / "malformed_xml.3mf"
        self.assertTrue(malformed_fixture.exists())

        parsed = parse_3mf(malformed_fixture)
        self.assertIsNotNone(parsed.get("error"))
        self.assertIn("Malformed 3MF XML", parsed["error"])

    def test_oversized_thumbnail_bounded(self) -> None:
        thumb_fixture = self.fixtures_dir / "oversized_thumb.3mf"
        self.assertTrue(thumb_fixture.exists())

        parsed = parse_3mf(thumb_fixture)
        self.assertTrue(parsed.get("has_thumbnail"))
        raw = parsed.get("thumbnail_data")
        self.assertIsNotNone(raw)
        self.assertLessEqual(len(raw), 5 * 1024 * 1024)

        try:
            from PIL import Image
            img = Image.open(io.BytesIO(raw))
            w, h = img.size
            self.assertLessEqual(w, 1024)
            self.assertLessEqual(h, 1024)
        except ImportError:
            pass


class Test3MFMilestone3Fixes(unittest.TestCase):
    def setUp(self) -> None:
        _ignore_download_readiness(self)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_root = Path(self.temp_dir.name)
        self.downloads = self.test_root / "Downloads"
        self.downloads.mkdir(parents=True, exist_ok=True)
        self.config = sorter.get_default_config()
        self.config["downloads_dir"] = str(self.downloads)
        self.config["watch"] = {"folder": str(self.downloads), "settle_seconds": 0, "min_age_minutes": 0}
        self.config["hold"]["enabled"] = True

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_sorter_sort_destination_contract_and_resolution(self) -> None:
        """BUG-03: Verify sorter.sort_destination resolves target Paths properly."""
        self.assertTrue(hasattr(sorter, "sort_destination"))
        # 3MF file routing
        dest_3mf = sorter.sort_destination("Bracket.3mf", self.config)
        self.assertIsInstance(dest_3mf, Path)
        self.assertIn("3D Models", str(dest_3mf))
        self.assertEqual(dest_3mf.name, "Bracket.3mf")

        # Code file routing
        dest_py = sorter.sort_destination(Path("script.py"), self.config)
        self.assertIsInstance(dest_py, Path)
        self.assertIn("Code", str(dest_py))

        # Absolute custom routing
        custom_dir = self.test_root / "CustomTarget"
        custom_config = dict(self.config)
        custom_config["keyword_rules"] = [
            {"match": ["ProjectAlpha"], "folder": str(custom_dir), "enabled": True}
        ]
        dest_custom = sorter.sort_destination("ProjectAlpha_Report.pdf", custom_config)
        self.assertEqual(dest_custom.parent.resolve(), custom_dir.resolve())

    def test_tray_make_icon_badge_rendering(self) -> None:
        """Verify tray.make_icon draws an amber badge when held_count > 0."""
        import tray
        # When held_count == 0: badge coordinate is transparent
        icon_empty = tray.make_icon(paused=False, held_count=0)
        pixel_empty = icon_empty.getpixel((50, 14))
        self.assertEqual(pixel_empty[3], 0, "Badge area should be transparent when held_count == 0")

        # When held_count > 0: badge coordinate is filled with warning amber (245, 173, 98)
        icon_held = tray.make_icon(paused=False, held_count=2)
        pixel_held = icon_held.getpixel((50, 14))
        self.assertEqual(pixel_held[:3], (245, 173, 98), "Badge area should be amber #F5AD62 when held_count > 0")
        self.assertEqual(pixel_held[3], 255)

    def test_gcode_peek_case_insensitive_and_fallback(self) -> None:
        """BUG-05: Case-insensitive candidate lookup and robust fallback."""
        sample_path = self.downloads / "LowerPlate.3mf"
        png_1x1 = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc`\x00\x00"
            b"\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        # 1. Lowercase metadata/plate_1.png
        with zipfile.ZipFile(sample_path, "w") as zf:
            zf.writestr("metadata/plate_1.png", png_1x1)
            zf.writestr("3D/3dmodel.model", "<model/>")
        meta = parse_3mf(sample_path)
        self.assertTrue(meta.get("has_thumbnail"))
        self.assertEqual(meta.get("thumbnail_name"), "metadata/plate_1.png")

        # 2. Corrupt plate_1.png with valid Metadata/thumbnail.png fallback
        sample_fallback = self.downloads / "CorruptCandidate.3mf"
        with zipfile.ZipFile(sample_fallback, "w") as zf:
            zf.writestr("Metadata/plate_1.png", b"not-a-valid-png")
            zf.writestr("Metadata/thumbnail.png", png_1x1)
            zf.writestr("3D/3dmodel.model", "<model/>")
        meta_fb = parse_3mf(sample_fallback)
        self.assertTrue(meta_fb.get("has_thumbnail"))
        self.assertEqual(meta_fb.get("thumbnail_name"), "Metadata/thumbnail.png")

    def test_watch_drain_held_activity_callback(self) -> None:
        """Verify watch.drain calls on_activity callback when file enters hold."""
        import watch
        sample = self.downloads / "HeldPlate.3mf"
        sample.write_text("3mf content")

        # Register into PENDING_DOWNLOADS
        watch.PENDING_DOWNLOADS.clear()
        watch.PENDING_DEADLINES.clear()
        watch.PENDING_DOWNLOADS.add(sample)
        watch.PENDING_DEADLINES[("download", sample)] = 0.0

        activity_mock = MagicMock()
        with patch("ribbon_popup.notify_file_held"):
            moved = watch.drain(self.config, root=self.downloads, on_activity=activity_mock)

        # File is held: moved count is 0, on_activity is signaled with 0
        self.assertEqual(moved, 0)
        self.assertTrue(hold_manager.is_held(sample))
        activity_mock.assert_called_with(0)
        # Must not be left in PENDING_DOWNLOADS to avoid busy loop
        self.assertNotIn(sample, watch.PENDING_DOWNLOADS)


if __name__ == "__main__":
    unittest.main()
