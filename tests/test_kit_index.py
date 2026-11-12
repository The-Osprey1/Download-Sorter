"""A re-saved multi-plate .3mf joins the kit folder of its model, even when the title changed (#27)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import zipfile

import hold_manager
import kits
import sorter


def _model_3mf(path: Path, title: str, model_id: str) -> None:
    header = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<model unit="millimeter" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">\n'
        ' <metadata name="Application">BambuStudio-02.09.01.52</metadata>\n'
        f' <metadata name="Title">{title}</metadata>\n'
        ' <metadata name="Designer">Someone</metadata>\n'
        f' <metadata name="DesignModelId">{model_id}</metadata>\n'
        ' <resources/>\n</model>\n'
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("3D/3dmodel.model", header)


class KitIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.downloads = root / "Downloads"
        self.downloads.mkdir()
        self.config = sorter.get_default_config()
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

    def test_same_model_id_reuses_the_folder_with_the_old_title(self) -> None:
        base = self.downloads / "3D Models" / "MakerWorld"
        (base / "Dragon Print").mkdir(parents=True)
        kits.remember_model("1234567", "Dragon Print")
        resaved = self.downloads / "dragon_plate_resaved.3mf"
        _model_3mf(resaved, "Dragon v2 final", "1234567")

        res = sorter.evaluate_routing(resaved.name, self.config, file_path=resaved)

        self.assertEqual(res["match_type"], "makerworld")
        self.assertEqual(res["destination"], "3D Models\\MakerWorld\\Dragon Print")

    def test_unknown_model_id_still_uses_its_title(self) -> None:
        new = self.downloads / "new.3mf"
        _model_3mf(new, "Flanged Mace", "999")
        res = sorter.evaluate_routing(new.name, self.config, file_path=new)
        self.assertEqual(res["destination"], "3D Models\\MakerWorld\\Flanged Mace")


if __name__ == "__main__":
    unittest.main()
