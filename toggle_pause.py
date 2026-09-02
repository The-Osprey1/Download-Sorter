"""Toggle or inspect pause state for download-sorter."""

from __future__ import annotations

import argparse
import sys
from sorter import is_paused, set_paused


def main() -> int:
    parser = argparse.ArgumentParser(description="Toggle or inspect pause state of download-sorter.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--pause", action="store_true", help="Explicitly pause download-sorter")
    group.add_argument("--resume", action="store_true", help="Explicitly resume download-sorter")
    group.add_argument("-s", "--status", action="store_true", help="Check current pause state without toggling")
    args = parser.parse_args()

    currently_paused = is_paused()

    if args.status:
        state = "PAUSED" if currently_paused else "ACTIVE (running)"
        print(f"DownloadSorter status: {state}")
        return 0

    if args.pause:
        set_paused(True)
        print("DownloadSorter is now PAUSED.")
        return 0

    if args.resume:
        set_paused(False)
        print("DownloadSorter is now RESUMED (active).")
        return 0

    # Default: toggle
    new_state = not currently_paused
    set_paused(new_state)
    if new_state:
        print("DownloadSorter is now PAUSED. Run this command again to resume.")
    else:
        print("DownloadSorter is now RESUMED (active).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
