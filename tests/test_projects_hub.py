"""Projects hub: registry, junctions that never touch the project, and Quick Route (RFC #1)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import projects_hub
import sorter


class ProjectsHubTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.hub = self.root / "Projects Hub"
        self.project = self.root / "code" / "DriftPad"
        self.project.mkdir(parents=True)
        (self.project / "main.py").write_text("print(1)", encoding="utf-8")
        self._patch = patch.object(sorter, "LOG_DIR", str(self.root / "log"))
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()
        self._tmp.cleanup()

    @unittest.skipUnless(sys.platform == "win32", "NTFS junctions")
    def test_add_makes_a_junction_that_opens_the_real_folder(self) -> None:
        entry = projects_hub.add(self.project, "software", hub=self.hub)
        link = Path(entry["link"])
        self.assertEqual(link, self.hub / "Software" / "DriftPad")
        self.assertTrue(projects_hub.is_junction(link))
        self.assertEqual((link / "main.py").read_text(encoding="utf-8"), "print(1)")
        self.assertTrue(projects_hub.is_registered(self.project))

    @unittest.skipUnless(sys.platform == "win32", "NTFS junctions")
    def test_remove_drops_only_the_junction(self) -> None:
        entry = projects_hub.add(self.project, "software", hub=self.hub)
        self.assertTrue(projects_hub.remove(self.project))
        self.assertFalse(Path(entry["link"]).exists())
        self.assertTrue((self.project / "main.py").exists(), "the project itself must stay")
        self.assertEqual(projects_hub.load(), [])

    def test_registry_without_links(self) -> None:
        projects_hub.add(self.project, "software", make_link=False)
        self.assertEqual([p["name"] for p in projects_hub.route_targets()], ["DriftPad"])

    def test_quick_route_moves_the_file_into_the_project(self) -> None:
        entry = projects_hub.add(self.project, "software", make_link=False)
        download = self.root / "Downloads" / "pinout.pdf"
        download.parent.mkdir()
        download.write_bytes(b"%PDF")
        target = projects_hub.route_file(download, entry)
        self.assertEqual(target, self.project / "pinout.pdf")
        self.assertTrue(target.exists())
        self.assertFalse(download.exists())

    def test_a_real_folder_in_the_way_is_not_replaced(self) -> None:
        (self.hub / "Software" / "DriftPad").mkdir(parents=True)
        with self.assertRaises(FileExistsError):
            projects_hub.add(self.project, "software", hub=self.hub)


if __name__ == "__main__":
    unittest.main()
