"""Put files back that were sorted out of Documents before undo history existed.

Early versions of the sorter moved files without writing a history record, so
`sort_undo.py` cannot see them. This script reconstructs those moves from
sorter.log, works out where each file came from, and moves it back.

    python restore_from_log.py              # show the plan, change nothing
    python restore_from_log.py --apply      # carry the plan out
    python restore_from_log.py --only .prproj --only .cfa
"""

from __future__ import annotations

import argparse
import logging
import re
import shutil
import uuid
from collections import defaultdict
from pathlib import Path

from sorter import (
    LOG_FILE,
    document_roots,
    load_config,
    record_history,
    setup_logging,
    unique_destination,
    watch_root,
)

# "2026-09-02 08:00:34,379 INFO Moved from Documents NAME -> Folder\NAME"
DOCS_MOVE = re.compile(r"INFO Moved from Documents (.+?) -> (.+)$")
SCANNING = re.compile(r"INFO Scanning Documents source (.+)$")

# Where files go when their original folder cannot be worked out.
RECOVERY_DIR = "Sorter Recovery"

# Folders whose contents belong to an application rather than to the user.
# Anything matching lands back where that application expects it.
APP_HOMES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\.(cfa|pek|ims)$", re.I), r"Adobe\Common\Media Cache Files"),
    (re.compile(r"--[0-9a-f-]{20,}-\d{4}-\d{2}-\d{2}.*\.prproj$", re.I),
     r"Adobe\Premiere Pro\26.0\Adobe Premiere Pro Auto-Save"),
]


def log_files() -> list[Path]:
    """The active log plus whatever rotated copies still exist, oldest first."""
    found = [LOG_FILE.with_name(f"{LOG_FILE.name}.{i}") for i in (3, 2, 1)]
    return [path for path in found if path.exists()] + [LOG_FILE]


def parse_moves() -> list[dict]:
    """Every Documents -> Downloads move the log remembers, in order."""
    moves: list[dict] = []
    for path in log_files():
        source_root = ""
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            logging.warning("Could not read %s: %s", path, exc)
            continue
        for line in lines:
            scanning = SCANNING.search(line)
            if scanning:
                source_root = scanning.group(1).strip()
                continue
            move = DOCS_MOVE.search(line)
            if move:
                moves.append({
                    "name": move.group(1).strip(),
                    "dest": move.group(2).strip(),
                    "source_root": source_root,
                })
    return moves


def plan_restore(config: dict, only: list[str]) -> list[dict]:
    downloads = watch_root(config)
    roots = document_roots(config)
    default_root = roots[0] if roots else Path.home() / "Documents"

    plan: list[dict] = []
    seen: set[Path] = set()
    for move in parse_moves():
        current = downloads / move["dest"]
        if not current.exists() or not current.is_file():
            continue  # already put back, deleted, or moved on by hand
        if current in seen:
            continue
        seen.add(current)

        if only and current.suffix.lower() not in only:
            continue

        raw_root = Path(move["source_root"]) if move["source_root"] else default_root
        root = raw_root if raw_root.exists() else default_root

        target_dir, how, certain = None, "", True
        for pattern, relative in APP_HOMES:
            if pattern.search(current.name):
                target_dir, how = root / relative, "the folder this application keeps them in"
                break
        if target_dir is None:
            # The log only kept file names, not their folders, so anything that
            # is not application-owned gets parked somewhere obvious instead of
            # being dropped into a folder picked by guesswork.
            target_dir = root / RECOVERY_DIR
            how = "original folder unknown - parked for you to place"
            certain = False

        plan.append({
            "from": current,
            "to": target_dir / current.name,
            "why": how,
            "certain": certain,
            "exists": target_dir.exists(),
        })
    return plan


def show(plan: list[dict]) -> None:
    if not plan:
        print("Nothing to restore: no logged Documents moves are still sitting in Downloads.")
        return
    groups: dict[Path, list[dict]] = defaultdict(list)
    for item in plan:
        groups[item["to"].parent].append(item)

    sure = sum(1 for item in plan if item["certain"])
    print(f"\n{len(plan)} file(s) to put back - {sure} to a known home, "
          f"{len(plan) - sure} parked for you to place:\n")
    for target, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        marker = "" if items[0]["exists"] else "   (will be created)"
        print(f"  {target}{marker}")
        print(f"    {len(items)} file(s) - {items[0]['why']}")
        for item in items[:3]:
            print(f"      {item['from'].name}")
        if len(items) > 3:
            print(f"      ... and {len(items) - 3} more")
        print()


def apply(plan: list[dict]) -> int:
    batch_id = uuid.uuid4().hex[:8]
    restored = 0
    for item in plan:
        source: Path = item["from"]
        target: Path = item["to"]
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            final = unique_destination(target.parent, target.name)
            shutil.move(str(source), str(final))
        except OSError as exc:
            logging.warning("Could not restore %s: %s", source, exc)
            continue
        record_history(source, final, batch_id)
        logging.info("Restored %s -> %s", source.name, final)
        restored += 1
    return restored


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore files the sorter moved out of Documents.")
    parser.add_argument("--apply", action="store_true", help="actually move the files back")
    parser.add_argument("--only", action="append", default=[],
                        help="limit to an extension, e.g. --only .prproj (repeatable)")
    args = parser.parse_args()

    setup_logging()
    config = load_config()
    only = [e if e.startswith(".") else f".{e}" for e in (x.lower() for x in args.only)]
    plan = plan_restore(config, only)
    show(plan)

    if not plan:
        return 0
    if not args.apply:
        print("Nothing has been moved. Re-run with --apply to carry this out.")
        return 0

    restored = apply(plan)
    print(f"Restored {restored} of {len(plan)} file(s). Undo them with: python sort_undo.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
