"""MakerWorld model kits and the Bambu Studio recent-list safety net (issue #27)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bambu_recent
import hold_manager
import kits
import sorter


class KitNamingTests(unittest.TestCase):
    def test_title_from_slug(self) -> None:
        self.assertEqual(kits.title_from_slug("articulated-dragon_v2"), "Articulated Dragon V2")
        self.assertEqual(kits.title_from_slug(""), "")

    def test_model_from_zone_text_reads_the_model_page(self) -> None:
        text = (
            "[ZoneTransfer]\nZoneId=3\n"
            "ReferrerUrl=https://makerworld.com/en/models/1234567-articulated-dragon\n"
            "HostUrl=https://files.example.net/x.stl\n"
        )
        self.assertEqual(kits.model_from_zone_text(text), ("1234567", "Articulated Dragon"))

    def test_other_sites_are_not_kits(self) -> None:
        self.assertIsNone(kits.model_from_zone_text("ReferrerUrl=https://example.com/models/1-x\n"))

    def test_existing_folder_ignores_case_and_punctuation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            (parent / "Articulated Dragon - v2").mkdir()
            self.assertEqual(kits.existing_folder(parent, "articulated dragon v2"), "Articulated Dragon - v2")
            self.assertEqual(kits.existing_folder(parent, "Flanged Mace"), "Flanged Mace")


class BambuRecentTests(unittest.TestCase):
    CONF_BODY = (
        '{\r\n'
        '    "recent_projects": {\r\n'
        '        "001": "C:\\\\Users\\\\devyn\\\\Downloads\\\\cablesA1v3.3mf"\r\n'
        '    },\r\n'
        '    "other": {\r\n'
        '        "keep": "C:\\\\Users\\\\devyn\\\\Downloads\\\\cablesA1v3.3mf"\r\n'
        '    }\r\n'
        '}\r\n'
    )

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.conf = self.dir / "BambuStudio.conf"
        self.log_dir = self.dir / "log"
        self._patches = [
            patch.object(sorter, "LOG_DIR", str(self.log_dir)),
            patch.object(hold_manager, "is_slicer_running", lambda names=None: False),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()

    def _write_conf(self, body: str) -> None:
        self.conf.write_bytes(
            (body + "# MD5 checksum " + bambu_recent.checksum_of(body) + "\r\n").encode("utf-8")
        )

    def test_recent_entry_follows_the_moved_file(self) -> None:
        self._write_conf(self.CONF_BODY)
        old = Path("C:/Users/devyn/Downloads/cablesA1v3.3mf")
        new = Path("C:/Users/devyn/Downloads/3D Models/cablesA1v3/cablesA1v3.3mf")
        bambu_recent.record_move(old, new)

        changed = bambu_recent.apply_pending(self.conf)

        self.assertEqual(changed, 1)
        text = self.conf.read_text(encoding="utf-8")
        body, footer = text.split("# MD5 checksum ")
        self.assertIn("3D Models", body.split('"other"')[0])
        # The unrelated section is untouched.
        self.assertIn('"keep": "C:\\\\Users\\\\devyn\\\\Downloads\\\\cablesA1v3.3mf"', body)
        json.loads(body.replace("\r\n", "\n"))
        self.assertEqual(footer.strip(), bambu_recent.checksum_of(body))
        self.assertTrue((self.conf.parent / bambu_recent.BACKUP_NAME).exists())
        # The queue is empty once applied.
        self.assertEqual(bambu_recent.apply_pending(self.conf), 0)

    def test_nothing_is_written_while_bambu_is_running(self) -> None:
        self._write_conf(self.CONF_BODY)
        bambu_recent.record_move("C:/Users/devyn/Downloads/cablesA1v3.3mf", "C:/x/cablesA1v3.3mf")
        before = self.conf.read_bytes()
        with patch.object(hold_manager, "is_slicer_running", lambda names=None: True):
            self.assertEqual(bambu_recent.apply_pending(self.conf), 0)
        self.assertEqual(self.conf.read_bytes(), before)

    def test_non_3mf_moves_are_not_queued(self) -> None:
        bambu_recent.record_move("C:/Downloads/a.stl", "C:/x/a.stl")
        self.assertEqual(bambu_recent._load_pending(), [])

    def test_disabled_by_default_does_not_write_conf(self) -> None:
        self._write_conf(self.CONF_BODY)
        before = self.conf.read_bytes()
        cfg = sorter.get_default_config()
        self.assertFalse(bambu_recent.is_enabled(cfg))
        bambu_recent.record_move("C:/Users/devyn/Downloads/cablesA1v3.3mf", "C:/x/cablesA1v3.3mf", config=cfg)
        self.assertEqual(bambu_recent._load_pending(), [])
        # Even if something were queued, apply_pending respects config.
        bambu_recent.record_move("C:/Users/devyn/Downloads/cablesA1v3.3mf", "C:/x/cablesA1v3.3mf")
        self.assertEqual(bambu_recent.apply_pending(self.conf, config=cfg), 0)
        self.assertEqual(self.conf.read_bytes(), before)

    def test_restore_backup_restores_original_conf(self) -> None:
        self._write_conf(self.CONF_BODY)
        before = self.conf.read_bytes()
        bambu_recent.record_move(
            Path("C:/Users/devyn/Downloads/cablesA1v3.3mf"),
            Path("C:/Users/devyn/Downloads/3D Models/cablesA1v3/cablesA1v3.3mf"),
        )
        self.assertEqual(bambu_recent.apply_pending(self.conf), 1)
        self.assertTrue(bambu_recent.has_backup(self.conf))
        self.assertNotEqual(self.conf.read_bytes(), before)

        with patch.object(hold_manager, "is_slicer_running", lambda names=None: True):
            self.assertFalse(bambu_recent.restore_backup(self.conf))
        self.assertTrue(bambu_recent.restore_backup(self.conf))
        self.assertEqual(self.conf.read_bytes(), before)


@unittest.skipUnless(sys.platform == "win32", "Zone.Identifier notes are a Windows feature")
class KitRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.downloads = Path(self._tmp.name) / "Downloads"
        self.downloads.mkdir()
        self.config = sorter.get_default_config()
        self.config["downloads_dir"] = str(self.downloads)
        self.config["watch"] = {"folder": str(self.downloads)}
        self._patch = patch.object(sorter, "LOG_DIR", str(Path(self._tmp.name) / "log"))
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()
        self._tmp.cleanup()

    def _download_from(self, name: str, referrer: str) -> Path:
        path = self.downloads / name
        path.write_bytes(b"solid")
        with open(f"{path}:Zone.Identifier", "w", encoding="utf-8") as handle:
            handle.write(f"[ZoneTransfer]\nZoneId=3\nReferrerUrl={referrer}\n")
        return path

    def test_stl_from_a_model_page_goes_to_the_kit_folder(self) -> None:
        path = self._download_from(
            "dragon_body.stl", "https://makerworld.com/en/models/1234567-articulated-dragon"
        )
        res = sorter.evaluate_routing(path.name, self.config, file_path=path)
        self.assertEqual(res["match_type"], "makerworld_kit")
        self.assertEqual(res["destination"], "3D Models\\MakerWorld\\Articulated Dragon")

    def test_kit_reuses_the_folder_the_3mf_already_made(self) -> None:
        existing = self.downloads / "3D Models" / "MakerWorld" / "articulated dragon"
        existing.mkdir(parents=True)
        path = self._download_from(
            "dragon_guide.pdf", "https://makerworld.com/en/models/1234567-articulated-dragon"
        )
        res = sorter.evaluate_routing(path.name, self.config, file_path=path)
        self.assertEqual(res["destination"], "3D Models\\MakerWorld\\articulated dragon")

    def test_name_rule_still_beats_the_kit(self) -> None:
        self.config["keyword_rules"] = [{
            "name": "Receipts", "folder": "Documents\\Receipts", "match": ["receipt"],
            "enabled": True,
        }]
        path = self._download_from(
            "receipt.pdf", "https://makerworld.com/en/models/1234567-articulated-dragon"
        )
        res = sorter.evaluate_routing(path.name, self.config, file_path=path)
        self.assertNotEqual(res["match_type"], "makerworld_kit")


if __name__ == "__main__":
    unittest.main()
