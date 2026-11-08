"""Regression tests for the 2.0.2 file-safety fixes.

Each test reproduces one problem found in the 2.0.1 review:

1. Duplicate cleanup deleted files, even ones marked "Keep here", with no undo.
2. Dry runs registered holds and showed notifications.
3. Undoing an extraction moved the extracted folder to the zip's old name.
4. Zip extraction had no size or entry limits.
5. A failed hold release forgot the hold.
6. Live reload did not move the folder watches.
7. ``main`` was missing from the packaged modules.
8. Undo was immediately re-sorted by the watcher.
"""

from __future__ import annotations

import json
import os
import sys
import time
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

import hold_manager
import safe_ops
import sort_undo
import sorter
import unzip_archive
import watch

REPO = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

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
                    "auto_unzip": True, "auto_unpack_3d_zips": False}
    cfg["notifications"] = False
    cfg["ribbon_enabled"] = False
    cfg["document_sources"] = []
    cfg["hold"]["enabled"] = True
    return cfg


def _make(path: Path, data: bytes = b"same bytes", age: float = 30.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    old = time.time() - age
    os.utime(path, (old, old))
    return path


def _active_moves() -> list[dict]:
    return sort_undo.get_active_moves(sort_undo.load_history())


def _hold_state() -> dict:
    target = hold_manager.state_file()
    return json.loads(target.read_text(encoding="utf-8")) if target.exists() else {"held": {}}


# ---------------------------------------------------------------------------
# 1. duplicates: retention first, quarantine instead of delete
# ---------------------------------------------------------------------------

def test_duplicate_is_quarantined_not_deleted_and_undo_restores_it(downloads, config):
    _make(downloads / "Documents" / "report.pdf", b"%PDF identical")
    dup = _make(downloads / "report.pdf", b"%PDF identical")

    result = sorter.move_file(dup, config, batch_id="dup1", check_age=False)

    assert result == downloads / "Documents" / "report.pdf"
    assert not dup.exists()
    parked = list((downloads / sorter.DUPLICATES_DIR_NAME).rglob("report.pdf"))
    assert len(parked) == 1, "duplicate must be kept in _Duplicates, not deleted"

    batch = [r for r in _active_moves() if r.get("batch_id") == "dup1"]
    assert batch and batch[-1]["op"] == "quarantine"
    assert sort_undo.undo_moves(batch) == 1
    assert dup.exists() and dup.read_bytes() == b"%PDF identical"


def test_keep_here_duplicate_is_never_touched(downloads, config):
    _make(downloads / "3D Models" / "Benchy.3mf", b"identical 3mf")
    kept = _make(downloads / "Benchy.3mf", b"identical 3mf")
    hold_manager.keep_here(kept)

    assert sorter.move_file(kept, config, check_age=False) is None
    assert kept.exists()
    assert not (downloads / sorter.DUPLICATES_DIR_NAME).exists()


def test_held_duplicate_stays_held_instead_of_being_set_aside(downloads, config):
    _make(downloads / "3D Models" / "Benchy.3mf", b"identical 3mf")
    fresh = _make(downloads / "Benchy.3mf", b"identical 3mf")

    assert sorter.move_file(fresh, config, check_age=False) is None
    assert fresh.exists() and hold_manager.is_held(fresh)
    assert not (downloads / sorter.DUPLICATES_DIR_NAME).exists()


def test_watcher_ignores_the_duplicates_folder(downloads, config):
    handler = watch.DownloadsHandler(downloads, config)
    parked = _make(downloads / sorter.DUPLICATES_DIR_NAME / "x.pdf")
    handler._queue(str(parked))
    assert not watch.PENDING_DOWNLOADS and not watch.PENDING_MISPLACED


# ---------------------------------------------------------------------------
# 2. dry runs never write hold state or notify
# ---------------------------------------------------------------------------

def test_dry_run_move_file_does_not_register_a_hold_or_notify(downloads, config):
    sample = _make(downloads / "Benchy.3mf", b"3mf")
    import types
    from unittest.mock import MagicMock
    notify = MagicMock()
    stub = types.SimpleNamespace(notify_file_held=notify)
    with patch.dict(sys.modules, {"ribbon_popup": stub}), \
            patch.object(hold_manager, "hold_file", wraps=hold_manager.hold_file) as hold:
        config["ribbon_enabled"] = config["notifications"] = True
        assert sorter.move_file(sample, config, dry_run=True, check_age=False) is None
    hold.assert_not_called()
    notify.assert_not_called()
    assert not hold_manager.state_file().exists()


def test_dry_run_does_not_reset_an_existing_hold(downloads, config):
    sample = _make(downloads / "Benchy.3mf", b"3mf")
    sorter.move_file(sample, config, check_age=False)
    before = hold_manager.get_held(sample)
    time.sleep(0.01)
    sorter.move_file(sample, config, dry_run=True, check_age=False)
    assert hold_manager.get_held(sample) == before


def test_dry_run_reclassify_and_documents_do_not_register_holds(tmp_path, downloads, config):
    misplaced = _make(downloads / "Documents" / "Part.3mf", b"3mf")
    docs = tmp_path / "Docs"
    docs.mkdir()
    config["document_sources"] = [str(docs)]
    config.setdefault("documents", {})
    in_docs = _make(docs / "Plate.3mf", b"3mf")
    with patch.object(hold_manager, "hold_file") as hold:
        sorter.reclassify_misplaced_file(misplaced, config, dry_run=True)
        sorter.relocate_from_documents(in_docs, config, dry_run=True)
    hold.assert_not_called()
    assert not hold_manager.state_file().exists()


# ---------------------------------------------------------------------------
# 3. extraction undo
# ---------------------------------------------------------------------------

def _zip(path: Path, members: dict[str, bytes], method=zipfile.ZIP_DEFLATED) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=method) as z:
        for name, data in members.items():
            z.writestr(name, data)
    old = time.time() - 30
    os.utime(path, (old, old))
    return path


