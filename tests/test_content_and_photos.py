"""PDFs told apart by their first page, and camera photos apart from screenshots (#25)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import hold_manager
import pdf_content
import photo_kind
import sorter


class PdfContentTests(unittest.TestCase):
    def test_statement_tax_shipping_and_manual(self) -> None:
        self.assertEqual(pdf_content.folder_for_text("account summary  statement period 1 nov"),
                         "Documents\\Statements")
        self.assertEqual(pdf_content.folder_for_text("form 1099-int  tax year 2025"), "Documents\\Tax")
        self.assertEqual(pdf_content.folder_for_text("ship to: jane  tracking number 1Z9"),
                         "Documents\\Shipping")
        self.assertEqual(pdf_content.folder_for_text("x200 user manual  table of contents"),
                         "Documents\\Manuals")

    def test_no_phrase_means_no_folder(self) -> None:
        self.assertIsNone(pdf_content.folder_for_text("meeting notes about the lab"))
        self.assertIsNone(pdf_content.folder_for_text(""))


class PhotoKindTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_screenshot_by_name(self) -> None:
        shot = self.dir / "Screenshot 2026-11-01 101010.png"
        shot.write_bytes(b"x")
        self.assertEqual(photo_kind.image_kind(shot), "screenshot")

    def test_camera_photo_by_exif_make(self) -> None:
        from PIL import Image
        photo = self.dir / "IMG_1234.jpg"
        exif = Image.Exif()
        exif[271] = "Apple"
        Image.new("RGB", (4, 4), "white").save(photo, exif=exif)
        self.assertEqual(photo_kind.image_kind(photo), "camera")

    def test_plain_image_is_left_alone(self) -> None:
        from PIL import Image
        plain = self.dir / "logo.jpg"
        Image.new("RGB", (4, 4), "white").save(plain)
        self.assertIsNone(photo_kind.image_kind(plain))


class RoutingTests(unittest.TestCase):
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

    def test_screenshots_go_to_their_own_folder(self) -> None:
        shot = self.downloads / "Screenshot 2026-11-01.png"
        shot.write_bytes(b"x")
        res = sorter.evaluate_routing(shot.name, self.config, file_path=shot)
        self.assertEqual(res["destination"], "Images\\Screenshots")

    def test_pdf_content_is_used_when_pdf_text_is_on(self) -> None:
        self.config["pdf_text"] = {"enabled": True, "max_mb": 10}
        pdf = self.downloads / "scan001.pdf"
        pdf.write_bytes(b"%PDF")
        with patch.object(sorter, "pdf_head_text", lambda path, cfg: "statement period 1 nov"):
            res = sorter.evaluate_routing(pdf.name, self.config, file_path=pdf)
        self.assertEqual(res["destination"], "Documents\\Statements")

    def test_upgraded_config_keeps_split_by_kind_off(self) -> None:
        upgraded = sorter.apply_v2_defaults({
            "onboarding_completed": True,
            "photos": {"by_month": False},
            "keyword_rules": [],
            "extension_rules": [],
        })
        self.assertFalse(upgraded["photos"]["split_by_kind"])
        older = sorter.apply_v2_defaults({
            "onboarding_completed": True,
            "keyword_rules": [],
            "extension_rules": [],
        })
        self.assertFalse(older["photos"]["split_by_kind"])


if __name__ == "__main__":
    unittest.main()
