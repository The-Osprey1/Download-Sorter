"""v3.0.0 release hardening: P0-1..P0-5 and P1-1..P1-4 from the pre-release audit.

- P0-1  watcher restart never joins while holding the service lock
- P0-2  background workers (inline under pytest, off-thread in the app)
- P0-3  two-phase PENDING -> COMPLETED move journal and crash reconciliation
- P0-4  undo: locked destination, re-occupied source, deleted destination
- P0-5  version parity and CLI surface (via verify_release)
- P1-1  slicer process detection and the .3mf hold extension
- P1-2  installer retry / rename-aside source
- P1-3  cloud placeholder guard and tiered duplicate hashing
- P1-4  per-connection pragmas and WAL truncation
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time

import pytest

from download_sorter import fs_ops
from download_sorter.config import Config, save_config
from download_sorter.db import StateDB
from download_sorter.paths import AppPaths
import download_sorter.service as service_module
from download_sorter.service import SLICER_HOLD_MAX_DAYS, SorterService

ROOT = Path(__file__).resolve().parent.parent
DAY = 86400.0


def _service(app_paths: AppPaths, **cfg_overrides) -> SorterService:  # noqa: ANN003
    cfg = Config.with_starter_defaults()
    cfg.settle_seconds = 0.0
    for key, value in cfg_overrides.items():
        setattr(cfg, key, value)
    save_config(cfg, app_paths)
    return SorterService(app_paths)


def _status(db: StateDB, move_id: int) -> str:
    rec = db.get_move(move_id)
    assert rec is not None
    return str(rec["status"])


# ---------------------------------------------------------------------------
# P0-1: observer stop/join happens outside SorterService._lock
# ---------------------------------------------------------------------------
class _ProbeObserver:
    """Stands in for watchdog's Observer.

    ``join()`` simulates an in-flight filesystem event: a second thread calls
    ``service._enqueue()`` (which needs the service lock) and must get through
    while the caller is still inside ``join()``.
    """

    instances: list["_ProbeObserver"] = []
    service: SorterService | None = None

    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.join_calls = 0
        self.enqueue_got_through: bool | None = None
        _ProbeObserver.instances.append(self)

    def schedule(self, handler, path, recursive=False) -> None:  # noqa: ANN001
        self.handler = handler
        self.path = path

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def join(self, timeout: float | None = None) -> None:
        self.join_calls += 1
        svc = _ProbeObserver.service
        assert svc is not None
        worker = threading.Thread(target=svc._enqueue, args=(Path("in-flight.pdf"),), daemon=True)
        worker.start()
        worker.join(timeout=2.0)
        self.enqueue_got_through = not worker.is_alive()


@pytest.fixture
def probe_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SorterService:
    """A service whose watch folder comes from config, so changing it restarts the watcher."""
    _ProbeObserver.instances = []
    monkeypatch.setattr(service_module, "Observer", _ProbeObserver)
    first = tmp_path / "DownloadsA"
    first.mkdir()
    (tmp_path / "DownloadsB").mkdir()
    data = tmp_path / "data"
    data.mkdir()
    paths = AppPaths(data_dir=data, downloads_dir=None)
    cfg = Config.with_starter_defaults()
    cfg.watch_folder = str(first)
    save_config(cfg, paths)
    svc = SorterService(paths)
    _ProbeObserver.service = svc
    svc._start_observer()
    assert len(_ProbeObserver.instances) == 1 and _ProbeObserver.instances[0].started
    yield svc
    svc.stop(timeout=0.5)
    _ProbeObserver.service = None


def _config_for(svc: SorterService, folder: Path) -> Config:
    import copy

    cfg = copy.deepcopy(svc.config)
    cfg.watch_folder = str(folder)
    return cfg


def test_p0_1_update_config_joins_observer_outside_lock(probe_service: SorterService, tmp_path: Path) -> None:
    old = _ProbeObserver.instances[0]
    started = time.monotonic()
    probe_service.update_config(_config_for(probe_service, tmp_path / "DownloadsB"))
    elapsed = time.monotonic() - started

    assert old.stopped and old.join_calls == 1
    assert old.enqueue_got_through is True, "watcher event blocked on the service lock during join()"
    assert elapsed < 1.5, f"update_config stalled for {elapsed:.2f}s"
    # A fresh watcher replaced the old one and points at the new folder.
    assert len(_ProbeObserver.instances) == 2
    new = _ProbeObserver.instances[1]
    assert probe_service._observer is new and new.started
    assert Path(new.path) == (tmp_path / "DownloadsB").resolve()
    assert Path("in-flight.pdf") in probe_service._pending


def test_p0_1_restart_observer_joins_outside_lock(probe_service: SorterService) -> None:
    old = _ProbeObserver.instances[0]
    probe_service._restart_observer()
    assert old.stopped and old.enqueue_got_through is True
    assert probe_service._observer is _ProbeObserver.instances[1]


def test_p0_1_reload_config_and_stop_join_outside_lock(probe_service: SorterService, tmp_path: Path) -> None:
    old = _ProbeObserver.instances[0]
    save_config(_config_for(probe_service, tmp_path / "DownloadsB"), probe_service.paths)
    probe_service.reload_config()
    assert old.enqueue_got_through is True

    current = _ProbeObserver.instances[1]
    probe_service.stop(timeout=0.5)
    assert current.stopped and current.enqueue_got_through is True
    assert probe_service._observer is None


def test_p0_1_update_config_same_folder_keeps_observer(probe_service: SorterService) -> None:
    import copy

    cfg = copy.deepcopy(probe_service.config)
    cfg.paused = True
    probe_service.update_config(cfg)
    assert len(_ProbeObserver.instances) == 1
    assert _ProbeObserver.instances[0].join_calls == 0


# ---------------------------------------------------------------------------
# P0-3 / P1-4: two-phase journal, reconciliation, pragmas, WAL hygiene
# ---------------------------------------------------------------------------
def _intent(db: StateDB, src: Path, dest: Path, batch: str = "b-test") -> int:
    return db.record_move_intent(
        batch_id=batch,
        filename=src.name,
        src_path=src,
        dest_path=dest,
        reason="test",
        matched_rule="test",
        match_type="category",
    )


def test_p0_3_pending_intent_is_invisible_until_completed(tmp_path: Path) -> None:
    db = StateDB(tmp_path / "state.db")
    move_id = _intent(db, tmp_path / "a.pdf", tmp_path / "Docs" / "a.pdf")

    assert _status(db, move_id) == "PENDING"
    assert db.list_active_moves() == []
    assert db.get_batch_moves() == []
    assert db.list_batches() == []
    assert db.stats()["total_moves"] == 0

    final = tmp_path / "Docs" / "a (2).pdf"
    db.complete_move_intent(move_id, final)
    assert _status(db, move_id) == "COMPLETED"
    rows = db.list_active_moves()
    assert [r["id"] for r in rows] == [move_id]
    assert rows[0]["dest_path"] == str(final)
    assert db.stats()["active_moves"] == 1


def test_p0_3_rollback_only_affects_pending_rows(tmp_path: Path) -> None:
    db = StateDB(tmp_path / "state.db")
    pending = _intent(db, tmp_path / "a.pdf", tmp_path / "Docs" / "a.pdf")
    done = _intent(db, tmp_path / "b.pdf", tmp_path / "Docs" / "b.pdf")
    db.complete_move_intent(done, tmp_path / "Docs" / "b.pdf")

    db.rollback_move_intent(pending)
    db.rollback_move_intent(done)  # must not un-complete a finished move
    assert _status(db, pending) == "ROLLED_BACK"
    assert _status(db, done) == "COMPLETED"
    assert db.stats()["total_moves"] == 1


def test_p0_3_execute_move_journals_before_touching_the_file(
    app_paths: AppPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = _service(app_paths)
    dl = app_paths.downloads_dir
    assert dl is not None
    src = dl / "report.pdf"
    src.write_bytes(b"pdf")

    seen: dict[str, object] = {}
    real_move = fs_ops._move_with_retries

    def spy(move_src: Path, move_dest: Path) -> None:
        pending = svc.db.list_pending_moves()
        seen["pending_rows"] = len(pending)
        seen["src_still_there"] = Path(move_src).exists()
        seen["journaled_dest"] = pending[0]["dest_path"] if pending else None
        seen["dest"] = str(move_dest)
        real_move(move_src, move_dest)

    monkeypatch.setattr(fs_ops, "_move_with_retries", spy)
    res = svc.scan_top_level_now(settle_seconds=0.0)

    assert seen["pending_rows"] == 1, "intent must be in state.db before the file moves"
    assert seen["src_still_there"] is True
    assert seen["journaled_dest"] == seen["dest"]
    assert len(res["moved"]) == 1
    move_id = res["moved"][0]["id"]
    assert _status(svc.db, move_id) == "COMPLETED"
    assert svc.db.list_pending_moves() == []
    assert Path(res["moved"][0]["dest_path"]).is_file()


def test_p0_3_failed_move_is_rolled_back_and_file_untouched(
    app_paths: AppPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = _service(app_paths)
    dl = app_paths.downloads_dir
    assert dl is not None
    src = dl / "report.pdf"
    src.write_bytes(b"pdf")

    def boom(_src: Path, _dest: Path) -> None:
        raise PermissionError(13, "simulated sharing violation")

    monkeypatch.setattr(fs_ops, "_move_with_retries", boom)
    res = svc.scan_top_level_now(settle_seconds=0.0)

    assert res["moved"] == [] and res["skipped_busy"] == 1
    assert src.read_bytes() == b"pdf"
    assert svc.db.list_pending_moves() == []
    assert svc.db.list_active_moves() == []
    with sqlite3.connect(svc.db.db_path) as conn:
        statuses = [row[0] for row in conn.execute("SELECT status FROM moves")]
    assert statuses == ["ROLLED_BACK"]
    assert svc.undo_last_batch()["restored"] == 0


def test_p0_3_reconcile_incomplete_moves_all_cases(tmp_path: Path) -> None:
    db = StateDB(tmp_path / "state.db")
    inbox = tmp_path / "Downloads"
    sorted_dir = tmp_path / "Downloads" / "Documents"
    sorted_dir.mkdir(parents=True)

    # 1. Crash after the move: source gone, destination present -> COMPLETED
    finished_dest = sorted_dir / "finished.pdf"
    finished_dest.write_bytes(b"moved")
    finished = _intent(db, inbox / "finished.pdf", finished_dest)

    # 2. Crash before the move: source present, destination absent -> ROLLED_BACK
    never_src = inbox / "never.pdf"
    never_src.write_bytes(b"still here")
    never = _intent(db, never_src, sorted_dir / "never.pdf")

    # 3. Crash mid cross-volume copy: both present, destination is a partial copy
    partial_src = inbox / "big.iso"
    partial_src.write_bytes(b"0123456789" * 1000)
    partial_dest = sorted_dir / "big.iso"
    partial_dest.write_bytes(b"0123456789" * 400)
    partial = _intent(db, partial_src, partial_dest)

    # 4. Both present but the destination is a DIFFERENT file: never delete it
    other_src = inbox / "notes.txt"
    other_src.write_bytes(b"my notes")
    other_dest = sorted_dir / "notes.txt"
    other_dest.write_bytes(b"somebody else's file")
    other = _intent(db, other_src, other_dest)

    # 5. Neither exists any more
    vanished = _intent(db, inbox / "gone.pdf", sorted_dir / "gone.pdf")

    summary = db.reconcile_incomplete_moves()

    assert summary == {"completed": 1, "rolled_back": 4, "partials_removed": 1}
    assert _status(db, finished) == "COMPLETED"
    assert _status(db, never) == "ROLLED_BACK" and never_src.read_bytes() == b"still here"
    assert _status(db, partial) == "ROLLED_BACK"
    assert not partial_dest.exists() and partial_src.stat().st_size == 10000
    assert _status(db, other) == "ROLLED_BACK"
    assert other_dest.read_bytes() == b"somebody else's file"
    assert _status(db, vanished) == "ROLLED_BACK"
    assert db.list_pending_moves() == []

    # The recovered move is ordinary history again: it can be undone.
    res = fs_ops.undo_batch(db)
    assert res["restored"] == 1
    assert (inbox / "finished.pdf").read_bytes() == b"moved"
    # Idempotent.
    assert db.reconcile_incomplete_moves() == {"completed": 0, "rolled_back": 0, "partials_removed": 0}


def test_p0_3_service_start_reconciles_interrupted_moves(app_paths: AppPaths) -> None:
    svc = _service(app_paths, paused=True)
    dl = app_paths.downloads_dir
    assert dl is not None
    dest = dl / "Documents" / "PDF" / "crashed.pdf"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"arrived just before the crash")
    move_id = _intent(svc.db, dl / "crashed.pdf", dest, batch="b-crash")

    svc.start()
    try:
        assert _status(svc.db, move_id) == "COMPLETED"
    finally:
        svc.stop(timeout=2.0)
    assert svc.undo_last_batch()["restored"] == 1
    assert (dl / "crashed.pdf").is_file()


def test_p0_3_existing_state_db_is_migrated(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE moves (
                id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT NOT NULL, filename TEXT NOT NULL,
                src_path TEXT NOT NULL, dest_path TEXT NOT NULL, reason TEXT NOT NULL,
                matched_rule TEXT NOT NULL, match_type TEXT NOT NULL,
                source_url TEXT NOT NULL DEFAULT '', size_bytes INTEGER NOT NULL DEFAULT 0,
                is_duplicate INTEGER NOT NULL DEFAULT 0, moved_at REAL NOT NULL, undone_at REAL
            )
            """
        )
        conn.execute(
            "INSERT INTO moves (batch_id, filename, src_path, dest_path, reason, matched_rule, match_type, moved_at)"
            " VALUES ('b-old', 'old.pdf', 'C:/a/old.pdf', 'C:/a/Docs/old.pdf', 'r', 'm', 'category', 1.0)"
        )
        conn.commit()

    db = StateDB(db_path)
    rows = db.list_active_moves()
    assert len(rows) == 1 and rows[0]["status"] == "COMPLETED"
    StateDB(db_path)  # opening again must not try to add the column twice


