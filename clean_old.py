"""Safely clean old installers and archives by moving them to the Windows Recycle Bin."""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import logging
import os
import sys
import time
from pathlib import Path

from sorter import folder_path, load_config, setup_logging, watch_root


class SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", wintypes.WORD),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", wintypes.LPVOID),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


FO_DELETE = 3
FOF_ALLOWUNDO = 0x0040        # Move to Recycle Bin
FOF_NOCONFIRMATION = 0x0010   # Silent, no prompt
FOF_SILENT = 0x0004           # No progress dialog
FOF_NOERRORUI = 0x0400


def send_to_recycle_bin(path: Path) -> bool:
    """Send path to Windows Recycle Bin. Returns True on success."""
    try:
        resolved = str(path.resolve())
        file_op = SHFILEOPSTRUCTW()
        file_op.hwnd = None
        file_op.wFunc = FO_DELETE
        # Must be double-null terminated
        file_op.pFrom = f"{resolved}\0"
        file_op.pTo = None
        file_op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
        result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(file_op))
        return result == 0 and not file_op.fAnyOperationsAborted
    except Exception as exc:
        logging.error("Recycle bin error for %s: %s", path.name, exc)
        return False


def find_old_files(target_dir: Path, min_age_days: float) -> list[tuple[Path, float, int]]:
    """Return list of (file_path, age_days, size_bytes) for files older than min_age_days."""
    if not target_dir.exists():
        return []
    cutoff_seconds = min_age_days * 86400
    now = time.time()
    candidates: list[tuple[Path, float, int]] = []

    for item in target_dir.iterdir():
        if item.is_file():
            try:
                stat = item.stat()
                age = now - stat.st_mtime
                if age >= cutoff_seconds:
                    candidates.append((item, round(age / 86400, 1), stat.st_size))
            except OSError:
                continue
    return candidates


def format_size(bytes_val: int) -> str:
    if bytes_val < 1024 * 1024:
        return f"{round(bytes_val / 1024, 1)} KB"
    elif bytes_val < 1024 * 1024 * 1024:
        return f"{round(bytes_val / (1024 * 1024), 1)} MB"
    else:
        return f"{round(bytes_val / (1024 * 1024 * 1024), 2)} GB"


def main() -> int:
    setup_logging()
    parser = argparse.ArgumentParser(description="Safely move old download files to the Windows Recycle Bin.")
    parser.add_argument("-d", "--days", type=float, default=30.0, help="Minimum file age in days (default: 30)")
    parser.add_argument("-n", "--dry-run", action="store_true", help="Preview files to be recycled without touching them")
    parser.add_argument("-a", "--all", action="store_true", help="Include 'Other' folder in addition to Installers & Archives")
    parser.add_argument("--folder", type=str, action="append", help="Custom folder name to scan inside Downloads")
    args = parser.parse_args()

    config = load_config()
    downloads = watch_root(config)

    scan_folders = args.folder or ["Installers", "Archives"]
    if args.all and "Other" not in scan_folders:
        scan_folders.append("Other")

    all_candidates: list[tuple[Path, float, int]] = []
    for folder_name in scan_folders:
        target_dir = folder_path(downloads, folder_name)
        candidates = find_old_files(target_dir, args.days)
        all_candidates.extend(candidates)

    if not all_candidates:
        print(f"No files older than {args.days} days found in: {', '.join(scan_folders)}.")
        return 0

    total_bytes = sum(size for _, _, size in all_candidates)

    print(f"\n--- Found {len(all_candidates)} file(s) older than {args.days} days ({format_size(total_bytes)} reclaimable) ---")
    for path, age, size in all_candidates:
        print(f"  [{age} days old | {format_size(size)}] {path.parent.name}\\{path.name}")
    print("--------------------------------------------------------------------------------\n")

    if args.dry_run:
        print(f"[DRY-RUN] Would send {len(all_candidates)} file(s) ({format_size(total_bytes)}) to the Windows Recycle Bin.")
        return 0

    recycled_count = 0
    recycled_bytes = 0
    for path, _, size in all_candidates:
        if send_to_recycle_bin(path):
            recycled_count += 1
            recycled_bytes += size
            logging.info("Recycled: %s (%s)", path.name, format_size(size))
        else:
            logging.warning("Failed to recycle %s", path.name)

    print(f"Moved {recycled_count} file(s) ({format_size(recycled_bytes)}) to the Windows Recycle Bin.")
    print("Tip: Files are safely in your Recycle Bin and can be restored at any time.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