def test_undo_after_extraction_brings_the_zip_back(downloads, config):
    archive = _zip(downloads / "bundle.zip", {"readme.txt": b"hello", "data/values.csv": b"1,2"})

    landing = sorter.move_file(archive, config, batch_id="ex1", check_age=False)

    assert landing is not None and landing.is_dir()
    assert (landing / "readme.txt").read_bytes() == b"hello"
    assert not archive.exists()
    batch = [r for r in _active_moves() if r.get("batch_id") == "ex1"]
    assert len(batch) == 1, "only the zip's move to Archives is an undoable move"
    assert Path(batch[0]["dest"]).suffix == ".zip"
    extract_records = [r for r in safe_ops.read_journal() if r.get("action") == "extract"]
    assert extract_records and extract_records[-1]["archive"].endswith("bundle.zip")

    assert sort_undo.undo_moves(batch) == 1
    assert archive.is_file() and zipfile.is_zipfile(archive)
    assert landing.is_dir(), "the extracted copy is left alone"


def test_legacy_extraction_record_is_refused_not_turned_into_a_folder(downloads):
    extracted = downloads / "Extracted" / "old"
    extracted.mkdir(parents=True)
    (extracted / "a.txt").write_text("a")
    legacy = {"id": "legacy1", "batch_id": "old", "src": str(downloads / "old.zip"), "dest": str(extracted)}

    with pytest.raises(safe_ops.Refused) as info:
        safe_ops.restore(legacy)
    assert info.value.reason == "extraction-record"
    assert not (downloads / "old.zip").exists()
    assert extracted.is_dir()


# ---------------------------------------------------------------------------
# 4. archive limits
# ---------------------------------------------------------------------------