def test_p1_4_every_connection_uses_wal_and_synchronous_normal(tmp_path: Path) -> None:
    db = StateDB(tmp_path / "state.db")
    for _ in range(2):
        conn = db._connect()
        try:
            assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
            assert str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal"
        finally:
            conn.close()


def test_p1_4_close_truncates_wal_and_db_stays_usable(tmp_path: Path) -> None:
    db = StateDB(tmp_path / "state.db")
    for i in range(50):
        mid = _intent(db, tmp_path / f"f{i}.pdf", tmp_path / "Docs" / f"f{i}.pdf")
        db.complete_move_intent(mid, tmp_path / "Docs" / f"f{i}.pdf")
    db.checkpoint()  # PASSIVE: must not raise or lose anything
    assert db.stats()["active_moves"] == 50

    db.close()
    wal = tmp_path / "state.db-wal"
    assert not wal.exists() or wal.stat().st_size == 0
    assert db.stats()["active_moves"] == 50  # still usable after close()
    db.checkpoint(truncate=True)


def test_p1_4_service_stop_truncates_wal(app_paths: AppPaths) -> None:
    svc = _service(app_paths)
    dl = app_paths.downloads_dir
    assert dl is not None
    for i in range(5):
        (dl / f"doc{i}.pdf").write_bytes(b"x" * (i + 1))
    assert len(svc.scan_top_level_now(settle_seconds=0.0)["moved"]) == 5
    svc.stop(timeout=1.0)
    wal = Path(str(svc.db.db_path) + "-wal")
    assert not wal.exists() or wal.stat().st_size == 0


