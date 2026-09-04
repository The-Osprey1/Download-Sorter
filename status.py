"""Display system status, background service health, and sorting statistics."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from sorter import (
    FAILURE_FILE,
    HISTORY_FILE,
    RUNTIME_STATE_FILE,
    is_paused,
    load_config,
    watch_root,
)


def format_local_time(value: str | None) -> str:
    if not value:
        return "unknown"
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %I:%M %p")
    except (TypeError, ValueError):
        return str(value)


def get_runtime_health() -> tuple[str, float | None, str]:
    try:
        data = json.loads(RUNTIME_STATE_FILE.read_text(encoding="utf-8"))
        updated = datetime.fromisoformat(data.get("updated_at", ""))
        age = max(0.0, (datetime.now(timezone.utc) - updated.astimezone(timezone.utc)).total_seconds())
        return str(data.get("status", "unknown")), age, str(data.get("detail", ""))
    except (OSError, json.JSONDecodeError, TypeError, ValueError, AttributeError):
        return "unknown", None, "no runtime record"


def get_failure_summary() -> tuple[int, str | None, str | None]:
    try:
        data = json.loads(FAILURE_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return 0, None, None
        entries = [value for value in data.values() if isinstance(value, dict)]
        if not entries:
            return 0, None, None
        latest = max(entries, key=lambda item: str(item.get("last_seen", "")))
        return len(entries), str(latest.get("path", "")) or None, str(latest.get("last_seen", "")) or None
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return 0, None, None


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
    runtime_status, heartbeat_age, runtime_detail = get_runtime_health()
    failure_count, latest_failure, latest_failure_time = get_failure_summary()

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
    if heartbeat_age is None:
        print(f"  Runtime Health:      {runtime_status.upper()} ({runtime_detail})")
    else:
        print(f"  Runtime Health:      {runtime_status.upper()} (heartbeat {round(heartbeat_age)}s ago)")

    # Watcher Settings
    min_age = config.get("watch", {}).get("min_age_minutes", 0)
    auto_3d = config.get("watch", {}).get("auto_unpack_3d_zips", True)
    auto_unzip = config.get("watch", {}).get("auto_unzip", config.get("auto_unzip", True))
    extracted_dest = config.get("extracted_destination", "Extracted")
    print(f"  Grace Period:        {min_age} minute(s)")
    print(f"  3D Zip Auto-Unpack:  {'Enabled' if auto_3d else 'Disabled'}")
    print(f"  Auto-Unzipper:       {'Enabled (' + extracted_dest + ')' if auto_unzip else 'Disabled'}")

    # History
    print("-" * 55)
    print(f"  All-Time Moves:      {total_moves} file(s) recorded in history")
    if last_batch:
        clean_time = format_local_time(last_time)
        print(f"  Last Batch:          {last_batch} ({clean_time})")
    if failure_count:
        print(f"  Move Failures:       {failure_count} tracked; latest {format_local_time(latest_failure_time)}")
        print(f"                       {latest_failure}")
    else:
        print("  Move Failures:       None")

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
