"""A MakerWorld part pack keeps its models, guide and photos together in one kit folder."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import zipfile

import hold_manager
import sorter
import unzip_3d


@unittest.skipUnless(sys.platform == "win32", "Zone.Identifier notes are a Windows feature")
class MakerWorldZipKitTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.downloads = root / "Downloads"
        self.downloads.mkdir()
        self.config = sorter.get_default_config()
        self.config["downloads_dir"] = str(self.downloads)
        self.config["watch"] = {"folder": str(self.downloads)}
        self._patches = [
            patch.object(sorter, "LOG_DIR", str(root / "log")),
            patch.object(hold_manager, "is_slicer_running", lambda names=None: False),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()

    def test_part_pack_lands_in_one_folder_with_guide_and_photo(self) -> None:
        archive = self.downloads / "dragon_parts.zip"
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("parts/body.stl", "solid body")
            z.writestr("parts/wing.stl", "solid wing")
            z.writestr("build_guide.pdf", "%PDF guide")
            z.writestr("photos/finished.jpg", "jpg")
            z.writestr("../escape.stl", "solid nope")
        with open(f"{archive}:Zone.Identifier", "w", encoding="utf-8") as handle:
            handle.write("[ZoneTransfer]\nZoneId=3\n"
                         "ReferrerUrl=https://makerworld.com/en/models/1234567-articulated-dragon\n")

        unzip_3d.unpack_3d_archive(archive, self.config, active_project=None)

        kit = self.downloads / "3D Models" / "MakerWorld" / "Articulated Dragon"
        self.assertTrue((kit / "parts" / "body.stl").exists())
        self.assertTrue((kit / "parts" / "wing.stl").exists())
        self.assertTrue((kit / "build_guide.pdf").exists())
        self.assertTrue((kit / "photos" / "finished.jpg").exists())
        # A path that tries to climb out of the kit is kept inside it.
        self.assertTrue((kit / "escape.stl").exists())
        self.assertFalse((self.downloads / "escape.stl").exists())


if __name__ == "__main__":
    unittest.main()