# ---------------------------------------------------------------------------
# P0-4: safe undo
# ---------------------------------------------------------------------------
def test_p0_4_undo_never_overwrites_a_newer_file_at_the_source(app_paths: AppPaths) -> None:
    svc = _service(app_paths)
    dl = app_paths.downloads_dir
    assert dl is not None
    original = dl / "invoice.pdf"
    original.write_bytes(b"first download")
    first = svc.scan_top_level_now(settle_seconds=0.0)
    assert len(first["moved"]) == 1

    # A different file with the same name arrives; sorting is paused so it stays put.
    original.write_bytes(b"NEWER download")
    res = svc.undo_last_batch()

    assert res["restored"] == 1 and res["skipped"] == 0
    assert original.read_bytes() == b"NEWER download", "undo overwrote the newer file"
    restored = dl / "invoice (Restored).pdf"
    assert restored.read_bytes() == b"first download"
    assert res["restored_renamed"] == [{"original": str(original), "restored_as": str(restored)}]
    assert svc.db.list_active_moves() == []  # marked undone


def test_p0_4_restored_suffix_counts_up(app_paths: AppPaths) -> None:
    svc = _service(app_paths)
    dl = app_paths.downloads_dir
    assert dl is not None
    (dl / "invoice.pdf").write_bytes(b"v1")
    svc.scan_top_level_now(settle_seconds=0.0)
    (dl / "invoice.pdf").write_bytes(b"occupier")
    (dl / "invoice (Restored).pdf").write_bytes(b"earlier restore")

    move_id = svc.db.list_active_moves()[0]["id"]
    res = svc.undo_single_move(move_id)

    assert res["restored"] == 1
    assert (dl / "invoice.pdf").read_bytes() == b"occupier"
    assert (dl / "invoice (Restored).pdf").read_bytes() == b"earlier restore"
    assert (dl / "invoice (Restored 2).pdf").read_bytes() == b"v1"


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 share-mode locking")
def test_p0_4_undo_batch_skips_locked_destination_and_finishes_the_rest(app_paths: AppPaths) -> None:
    svc = _service(app_paths)
    dl = app_paths.downloads_dir
    assert dl is not None
    (dl / "budget.pdf").write_bytes(b"budget")
    (dl / "notes.pdf").write_bytes(b"notes")
    moved = svc.scan_top_level_now(settle_seconds=0.0)["moved"]
    assert len(moved) == 2
    by_name = {m["filename"]: Path(m["dest_path"]) for m in moved}

    with open(by_name["budget.pdf"], "rb"):  # another app has it open
        res = svc.undo_last_batch()

        assert res["restored"] == 1
        assert res["skipped_locked"] == 1 and res["skipped"] == 1
        assert res["locked_files"] == [str(by_name["budget.pdf"])]
        assert (dl / "notes.pdf").read_bytes() == b"notes"
        assert by_name["budget.pdf"].is_file() and not (dl / "budget.pdf").exists()

    # The locked file is still in history, so undo works once it is closed.
    assert [r["filename"] for r in svc.db.list_active_moves()] == ["budget.pdf"]
    again = svc.undo_last_batch()
    assert again["restored"] == 1 and again["skipped_locked"] == 0
    assert (dl / "budget.pdf").read_bytes() == b"budget"


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 share-mode locking")
def test_p0_4_undo_single_move_reports_locked_file(app_paths: AppPaths) -> None:
    svc = _service(app_paths)
    dl = app_paths.downloads_dir
    assert dl is not None
    (dl / "budget.pdf").write_bytes(b"budget")
    rec = svc.scan_top_level_now(settle_seconds=0.0)["moved"][0]
    with open(rec["dest_path"], "rb"):
        res = svc.undo_single_move(rec["id"])
    assert res["restored"] == 0 and res["skipped_locked"] == 1
    assert Path(rec["dest_path"]).is_file()


