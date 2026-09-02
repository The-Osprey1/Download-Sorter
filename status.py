"""Display system status, background service health, and sorting statistics."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from sorter import (
    HISTORY_FILE,
    is_paused,
    load_config,
    watch_root,
)


def get_watcher_process() -> tuple[int | None, float | None]:
    """Return (pid, memory_mb) of the active background watcher if running."""
    try:
        import subprocess
        ps_script = "Get-Process pythonw -ErrorAction SilentlyContinue | Where-Object { $_.Path -match 'download-sorter' } | Select-Object -Last 1 Id, WorkingSet64 | ConvertTo-Json"
        res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_script], capture_output=True, text=True)
        out = res.stdout.strip()
        if out:
            data = json.loads(out)
            pid = data.get("Id")
            mem_mb = round(data.get("WorkingSet64", 0) / (1024 * 1024), 1)
            return pid, mem_mb
    except Exception:
        pass
    return None, None


def count_category_files(root: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    if not root.exists():
        return counts
    for item in root.iterdir():
        if item.is_dir():
            file_count = sum(1 for p in item.rglob("*") if p.is_file())
            if file_count > 0:
                counts[item.name] = file_count
    return counts


def get_history_summary() -> tuple[int, str | None, str | None]:
    if not HISTORY_FILE.exists():
        return 0, None, None
    total_moves = 0
    last_batch = None
    last_time = None
    try:
        with HISTORY_FILE.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if rec.get("action") != "undo":
                    total_moves += 1
                    last_batch = rec.get("batch_id")
                    last_time = rec.get("timestamp")
    except Exception:
        pass
    return total_moves, last_batch, last_time


def check_context_menu() -> bool:
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\Directory\shell\DownloadSorter")
        winreg.CloseKey(key)
        return True
    except Exception:
        return False


def get_reclaimable_space(root: Path, days: float = 30.0) -> tuple[int, int]:
    from clean_old import find_old_files, folder_path
    candidates = []
    for folder in ["Installers", "Archives"]:
        d = folder_path(root, folder)
        candidates.extend(find_old_files(d, days))
    total_bytes = sum(size for _, _, size in candidates)
    return len(candidates), total_bytes


def format_size(bytes_val: int) -> str:
    if bytes_val < 1024 * 1024:
        return f"{round(bytes_val / 1024, 1)} KB"
    elif bytes_val < 1024 * 1024 * 1024:
        return f"{round(bytes_val / (1024 * 1024), 1)} MB"
    else:
        return f"{round(bytes_val / (1024 * 1024 * 1024), 2)} GB"


def main() -> int:
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    config = load_config()
    downloads = watch_root(config)

    pid, mem_mb = get_watcher_process()
    paused = is_paused()
    total_moves, last_batch, last_time = get_history_summary()
    menu_installed = check_context_menu()
    reclaim_count, reclaim_bytes = get_reclaimable_space(downloads, 30.0)
    cat_counts = count_category_files(downloads)

    print("\n" + "=" * 55)
    print("           DOWNLOAD-SORTER SYSTEM DASHBOARD")
    print("=" * 55)

    # Watcher Service
    if pid:
        status_str = f"RUNNING (PID: {pid}, RAM: {mem_mb} MB)"
    else:
        status_str = "STOPPED (Task installed)"
    print(f"  Background Service:  {status_str}")
    print(f"  Active Mode:         {'PAUSED' if paused else 'ACTIVE (Organizing)'}")
    print(f"  Explorer Menus:      {'Installed (HKCU)' if menu_installed else 'Not installed'}")

    # Watcher Settings
    min_age = config.get("watch", {}).get("min_age_minutes", 0)
    auto_3d = config.get("watch", {}).get("auto_unpack_3d_zips", True)
    print(f"  Grace Period:        {min_age} minute(s)")
    print(f"  3D Zip Auto-Unpack:  {'Enabled' if auto_3d else 'Disabled'}")

    # History
    print("-" * 55)
    print(f"  All-Time Moves:      {total_moves} file(s) recorded in history")
    if last_batch:
        clean_time = last_time.split("T")[0] if last_time else ""
        print(f"  Last Batch:          {last_batch} ({clean_time})")

    # Categories
    print("-" * 55)
    print("  Organized Folders in Downloads:")
    if cat_counts:
        for cat, count in sorted(cat_counts.items(), key=lambda x: -x[1]):
            print(f"    • {cat:<20} {count:>4} file(s)")
    else:
        print("    (No categorized subfolders yet)")

    # Janitor
    print("-" * 55)
    if reclaim_count > 0:
        print(f"  Janitor Notice:      {reclaim_count} old file(s) ({format_size(reclaim_bytes)}) ready for Recycle Bin")
        print("                       Run: python clean_old.py --dry-run")
    else:
        print("  Janitor Notice:      All installers/archives are clean (<30 days)")

    print("=" * 55 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
