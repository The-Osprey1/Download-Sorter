"""A 3MF hold counts from when the file arrived, and the cap files the oldest instead of forgetting it.

What went wrong:

* The hold started when Download Sorter first looked at a file. A sweep over 3MFs that had sat in
  Downloads for days gave every one a fresh week, and so did a reinstall that forgot the holds.
* At most ``max_held`` (15) files are held. Over that, the oldest hold *record* was deleted: the file
  stayed where it was, with nothing left that would ever release it, and the sweeps kept registering
  and dropping each other. With 24 3MFs, nine of them were never filed.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

import hold_manager
import sorter
import watch

DAY = 86400.0
FILED = Path("3D Models") / "3MF"


@pytest.fixture(autouse=True)
def downloads_are_finished(finished_downloads):
    """These tests are about the hold, not about waiting for a download to finish."""


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
    cfg["batch_handoff"]["enabled"] = False
    cfg["quick_switcher_enabled"] = False
    cfg["hold"].update({"enabled": True, "hold_days": 7, "max_held": 15})
    return cfg


@pytest.fixture
def arrival(monkeypatch):
    """Pretend a file arrived some days ago (the rest keep their real arrival time)."""
    pretended: dict[Path, float] = {}
    real = sorter.arrival_time

    def arrival_time(path):
        return pretended.get(Path(path).resolve(), real(path))

    monkeypatch.setattr(sorter, "arrival_time", arrival_time)

    def arrived_days_ago(path: Path, days: float) -> None:
        pretended[Path(path).resolve()] = time.time() - days * DAY

    return arrived_days_ago


@pytest.fixture
def no_slicer(monkeypatch):
    monkeypatch.setattr(hold_manager, "is_slicer_running", lambda *args, **kwargs: False)


def _3mf(path: Path, text: str = "3mf") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    old = time.time() - 3600  # long enough ago that moving it is allowed
    os.utime(path, (old, old))
    return path


def _planned(downloads: Path, name: str) -> Path:
    return downloads / FILED / name


def _records() -> dict:
    return hold_manager.read_state().get("held", {})


# ---------------------------------------------------------------------------
# The clock
# ---------------------------------------------------------------------------

def test_a_file_that_arrived_before_the_hold_began_is_filed_not_held(downloads, config, arrival):
    old = _3mf(downloads / "old.3mf")
    arrival(old, 10)

    assert hold_manager.hold_file(old, config, _planned(downloads, old.name)) is False
    assert not hold_manager.is_held(old)

    filed = sorter.move_file(old, config, source_root=downloads, check_age=False)

    assert filed == _planned(downloads, "old.3mf").resolve() and filed.exists() and not old.exists()
    assert not _records()


def test_the_hold_runs_from_when_the_file_arrived(downloads, config, arrival):
    plate = _3mf(downloads / "three_days.3mf")
    arrival(plate, 3)

    assert hold_manager.hold_file(plate, config, _planned(downloads, plate.name)) is True

    record = hold_manager.get_held(plate)
    days_left = (record["release_at"] - time.time()) / DAY
    assert 3.99 < days_left < 4.01, "7 days from arrival, 3 of them already gone"


def test_a_fresh_download_is_held_the_full_time(downloads, config):
    plate = _3mf(downloads / "fresh.3mf")

    assert hold_manager.hold_file(plate, config, _planned(downloads, plate.name)) is True

    days_left = (hold_manager.get_held(plate)["release_at"] - time.time()) / DAY
    assert 6.99 < days_left < 7.01


@pytest.mark.skipif(sys.platform != "win32", reason="the created time of a file is how Windows tells")
def test_a_download_stamped_with_an_old_server_date_is_still_held_a_week(downloads, config):
    # Some browsers stamp a download with the server's modified date. It landed just now, though:
    # its created time says so, and the hold counts from the newer of the two.
    plate = _3mf(downloads / "from_the_web.3mf")
    stamped = time.time() - 400 * DAY
    os.utime(plate, (stamped, stamped))

    assert hold_manager.hold_file(plate, config, _planned(downloads, plate.name)) is True

    days_left = (hold_manager.get_held(plate)["release_at"] - time.time()) / DAY
    assert 6.99 < days_left < 7.01


def test_a_held_file_that_is_found_again_keeps_its_schedule(downloads, config):
    plate = _3mf(downloads / "again.3mf")
    hold_manager.hold_file(plate, config, _planned(downloads, plate.name))
    first = hold_manager.get_held(plate)["release_at"]

    assert hold_manager.hold_file(plate, config, _planned(downloads, plate.name), now=time.time() + 3 * DAY) is True

    assert hold_manager.get_held(plate)["release_at"] == first


def test_the_dry_run_says_what_the_real_run_does(downloads, config, arrival):
    old, fresh = _3mf(downloads / "old.3mf"), _3mf(downloads / "fresh.3mf")
    arrival(old, 30)

    # an old file would be filed, so the dry run goes on to report where
    assert sorter.hold_in_place(old, config, _planned(downloads, old.name), dry_run=True) is False
    # a fresh one would be held
    assert sorter.hold_in_place(fresh, config, _planned(downloads, fresh.name), dry_run=True) is True
    assert not _records(), "a dry run never registers a hold"


def test_the_hold_period_is_over_only_after_hold_days(downloads, config, arrival):
    plate = _3mf(downloads / "p.3mf")

    arrival(plate, 6.9)
    assert hold_manager.hold_period_over(plate, config) is False
    arrival(plate, 7.1)
    assert hold_manager.hold_period_over(plate, config) is True
    config["hold"]["hold_days"] = 14
    assert hold_manager.hold_period_over(plate, config) is False


# ---------------------------------------------------------------------------
# The cap
# ---------------------------------------------------------------------------

def _hold_in_order(downloads: Path, config: dict, count: int, start: float) -> list[Path]:
    """Register ``count`` files one after another, a second apart, from ``start``."""
    files = [_3mf(downloads / f"part_{n}.3mf") for n in range(count)]
    for n, plate in enumerate(files):
        assert hold_manager.hold_file(plate, config, _planned(downloads, plate.name), now=start + n)
    return files


def test_over_the_cap_the_earliest_are_made_due_and_kept_on_record(downloads, config):
    config["hold"]["max_held"] = 3
    start = time.time() - 100

    files = _hold_in_order(downloads, config, 5, start)

    records = _records()
    assert len(records) == 5, "no hold is forgotten any more"
    due = [p.name for p in files if records[str(p.resolve())]["release_at"] <= start + 4]
    waiting = [p.name for p in files if records[str(p.resolve())]["release_at"] > start + 100]
    assert due == ["part_0.3mf", "part_1.3mf"], "the ones held longest are the ones to go"
    assert waiting == ["part_2.3mf", "part_3.3mf", "part_4.3mf"], "the newest downloads keep their hold"


def test_a_file_made_due_by_the_cap_is_released_by_the_normal_release(downloads, config, no_slicer):
    config["hold"]["max_held"] = 3
    files = _hold_in_order(downloads, config, 5, time.time() - 100)

    ready = [src.name for src, _planned_dest in hold_manager.check_releases(config)]

    assert sorted(ready) == ["part_0.3mf", "part_1.3mf"]
    assert all(p.exists() for p in files)


def test_files_marked_keep_here_do_not_count_toward_the_cap(downloads, config):
    config["hold"]["max_held"] = 2
    start = time.time() - 100
    kept = _3mf(downloads / "kept.3mf")
    hold_manager.hold_file(kept, config, _planned(downloads, kept.name), now=start)
    assert hold_manager.keep_here(kept)

    _hold_in_order(downloads, config, 3, start + 1)

    records = _records()
    assert records[str(kept.resolve())]["keep_here"] is True
    assert records[str(kept.resolve())]["release_at"] > time.time(), "a file you chose to keep is never made due"
    due = sorted(Path(k).name for k, r in records.items() if not r["keep_here"] and r["release_at"] <= start + 100)
    assert due == ["part_0.3mf"], "two of the three waiting fit under the cap of 2; only one has to go"


def test_a_cap_of_zero_means_nothing_is_held(downloads, config):
    config["hold"]["max_held"] = 0
    plate = _3mf(downloads / "p.3mf")

    assert hold_manager.hold_file(plate, config, _planned(downloads, plate.name)) is False


def test_a_burst_of_more_3mfs_than_the_cap_settles_and_stays_settled(downloads, config, no_slicer):
    # 24 3MFs and a cap of 15, as on the PC where this was found. Sweeping used to hold and
    # forget them in turn for good: nine were never filed.
    names = [f"model_{n:02d}.3mf" for n in range(24)]
    for name in names:
        _3mf(downloads / name)

    sorter.sort_existing(config)
    assert len(_records()) == 24, "every file has a record: none is forgotten"
    assert sorted(p.name for p in downloads.glob("*.3mf")) == names, "nothing has moved yet"

    assert watch.process_hold_releases(config, downloads) == 9, "the nine over the cap are filed"
    still_held = sorted(p.name for p in downloads.glob("*.3mf"))
    assert len(still_held) == 15
    assert sorted(p.name for p in (downloads / FILED).glob("*.3mf")) == sorted(set(names) - set(still_held))
    assert len(_records()) == 15

    # another sweep (the app does this on every start) changes nothing: no churn
    sorter.sort_existing(config)
    assert watch.process_hold_releases(config, downloads) == 0
    assert sorted(p.name for p in downloads.glob("*.3mf")) == still_held
    assert len(_records()) == 15 and len(list((downloads / FILED).glob("*.3mf"))) == 9


def test_old_3mfs_found_by_a_sweep_are_filed_at_once(downloads, config, arrival):
    # The reinstall case: 3MFs that came days ago, found by the first sweep of an app that
    # remembers nothing. They have waited long enough.
    old = [_3mf(downloads / f"old_{n}.3mf") for n in range(4)]
    for plate in old:
        arrival(plate, 9)
    new = _3mf(downloads / "new.3mf")

    sorter.sort_existing(config)

    assert sorted(p.name for p in (downloads / FILED).glob("*.3mf")) == [p.name for p in old]
    assert [p.name for p in downloads.glob("*.3mf")] == ["new.3mf"], "only the new one is held"
    assert list(_records()) == [str(new.resolve())]


# ---------------------------------------------------------------------------
# arrival_time
# ---------------------------------------------------------------------------

def test_arrival_time_is_none_for_a_file_that_is_not_there(tmp_path):
    assert sorter.arrival_time(tmp_path / "missing.3mf") is None


@pytest.mark.skipif(sys.platform != "win32", reason="the created time of a file is how Windows tells")
def test_arrival_time_is_the_newer_of_modified_and_created(tmp_path):
    plate = _3mf(tmp_path / "p.3mf")
    stamped = time.time() - 100 * DAY
    os.utime(plate, (stamped, stamped))

    assert time.time() - sorter.arrival_time(plate) < 60, "created just now, whatever the stamp says"


def test_arrived_before_still_means_what_it_did(tmp_path):
    plate = _3mf(tmp_path / "p.3mf")
    arrived = sorter.arrival_time(plate)

    assert sorter.arrived_before(plate, None) is False
    assert sorter.arrived_before(plate, arrived + 10) is True
    assert sorter.arrived_before(plate, arrived - 10) is False
    assert sorter.arrived_before(tmp_path / "missing.3mf", arrived + 10) is False
