"""How much room the sorter's own leftovers take up, and clearing them safely (#25).

Covers the three places that quietly keep files: `_Duplicates` (exact copies set aside),
`_Remodel Quarantine` (batches from Clean Up, kept for 30 days so Undo can bring them back), and
old installers and archives. Clearing always goes to the Recycle Bin, never straight to deletion.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

CATEGORIES = ("duplicates", "quarantine", "old_downloads")


def _size_and_count(folder: Path) -> tuple[int, int]:
    total = count = 0
    if not folder.is_dir():
        return 0, 0
    for path in folder.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
                count += 1
        except OSError:
            continue
    return total, count


def _old_download_candidates(config: dict, downloads: Path) -> list[tuple[Path, float, int]]:
    """Return old files matching daily clean-up's resolved folders, age, and extension rules."""
    import clean_old
    import sorter

    cleanup = config.get("cleanup") if isinstance(config.get("cleanup"), dict) else {}
    days = float(cleanup.get("days", 30) or 30)
    old_files: list[tuple[Path, float, int]] = []
    for folder in clean_old.resolve_cleanup_folders(config):
        allowed_exts = clean_old.get_folder_cleanup_extensions(folder, config)
        old_files.extend(
            clean_old.find_old_files(sorter.folder_path(downloads, folder), days, allowed_extensions=allowed_exts)
        )
    return old_files


def report(config: dict, now: float | None = None) -> dict:
    """{"duplicates": {...}, "quarantine": {...}, "old_downloads": {...}} with bytes and files."""
    import sorter

    downloads = sorter.watch_root(config)
    now = time.time() if now is None else now
    dup_bytes, dup_count = _size_and_count(downloads / sorter.DUPLICATES_DIR_NAME)
    q_bytes, q_count = _size_and_count(downloads / "_Remodel Quarantine")
    old_files = _old_download_candidates(config, downloads)
    return {
        "duplicates": {"files": dup_count, "bytes": dup_bytes},
        "quarantine": {"files": q_count, "bytes": q_bytes},
        "old_downloads": {"files": len(old_files), "bytes": sum(size for _p, _a, size in old_files)},
    }


def batches_in_quarantine(config: dict) -> list[Path]:
    """Each Clean Up batch folder, so one can be restored with Undo or sent to the Recycle Bin."""
    import sorter
    base = sorter.watch_root(config) / "_Remodel Quarantine"
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir() if p.is_dir())


def send_category_to_recycle_bin(config: dict, category: str) -> int:
    """Send one category's contents to the Recycle Bin. Returns how many items were sent.

    Old downloads are only the files the daily clean-up would already send (older than the
    cleanup age in Settings), never everything in those folders.
    """
    import clean_old
    import sorter

    if category not in CATEGORIES:
        raise ValueError(f"unknown category {category!r}")
    downloads = sorter.watch_root(config)
    if category == "duplicates":
        dup_dir = downloads / sorter.DUPLICATES_DIR_NAME
        targets = list(dup_dir.iterdir()) if dup_dir.is_dir() else []
    elif category == "quarantine":
        targets = batches_in_quarantine(config)
    else:
        targets = [p for p, _a, _s in _old_download_candidates(config, downloads)]
    sent = 0
    for target in targets:
        if clean_old.send_to_recycle_bin(target):
            sent += 1
        else:
            logging.warning("Could not send %s to the Recycle Bin", target)
    return sent


def format_line(label: str, row: dict) -> str:
    import clean_old
    return f"{label}: {row['files']} file(s), {clean_old.format_size(row['bytes'])}"