def test_p0_4_undo_when_sorted_file_was_deleted_marks_it_orphaned(app_paths: AppPaths) -> None:
    svc = _service(app_paths)
    dl = app_paths.downloads_dir
    assert dl is not None
    (dl / "kept.pdf").write_bytes(b"kept")
    (dl / "deleted.pdf").write_bytes(b"deleted")
    moved = svc.scan_top_level_now(settle_seconds=0.0)["moved"]
    by_name = {m["filename"]: m for m in moved}
    Path(by_name["deleted.pdf"]["dest_path"]).unlink()  # user deleted the sorted copy

    res = svc.undo_last_batch()  # must not raise FileNotFoundError

    assert res["restored"] == 1 and res["orphaned"] == 1 and res["skipped_locked"] == 0
    assert (dl / "kept.pdf").is_file() and not (dl / "deleted.pdf").exists()
    assert _status(svc.db, by_name["deleted.pdf"]["id"]) == "ORPHANED"
    assert svc.db.list_active_moves() == []
    # Nothing left to undo, and asking again is harmless.
    assert svc.undo_last_batch()["batch_id"] == ""
    assert svc.undo_single_move(by_name["deleted.pdf"]["id"])["restored"] == 0


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 share-mode locking")
def test_p0_4_is_file_locked_detects_a_file_open_elsewhere(tmp_path: Path) -> None:
    target = tmp_path / "open.xlsx"
    target.write_bytes(b"data")
    assert fs_ops.is_file_locked(target) is False
    with open(target, "rb"):
        assert fs_ops.is_file_locked(target) is True
    assert fs_ops.is_file_locked(target) is False
    assert fs_ops.is_file_locked(tmp_path / "missing.xlsx") is False
    assert target.read_bytes() == b"data"  # the check itself changes nothing


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 share-mode locking")
def test_p0_4_sorting_skips_a_file_that_is_open_elsewhere(app_paths: AppPaths) -> None:
    svc = _service(app_paths)
    dl = app_paths.downloads_dir
    assert dl is not None
    busy = dl / "still-writing.pdf"
    busy.write_bytes(b"partial")
    with open(busy, "rb"):
        res = svc.scan_top_level_now(settle_seconds=0.0)
        assert res["moved"] == [] and res["skipped_busy"] == 1
    assert busy.is_file()
    assert svc.db.list_pending_moves() == []  # skipped before any intent was journaled
    assert len(svc.scan_top_level_now(settle_seconds=0.0)["moved"]) == 1


