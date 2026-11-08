"""Create the Downloads folder tree, sort Downloads, and sweep Documents."""

from __future__ import annotations

import argparse
import about
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


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    import watch
    if not watch.is_instance_running():
        try:
            import safe_ops
            safe_ops.recover_pending_journal(min_age_seconds=10.0)
        except Exception:
            pass
    parser = argparse.ArgumentParser(description="Sort Downloads and sweep Documents.")
    parser.add_argument("--version", action="version", version=about.version_string())
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
    parser.add_argument(
        "--notify",
        action="store_true",
        help="Display desktop ribbon summary toast upon completion",
    )
    parser.add_argument(
        "--quiet-toast",
        action="store_true",
        help="Quietly display desktop ribbon summary toast",
    )
    args = parser.parse_args(argv)

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

    if getattr(args, "notify", False) or getattr(args, "quiet_toast", False):
        try:
            import ribbon_popup
            msg = f"Filed {total} file(s) into category folders." if total > 0 else "Downloads is already organized."
            ribbon_popup.notify_summary("Downloads Organized", msg, accent="#69E6AD")
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
