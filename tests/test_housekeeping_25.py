"""Tests for the smaller #25 features: rule checks, leftover space, Desktop sweep, prints, export."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import desktop_sweep
import hold_manager
import prints
import rule_check
import setup_export
import sorter
import space_report


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.downloads = self.root / "Downloads"
        self.downloads.mkdir()
        self.config = sorter.get_default_config()
        self.config["watch"] = {"folder": str(self.downloads)}
        self._patches = [
            patch.object(sorter, "LOG_DIR", str(self.root / "log")),
            patch.object(hold_manager, "is_slicer_running", lambda names=None: False),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()


class RuleCheckTests(_Base):
    def test_a_rule_taken_by_an_earlier_rule_is_flagged(self) -> None:
        rules = [
            {"name": "Statements", "folder": "Documents\\Banking", "match": ["statement"], "enabled": True},
            {"name": "Bank PDFs", "folder": "Documents\\Money", "match": ["statement of account"], "enabled": True},
        ]
        self.assertEqual(rule_check.shadowed_by(rules, 1), ["Statements"])
        self.assertEqual(rule_check.shadowed_by(rules, 0), [])

    def test_a_website_rule_does_not_shadow_a_general_one(self) -> None:
        # The earlier rule only takes statements from one bank, so it cannot take all of them.
        rules = [
            {"name": "From bank", "folder": "B", "match": ["statement"], "sites": ["bank.com"], "enabled": True},
            {"name": "Any statement", "folder": "A", "match": ["statement"], "enabled": True},
        ]
        self.assertEqual(rule_check.shadowed_by(rules, 1), [])
        # The reverse is shadowed: the general rule takes everything the bank rule would.
        self.assertEqual(rule_check.shadowed_by([rules[1], rules[0]], 1), ["Any statement"])

    def test_matching_downloads_and_move(self) -> None:
        rule = {"name": "Receipts", "folder": "Documents\\Receipts", "match": ["receipt"],
                "extensions": [], "enabled": True}
        self.config["keyword_rules"] = [rule]
        (self.downloads / "receipt_jan.pdf").write_bytes(b"%PDF")
        (self.downloads / "other.pdf").write_bytes(b"%PDF")

        found = [p.name for p in rule_check.matching_downloads(self.config, rule)]
        self.assertEqual(found, ["receipt_jan.pdf"])

        self.assertEqual(rule_check.move_matching(self.config, rule), 1)
        self.assertTrue((self.downloads / "Documents" / "Receipts" / "receipt_jan.pdf").exists())
        self.assertTrue((self.downloads / "other.pdf").exists())


class SpaceReportTests(_Base):
    def test_report_counts_duplicates_and_quarantine(self) -> None:
        dup = self.downloads / sorter.DUPLICATES_DIR_NAME / "2026-11"
        dup.mkdir(parents=True)
        (dup / "same.pdf").write_bytes(b"x" * 100)
        batch = self.downloads / "_Remodel Quarantine" / "r1"
        batch.mkdir(parents=True)
        (batch / "old.stl").write_bytes(b"y" * 50)

        rep = space_report.report(self.config)

        self.assertEqual(rep["duplicates"]["files"], 1)
        self.assertEqual(rep["duplicates"]["bytes"], 100)
        self.assertEqual(rep["quarantine"]["files"], 1)
        self.assertEqual(len(space_report.batches_in_quarantine(self.config)), 1)


class DesktopSweepTests(_Base):
    def test_only_old_loose_files_move_and_shortcuts_stay(self) -> None:
        desk = self.root / "Desktop"
        desk.mkdir()
        old = desk / "old_notes.txt"
        old.write_text("x", encoding="utf-8")
        new = desk / "fresh.txt"
        new.write_text("y", encoding="utf-8")
        shortcut = desk / "App.lnk"
        shortcut.write_text("z", encoding="utf-8")
        past = time.time() - 30 * 86400
        for p in (old, shortcut):
            os.utime(p, (past, past))
        self.config["desktop_sweep"] = {"enabled": True, "days": 7}

        moved = desktop_sweep.sweep(self.config, desktop=desk, force=True)

        self.assertEqual(moved, 1)
        self.assertTrue((self.downloads / "old_notes.txt").exists())
        self.assertTrue(new.exists())
        self.assertTrue(shortcut.exists())

    def test_off_by_default(self) -> None:
        desk = self.root / "Desktop"
        desk.mkdir()
        (desk / "x.txt").write_text("x", encoding="utf-8")
        self.assertEqual(desktop_sweep.sweep(self.config, desktop=desk, force=True), 0)


class PrintsTests(_Base):
    def test_prints_are_found_by_their_details(self) -> None:
        gcode = self.downloads / "Bracket.3mf"
        gcode.write_bytes(b"x")
        prints.record_print(gcode, {
            "slicer": "Bambu Studio", "printer": "Bambu Lab X1C", "filament_type": "PLA",
            "print_time": "3h 12m", "plate_count": 2,
        })
        self.assertEqual(prints.matching_files("x1c"), {"Bracket.3mf"})
        self.assertEqual(prints.matching_files("pla"), {"Bracket.3mf"})
        self.assertEqual(prints.matching_files("petg"), set())
        self.assertEqual(prints.describe(prints.latest_for("Bracket.3mf")),
                         "Bambu Lab X1C · PLA · 2 plates · 3h 12m")

    def test_other_files_are_not_recorded(self) -> None:
        prints.record_print(self.downloads / "doc.pdf", {"slicer": "x"})
        self.assertEqual(prints.rows(), [])


class SetupExportTests(_Base):
    def test_export_drops_absolute_paths_and_learned_history(self) -> None:
        profile = os.environ.get("USERPROFILE") or str(Path.home())
        self.config["my_folders"] = {"enabled": True, "skip": [f"{profile}\\Downloads\\Old"]}
        self.config["active_project"] = {"enabled": True, "target_directory": f"{profile}\\Projects\\X"}
        self.config["keyword_rules"] = [{"name": "Banking", "folder": "Documents\\Banking",
                                         "match": ["statement"], "pending": {"x": 1}}]

        out = self.root / "setup.json"
        setup_export.export(self.config, out)
        data = json.loads(out.read_text(encoding="utf-8"))

        text = out.read_text(encoding="utf-8")
        self.assertNotIn(profile, text)
        self.assertNotIn("active_project", data)
        self.assertNotIn("pending", data["keyword_rules"][0])
        self.assertEqual(data["keyword_rules"][0]["match"], ["statement"])
        self.assertIn("%USERPROFILE%\\Downloads\\Old", data["my_folders"]["skip"])


class HotkeyTests(unittest.TestCase):
    def test_space_hotkey_parses_and_invalid_returns_none(self) -> None:
        import batch_handoff
        import hotkey

        parsed = batch_handoff.parse_hotkey("Ctrl+Shift+Space")
        self.assertEqual(parsed, (batch_handoff.MOD_CONTROL | batch_handoff.MOD_SHIFT, 0x20))
        self.assertIsNone(hotkey.parse_shelf_hotkey("not-a-key"))


if __name__ == "__main__":
    unittest.main()