# ---------------------------------------------------------------------------
# P1-1: slicer detection and .3mf hold extension
# ---------------------------------------------------------------------------
@pytest.mark.skipif(sys.platform != "win32", reason="Win32 process snapshot")
def test_p1_1_process_snapshot_sees_this_interpreter_without_a_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_subprocess(*_a, **_k):  # noqa: ANN002, ANN003
        raise AssertionError("is_slicer_running must not shell out")

    monkeypatch.setattr(subprocess, "Popen", no_subprocess)
    monkeypatch.setattr(subprocess, "run", no_subprocess)
    monkeypatch.setattr(os, "system", no_subprocess)

    me = Path(sys.executable).name.lower()
    names = set(fs_ops._iter_process_names())
    assert me in names
    assert all(n == n.lower() for n in names)
    assert fs_ops.is_slicer_running({me.upper()}) is True  # case-insensitive
    assert fs_ops.is_slicer_running({"definitely-not-a-real-process-7f3a.exe"}) is False
    assert fs_ops.is_slicer_running(set()) is False


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 process snapshot")
def test_p1_1_process_snapshot_is_fast() -> None:
    fs_ops.is_slicer_running()  # warm up ctypes
    started = time.perf_counter()
    for _ in range(20):
        fs_ops.is_slicer_running()
    per_call_ms = (time.perf_counter() - started) / 20 * 1000
    # Typically well under 1 ms; the bound only catches a regression to a subprocess (80 ms+).
    assert per_call_ms < 25, f"{per_call_ms:.2f} ms per call"


@pytest.mark.parametrize(
    "image",
    ["bambu-studio.exe", "BambuStudio.exe", "orca-slicer.exe", "OrcaSlicer.exe", "prusaslicer.exe", "Cura.exe"],
)
def test_p1_1_default_slicer_names_are_recognised(monkeypatch: pytest.MonkeyPatch, image: str) -> None:
    monkeypatch.setattr(fs_ops, "_iter_process_names", lambda: iter(["explorer.exe", image.lower()]))
    assert fs_ops.is_slicer_running() is True
    monkeypatch.setattr(fs_ops, "_iter_process_names", lambda: iter(["explorer.exe", "notepad.exe"]))
    assert fs_ops.is_slicer_running() is False


