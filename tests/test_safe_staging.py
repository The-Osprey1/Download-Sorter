"""Unit tests for safe_ops.py crash-safe .staging/<batch_id>/, PENDING->COMMITTED, and log rotation."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

import safe_ops
import sorter


@pytest.fixture()
def isolated_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    hist = tmp_path / "state" / "history.jsonl"
    monkeypatch.setattr(sorter, "HISTORY_FILE", hist)
    return hist


def test_staged_move_writes_pending_then_committed_and_cleans_staging(
    tmp_path: Path, isolated_history: Path
) -> None:
    src = tmp_path / "downloads" / "bracket.step"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"STEP-DATA")
    dest = tmp_path / "sorted" / "3D Models" / "bracket.step"

    final = safe_ops.staged_move(
        src,
        dest,
        batch_id="batch42",
        staging_root=tmp_path / "sorted",
        settle_seconds=0.0,
    )
    assert final == dest
    assert final.exists() and final.read_bytes() == b"STEP-DATA"
    assert not src.exists()

    # Empty batch staging dir should be cleaned up
    assert not (tmp_path / "sorted" / ".staging" / "batch42").exists()

    records = safe_ops.read_journal()
    assert len(records) == 2
    assert records[0]["action"] == "planned"
    assert records[0]["phase"] == "PENDING"
    assert records[1]["phase"] == "COMMITTED"
    assert records[1]["planned_id"] == records[0]["id"]


def test_cleanup_orphaned_staging(tmp_path: Path) -> None:
    old_batch = safe_ops.staging_dir_for(tmp_path, "old_batch")
    (old_batch / "partial.tmp").write_bytes(b"123")
    old_ts = time.time() - 600.0
    os.utime(old_batch, (old_ts, old_ts))

    fresh_batch = safe_ops.staging_dir_for(tmp_path, "fresh_batch")
    (fresh_batch / "active.tmp").write_bytes(b"456")

    removed = safe_ops.cleanup_orphaned_staging(tmp_path, max_age_seconds=300.0)
    assert old_batch in removed
    assert not old_batch.exists()
    assert fresh_batch.exists()


def test_rotate_journal_preserves_unresolved_pending(tmp_path: Path, isolated_history: Path) -> None:
    # Write several committed entries and one unresolved PENDING entry
    for i in range(10):
        op_id = safe_ops._plan("move", tmp_path / f"a{i}.txt", tmp_path / f"b{i}.txt", "b1")
        safe_ops.append_journal(
            safe_ops._done_record(op_id, "b1", str(tmp_path / f"a{i}.txt"), str(tmp_path / f"b{i}.txt"), "move", True),
            auto_rotate=False,
        )

    unresolved_id = safe_ops._plan("move", tmp_path / "inflight.txt", tmp_path / "dest.txt", "b2")
    rotated = safe_ops.rotate_journal(max_bytes=100, keep_files=2)
    assert rotated is not None and rotated.exists()

    # Fresh history.jsonl should retain only the unresolved PENDING record
    current_records = safe_ops.read_journal()
    assert len(current_records) == 1
    assert current_records[0]["id"] == unresolved_id
    assert current_records[0]["phase"] == "PENDING"
