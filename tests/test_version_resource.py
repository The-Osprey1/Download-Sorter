"""The .exe carries a publisher and version (issue #9)."""

from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path

import about
import version_resource


class VersionResourceTests(unittest.TestCase):
    def test_version_numbers_are_four_parts(self) -> None:
        self.assertEqual(version_resource.version_tuple("2.3.0"), (2, 3, 0, 0))
        self.assertEqual(version_resource.version_tuple("2.5.0-beta1"), (2, 5, 0, 0))

    def test_written_file_names_the_publisher_and_matches_the_app_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = version_resource.write(Path(tmp) / "fv.txt")
            text = path.read_text(encoding="utf-8")
        ast.parse(text)  # PyInstaller reads it as Python
        self.assertIn(f"u'CompanyName', u'{about.AUTHOR}'", text)
        self.assertIn(f"u'FileVersion', u'{about.__version__}'", text)
        self.assertIn(f"u'ProductName', u'{about.APP_NAME}'", text)
        self.assertIn("Licensed under the MIT License", text)


if __name__ == "__main__":
    unittest.main()
