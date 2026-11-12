"""A held 3MF is announced once, when it is held, not every time it is looked at.

What went wrong: ``sorter.hold_in_place`` showed the "Holding in Downloads" notice whenever ``hold_file``
said the file stays put, and it says so for a file that is held already and has not changed. So every new
3MF download showed the notice twice (a second event for the same file, a few seconds later), and every
start of the app showed it again for every file that was still held: five at once, up to fifteen.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

import hold_manager
import sorter

FILED = Path("3D Models") / "3MF"


@pytest.fixture(autouse=True)
def downloads_are_finished(finished_downloads):
    """These tests are about the notice, not about waiting for a download to finish."""


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
    cfg["notifications"] = True
    cfg["ribbon_enabled"] = True  # the notice is on
    cfg["batch_handoff"]["enabled"] = False
    cfg["quick_switcher_enabled"] = False
    cfg["hold"].update({"enabled": True, "hold_days": 7, "max_held": 15})
    return cfg


@pytest.fixture
def announced():
    """The held notice, replaced by a recorder: one call for each time it would have been shown."""
    with patch("ribbon_popup.notify_file_held") as notify:
        yield notify


def _names(announced) -> list[str]:
    return [call.args[0].name for call in announced.call_args_list]


def _3mf(path: Path, text: str = "3mf") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    old = time.time() - 3600  # long enough ago that moving it is allowed
    os.utime(path, (old, old))
    return path


def test_a_file_is_announced_when_it_is_held_and_not_when_it_is_found_again(downloads, config, announced):
    plate = _3mf(downloads / "plate.3mf")
    planned = downloads / FILED / plate.name

    assert sorter.hold_in_place(plate, config, planned, downloads=downloads) is True
    assert _names(announced) == ["plate.3mf"]

    for _ in range(3):  # a second event for the same download, a sweep, the next start-up
        assert sorter.hold_in_place(plate, config, planned, downloads=downloads) is True

    assert _names(announced) == ["plate.3mf"], "it is held already, and announced already"


@pytest.mark.parametrize("change", ["size", "time"])
def test_a_file_that_changed_is_a_new_hold_and_is_announced_again(downloads, config, announced, change):
    plate = _3mf(downloads / "plate.3mf")
    planned = downloads / FILED / plate.name
    sorter.hold_in_place(plate, config, planned, downloads=downloads)
    stamped = plate.stat().st_mtime
    if change == "size":
        plate.write_text("3mf, but another one", encoding="utf-8")
        os.utime(plate, (stamped, stamped))  # only the size is different
    else:
        os.utime(plate, (stamped + 30, stamped + 30))  # only the time is different

    assert sorter.hold_in_place(plate, config, planned, downloads=downloads) is True

    assert _names(announced) == ["plate.3mf", "plate.3mf"]


def test_a_sweep_announces_only_the_file_that_is_new(downloads, config, announced):
    held = [_3mf(downloads / f"held_{n}.3mf") for n in range(3)]
    for plate in held:
        assert hold_manager.hold_file(plate, config, downloads / FILED / plate.name)
    new = _3mf(downloads / "new.3mf")

    sorter.sort_existing(config)

    assert _names(announced) == ["new.3mf"], "the files held before are not announced again at every start"
    assert all(hold_manager.is_held(plate) for plate in [*held, new])


def test_a_file_the_user_chose_to_keep_is_not_announced(downloads, config, announced):
    plate = _3mf(downloads / "plate.3mf")
    assert hold_manager.keep_here(plate)

    assert sorter.hold_in_place(plate, config, downloads / FILED / plate.name, downloads=downloads) is True

    assert announced.call_count == 0


def test_register_hold_says_what_it_found(downloads, config):
    plate = _3mf(downloads / "plate.3mf")
    planned = downloads / FILED / plate.name

    assert hold_manager.register_hold(plate, config, planned) == hold_manager.HOLD_NEW
    assert hold_manager.register_hold(plate, config, planned) == hold_manager.HOLD_UNCHANGED
    assert hold_manager.hold_file(plate, config, planned) is True, "hold_file still answers yes or no"

    plate.write_text("a different file", encoding="utf-8")
    assert hold_manager.register_hold(plate, config, planned) == hold_manager.HOLD_NEW

    assert hold_manager.keep_here(plate)
    assert hold_manager.register_hold(plate, config, planned) == hold_manager.HOLD_KEPT_HERE
    assert hold_manager.hold_file(plate, config, planned) is True

    config["hold"]["enabled"] = False
    assert hold_manager.register_hold(plate, config, planned) == hold_manager.HOLD_NOT_HELD
    assert hold_manager.hold_file(plate, config, planned) is False
