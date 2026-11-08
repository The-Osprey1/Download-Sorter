"""The sorter must recognise the Downloads folder however its path is spelled.

On Windows a folder can be reached by more than one spelling: a short 8.3 name
(C:\\Users\\RUNNER~1\\...) and its long name, a linked folder, a mapped drive.
move_file compared the file's parent folder with the configured Downloads folder
as written, so a file reached through a different spelling of the same folder
was treated as "not directly inside Downloads" and silently skipped: never
sorted and never put on a 3MF hold. GitHub's Windows runners hit this every
time, because their temporary folder uses a short name.

These tests reproduce the same mismatch on any system with a linked folder.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

import hold_manager
import sorter


@pytest.fixture
def two_spellings(tmp_path) -> tuple[Path, Path]:
    """(real Downloads folder, a link to it) — one folder, two spellings."""
    real = tmp_path / "real" / "Downloads"
    real.mkdir(parents=True)
    link = tmp_path / "Downloads-link"
    try:
        link.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:  # e.g. Windows without link rights
        pytest.skip(f"cannot create a linked folder here: {exc}")
    return real, link


def _config(folder: Path) -> dict:
    cfg = sorter.get_default_config()
    cfg["downloads_dir"] = str(folder)
    cfg["watch"] = {"folder": str(folder), "settle_seconds": 0, "min_age_minutes": 0,
                    "auto_unzip": False, "auto_unpack_3d_zips": False}
    cfg["notifications"] = False
    cfg["ribbon_enabled"] = False
    return cfg


def _old_file(path: Path, text: str = "data") -> Path:
    path.write_text(text, encoding="utf-8")
    past = time.time() - 60
    os.utime(path, (past, past))
    return path


def test_same_folder_ignores_spelling(two_spellings):
    real, link = two_spellings
    assert sorter.same_folder(real, link)
    assert sorter.same_folder(link, real)
    assert not sorter.same_folder(real, real.parent)


def test_file_reached_through_another_spelling_is_sorted(two_spellings):
    real, link = two_spellings
    cfg = _config(real)
    report = _old_file(link / "report.pdf")

    dest = sorter.move_file(report, cfg)

    assert dest is not None, "a file directly in Downloads was skipped"
    assert (real / "Documents" / "PDFs" / "report.pdf").exists()


def test_3mf_reached_through_another_spelling_is_held(two_spellings):
    real, link = two_spellings
    cfg = _config(real)
    cfg["hold"]["enabled"] = True
    plate = _old_file(link / "Plate.3mf", "3mf content")

    sorter.move_file(plate, cfg)

    assert plate.exists(), "a held 3MF must stay where it is"
    assert hold_manager.is_held(plate)