def test_high_ratio_member_is_rejected_before_extraction(downloads, config):
    bomb = _zip(downloads / "bomb.zip", {"zeros.bin": b"\0" * (8 * 1024 * 1024)})
    assert unzip_archive.unpack_archive(bomb, config) is None
    assert not (downloads / "Extracted").exists() or not any((downloads / "Extracted").iterdir())
    assert bomb.exists()


def test_too_many_entries_and_total_size_limits(downloads, config):
    many = _zip(downloads / "many.zip", {f"f{i}.txt": b"x" for i in range(30)})
    config["archive_limits"] = {"max_entries": 10}
    assert unzip_archive.unpack_archive(many, config) is None

    big = _zip(downloads / "big.zip", {"blob.bin": os.urandom(3 * 1024 * 1024)})
    config["archive_limits"] = {"max_total_mb": 1}
    assert unzip_archive.unpack_archive(big, config) is None
    assert big.exists() and many.exists()


def test_rejected_zip_is_filed_as_a_normal_archive(downloads, config):
    bomb = _zip(downloads / "bomb.zip", {"zeros.bin": b"\0" * (8 * 1024 * 1024)})
    dest = sorter.move_file(bomb, config, check_age=False)
    assert dest is not None and dest.suffix == ".zip" and dest.is_file()


def test_failed_extraction_leaves_no_partial_folder(downloads, config):
    archive = _zip(downloads / "corrupt.zip", {"a.txt": b"A" * 5000, "b.txt": b"B" * 5000},
                   method=zipfile.ZIP_STORED)
    raw = bytearray(archive.read_bytes())
    idx = raw.find(b"B" * 100)
    raw[idx:idx + 10] = b"X" * 10  # breaks b.txt's CRC
    archive.write_bytes(bytes(raw))

    assert unzip_archive.unpack_archive(archive, config) is None
    extracted = downloads / "Extracted"
    leftovers = list(extracted.rglob("*")) if extracted.exists() else []
    assert leftovers == [], f"partial extraction left {leftovers}"
    assert archive.exists()


# ---------------------------------------------------------------------------
# 5. hold releases are only dropped after a successful move
# ---------------------------------------------------------------------------

def _due_hold(downloads, config) -> Path:
    sample = _make(downloads / "Benchy.3mf", b"3mf", age=10 * 86400)
    sorter.move_file(sample, config, check_age=False)
    state = _hold_state()
    rec = state["held"][str(sample.resolve())]
    rec["release_at"] = time.time() - 60
    hold_manager.write_state(state)
    return sample


def test_failed_release_keeps_the_hold_and_retries_later(downloads, config):
    sample = _due_hold(downloads, config)
    with patch.object(hold_manager, "is_slicer_running", return_value=False), \
            patch.object(safe_ops, "move", side_effect=OSError("disk full")):
        assert watch.process_hold_releases(config, downloads) == 0
    rec = hold_manager.get_held(sample)
    assert rec is not None, "a failed release must not forget the hold"
    assert rec["release_failures"] == 1
    assert rec["release_at"] > time.time() + 3000


def test_successful_release_drops_the_hold(downloads, config):
    sample = _due_hold(downloads, config)
    with patch.object(hold_manager, "is_slicer_running", return_value=False):
        assert watch.process_hold_releases(config, downloads) == 1
    assert not sample.exists()
    assert hold_manager.get_held(sample) is None


def test_file_now_failure_keeps_the_hold(downloads, config):
    sample = _make(downloads / "Benchy.3mf", b"3mf")
    sorter.move_file(sample, config, check_age=False)
    with patch.object(safe_ops, "move", side_effect=OSError("locked")):
        assert hold_manager.file_now(sample, config) is None
    assert hold_manager.is_held(sample)


# ---------------------------------------------------------------------------
# 6. live reload moves the folder watches
# ---------------------------------------------------------------------------

class _FakeObserver:
    def __init__(self) -> None:
        self.active: dict[int, str] = {}
        self._next = 0

    def schedule(self, handler, path, recursive=False):
        self._next += 1
        self.active[self._next] = path
        return self._next

    def unschedule(self, watch_id):
        del self.active[watch_id]


