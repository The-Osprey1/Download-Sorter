"""Create the Downloads folder tree, sort Downloads, and sweep Documents."""

from __future__ import annotations

import argparse
import sys
import uuid

from sorter import load_config, setup_logging, sort_existing, sort_existing_documents


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

    if args.dry_run:
        print(f"\n[DRY-RUN] Simulated sort: Would move {downloads_moved} Downloads file(s) and {documents_moved} Documents file(s).")
    else:
        print(f"Moved {downloads_moved} Downloads file(s) and {documents_moved} Documents file(s). [Batch: {batch_id}]")
        if downloads_moved + documents_moved > 0:
            print("Tip: To undo these moves, run: python sort_undo.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
