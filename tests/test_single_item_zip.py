"""A zip holding one PDF, font or model is filed where that file belongs (#25)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import zipfile

import hold_manager
import sorter
import unzip_archive


class SingleItemZipTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.downloads = Path(self._tmp.name) / "Downloads"
        self.downloads.mkdir()
        self.config = sorter.get_default_config()
        self.config["watch"] = {"folder": str(self.downloads)}
        self._patches = [
            patch.object(sorter, "LOG_DIR", str(Path(self._tmp.name) / "log")),
            patch.object(hold_manager, "is_slicer_running", lambda names=None: False),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()

    def test_one_pdf_goes_to_documents_not_extracted(self) -> None:
        archive = self.downloads / "receipts.zip"
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("manual_final.pdf", "%PDF text")
        unzip_archive.unpack_archive(archive, self.config)

        self.assertTrue((self.downloads / "Documents" / "PDF" / "manual_final.pdf").exists())
        self.assertFalse((self.downloads / "Extracted" / "receipts").exists())

    def test_a_code_project_stays_together(self) -> None:
        archive = self.downloads / "tool.zip"
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("tool/main.py", "print(1)")
            z.writestr("tool/util.py", "x = 1")
        unzip_archive.unpack_archive(archive, self.config)

        self.assertTrue((self.downloads / "Extracted" / "tool" / "main.py").exists())
        self.assertTrue((self.downloads / "Extracted" / "tool" / "util.py").exists())


if __name__ == "__main__":
    unittest.main()
