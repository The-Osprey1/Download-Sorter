"""Create the Downloads folder tree, sort Downloads, and sweep Documents."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import uuid

from sorter import (
    load_config,
    setup_logging,
    sort_existing,
    sort_existing_documents,
    sort_existing_misplaced,
)


def main() -> int:
    setup_logging()
    parser = argparse.ArgumentParser(description="Sort Downloads and sweep Documents.")
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Simulate and preview what would be moved without modifying files on disk",
    )
    parser.add_argument(
        "-t",
        "--target",
        type=str,
        default=None,
        help="Specific folder to sort into Downloads tree (defaults to Downloads)",
    )
    args = parser.parse_args()

    config = load_config()
    batch_id = uuid.uuid4().hex[:8]
    target_dir = Path(args.target) if args.target else None
    downloads_moved = sort_existing(config, dry_run=args.dry_run, batch_id=batch_id, target_dir=target_dir)
    documents_moved = 0 if target_dir else sort_existing_documents(config, dry_run=args.dry_run, batch_id=batch_id)
    misplaced_moved = 0 if target_dir else sort_existing_misplaced(config, dry_run=args.dry_run, batch_id=batch_id)

    total = downloads_moved + documents_moved + misplaced_moved
    if args.dry_run:
        print(f"\n[DRY-RUN] Simulated sort: Would move {downloads_moved} Downloads file(s), {documents_moved} Documents file(s), and {misplaced_moved} misplaced category file(s).")
    else:
        print(f"Moved {downloads_moved} Downloads file(s), {documents_moved} Documents file(s), and {misplaced_moved} misplaced file(s). [Batch: {batch_id}]")
        if total > 0:
            print("Tip: To undo these moves, run: python sort_undo.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