def test_p1_1_slicer_scan_failure_never_breaks_sorting(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken():
        raise OSError("snapshot failed")

    monkeypatch.setattr(fs_ops, "_iter_process_names", broken)
    assert fs_ops.is_slicer_running() is False


def _aged_3mf(folder: Path, name: str, age_days: float) -> Path:
    path = folder / name
    path.write_bytes(b"3mf " + name.encode())
    stamp = time.time() - age_days * DAY
    os.utime(path, (stamp, stamp))
    return path


def test_p1_1_expired_3mf_stays_held_while_slicer_runs(
    app_paths: AppPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = _service(app_paths, hold_3mf_enabled=True, hold_3mf_days=7)
    dl = app_paths.downloads_dir
    assert dl is not None
    model = _aged_3mf(dl, "benchy.3mf", age_days=10)  # hold ran out 3 days ago

    monkeypatch.setattr(service_module, "is_slicer_running", lambda *a, **k: True)
    res = svc.scan_top_level_now(settle_seconds=0.0)
    assert res["moved"] == [] and res["held"] == 1
    assert model.is_file()
    assert svc.list_held_3mf() == [model]
    sim = dict((p.name, d) for p, d in svc.simulate_top_level())
    assert sim["benchy.3mf"].match_type == "hold"

    # Slicer closed: the next automatic scan files it.
    monkeypatch.setattr(service_module, "is_slicer_running", lambda *a, **k: False)
    res = svc.scan_top_level_now(settle_seconds=0.0)
    assert len(res["moved"]) == 1 and res["held"] == 0
    assert not model.exists()


def test_p1_1_explicit_file_held_now_moves_despite_running_slicer(
    app_paths: AppPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = _service(app_paths, hold_3mf_enabled=True, hold_3mf_days=7)
    dl = app_paths.downloads_dir
    assert dl is not None
    model = _aged_3mf(dl, "benchy.3mf", age_days=10)
    monkeypatch.setattr(service_module, "is_slicer_running", lambda *a, **k: True)

    res = svc.file_held_3mf_now()
    assert len(res["moved"]) == 1
    assert not model.exists()


def test_p1_1_slicer_hold_has_a_14_day_cap(app_paths: AppPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    assert SLICER_HOLD_MAX_DAYS == 14
    svc = _service(app_paths, hold_3mf_enabled=True, hold_3mf_days=7)
    dl = app_paths.downloads_dir
    assert dl is not None
    inside_cap = _aged_3mf(dl, "inside.3mf", age_days=13.5)
    past_cap = _aged_3mf(dl, "past.3mf", age_days=14.5)
    monkeypatch.setattr(service_module, "is_slicer_running", lambda *a, **k: True)

    res = svc.scan_top_level_now(settle_seconds=0.0)
    assert [m["filename"] for m in res["moved"]] == ["past.3mf"]
    assert res["held"] == 1
    assert inside_cap.is_file() and not past_cap.exists()


def test_p1_1_slicer_is_not_consulted_when_hold_is_off_or_still_running(
    app_paths: AppPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []

    def counting(*_a, **_k) -> bool:  # noqa: ANN002, ANN003
        calls.append(1)
        return True

    monkeypatch.setattr(service_module, "is_slicer_running", counting)
    dl = app_paths.downloads_dir
    assert dl is not None
    fresh = _aged_3mf(dl, "fresh.3mf", age_days=1)
    old = _aged_3mf(dl, "old.3mf", age_days=10)

    cfg = Config.with_starter_defaults()
    cfg.hold_3mf_enabled, cfg.hold_3mf_days = True, 7
    assert SorterService._is_3mf_held(fresh, cfg) is True
    assert calls == [], "inside the normal hold window no process scan is needed"

    cfg.hold_3mf_enabled = False
    assert SorterService._is_3mf_held(old, cfg) is False
    assert calls == [], "holding switched off means a slicer cannot hold files either"

    cfg.hold_3mf_enabled = True
    assert SorterService._is_3mf_held(old, cfg) is True
    assert calls == [1]
    assert SorterService._is_3mf_held(dl / "old.pdf", cfg) is False


# ---------------------------------------------------------------------------
# P1-3: cloud placeholders and tiered hashing
# ---------------------------------------------------------------------------
@pytest.mark.skipif(sys.platform != "win32", reason="NTFS file attributes")
def test_p1_3_is_cloud_placeholder_reads_real_file_attributes(tmp_path: Path) -> None:
    normal = tmp_path / "local.bin"
    normal.write_bytes(b"on disk")
    assert fs_ops.is_cloud_placeholder(normal) is False
    assert fs_ops.is_cloud_placeholder(tmp_path / "missing.bin") is False

    offline = tmp_path / "cloud.bin"
    offline.write_bytes(b"pretend this is in the cloud")
    if not ctypes.windll.kernel32.SetFileAttributesW(str(offline), fs_ops.FILE_ATTRIBUTE_OFFLINE):
        pytest.skip("this volume does not accept FILE_ATTRIBUTE_OFFLINE")
    try:
        assert fs_ops.is_cloud_placeholder(offline) is True
    finally:
        ctypes.windll.kernel32.SetFileAttributesW(str(offline), 0x80)


@pytest.mark.parametrize(
    ("attrs", "expected"),
    [
        (0x00000020, False),  # ARCHIVE
        (0x00001000, True),   # OFFLINE
        (0x00400000, True),   # RECALL_ON_DATA_ACCESS
        (0x00400020, True),
        (0x00040000, False),  # RECALL_ON_OPEN alone: metadata only, data is local
    ],
)
def test_p1_3_is_cloud_placeholder_attribute_bits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, attrs: int, expected: bool
) -> None:
    class _Stat:
        st_file_attributes = attrs

    monkeypatch.setattr(fs_ops.os, "stat", lambda _p: _Stat())
    assert fs_ops.is_cloud_placeholder(tmp_path / "x.bin") is expected


def test_p1_3_cloud_placeholders_are_never_read_for_duplicate_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(b"same")
    b.write_bytes(b"same")

    def must_not_read(*_a, **_k):  # noqa: ANN002, ANN003
        raise AssertionError("placeholder content was read (would trigger a download)")

    monkeypatch.setattr(fs_ops, "is_cloud_placeholder", lambda p: Path(p) == b)
    monkeypatch.setattr(fs_ops, "_sha256", must_not_read)
    monkeypatch.setattr(fs_ops, "_sample_sha256", must_not_read)
    assert fs_ops._files_identical(a, b) is False
    assert fs_ops.files_are_identical(b, a) is False


def test_p1_3_placeholder_collision_gets_numbered_name_not_quarantine(
    app_paths: AppPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = _service(app_paths)
    dl = app_paths.downloads_dir
    assert dl is not None
    existing = dl / "Documents" / "PDF" / "scan.pdf"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"identical")
    (dl / "scan.pdf").write_bytes(b"identical")
    monkeypatch.setattr(fs_ops, "is_cloud_placeholder", lambda p: Path(p) == existing)

    res = svc.scan_top_level_now(settle_seconds=0.0)
    assert len(res["moved"]) == 1 and res["moved"][0]["is_duplicate"] is False
    assert (existing.parent / "scan (2).pdf").read_bytes() == b"identical"
    assert not (dl / "_Duplicates").exists()


def _count_calls(monkeypatch: pytest.MonkeyPatch, name: str) -> list[Path]:
    calls: list[Path] = []
    real = getattr(fs_ops, name)

    def wrapper(path, *args):  # noqa: ANN001, ANN002
        calls.append(Path(path))
        return real(path, *args)

    monkeypatch.setattr(fs_ops, name, wrapper)
    return calls


def test_p1_3_tiered_hashing_rejects_on_sample_without_full_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    size = 300 * 1024
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(b"A" * size)
    b.write_bytes(b"A" * (size - 10) + b"B" * 10)  # same size, different tail
    full = _count_calls(monkeypatch, "_sha256")
    sample = _count_calls(monkeypatch, "_sample_sha256")

    assert fs_ops._files_identical(a, b) is False
    assert len(sample) == 2
    assert full == [], "the head/tail sample differed, so neither file should be read in full"


def test_p1_3_tiered_hashing_falls_back_to_full_hash_when_samples_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    size = 300 * 1024
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    c = tmp_path / "c.bin"
    a.write_bytes(b"A" * size)
    middle = bytearray(b"A" * size)
    middle[size // 2] = ord("Z")  # identical head and tail, different middle
    b.write_bytes(bytes(middle))
    c.write_bytes(b"A" * size)
    full = _count_calls(monkeypatch, "_sha256")

    assert fs_ops._files_identical(a, b) is False
    assert len(full) == 2, "matching samples must not be trusted on their own"
    assert fs_ops._files_identical(a, c) is True


def test_p1_3_small_files_and_size_mismatch_skip_the_sample(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample = _count_calls(monkeypatch, "_sample_sha256")
    full = _count_calls(monkeypatch, "_sha256")
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(b"x" * (256 * 1024))  # exactly at the threshold: not "larger than"
    b.write_bytes(b"x" * (256 * 1024))
    assert fs_ops._files_identical(a, b) is True
    assert sample == [] and len(full) == 2

    full.clear()
    b.write_bytes(b"x" * 100)
    assert fs_ops._files_identical(a, b) is False
    assert sample == [] and full == []


def test_p1_3_sample_covers_head_and_tail(tmp_path: Path) -> None:
    size = 1024 * 1024
    base = bytearray(os.urandom(size))
    a = tmp_path / "a.bin"
    a.write_bytes(bytes(base))
    for position, should_differ in ((0, True), (size - 1, True), (size // 2, False)):
        changed = bytearray(base)
        changed[position] ^= 0xFF
        b = tmp_path / "b.bin"
        b.write_bytes(bytes(changed))
        differs = fs_ops._sample_sha256(a, size) != fs_ops._sample_sha256(b, size)
        assert differs is should_differ, f"byte {position}"


# ---------------------------------------------------------------------------
# P0-2: background workers
# ---------------------------------------------------------------------------
def _pump(app, condition, timeout: float = 5.0) -> bool:  # noqa: ANN001
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.005)
    return False


def test_p0_2_run_async_is_inline_under_pytest(v3_qapp) -> None:  # noqa: ANN001
    from download_sorter.ui.workers import async_enabled, run_async

    assert async_enabled() is False
    order: list[str] = []
    main_thread = threading.get_ident()

    def work() -> int:
        order.append("work" if threading.get_ident() == main_thread else "work-off-thread")
        return 41

    dispatched = run_async(work, lambda r: order.append(f"done:{r + 1}"))
    assert dispatched is False
    assert order == ["work", "done:42"]

    with pytest.raises(ValueError):  # synchronous callers see exceptions directly
        run_async(lambda: (_ for _ in ()).throw(ValueError("boom")), lambda _r: None)


def test_p0_2_run_async_offloads_and_delivers_on_gui_thread(v3_qapp) -> None:  # noqa: ANN001
    from download_sorter.ui.workers import run_async

    main_thread = threading.get_ident()
    seen: dict[str, object] = {}

    def work() -> dict[str, object]:
        return {"worker_thread": threading.get_ident(), "rows": [1, 2, 3]}

    def done(snapshot: dict[str, object]) -> None:
        seen["snapshot"] = snapshot
        seen["done_thread"] = threading.get_ident()

    assert run_async(work, done, force_async=True) is True
    assert "snapshot" not in seen, "the result must arrive later, through the event loop"
    assert _pump(v3_qapp, lambda: "snapshot" in seen)
    assert seen["snapshot"]["rows"] == [1, 2, 3]
    assert seen["snapshot"]["worker_thread"] != main_thread, "work ran on the GUI thread"
    assert seen["done_thread"] == main_thread, "widgets may only be touched on the GUI thread"


def test_p0_2_run_async_reports_errors_on_gui_thread(v3_qapp) -> None:  # noqa: ANN001
    from download_sorter.ui.workers import run_async

    main_thread = threading.get_ident()
    errors: list[tuple[str, int]] = []

    def work() -> None:
        raise OSError("disk went away")

    run_async(
        work,
        lambda _r: errors.append(("unexpected", 0)),
        lambda msg: errors.append((msg, threading.get_ident())),
        force_async=True,
    )
    assert _pump(v3_qapp, lambda: bool(errors))
    assert errors == [("OSError: disk went away", main_thread)]


def test_p0_2_result_for_a_destroyed_window_is_dropped(v3_qapp) -> None:  # noqa: ANN001
    from PySide6.QtCore import QObject
    from download_sorter.ui.workers import run_async

    owner = QObject()
    release = threading.Event()
    finished = threading.Event()
    delivered: list[int] = []

    def work() -> int:
        release.wait(5.0)
        finished.set()
        return 1

    import shiboken6

    run_async(work, delivered.append, parent=owner, force_async=True)
    shiboken6.delete(owner)  # the window is closed and destroyed while the task runs
    release.set()
    assert finished.wait(5.0)
    _pump(v3_qapp, lambda: False, timeout=0.2)
    assert delivered == []


def test_p0_2_main_window_refresh_collects_off_thread(
    v3_qapp, app_paths: AppPaths, monkeypatch: pytest.MonkeyPatch  # noqa: ANN001
) -> None:
    from download_sorter.ui.main_window import MainWindow

    svc = _service(app_paths, tutorial_completed=True)
    win = MainWindow(svc)  # built synchronously (pytest mode)
    assert win.tbl_sim.rowCount() == 0

    dl = app_paths.downloads_dir
    assert dl is not None
    (dl / "report.pdf").write_bytes(b"pdf")
    main_thread = threading.get_ident()
    collector_threads: list[int] = []
    real_simulate = svc.simulate_top_level

    def spying_simulate(*a, **k):  # noqa: ANN002, ANN003
        collector_threads.append(threading.get_ident())
        return real_simulate(*a, **k)

    monkeypatch.setattr(svc, "simulate_top_level", spying_simulate)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)  # behave like the real app

    after: list[int] = []
    win.refresh_all(then=lambda: after.append(win.tbl_sim.rowCount()))
    win.refresh_all()  # a second request while one is in flight is coalesced
    assert after == [], "refresh_all must return before the disk work is done"

    assert _pump(v3_qapp, lambda: bool(after))
    assert after == [1], "callback must run after the fresh rows are on screen"
    assert win.tbl_sim.item(0, 0).text() == "report.pdf"
    assert collector_threads and all(t != main_thread for t in collector_threads)
    assert _pump(v3_qapp, lambda: not win._refresh_running)
    win.close()


def test_p0_2_sort_now_runs_off_thread_and_reports(
    v3_qapp, app_paths: AppPaths, monkeypatch: pytest.MonkeyPatch  # noqa: ANN001
) -> None:
    from download_sorter.ui.main_window import MainWindow

    svc = _service(app_paths, tutorial_completed=True)
    win = MainWindow(svc)
    dl = app_paths.downloads_dir
    assert dl is not None
    (dl / "report.pdf").write_bytes(b"pdf")

    main_thread = threading.get_ident()
    scan_threads: list[int] = []
    real_scan = svc.scan_top_level_now

    def spying_scan(*a, **k):  # noqa: ANN002, ANN003
        scan_threads.append(threading.get_ident())
        return real_scan(*a, **k)

    monkeypatch.setattr(svc, "scan_top_level_now", spying_scan)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    win._on_sort_now()
    assert win._action_running is True
    win._on_sort_now()  # double-click: ignored, not queued
    assert _pump(v3_qapp, lambda: "moved 1 file" in win.lbl_footer.text())
    assert scan_threads and scan_threads[0] != main_thread
    assert len(scan_threads) == 1
    assert win._action_running is False
    assert win.tbl_activity.rowCount() == 1
    assert not (dl / "report.pdf").exists()
    win.close()


def test_p0_2_undo_messages_explain_locked_and_renamed_files(v3_qapp, app_paths: AppPaths) -> None:  # noqa: ANN001
    from download_sorter.ui.main_window import MainWindow

    notes = MainWindow._undo_notes(
        {
            "skipped_locked": 1,
            "locked_files": [r"C:\Downloads\Documents\budget.xlsx"],
            "restored_renamed": [{"original": "a", "restored_as": "a (Restored)"}],
            "orphaned": 2,
        }
    )
    assert "budget.xlsx" in notes and "(Restored)" in notes and "2 no longer exist" in notes
    assert MainWindow._undo_notes({"restored": 3}) == ""


# ---------------------------------------------------------------------------
# P1-2 / P0-5: installer source and release preflight
# ---------------------------------------------------------------------------
def _load_root_module(name: str):  # noqa: ANN202
    import importlib.util

    spec = importlib.util.spec_from_file_location(f"_v3_root_{name}", ROOT / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_p1_2_installer_source_waits_retries_and_renames_aside() -> None:
    builder = _load_root_module("installer_builder")
    source = builder._generate_csharp_source("3.0.0", "Author", "Credit", "https://example.invalid", "Download Sorter")

    assert "WaitForExit(3000)" in source
    assert "ExtractAttempts = 10" in source
    assert "RenameAsideFromAttempt = 5" in source
    assert "delayMs = Math.Min(delayMs * 2" in source, "retry wait must back off exponentially"
    assert '".old_" + Guid.NewGuid().ToString("N")' in source
    assert "CleanupRenamedAside(targetDir);" in source
    assert "IOException" in source and "UnauthorizedAccessException" in source
    assert "3.0.0" in source and "__APP_VERSION__" not in source
    assert source.count("{") == source.count("}")


def test_p0_5_versions_agree_and_cli_flags_are_registered() -> None:
    verify = _load_root_module("verify_release")
    errors, version = verify.check_versions()
    assert errors == [], errors
    assert version == "3.0.0"
    assert set(verify.collect_versions()) >= {
        "about.py",
        "src/download_sorter/about.py",
        "pyproject.toml",
        "CHANGELOG.md",
        "installer.iss",
    }
    assert verify.check_cli_flags() == []
    assert "--dialog" in verify.REQUIRED_CLI_FLAGS and verify.TESTS_DIR == "tests_v3"

    import download_sorter

    assert download_sorter.__version__ == version


def test_p0_5_verify_release_detects_version_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    verify = _load_root_module("verify_release")
    fake_root = tmp_path / "proj"
    (fake_root / "src" / "download_sorter").mkdir(parents=True)
    (fake_root / "about.py").write_text('__version__ = "3.0.0"\n', encoding="utf-8")
    (fake_root / "src" / "download_sorter" / "about.py").write_text('__version__ = "3.0.0"\n', encoding="utf-8")
    (fake_root / "pyproject.toml").write_text('[tool.setuptools.dynamic]\nversion = {attr = "about.__version__"}\n', encoding="utf-8")
    (fake_root / "CHANGELOG.md").write_text("## [Unreleased]\n\n## [2.8.0] - 2026-11-13\n", encoding="utf-8")
    (fake_root / "installer.iss").write_text('#define MyAppVersion "2.8.0"\n', encoding="utf-8")
    monkeypatch.setattr(verify, "ROOT", fake_root)
    monkeypatch.setattr(verify, "SRC", fake_root / "src")

    errors, _version = verify.check_versions()
    assert len(errors) == 2
    assert any("CHANGELOG.md" in e for e in errors) and any("installer.iss" in e for e in errors)
