"""The window refreshes after every sort, and each refresh lists ``_Duplicates``.
Matching a duplicate with its original reads both files in full, so the answer is
kept until either file changes; a 4 GB duplicate shouldn't mean 8 GB of reading on
every refresh."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from download_sorter import fs_ops


@pytest.fixture
def downloads_with_duplicate(tmp_path: Path) -> Path:
    root = tmp_path / "Downloads"
    (root / "Documents").mkdir(parents=True)
    (root / "_Duplicates").mkdir()
    payload = b"same bytes " * 1000
    (root / "Documents" / "report.pdf").write_bytes(payload)
    (root / "_Duplicates" / "report (1).pdf").write_bytes(payload)
    fs_ops.clear_comparison_cache()
    return root


@pytest.fixture
def full_reads(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    calls: list[Path] = []
    real = fs_ops._sha256

    def counting(path: Path) -> str:
        calls.append(Path(path))
        return real(path)

    monkeypatch.setattr(fs_ops, "_sha256", counting)
    return calls


def test_second_scan_does_not_read_the_files_again(downloads_with_duplicate: Path, full_reads: list[Path]) -> None:
    first = fs_ops.scan_duplicate_files(downloads_with_duplicate)
    reads_after_first = len(full_reads)
    assert reads_after_first > 0
    second = fs_ops.scan_duplicate_files(downloads_with_duplicate)
    assert len(full_reads) == reads_after_first
    assert first == second
    assert first[0]["existing_rel_path"] == os.path.join("Documents", "report.pdf")


def test_changing_the_original_compares_again(downloads_with_duplicate: Path, full_reads: list[Path]) -> None:
    fs_ops.scan_duplicate_files(downloads_with_duplicate)
    reads_after_first = len(full_reads)
    original = downloads_with_duplicate / "Documents" / "report.pdf"
    size = original.stat().st_size
    original.write_bytes(b"X" * size)  # same size, so only reading the bytes can tell
    stat = original.stat()
    os.utime(original, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    dup = downloads_with_duplicate / "_Duplicates" / "report (1).pdf"
    assert fs_ops.files_are_identical_cached(dup, original) is False
    assert len(full_reads) > reads_after_first


def test_cached_answer_matches_a_fresh_comparison(downloads_with_duplicate: Path) -> None:
    dup = downloads_with_duplicate / "_Duplicates" / "report (1).pdf"
    original = downloads_with_duplicate / "Documents" / "report.pdf"
    assert fs_ops.files_are_identical_cached(dup, original) is True
    assert fs_ops.files_are_identical_cached(dup, original) is True
    other = downloads_with_duplicate / "Documents" / "other.pdf"
    other.write_bytes(b"x" * dup.stat().st_size)
    assert fs_ops.files_are_identical_cached(dup, other) is False
