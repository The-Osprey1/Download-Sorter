"""Reversibly undo file moves recorded by download-sorter."""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
import uuid

from sorter import (
    HISTORY_FILE,
    LOG_DIR,
    setup_logging,
    unique_destination,
)

try:
    from ribbon_popup import dismiss_active_ribbons
except ImportError:
    dismiss_active_ribbons = None

try:
    from quick_switcher import clear_recent_file, get_recent_file
except ImportError:
    clear_recent_file = None
    get_recent_file = None


def load_history() -> list[dict]:
    if not HISTORY_FILE.exists():
        return []
    records: list[dict] = []
    with HISTORY_FILE.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def get_active_moves(records: list[dict]) -> list[dict]:
    """Return all move records that have not been undone yet."""
    undone_ids: set[str] = set()
    for rec in records:
        if rec.get("action") == "undo" and "ref_id" in rec:
            undone_ids.add(rec["ref_id"])

    active: list[dict] = []
    for rec in records:
        if rec.get("action") != "undo" and rec.get("id") not in undone_ids:
            active.append(rec)
    return active


def record_undo(ref_id: str, original_src: str, current_dest: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "id": uuid.uuid4().hex,
        "action": "undo",
        "ref_id": ref_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "restored_from": current_dest,
        "restored_to": original_src,
    }
    with HISTORY_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def undo_moves(targets: list[dict], dry_run: bool = False) -> int:
    restored_count = 0
    for rec in targets:
        dest_path = Path(rec["dest"])
        src_path = Path(rec["src"])
        rec_id = rec["id"]

        if not dest_path.exists():
            logging.warning("Cannot undo %s: file not found at %s", dest_path.name, dest_path)
            continue

        target_dir = src_path.parent
        target_file = unique_destination(target_dir, src_path.name)

        if dry_run:
            logging.info("[DRY-RUN] Would restore: %s -> %s", dest_path, target_file)
            restored_count += 1
            continue

        target_dir.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(dest_path), str(target_file))
            record_undo(rec_id, str(target_file), str(dest_path))
            logging.info("Restored: %s -> %s", dest_path.name, target_file)
            restored_count += 1
            if dismiss_active_ribbons:
                try:
                    dismiss_active_ribbons(dest_path)
                except Exception:
                    pass
            if clear_recent_file and get_recent_file:
                try:
                    rec_file = get_recent_file(max_age_sec=86400)
                    if rec_file and Path(rec_file.get("path", "")).resolve() == dest_path.resolve():
                        clear_recent_file()
                except Exception:
                    pass
        except OSError as exc:
            logging.error("Failed to restore %s: %s", dest_path, exc)

    return restored_count


def main() -> int:
    setup_logging()
    parser = argparse.ArgumentParser(description="Undo recent download-sorter moves.")
    parser.add_argument(
        "-n", "--dry-run", action="store_true", help="Preview undo operations without moving files"
    )
    parser.add_argument(
        "-c", "--count", type=int, default=None, help="Number of files to undo (default: last batch)"
    )
    parser.add_argument(
        "-b", "--batch", type=str, default=None, help="Specific batch ID to undo"
    )
    parser.add_argument(
        "-a", "--all", action="store_true", help="Undo all active recorded moves"
    )
    parser.add_argument(
        "-l", "--list", action="store_true", help="List recent active move history"
    )
    args = parser.parse_args()

    records = load_history()
    active = get_active_moves(records)

    if not active:
        print("No reversible move history found.")
        return 0

    if args.list:
        print(f"\n--- Recent Active Moves ({len(active)} total) ---")
        for rec in active[-20:]:
            print(f"[{rec.get('batch_id', 'unknown')}] {rec.get('timestamp', '')} : {Path(rec['src']).name} -> {rec['dest']}")
        print("----------------------------------------\n")
        return 0

    # Determine targets to undo
    if args.all:
        targets = list(reversed(active))
    elif args.batch:
        targets = [rec for rec in reversed(active) if rec.get("batch_id") == args.batch]
        if not targets:
            print(f"No active moves found for batch '{args.batch}'.")
            return 0
    elif args.count is not None:
        targets = list(reversed(active[-args.count:]))
    else:
        # Default: undo the most recent batch
        last_batch = active[-1].get("batch_id")
        targets = [rec for rec in reversed(active) if rec.get("batch_id") == last_batch]
        print(f"Selecting most recent batch '{last_batch}' ({len(targets)} file(s)).")

    count = undo_moves(targets, dry_run=args.dry_run)
    action_word = "Would restore" if args.dry_run else "Restored"
    print(f"{action_word} {count} file(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
