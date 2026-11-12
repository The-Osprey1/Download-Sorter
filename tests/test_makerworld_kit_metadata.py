"""MakerWorld / Bambu .3mf inspection: the model header, not the slice info, identifies a kit (issue #27)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import zipfile

import gcode_peek
from gcode_peek import parse_3mf

MODEL_HEADER = """<?xml version="1.0" encoding="UTF-8"?>
<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
 <metadata name="Application">BambuStudio-02.09.01.52</metadata>
 <metadata name="Title">Articulated Dragon</metadata>
 <metadata name="Designer">DevynFidel</metadata>
 <metadata name="DesignModelId">1234567</metadata>
 <metadata name="DesignProfileId">7654321</metadata>
 <metadata name="ProfileTitle">0.2mm layer, 3 walls, 15% infill</metadata>
"""

SLICE_INFO = """<?xml version="1.0" encoding="UTF-8"?>
<config>
  <header>
    <header_item key="X-BBL-Client-Type" value="slicer"/>
    <header_item key="X-BBL-Client-Version" value="02.09.01.52"/>
  </header>
</config>
"""


def _write_3mf(path: Path, model_xml: str, extra: dict[str, bytes] | None = None) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
        z.writestr("Metadata/slice_info.config", SLICE_INFO)
        z.writestr("3D/3dmodel.model", model_xml)
        for name, data in (extra or {}).items():
            z.writestr(name, data)


class MakerWorldKitMetadataTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_model_header_identifies_makerworld_kit(self) -> None:
        path = self.dir / "dragon_plate.3mf"
        _write_3mf(path, MODEL_HEADER + "<resources/></model>")

        parsed = parse_3mf(path)

        self.assertIsNone(parsed.get("error"))
        self.assertTrue(parsed["is_makerworld"])
        self.assertEqual(parsed["slicer"], "Bambu Studio")
        self.assertEqual(parsed["slicer_version"], "02.09.01.52")
        meta = parsed["makerworld_meta"]
        self.assertEqual(meta["model_id"], "1234567")
        self.assertEqual(meta["profile_id"], "7654321")
        self.assertEqual(meta["designer"], "DevynFidel")
        # The folder name comes from Title; the print profile is kept as metadata only.
        self.assertEqual(meta["title"], "Articulated Dragon")
        self.assertEqual(meta["profile_title"], "0.2mm layer, 3 walls, 15% infill")

    def test_title_falls_back_to_file_name(self) -> None:
        path = self.dir / "untitled_kit.3mf"
        model = MODEL_HEADER.replace(' <metadata name="Title">Articulated Dragon</metadata>\n', "")
        _write_3mf(path, model + "<resources/></model>")

        parsed = parse_3mf(path)

        self.assertTrue(parsed["is_makerworld"])
        self.assertEqual(parsed["makerworld_meta"]["title"], "untitled_kit")

    def test_large_model_is_read_by_header_only(self) -> None:
        # A big multi-plate model must not trip the size limits: only its header is read.
        mesh = "<object id=\"1\"><mesh><vertices>" + ("<vertex x=\"1\" y=\"2\" z=\"3\"/>" * 60000) + \
            "</vertices></mesh></object>"
        path = self.dir / "big_kit.3mf"
        _write_3mf(path, MODEL_HEADER + "<resources>" + mesh + "</resources></model>")
        self.assertGreater(path.stat().st_size, 1024 * 1024)

        with patch.object(gcode_peek, "MAX_UNCOMPRESSED_BYTES", 64 * 1024), \
                patch.object(gcode_peek, "MAX_FULL_MODEL_VALIDATE_BYTES", 1024):
            parsed = parse_3mf(path)

        self.assertIsNone(parsed.get("error"))
        self.assertTrue(parsed["is_makerworld"])
        self.assertEqual(parsed["makerworld_meta"]["title"], "Articulated Dragon")

    def test_large_non_model_entry_still_rejected(self) -> None:
        # The size limit still stops a zip bomb hidden outside the model file.
        path = self.dir / "bomb.3mf"
        _write_3mf(path, MODEL_HEADER + "<resources/></model>",
                   extra={"3D/huge_data.bin": b"\0" * (256 * 1024)})

        with patch.object(gcode_peek, "MAX_UNCOMPRESSED_BYTES", 64 * 1024):
            parsed = parse_3mf(path)

        self.assertIn("max uncompressed size", parsed.get("error") or "")

    def test_generic_slicer_label_is_not_used_as_the_slicer_name(self) -> None:
        path = self.dir / "plain.3mf"
        _write_3mf(path, "<?xml version=\"1.0\"?><model><resources/></model>")

        parsed = parse_3mf(path)

        self.assertNotEqual(parsed["slicer"], "slicer")


if __name__ == "__main__":
    unittest.main()
