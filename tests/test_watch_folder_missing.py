"""A watch folder that is not there is waited for, never swapped for another folder.

What went wrong: ``sorter.watch_root`` answered with the Downloads folder that Windows keeps in the
registry for ANY folder that did not exist, not only for a default Downloads that had been moved. A
watch folder on a drive that was not plugged in (or one that was deleted or misspelled) therefore made
the app sort the user's Downloads folder instead.

* ``watch_root`` follows a moved Downloads folder for the default folder only.
* The watcher, which used to fail on a folder that is not there, waits for it, says so, and starts when
  it is back. It does not make the folder: it may be a drive that is not plugged in.
* A sweep that cannot reach the folder moves nothing and says why.
* A folder that goes away while the app runs (a drive unplugged) is noticed too. watchdog stops its watch
  by itself and says nothing, so the app used to keep saying "running", forgot the 3MFs held on that
  drive, and never watched the folder again when it came back.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import threading
import time
from pathlib import Path

import pytest

import hold_manager
import sorter
import tray
import watch

SHELL_FOLDERS = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
DOWNLOADS_ID = "{374DE290-123F-4565-9164-39C4925E467B}"

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="the moved-Downloads lookup is Windows-only")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def windows_says_downloads_moved_to(folder: Path) -> None:
    """What Windows records when a person moves their Downloads folder (in this test's own copy of the registry)."""
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, SHELL_FOLDERS) as key:
        winreg.SetValueEx(key, DOWNLOADS_ID, 0, winreg.REG_EXPAND_SZ, str(folder))


def make_config(root: Path, docs: Path | None = None) -> dict:
    config = sorter.get_default_config()
    config["downloads_dir"] = str(root)
    config["watch"] = dict(config.get("watch", {}), folder=str(root), settle_seconds=0, min_age_minutes=0,
                           auto_unzip=False, auto_unpack_3d_zips=False)
    config["document_sources"] = [str(docs)] if docs else []
    config["notifications"] = False
    config["ribbon_enabled"] = False
    config["quick_switcher_enabled"] = False
    config["onboarding_completed"] = True
    if isinstance(config.get("batch_handoff"), dict):
        config["batch_handoff"]["enabled"] = False
    return config


