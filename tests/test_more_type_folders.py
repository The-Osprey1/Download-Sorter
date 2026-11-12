"""HEIC, WEBP, TIFF and M4A files get their own folders, like JPG and MP3 do.

They used to stay loose in Images and Audio: the built-in list of type folders had no entry for them
(42 phone photos, 9 WEBP pictures and 20 voice memos on the PC where this was found), and a kind
without an entry stays one level deep by design. Kinds still without an entry are listed here too, so
that what stays flat is a decision and not an accident.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

import sort_undo
import sorter


@pytest.fixture
def downloads(tmp_path) -> Path:
    root = tmp_path / "Downloads"
    root.mkdir()
    return root


@pytest.fixture
def config(downloads) -> dict:
    cfg = sorter.get_default_config()
    cfg["downloads_dir"] = str(downloads)
    cfg["watch"] = {"folder": str(downloads), "settle_seconds": 0, "min_age_minutes": 0,
                    "auto_unzip": False, "auto_unpack_3d_zips": False}
    cfg["notifications"] = False
    cfg["ribbon_enabled"] = False
    cfg["hold"]["enabled"] = False
    cfg["batch_handoff"]["enabled"] = False
    cfg["quick_switcher_enabled"] = False
    return cfg


def _file(path: Path, text: str = "data") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    old = time.time() - 3600
    os.utime(path, (old, old))
    return path


def _goes_to(name: str, config: dict) -> str:
    return sorter.evaluate_routing(name, config, active_project={"enabled": False})["destination"]


@pytest.mark.parametrize("name, folder", [
    ("IMG_2041.HEIC", "Images\\HEIC"),
    ("IMG_2041.heic", "Images\\HEIC"),
    ("sticker.webp", "Images\\WEBP"),
    ("scan.tif", "Images\\TIFF"),
    ("scan.tiff", "Images\\TIFF"),
    ("Voice Memo.m4a", "Audio\\M4A"),
    # the ones that were already there
    ("photo.jpg", "Images\\JPG"),
    ("song.mp3", "Audio\\MP3"),
])
def test_these_kinds_have_a_folder_of_their_own(config, name, folder):
    assert _goes_to(name, config) == folder


def test_heif_is_a_heic_too():
    assert sorter.type_folder_for(".heif", {}) == "HEIC"
    assert sorter.type_folder_for(".HEIC", {}) == "HEIC"


@pytest.mark.parametrize("name, folder", [
    ("favicon.ico", "Images"),
    ("old.bmp", "Images"),
    ("take.ogg", "Audio"),
    ("home_movie.avi", "Videos"),
    ("setup.exe", "Installers"),
    ("bundle.zip", "Archives"),
])
def test_these_kinds_still_stay_one_level_deep(config, name, folder):
    assert _goes_to(name, config) == folder


def test_a_name_in_your_settings_still_wins(config):
    config["type_folders"]["names"] = {".heic": "iPhone", ".m4a": ""}

    assert _goes_to("IMG_1.heic", config) == "Images\\iPhone"
    assert _goes_to("memo.m4a", config) == "Audio", "an empty name leaves the kind in the main folder"
    assert _goes_to("sticker.webp", config) == "Images\\WEBP"


def test_the_settings_that_added_these_by_hand_still_agree(config):
    # The same folders were added to one person's settings before they were built in.
    config["type_folders"]["names"] = {".heic": "HEIC", ".heif": "HEIC", ".webp": "WEBP",
                                       ".tif": "TIFF", ".tiff": "TIFF", ".m4a": "M4A"}

    assert _goes_to("a.heic", config) == "Images\\HEIC"
    assert _goes_to("b.tiff", config) == "Images\\TIFF"
    assert _goes_to("c.m4a", config) == "Audio\\M4A"


def test_loose_files_in_a_main_folder_are_moved_into_them_as_one_batch_undo_puts_back(config, downloads):
    loose = [_file(downloads / "Images" / "IMG_1.heic"), _file(downloads / "Images" / "sticker.webp"),
             _file(downloads / "Images" / "scan.tif"), _file(downloads / "Audio" / "memo.m4a")]
    stays = _file(downloads / "Images" / "favicon.ico")

    assert sorter.sort_existing_misplaced(config, batch_id="tidy1") == 4

    assert (downloads / "Images" / "HEIC" / "IMG_1.heic").exists()
    assert (downloads / "Images" / "WEBP" / "sticker.webp").exists()
    assert (downloads / "Images" / "TIFF" / "scan.tif").exists()
    assert (downloads / "Audio" / "M4A" / "memo.m4a").exists()
    assert stays.exists(), "a kind without a folder is left where it is"
    assert not any(p.exists() for p in loose)

    assert sort_undo.main(["--batch", "tidy1"]) == 0
    assert all(p.exists() for p in loose), "one Undo puts them all back"


def test_a_folder_you_made_with_one_of_these_kinds_in_it_is_left_alone(config, downloads):
    mine = _file(downloads / "Holiday 2025" / "IMG_1.heic")

    assert sorter.sort_existing_misplaced(config, batch_id="tidy2") == 0
    assert mine.exists()
