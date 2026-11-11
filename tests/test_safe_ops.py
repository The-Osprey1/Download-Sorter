"""Tests for the shared safety layer: journal ordering, crash recovery, preflight."""

from __future__ import annotations

import errno
import json
import os
import sys
import time
from pathlib import Path

import pytest

import safe_ops
import sorter
import sort_undo


def _history() -> list[dict]:
    return safe_ops.read_journal()


def _make(path: Path, text: str = "data", age: float = 10.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    old = time.time() - age
    os.utime(path, (old, old))
    return path


# ---- write-ahead journal -----------------------------------------------------

def test_move_writes_planned_then_done_in_order(tmp_path):
    src = _make(tmp_path / "in" / "part.stl")
    dest = safe_ops.move(src, tmp_path / "out" / "part.stl", batch_id="b1")
    assert dest.exists() and not src.exists()
    records = _history()
    assert [r.get("action") for r in records] == ["planned", None]
    planned, done = records
    assert planned["src"] == done["src"] and planned["dest"] == done["dest"]
    assert done["planned_id"] == planned["id"]
    assert done["batch_id"] == "b1"


def test_move_never_overwrites_existing_destination(tmp_path):
    src = _make(tmp_path / "a" / "x.pdf", "new")
    existing = _make(tmp_path / "out" / "x.pdf", "old")
    final = safe_ops.move(src, existing)
    assert final.name == "x (2).pdf"
    assert existing.read_text() == "old"
    assert final.read_text() == "new"


def test_journal_bookkeeping_is_not_counted_as_a_move(tmp_path):
    src = _make(tmp_path / "a" / "x.pdf")
    safe_ops.move(src, tmp_path / "out" / "x.pdf")
    reader = sorter.IncrementalHistoryReader(sorter.HISTORY_FILE)
    assert reader.moves_today() == 1  # not 2: the planned record is ignored
    active = sort_undo.get_active_moves(sort_undo.load_history())
    assert len(active) == 1 and active[0].get("action") is None


def test_failed_disk_operation_marks_journal_aborted(tmp_path, monkeypatch):
    src = _make(tmp_path / "a" / "x.pdf")

    def boom(*_a, **_k):
        raise PermissionError("denied")

    monkeypatch.setattr(safe_ops, "_move_without_overwriting", boom)
    with pytest.raises(PermissionError):
        safe_ops.move(src, tmp_path / "out" / "x.pdf")
    assert [r.get("action") for r in _history()] == ["planned", "aborted"]
    assert safe_ops.recover_pending_journal(min_age_seconds=0) == []


# ---- crash recovery ---------------------------------------------------------------

def test_recovery_crash_before_move_marks_aborted(tmp_path):
    src = _make(tmp_path / "a" / "x.pdf")
    dest = tmp_path / "out" / "x.pdf"
    safe_ops._plan("move", src, dest, "b")  # process dies right here
    outcomes = safe_ops.recover_pending_journal(min_age_seconds=0)
    assert [o["outcome"] for o in outcomes] == ["aborted"]
    assert src.exists()
    assert safe_ops.recover_pending_journal(min_age_seconds=0) == []  # idempotent


def test_recovery_crash_after_move_completes_record_and_stays_undoable(tmp_path):
    src = _make(tmp_path / "a" / "x.pdf")
    dest = tmp_path / "out" / "x.pdf"
    safe_ops._plan("move", src, dest, "b")
    dest.parent.mkdir(parents=True)
    src.replace(dest)  # the move happened, the 'done' write did not
    outcomes = safe_ops.recover_pending_journal(min_age_seconds=0)
    assert [o["outcome"] for o in outcomes] == ["completed"]
    active = sort_undo.get_active_moves(sort_undo.load_history())
    assert len(active) == 1
    assert sort_undo.undo_moves(active) == 1
    assert src.exists()


def test_recovery_both_present_is_flagged_never_deleted(tmp_path):
    src = _make(tmp_path / "a" / "x.pdf")
    dest = _make(tmp_path / "out" / "x.pdf")
    safe_ops._plan("move", src, dest, "b")
    outcomes = safe_ops.recover_pending_journal(min_age_seconds=0)
    assert [o["outcome"] for o in outcomes] == ["conflict"]
    assert src.exists() and dest.exists()


def test_recovery_ignores_very_recent_plans_from_other_processes(tmp_path):
    src = _make(tmp_path / "a" / "x.pdf")
    safe_ops._plan("move", src, tmp_path / "out" / "x.pdf", "b")
    assert safe_ops.recover_pending_journal(min_age_seconds=60) == []


# ---- pre-flight ------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["~$report.docx", "model.3mf.tmp", "x.tmp", "x.part", "x.crdownload", ".~lock.a.odt#"])
def test_in_progress_save_names_are_refused(tmp_path, name):
    src = _make(tmp_path / name)
    with pytest.raises(safe_ops.Refused) as err:
        safe_ops.move(src, tmp_path / "out" / name, settle_seconds=0)
    assert err.value.reason == "temp-save"
    assert src.exists()
    assert _history() == []  # refused before anything was planned


def test_unsettled_file_is_refused(tmp_path):
    src = _make(tmp_path / "fresh.pdf", age=0.0)
    with pytest.raises(safe_ops.Refused) as err:
        safe_ops.move(src, tmp_path / "out" / "fresh.pdf", settle_seconds=30)
    assert err.value.reason == "not-settled"


def test_default_settle_is_three_seconds(tmp_path):
    assert safe_ops.DEFAULT_SETTLE_SECONDS == 3.0
    src = _make(tmp_path / "two.pdf", age=2.0)
    with pytest.raises(safe_ops.Refused):
        safe_ops.move(src, tmp_path / "out" / "two.pdf")
    src2 = _make(tmp_path / "four.pdf", age=4.0)
    assert safe_ops.move(src2, tmp_path / "out" / "four.pdf").exists()


def test_keep_marker_protects_everything_below_it(tmp_path):
    folder = tmp_path / "Project"
    (folder / "sub").mkdir(parents=True)
    (folder / safe_ops.KEEP_MARKER).write_text("")
    src = _make(folder / "sub" / "a.step")
    with pytest.raises(safe_ops.Refused) as err:
        safe_ops.move(src, tmp_path / "out" / "a.step", settle_seconds=0)
    assert err.value.reason == "keep-marker"


@pytest.mark.skipif(sys.platform != "win32", reason="exclusive-open check is Win32")
def test_file_held_open_by_another_process_is_refused(tmp_path):
    src = _make(tmp_path / "busy.pdf")
    with open(src, "r+b"):
        with pytest.raises(safe_ops.Refused) as err:
            safe_ops.move(src, tmp_path / "out" / "busy.pdf", settle_seconds=0)
    assert err.value.reason == "locked"


# ---- link / quarantine / restore -----------------------------------------------------------

@pytest.mark.ntfs
def test_link_creates_hardlink_and_is_not_an_undoable_move(tmp_path):
    src = _make(tmp_path / "a" / "m.3mf", "payload")
    link = safe_ops.link(src, tmp_path / "b" / "m.3mf", batch_id="b")
    assert os.stat(src).st_ino == os.stat(link).st_ino
    assert [r.get("action") for r in _history()] == ["planned", "link"]
    assert sort_undo.get_active_moves(sort_undo.load_history()) == []


def test_link_refuses_to_overwrite(tmp_path):
    src = _make(tmp_path / "a" / "m.3mf", "payload")
    other = _make(tmp_path / "b" / "m.3mf", "other")
    link = safe_ops.link(src, other)
    assert link.name == "m (2).3mf"
    assert other.read_text() == "other"


def test_quarantine_preserves_relative_path(tmp_path):
    root = tmp_path / "Docs"
    src = _make(root / "old" / "deep" / "dup.pdf")
    q = tmp_path / "Docs" / "_Remodel Quarantine" / "remodel_1"
    final = safe_ops.quarantine(src, q, rel_root=root)
    assert final == q / "old" / "deep" / "dup.pdf"
    assert final.exists() and not src.exists()


def test_restore_never_overwrites_a_reoccupied_original(tmp_path):
    src = _make(tmp_path / "a" / "x.pdf", "first")
    dest = safe_ops.move(src, tmp_path / "out" / "x.pdf")
    _make(src, "someone else wrote here meanwhile")
    entry = [r for r in _history() if r.get("action") is None][0]
    restored = safe_ops.restore(entry)
    assert restored.name == "x (2).pdf"
    assert src.read_text() == "someone else wrote here meanwhile"
    assert restored.read_text() == "first"
    assert not dest.exists()


# ---- a name that is taken while a move is under way ------------------------------------
#
# The destination name is free when it is chosen, but another program (or a second
# Download Sorter) takes it before the move happens. Nothing may be overwritten.
# These tests use finished_downloads so that antivirus holding a fresh file open
# can't make the move fail as "locked" instead.

def test_move_never_overwrites_a_file_that_appears_after_the_name_was_chosen(tmp_path, monkeypatch, finished_downloads):
    src = _make(tmp_path / "in" / "report.pdf", "new download")
    final = tmp_path / "out" / "report.pdf"
    real_resolve = safe_ops._resolve_dest

    def resolve_then_lose_the_name(dst):
        chosen = real_resolve(dst)
        chosen.write_text("someone else's file", encoding="utf-8")
        return chosen

    monkeypatch.setattr(safe_ops, "_resolve_dest", resolve_then_lose_the_name)
    with pytest.raises(safe_ops.Refused) as err:
        safe_ops.move(src, final, settle_seconds=0)
    assert err.value.reason == "destination-exists"
    assert final.read_text(encoding="utf-8") == "someone else's file"
    assert src.read_text(encoding="utf-8") == "new download"
    assert [r.get("action") for r in _history()] == ["planned", "aborted"]
    assert safe_ops.recover_pending_journal(min_age_seconds=0) == []


def _rename_fails_like_another_drive(monkeypatch, name):
    """Make os.rename fail the way a move to another drive does, for the file called ``name``."""
    real_rename = os.rename

    def rename(src, dst, *args, **kwargs):
        if Path(src).name == name and Path(dst).name == name:
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_rename(src, dst, *args, **kwargs)

    monkeypatch.setattr(safe_ops.os, "rename", rename)


def test_move_to_another_drive_copies_then_renames_and_leaves_no_partial_file(tmp_path, monkeypatch, finished_downloads):
    src = _make(tmp_path / "in" / "big.stl", "payload")
    final = tmp_path / "out" / "big.stl"
    _rename_fails_like_another_drive(monkeypatch, "big.stl")
    assert safe_ops.move(src, final, settle_seconds=0) == final
    assert final.read_text(encoding="utf-8") == "payload"
    assert not src.exists()
    assert [p.name for p in final.parent.iterdir()] == ["big.stl"]
    assert [r.get("action") for r in _history()] == ["planned", None]


def test_move_to_another_drive_never_overwrites_a_name_taken_during_the_copy(tmp_path, monkeypatch, finished_downloads):
    src = _make(tmp_path / "in" / "big.stl", "new download")
    final = tmp_path / "out" / "big.stl"
    _rename_fails_like_another_drive(monkeypatch, "big.stl")
    real_copy = safe_ops.shutil.copy2

    def copy_then_lose_the_name(source, target, **kwargs):
        result = real_copy(source, target, **kwargs)
        final.write_text("someone else's file", encoding="utf-8")
        return result

    monkeypatch.setattr(safe_ops.shutil, "copy2", copy_then_lose_the_name)
    with pytest.raises(safe_ops.Refused) as err:
        safe_ops.move(src, final, settle_seconds=0)
    assert err.value.reason == "destination-exists"
    assert final.read_text(encoding="utf-8") == "someone else's file"
    assert src.read_text(encoding="utf-8") == "new download"
    assert [p.name for p in final.parent.iterdir()] == ["big.stl"]  # the partial copy is gone
    assert [r.get("action") for r in _history()] == ["planned", "aborted"]


def test_restore_never_overwrites_a_name_taken_after_it_was_chosen(tmp_path, monkeypatch, finished_downloads):
    src = _make(tmp_path / "a" / "x.pdf", "first")
    dest = safe_ops.move(src, tmp_path / "out" / "x.pdf")
    entry = [r for r in _history() if r.get("action") is None][0]
    real_unique = sorter.unique_destination

    def unique_then_lose_the_name(directory, name):
        chosen = real_unique(directory, name)
        chosen.write_text("someone else's file", encoding="utf-8")
        return chosen

    monkeypatch.setattr(sorter, "unique_destination", unique_then_lose_the_name)
    with pytest.raises(safe_ops.Refused) as err:
        safe_ops.restore(entry)
    assert err.value.reason == "destination-exists"
    assert src.read_text(encoding="utf-8") == "someone else's file"
    assert dest.read_text(encoding="utf-8") == "first"  # still where it was sorted
    assert [r.get("action") for r in _history()][-2:] == ["planned", "aborted"]


# ---- sorter integration --------------------------------------------------------------------------

def _config(tmp_path, settle):
    cfg = sorter.get_default_config()
    cfg["watch"]["folder"] = str(tmp_path / "Downloads")
    cfg["watch"]["settle_seconds"] = settle
    cfg["document_sources"] = []
    return cfg


def test_move_file_goes_through_journal(tmp_path):
    cfg = _config(tmp_path, 0)
    downloads = tmp_path / "Downloads"
    src = _make(downloads / "bracket.step")
    dest = sorter.move_file(src, cfg, batch_id="bx")
    assert dest is not None and dest.exists()
    assert [r.get("action") for r in _history()] == ["planned", None]


def test_move_file_honors_configured_settle(tmp_path):
    cfg = _config(tmp_path, 60)
    src = _make(tmp_path / "Downloads" / "new.step", age=1.0)
    assert sorter.move_file(src, cfg) is None
    assert src.exists()