def test_retarget_moves_watches_when_folders_change(tmp_path, downloads, config):
    observer = _FakeObserver()
    folders = watch.FolderWatches(observer)
    folders.build(config)
    assert sorted(observer.active.values()) == [str(downloads.resolve())]

    assert folders.retarget(config) is False, "unchanged config keeps the same watches"

    new_root = tmp_path / "NewDownloads"
    new_root.mkdir()
    docs = tmp_path / "Docs"
    docs.mkdir()
    watch.PENDING_DOWNLOADS.add(downloads / "stale.pdf")
    changed = dict(config, watch=dict(config["watch"], folder=str(new_root)), document_sources=[str(docs)])

    assert folders.retarget(changed) is True
    assert sorted(observer.active.values()) == sorted([str(new_root.resolve()), str(docs.resolve())])
    assert not watch.PENDING_DOWNLOADS, "events from the old folder are dropped"
    assert folders.downloads_handler.root == new_root.resolve()


# ---------------------------------------------------------------------------
# 7. packaging
# ---------------------------------------------------------------------------

def test_every_console_script_module_is_packaged():
    tomllib = pytest.importorskip("tomllib")  # Python 3.11+; the app supports 3.10
    meta = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    modules = set(meta["tool"]["setuptools"]["py-modules"])
    for name, target in meta["project"]["scripts"].items():
        module = target.split(":", 1)[0]
        assert module in modules, f"{name} -> {module} is not listed in py-modules"
        assert (REPO / f"{module}.py").is_file()


# ---------------------------------------------------------------------------
# 8. undo sticks while the watcher runs
# ---------------------------------------------------------------------------

def test_undone_file_is_left_alone_until_it_changes(downloads, config):
    report = _make(downloads / "report.pdf", b"%PDF data")
    sorter.move_file(report, config, batch_id="u1", check_age=False)
    batch = [r for r in _active_moves() if r.get("batch_id") == "u1"]
    assert sort_undo.undo_moves(batch) == 1 and report.exists()

    assert sorter.move_file(report, config, check_age=False) is None, "watcher re-sorted an undone file"
    assert report.exists()

    report.write_bytes(b"%PDF edited after undo")
    old = time.time() - 100
    os.utime(report, (old, old))
    assert sorter.move_file(report, config, check_age=False) is not None, "an edited file sorts again"


def test_drain_does_not_spin_on_an_undone_file(downloads, config):
    report = _make(downloads / "report.pdf", b"%PDF data")
    sorter.remember_restored(report)
    watch.PENDING_DOWNLOADS.add(report)
    watch.PENDING_DEADLINES[("download", report)] = 0
    watch.drain(config, root=downloads)
    assert report not in watch.PENDING_DOWNLOADS


def test_restart_sweep_does_not_push_back_an_unchanged_hold(downloads, config):
    sample = _make(downloads / "Benchy.3mf", b"3mf")
    sorter.move_file(sample, config, check_age=False)
    state = _hold_state()
    rec = state["held"][str(sample.resolve())]
    rec["release_at"] = time.time() - 60
    hold_manager.write_state(state)

    sorter.sort_existing(config)  # what every restart does

    assert hold_manager.get_held(sample)["release_at"] < time.time(), "restart reset the hold"


def test_editing_a_held_file_restarts_its_hold(downloads, config):
    sample = _make(downloads / "Benchy.3mf", b"3mf")
    sorter.move_file(sample, config, check_age=False)
    state = _hold_state()
    state["held"][str(sample.resolve())]["release_at"] = time.time() - 60
    hold_manager.write_state(state)

    _make(sample, b"3mf saved again in the slicer", age=10)
    sorter.move_file(sample, config, check_age=False)

    assert hold_manager.get_held(sample)["release_at"] > time.time() + 6 * 86400
