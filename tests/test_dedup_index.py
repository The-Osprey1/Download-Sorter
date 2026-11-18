"""Unit tests for dedup_index.py (3-point sparse hashing + SQLite duplicate index)."""

from __future__ import annotations

from pathlib import Path

import dedup_index


def test_sparse_hash_detects_mid_and_tail_differences(tmp_path: Path) -> None:
    size = 300 * 1024  # > 3 * 64 KB
    base = bytearray(b"A" * size)
    f1 = tmp_path / "f1.bin"
    f2 = tmp_path / "f2.bin"
    f3 = tmp_path / "f3.bin"

    f1.write_bytes(base)

    mid_mod = bytearray(base)
    mid_mod[size // 2] = ord("B")
    f2.write_bytes(mid_mod)

    tail_mod = bytearray(base)
    tail_mod[-10] = ord("C")
    f3.write_bytes(tail_mod)

    h1 = dedup_index.compute_sparse_hash(f1)
    h2 = dedup_index.compute_sparse_hash(f2)
    h3 = dedup_index.compute_sparse_hash(f3)
    assert len({h1, h2, h3}) == 3


def test_dedup_index_finds_renamed_duplicate_and_skips_size_mismatch(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "dedup.db"
    orig = tmp_path / "original_model.stl"
    renamed_dupe = tmp_path / "download (1).stl"
    different_size = tmp_path / "other.stl"

    payload = b"solid test\n" + (b"X" * 250_000)
    orig.write_bytes(payload)
    renamed_dupe.write_bytes(payload)
    different_size.write_bytes(b"short")

    with dedup_index.DedupIndex(db_path) as idx:
        idx.record_file(orig)

        # Size mismatch should not even compute sparse or full hash
        def fail_sparse(*_a, **_kw):
            raise AssertionError("compute_sparse_hash should not run when no size match exists")

        monkeypatch.setattr(dedup_index, "compute_sparse_hash", fail_sparse)
        assert idx.find_duplicate(different_size) is None

    # Restore real sparse hash and verify renamed duplicate detection
    monkeypatch.undo()
    with dedup_index.DedupIndex(db_path) as idx:
        match = idx.find_duplicate(renamed_dupe)
        assert match is not None
        assert match.resolve() == orig.resolve()
        # Full hash should now be backfilled on the original entry
        entry = idx.get_entry(orig)
        assert entry is not None and entry.full_hash is not None


def test_dedup_index_prunes_missing_files(tmp_path: Path) -> None:
    db_path = tmp_path / "dedup.db"
    f1 = tmp_path / "gone.pdf"
    f2 = tmp_path / "present.pdf"
    f1.write_bytes(b"abc")
    f2.write_bytes(b"def")

    with dedup_index.DedupIndex(db_path) as idx:
        idx.record_file(f1)
        idx.record_file(f2)
        f1.unlink()
        removed = idx.prune_missing()
        assert removed == 1
        assert idx.get_entry(f1) is None
        assert idx.get_entry(f2) is not None
