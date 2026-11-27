"""Unit tests for v3.5.0 sparse-hash dedup, .staging/<batch_id>/, Zip Slip, and cloud placeholder guards."""

from __future__ import annotations

import os
import stat
import time
import zipfile
from pathlib import Path

import pytest

from download_sorter import fs_ops
from download_sorter.db import StateDB
from download_sorter.paths import AppPaths
from download_sorter.router import RouteDecision


def test_3point_sparse_hash_detects_mid_and_tail_differences(
    app_paths: AppPaths, create_test_file
) -> None:
    size = 300 * 1024  # > 3 * 64 KB
    base = bytearray(b"A" * size)
    f1 = create_test_file("f1.bin", bytes(base))

    mid_mod = bytearray(base)
    mid_mod[size // 2] = ord("B")
    f2 = create_test_file("f2.bin", bytes(mid_mod))

    tail_mod = bytearray(base)
    tail_mod[-10] = ord("C")
    f3 = create_test_file("f3.bin", bytes(tail_mod))

    h1 = fs_ops.compute_sparse_hash(f1)
    h2 = fs_ops.compute_sparse_hash(f2)
    h3 = fs_ops.compute_sparse_hash(f3)
    assert len({h1, h2, h3}) == 3


def test_renamed_duplicate_quarantined_via_state_db_hash_index(
    app_paths: AppPaths, create_test_file
) -> None:
    db = StateDB(app_paths.db_file)
    payload = b"solid bracket\n" + (b"X" * 250_000)

    first = create_test_file("bracket_v1.stl", payload)
    dec = RouteDecision(
        action="move",
        relative_dest=Path("3D Models") / "STL" / "bracket_v1.stl",
        reason="STL rule",
        matched_rule="3D STL",
        match_type="rule",
    )
    rec1 = fs_ops.execute_move(
        first, dec, batch_id="b1", db=db, watch_root=app_paths.downloads_dir, settle_seconds=0.0
    )
    assert rec1 is not None and not rec1["is_duplicate"]
    assert Path(rec1["dest_path"]).is_file()

    # Renamed duplicate arrives later -> quarantined to Duplicates/<YYYY-MM>
    second = create_test_file("totally_renamed_copy.stl", payload)
    dec2 = RouteDecision(
        action="move",
        relative_dest=Path("3D Models") / "STL" / "totally_renamed_copy.stl",
        reason="STL rule",
        matched_rule="3D STL",
        match_type="rule",
    )
    rec2 = fs_ops.execute_move(
        second, dec2, batch_id="b2", db=db, watch_root=app_paths.downloads_dir, settle_seconds=0.0
    )
    assert rec2 is not None and rec2["is_duplicate"] is True
    assert "Duplicates" in rec2["dest_path"]
    assert Path(rec2["dest_path"]).is_file()
    assert rec2["existing_path"] == str(Path(rec1["dest_path"]).resolve())


def test_cloud_placeholder_checked_before_open(
    app_paths: AppPaths, create_test_file, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = StateDB(app_paths.db_file)
    zpath = create_test_file("cloud_archive.zip", b"not-even-read")

    monkeypatch.setattr(fs_ops, "is_cloud_placeholder", lambda _p: True)

    def fail_open(*_a, **_kw):
        raise AssertionError("open() must not be called on cloud placeholders")

    monkeypatch.setattr("builtins.open", fail_open)

    assert fs_ops.find_renamed_duplicate(zpath, db=db, watch_root=app_paths.downloads_dir) is None
    res = fs_ops.extract_zip_safely(
        zpath, app_paths.downloads_dir / "Extracted", app_paths.downloads_dir
    )
    assert res["ok"] is False
    assert "placeholder" in res["error"].lower()


@pytest.mark.parametrize("bad_entry", ["../evil.txt", "..\\evil.txt"])
def test_extract_zip_safely_blocks_zip_slip_and_cleans_staging(
    app_paths: AppPaths, bad_entry: str
) -> None:
    zpath = app_paths.downloads_dir / "bad.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr(bad_entry, "pwned")

    target_dir = app_paths.downloads_dir / "Extracted"
    res = fs_ops.extract_zip_safely(
        zpath, target_dir, app_paths.downloads_dir, batch_id="zslip1"
    )
    assert res["ok"] is False
    assert res["zip_bomb"] is True
    assert not (target_dir / "bad").exists()
    assert not (target_dir / ".staging" / "zslip1").exists()


def test_extract_zip_safely_blocks_symlinks(app_paths: AppPaths) -> None:
    zpath = app_paths.downloads_dir / "symlink.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        info = zipfile.ZipInfo("link.txt")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, "/etc/passwd")

    target_dir = app_paths.downloads_dir / "Extracted"
    res = fs_ops.extract_zip_safely(
        zpath, target_dir, app_paths.downloads_dir, batch_id="zsym1"
    )
    assert res["ok"] is False
    assert res["zip_bomb"] is True


def test_service_start_cleans_orphaned_staging(
    app_paths: AppPaths, service_instance
) -> None:
    old_stage = fs_ops.staging_dir_for(app_paths.downloads_dir, "abandoned_batch")
    (old_stage / "partial.tmp").write_bytes(b"123")
    old_ts = time.time() - 600.0
    os.utime(old_stage, (old_ts, old_ts))

    fresh_stage = fs_ops.staging_dir_for(app_paths.downloads_dir, "active_batch")
    (fresh_stage / "active.tmp").write_bytes(b"456")

    service_instance.start()
    service_instance.stop(timeout=2.0)

    assert not old_stage.exists()
    assert fresh_stage.exists()