def wait_for(condition, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return bool(condition())


class FakeObserver:
    def __init__(self) -> None:
        self.active: dict[int, str] = {}
        self._next = 0

    def schedule(self, handler, path, recursive=False):
        self._next += 1
        self.active[self._next] = path
        return self._next

    def unschedule(self, watch_id):
        del self.active[watch_id]


@pytest.fixture
def home(own_home_folder) -> Path:
    return own_home_folder


@pytest.fixture
def moved_downloads(tmp_path) -> Path:
    """The Downloads folder as it is after Windows moved it to another drive."""
    folder = tmp_path / "other-drive" / "Downloads"
    folder.mkdir(parents=True)
    return folder


@pytest.fixture
def unreachable(tmp_path) -> Path:
    """A folder that is not there and cannot be made, like one on a drive that is not plugged in."""
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("a file where the drive should be", encoding="utf-8")
    return blocker / "Inbox"


# ---------------------------------------------------------------------------
# watch_root
# ---------------------------------------------------------------------------

@windows_only
def test_a_named_folder_that_is_missing_is_not_swapped_for_the_moved_downloads(tmp_path, moved_downloads):
    windows_says_downloads_moved_to(moved_downloads)
    unplugged = tmp_path / "unplugged-drive" / "Inbox"

    assert sorter.watch_root({"watch": {"folder": str(unplugged)}}) == unplugged.resolve()
    assert sorter.watch_root({"downloads_dir": str(unplugged)}) == unplugged.resolve()


@windows_only
@pytest.mark.parametrize(
    "settings",
    [
        {},
        {"watch": {"folder": ""}},
        {"watch": {"folder": "${USERPROFILE}\\Downloads"}},
        {"watch": {"folder": "%USERPROFILE%\\Downloads"}},
        {"downloads_dir": "${USERPROFILE}\\Downloads"},
    ],
    ids=["nothing named", "empty folder", "the default as shipped", "the default, percent form", "the old setting"],
)
def test_the_default_downloads_follows_a_moved_downloads_folder(home, moved_downloads, settings):
    (home / "Downloads").rmdir()
    windows_says_downloads_moved_to(moved_downloads)

    assert sorter.watch_root(settings) == moved_downloads.resolve()


@windows_only
def test_the_default_folder_typed_out_in_capitals_still_follows_a_moved_downloads_folder(home, moved_downloads):
    typed_out = str(home / "Downloads").upper()
    (home / "Downloads").rmdir()
    windows_says_downloads_moved_to(moved_downloads)

    assert sorter.watch_root({"watch": {"folder": typed_out}}) == moved_downloads.resolve()


@windows_only
def test_the_default_downloads_is_used_when_it_is_there(home, moved_downloads):
    windows_says_downloads_moved_to(moved_downloads)

    assert sorter.watch_root({}) == (home / "Downloads").resolve()


def test_a_missing_default_downloads_stays_where_it_is_when_windows_knows_no_other(home):
    (home / "Downloads").rmdir()

    assert sorter.watch_root({}) == (home / "Downloads").resolve()


def test_a_named_folder_that_is_there_is_used_as_it_is(tmp_path):
    folder = tmp_path / "Inbox"
    folder.mkdir()

    assert sorter.watch_root({"watch": {"folder": str(folder)}}) == folder.resolve()


# ---------------------------------------------------------------------------
# a sweep that cannot reach the folder
# ---------------------------------------------------------------------------

def test_a_sweep_of_a_folder_that_cannot_be_reached_says_so_and_moves_nothing(home, unreachable, caplog):
    config = make_config(unreachable)

    with caplog.at_level(logging.WARNING):
        assert sorter.sort_existing(config) == 0
        assert sorter.sort_existing_documents(config) == 0

    assert "was not found and could not be created" in caplog.text
    assert str(sorter.watch_root(config)) in caplog.text
    assert list((home / "Downloads").iterdir()) == [], "the Downloads folder is left alone"


def test_a_problem_inside_a_folder_that_is_there_is_not_hidden(tmp_path, monkeypatch):
    folder = tmp_path / "Inbox"
    folder.mkdir()

    def refuse(_config):
        raise PermissionError("no access to one of the folders in it")

    monkeypatch.setattr(sorter, "ensure_tree", refuse)

    with pytest.raises(PermissionError):
        sorter.sort_existing(make_config(folder))


# ---------------------------------------------------------------------------
# the watches
# ---------------------------------------------------------------------------

def test_nothing_is_watched_while_the_folder_is_missing(tmp_path, caplog):
    missing, docs = tmp_path / "Inbox", tmp_path / "Docs"
    docs.mkdir()
    observer = FakeObserver()
    folders = watch.FolderWatches(observer)

    with caplog.at_level(logging.WARNING):
        folders.build(make_config(missing, docs))

    assert folders.root_missing is True
    assert observer.active == {}, "not the folder, and not the Documents sources that are filed into it"
    assert "was not found" in caplog.text
    assert not missing.exists(), "the folder is not made for you: it may be a drive that is not plugged in"


def test_the_watches_start_when_the_folder_comes_back(tmp_path):
    missing, docs = tmp_path / "Inbox", tmp_path / "Docs"
    docs.mkdir()
    config = make_config(missing, docs)
    observer = FakeObserver()
    folders = watch.FolderWatches(observer)
    folders.build(config)

    assert folders.retry(config) is False
    assert observer.active == {}

    missing.mkdir()

    assert folders.retry(config) is True
    assert sorted(observer.active.values()) == sorted([str(missing.resolve()), str(docs.resolve())])
    assert folders.root_missing is False
    assert folders.retry(config) is False, "nothing left to retry"
    assert len(observer.active) == 2, "and no second set of watches"


def test_choosing_another_folder_ends_the_wait(tmp_path):
    missing, other = tmp_path / "Inbox", tmp_path / "Other"
    other.mkdir()
    observer = FakeObserver()
    folders = watch.FolderWatches(observer)
    folders.build(make_config(missing))

    assert folders.retarget(make_config(other)) is True

    assert list(observer.active.values()) == [str(other.resolve())]
    assert folders.root_missing is False


def test_other_changes_in_the_settings_keep_waiting_for_the_same_folder(tmp_path):
    missing = tmp_path / "Inbox"
    config = make_config(missing)
    observer = FakeObserver()
    folders = watch.FolderWatches(observer)
    folders.build(config)

    assert folders.retarget(dict(config, notifications=True)) is False

    assert folders.root_missing is True and observer.active == {}


# ---------------------------------------------------------------------------
# the watcher loop, for real
# ---------------------------------------------------------------------------

@pytest.fixture
def start_watcher(finished_downloads):
    """Run ``watch.run`` in a thread with the given settings, and stop it when the test is over."""
    started = []

    def start(config: dict):
        config_file = Path(watch.CONFIG_PATH)
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text(json.dumps(config), encoding="utf-8")
        stop = threading.Event()
        errors: list[BaseException] = []

        def target() -> None:
            try:
                watch.run(stop=stop)
            except BaseException as exc:  # the test reports it
                errors.append(exc)

        thread = threading.Thread(target=target, name="test-watch", daemon=True)
        thread.start()
        started.append((thread, stop))
        return thread, stop, errors

    yield start

    for thread, stop in started:
        stop.set()
        watch.WAKE_EVENT.set()
        thread.join(15)


def _runtime_status() -> str:
    return json.loads(sorter.RUNTIME_STATE_FILE.read_text(encoding="utf-8")).get("status", "")


def test_the_watcher_waits_for_a_missing_folder_and_sorts_once_it_is_back(tmp_path, home, moved_downloads, start_watcher):
    if sys.platform == "win32":
        windows_says_downloads_moved_to(moved_downloads)  # the old code sorted this folder instead
    bait = moved_downloads / "bait.pdf"
    bait.write_text("not yours to sort", encoding="utf-8")
    long_ago = time.time() - 3600
    os.utime(bait, (long_ago, long_ago))
    missing = tmp_path / "Inbox"

    thread, stop, errors = start_watcher(make_config(missing))

    assert wait_for(watch.WATCH_FOLDER_MISSING.is_set), "the watcher says it is waiting"
    assert thread.is_alive() and not errors
    assert wait_for(lambda: _runtime_status() == "waiting_for_folder")
    assert not missing.exists(), "the folder is not made for you"
    assert list(moved_downloads.iterdir()) == [bait], "no other folder is sorted in its place"
    assert list((home / "Downloads").iterdir()) == []

    # the drive is plugged in again, with a file on it
    missing.mkdir()
    report = missing / "report.pdf"
    report.write_text("a report", encoding="utf-8")
    os.utime(report, (long_ago, long_ago))
    watch.WAKE_EVENT.set()

    assert wait_for(lambda: not watch.WATCH_FOLDER_MISSING.is_set()), "the watcher noticed it is back"
    assert wait_for(lambda: not report.exists() and len(list(missing.rglob("report.pdf"))) == 1), "and filed what was in it"
    assert bait.exists(), "still nothing taken from the other folder"
    assert _runtime_status() == "running"

    stop.set()
    watch.WAKE_EVENT.set()
    thread.join(15)
    assert not thread.is_alive() and not errors
    assert not watch.WATCH_FOLDER_MISSING.is_set()


@pytest.fixture
def go_round(monkeypatch):
    """A function that makes the watcher loop go round once, and returns when it has finished doing so."""
    rounds = []
    real_wait = watch.WatchCoordinator.wait

    def counting_wait(self, ignore_pending=False):
        rounds.append(1)  # the loop is back at the top: whatever it did in the round before is done
        return real_wait(self, ignore_pending=ignore_pending)

    monkeypatch.setattr(watch.WatchCoordinator, "wait", counting_wait)

    def go() -> None:
        seen = len(rounds)
        watch.WAKE_EVENT.set()
        assert wait_for(lambda: len(rounds) > seen), "the loop went round"

    return go


def test_the_hold_records_of_an_absent_folder_are_kept(tmp_path, start_watcher, go_round):
    # A 3MF that is held has a record, and a record whose file is gone is dropped. A drive that is
    # not plugged in is not the same as files that are gone: the holds must still be there when it is back.
    missing = tmp_path / "Inbox"
    record = str(missing / "model.3mf")
    with hold_manager.state_lock():
        state = hold_manager.read_state()
        state.setdefault("held", {})[record] = {
            "mtime": 0.0, "release_at": time.time() + 86400.0, "hold_days": 7, "planned_dest": "",
        }
        hold_manager.write_state(state)

    thread, stop, errors = start_watcher(make_config(missing))
    assert wait_for(watch.WATCH_FOLDER_MISSING.is_set)
    for _ in range(3):
        go_round()

    assert record in hold_manager.read_state().get("held", {})
    assert thread.is_alive() and not errors


def test_choosing_another_missing_folder_keeps_waiting_and_makes_nothing(tmp_path, start_watcher, go_round, caplog):
    first, second = tmp_path / "First", tmp_path / "Second"
    caplog.set_level(logging.INFO)
    thread, stop, errors = start_watcher(make_config(first))
    assert wait_for(watch.WATCH_FOLDER_MISSING.is_set)

    Path(watch.CONFIG_PATH).write_text(json.dumps(make_config(second)), encoding="utf-8")
    watch.WAKE_EVENT.set()
    assert wait_for(lambda: "Watched folders changed" in caplog.text), "the new setting was read"
    go_round()  # the round that read it is over

    assert watch.WATCH_FOLDER_MISSING.is_set()
    assert not first.exists() and not second.exists(), "neither folder is made for you"
    assert thread.is_alive() and not errors


# ---------------------------------------------------------------------------
# a folder that goes away while the app runs
# ---------------------------------------------------------------------------

class FakeEmitter:
    """What an observer keeps for a watch: the thread that does the watching, alive or stopped."""

    def __init__(self, watch_id, alive: bool) -> None:
        self.watch = watch_id
        self._alive = alive

    def is_alive(self) -> bool:
        return self._alive


class WatchingObserver(FakeObserver):
    """A FakeObserver that also lists its emitters, as a real one does."""

    def __init__(self) -> None:
        super().__init__()
        self.stopped: set[int] = set()  # the watches whose thread has stopped

    @property
    def emitters(self):
        return [FakeEmitter(watch_id, alive=watch_id not in self.stopped) for watch_id in self.active]


class FlakyObserver(FakeObserver):
    """An observer that cannot open the Nth folder it is asked to watch (a drive that goes away just then)."""

    def __init__(self, fail_on: int) -> None:
        super().__init__()
        self.fail_on = fail_on
        self.calls = 0

    def schedule(self, handler, path, recursive=False):
        self.calls += 1
        if self.calls == self.fail_on:
            raise OSError(f"cannot open {path}")
        return super().schedule(handler, path, recursive)


def test_a_folder_that_goes_away_is_noticed_and_its_watches_stop(tmp_path):
    root, docs = tmp_path / "Inbox", tmp_path / "Docs"
    root.mkdir()
    docs.mkdir()
    config = make_config(root, docs)
    observer = WatchingObserver()
    folders = watch.FolderWatches(observer)
    folders.build(config)

    assert folders.watch_lost() is False, "a folder that is there is fine"
    assert len(observer.active) == 2

    shutil.rmtree(root)

    assert folders.watch_lost() is True
    assert folders.root_missing is True
    assert observer.active == {}, "the Documents source goes too: it is filed into that folder"
    assert folders.watch_lost() is False, "it says so once"

    root.mkdir()

    assert folders.retry(config) is True
    assert sorted(observer.active.values()) == sorted([str(root.resolve()), str(docs.resolve())])


def test_a_watch_that_died_is_replaced_even_when_the_folder_is_back(tmp_path):
    root = tmp_path / "Inbox"
    root.mkdir()
    config = make_config(root)
    observer = WatchingObserver()
    folders = watch.FolderWatches(observer)
    folders.build(config)
    (first,) = observer.active
    observer.stopped.add(first)  # its thread stopped while the drive was away, and the folder is back now

    assert folders.watch_lost() is True
    assert folders.retry(config) is True

    assert folders.root_missing is False
    assert list(observer.active.values()) == [str(root.resolve())]
    assert first not in observer.active, "a new watch, not the dead one"
    assert folders.watch_lost() is False


@pytest.mark.parametrize("fail_on", [1, 2], ids=["the folder itself", "a Documents source"])
def test_a_folder_that_cannot_be_opened_is_waited_for_not_a_crash(tmp_path, fail_on, caplog):
    root, docs = tmp_path / "Inbox", tmp_path / "Docs"
    root.mkdir()
    docs.mkdir()
    config = make_config(root, docs)
    observer = FlakyObserver(fail_on)
    folders = watch.FolderWatches(observer)

    with caplog.at_level(logging.WARNING):
        folders.build(config)

    assert folders.root_missing is True
    assert observer.active == {}, "whatever was watched already is let go"
    assert "could not be opened" in caplog.text

    assert folders.retry(config) is True, "and it is tried again"
    assert len(observer.active) == 2


@pytest.fixture
def share_that_drops(monkeypatch):
    """Make ``Path.is_dir`` fail for a folder, as it can for a network share that has dropped. Returns a switch."""
    real = Path.is_dir
    drops: dict[Path, bool] = {}

    def is_dir(self):
        if drops.get(self):
            raise OSError(64, "The specified network name is no longer available")
        return real(self)

    monkeypatch.setattr(Path, "is_dir", is_dir)

    def switch(folder: Path, dropped: bool = True) -> None:
        drops[folder.resolve()] = dropped

    return switch


def test_a_folder_that_cannot_be_looked_at_counts_as_gone_not_as_a_crash(tmp_path, share_that_drops):
    root = tmp_path / "Inbox"
    root.mkdir()
    config = make_config(root)
    observer = WatchingObserver()
    folders = watch.FolderWatches(observer)
    folders.build(config)

    share_that_drops(root)

    assert folders.watch_lost() is True
    assert folders.retry(config) is False, "it is not there yet"

    share_that_drops(root, dropped=False)

    assert folders.retry(config) is True
    assert list(observer.active.values()) == [str(root.resolve())]


def test_a_folder_that_cannot_be_looked_at_when_the_app_starts_is_waited_for(tmp_path, share_that_drops):
    root = tmp_path / "Inbox"
    root.mkdir()
    share_that_drops(root)
    folders = watch.FolderWatches(WatchingObserver())

    folders.build(make_config(root))

    assert folders.root_missing is True


def test_trying_again_never_raises_for_a_drive_that_is_only_half_there(tmp_path, monkeypatch):
    config = make_config(tmp_path / "Inbox")
    folders = watch.FolderWatches(WatchingObserver())
    folders.build(config)  # the folder is not there

    def half_there(_config):
        raise OSError(64, "The specified network name is no longer available")

    monkeypatch.setattr(watch.FolderWatches, "targets", staticmethod(half_there))

    assert folders.retry(config) is False


@pytest.fixture
def no_slicer(monkeypatch):
    monkeypatch.setattr(hold_manager, "is_slicer_running", lambda *args, **kwargs: False)


def _hold(path: Path, keep_here: bool = False) -> None:
    with hold_manager.state_lock():
        state = hold_manager.read_state()
        state.setdefault("held", {})[str(path)] = {
            "mtime": 0.0, "release_at": time.time() + 86400.0, "hold_days": 7, "planned_dest": "",
            "keep_here": keep_here,
        }
        hold_manager.write_state(state)


def _held() -> dict:
    return hold_manager.read_state().get("held", {})


def test_the_holds_of_a_folder_that_is_out_of_reach_are_kept(tmp_path, no_slicer):
    away = tmp_path / "Inbox"  # not there: a drive that is not plugged in
    _hold(away / "plate.3mf")
    _hold(away / "kept.3mf", keep_here=True)

    assert hold_manager.check_releases(make_config(away)) == []

    assert set(_held()) == {str(away / "plate.3mf"), str(away / "kept.3mf")}


def test_the_holds_of_a_folder_that_cannot_be_looked_at_are_kept(tmp_path, no_slicer, share_that_drops):
    inbox = tmp_path / "Inbox"
    inbox.mkdir()
    _hold(inbox / "plate.3mf")
    share_that_drops(inbox)

    assert hold_manager.check_releases(make_config(inbox)) == []

    assert str(inbox / "plate.3mf") in _held()


def test_a_hold_whose_file_is_gone_from_a_folder_that_is_there_is_dropped(tmp_path, no_slicer):
    inbox = tmp_path / "Inbox"
    inbox.mkdir()
    _hold(inbox / "deleted.3mf")

    hold_manager.check_releases(make_config(inbox))

    assert _held() == {}


@pytest.mark.parametrize("root_is_there", [True, False], ids=["the folder is there", "the folder is away"])
def test_a_hold_outside_the_folder_being_sorted_is_dropped_when_its_file_is_gone(tmp_path, no_slicer, root_is_there):
    inbox = tmp_path / "Inbox"
    if root_is_there:
        inbox.mkdir()
    _hold(tmp_path / "OldFolder" / "plate.3mf")  # not there either, but not where the app sorts any more

    hold_manager.check_releases(make_config(inbox))

    assert _held() == {}, "so the holds of a folder you stopped using do not pile up for ever"


def emitter_threads() -> list:
    from watchdog.observers.api import EventEmitter

    return [thread for thread in threading.enumerate() if isinstance(thread, EventEmitter)]


def _filed(folder: Path, name: str) -> bool:
    """The file is no longer loose in the folder and is somewhere below it."""
    return not (folder / name).exists() and len(list(folder.rglob(name))) == 1


def test_a_drive_unplugged_while_the_app_runs_is_waited_for_and_watched_again(
    tmp_path, start_watcher, go_round, no_slicer
):
    drive = tmp_path / "drive"
    inbox = drive / "Inbox"
    inbox.mkdir(parents=True)
    long_ago = time.time() - 3600
    plate = inbox / "plate.3mf"
    plate.write_text("3mf", encoding="utf-8")
    os.utime(plate, (long_ago, long_ago))
    config = make_config(inbox)
    assert hold_manager.hold_file(plate, config, inbox / "3D Models" / "3MF" / "plate.3mf")
    record = str(plate.resolve())

    thread, stop, errors = start_watcher(config)
    go_round()  # it has started, and finished its first sort

    shutil.rmtree(drive)  # the drive is unplugged
    go_round()

    assert watch.WATCH_FOLDER_MISSING.is_set(), "the watcher noticed"
    assert _runtime_status() == "waiting_for_folder"
    assert record in _held(), "the hold of a file on a drive that is away is not forgotten"
    assert thread.is_alive() and not errors

    # plugged in again, with a file that arrived meanwhile
    inbox.mkdir(parents=True)
    report = inbox / "report.pdf"
    report.write_text("a report", encoding="utf-8")
    os.utime(report, (long_ago, long_ago))
    go_round()

    assert not watch.WATCH_FOLDER_MISSING.is_set()
    assert wait_for(lambda: _filed(inbox, "report.pdf")), "what was in it is filed"
    assert _runtime_status() == "running"

    later = inbox / "later.pdf"
    later.write_text("another", encoding="utf-8")
    os.utime(later, (long_ago, long_ago))

    assert wait_for(lambda: _filed(inbox, "later.pdf")), "and the new watch sees the files that arrive from now on"
    assert thread.is_alive() and not errors


def test_a_drive_that_is_back_before_the_app_looks_is_watched_again(tmp_path, start_watcher, go_round):
    drive = tmp_path / "drive"
    inbox = drive / "Inbox"
    inbox.mkdir(parents=True)
    long_ago = time.time() - 3600
    thread, stop, errors = start_watcher(make_config(inbox))
    go_round()  # it has started
    watching = len(emitter_threads())

    shutil.rmtree(drive)
    assert wait_for(lambda: len(emitter_threads()) < watching), "watchdog stops the watch of a folder that goes away"
    inbox.mkdir(parents=True)  # and the drive is back before the loop has looked
    during = inbox / "during.pdf"
    during.write_text("arrived while nothing was watching", encoding="utf-8")
    os.utime(during, (long_ago, long_ago))
    go_round()

    assert wait_for(lambda: _filed(inbox, "during.pdf")), "what arrived meanwhile is found by the sweep"
    after = inbox / "after.pdf"
    after.write_text("arrives later", encoding="utf-8")
    os.utime(after, (long_ago, long_ago))

    assert wait_for(lambda: _filed(inbox, "after.pdf")), "and the watch that was dead has been replaced"
    assert thread.is_alive() and not errors


# ---------------------------------------------------------------------------
# the tray
# ---------------------------------------------------------------------------

def test_the_tray_tooltip_says_the_watch_folder_is_missing():
    icon = tray.SorterTray()
    assert "Watch folder not found" not in icon.tooltip()

    watch.WATCH_FOLDER_MISSING.set()
    try:
        assert "Watch folder not found" in icon.tooltip()
    finally:
        watch.WATCH_FOLDER_MISSING.clear()


def _save_settings(config: dict) -> None:
    """Save settings to the file the tray reads, the way the window does."""
    sorter.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    sorter.CONFIG_PATH.write_text(json.dumps(config), encoding="utf-8")


def test_sort_now_from_the_tray_says_when_the_folder_is_missing(tmp_path, monkeypatch):
    icon = tray.SorterTray()
    missing = tmp_path / "Inbox"
    _save_settings(make_config(missing))  # settings that name a folder that is not there
    said, messages, sorted_calls = threading.Event(), [], []
    icon.notify = lambda message, title="Download Sorter": (messages.append(message), said.set())
    monkeypatch.setattr(tray, "sort_existing", lambda *args, **kwargs: sorted_calls.append(args) or 0)

    icon.on_sort_now()

    assert said.wait(10)
    assert str(sorter.watch_root(icon.config)) in messages[0]
    assert sorted_calls == []
    assert not missing.exists()


def test_open_downloads_from_the_tray_says_when_the_folder_is_missing(tmp_path, monkeypatch):
    icon = tray.SorterTray()
    opened, messages = [], []
    monkeypatch.setattr(tray, "open_in_explorer", opened.append)
    icon.notify = lambda message, title="Download Sorter": messages.append(message)
    missing = tmp_path / "Inbox"
    _save_settings(make_config(missing))  # settings that name a folder that is not there

    icon.on_open_downloads()

    assert opened == [] and len(messages) == 1 and str(missing.resolve()) in messages[0]

    missing.mkdir()
    icon.on_open_downloads()

    assert opened == [missing.resolve()]
