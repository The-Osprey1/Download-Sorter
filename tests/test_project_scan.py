"""Project folders are recognised by their markers, and archiving leaves the original alone (RFC #1)."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

import project_scan


class ProjectScanTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _folder(self, name: str, files: dict[str, str]) -> Path:
        folder = self.root / name
        for rel, text in files.items():
            path = folder / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return folder

    def test_markers_give_the_kind(self) -> None:
        self.assertEqual(project_scan.fingerprint(self._folder("api", {"pyproject.toml": "x"})), "software")
        self.assertEqual(project_scan.fingerprint(self._folder("bracket", {"part.f3d": "x"})), "cad_cam")
        self.assertEqual(project_scan.fingerprint(self._folder("board", {"board.kicad_pro": "x"})), "electronics")
        self.assertEqual(project_scan.fingerprint(self._folder("IME3510 lab", {"notes.txt": "x"})), "coursework")
        self.assertIsNone(project_scan.fingerprint(self._folder("random", {"photo.txt": "x"})))

    def test_git_repository_is_software_and_reports_clean_state_only_when_git_runs(self) -> None:
        repo = self._folder("repo", {"README.md": "x"})
        (repo / ".git").mkdir()
        self.assertEqual(project_scan.fingerprint(repo), "software")

    def test_scan_lists_projects_under_each_root(self) -> None:
        self._folder("alpha", {"Cargo.toml": "x"})
        self._folder("beta", {"notes.txt": "x"})
        found = project_scan.scan([self.root])
        self.assertEqual([p.path.name for p in found], ["alpha"])
        self.assertEqual(found[0].kind, "software")

    def test_stale_is_older_than_ninety_days(self) -> None:
        folder = self._folder("old", {"main.py": "x"})
        past = time.time() - 120 * 86400
        for path in folder.rglob("*"):
            os.utime(path, (past, past))
        os.utime(folder, (past, past))
        proj = project_scan.Project(folder, "software", project_scan.last_active(folder), None)
        self.assertTrue(proj.stale)

    def test_archive_skips_regenerable_folders_and_keeps_the_original(self) -> None:
        folder = self._folder("tool", {"main.py": "x", "node_modules/pkg/index.js": "junk"})
        out = project_scan.archive(folder, self.root / "Archive", now=time.mktime((2026, 11, 1, 0, 0, 0, 0, 0, -1)))
        self.assertEqual(out.parent.name, "2026")
        with zipfile.ZipFile(out) as z:
            names = z.namelist()
        self.assertIn("tool/main.py", names)
        self.assertFalse(any("node_modules" in n for n in names))
        self.assertTrue((folder / "node_modules" / "pkg" / "index.js").exists())

    def test_archive_will_not_overwrite(self) -> None:
        folder = self._folder("tool", {"main.py": "x"})
        when = time.mktime((2026, 11, 1, 0, 0, 0, 0, 0, -1))
        project_scan.archive(folder, self.root / "Archive", now=when)
        with self.assertRaises(FileExistsError):
            project_scan.archive(folder, self.root / "Archive", now=when)


if __name__ == "__main__":
    unittest.main()
