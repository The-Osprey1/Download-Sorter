"""Reversibly undo file moves recorded by download-sorter."""

from __future__ import annotations

import argparse
import about
import json
import logging
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
import uuid

import safe_ops
from sorter import (
    HISTORY_FILE,
    LOG_DIR,
    NON_MOVE_ACTIONS,
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
        if rec.get("action") not in NON_MOVE_ACTIONS and rec.get("id") not in undone_ids:
            active.append(rec)
    return active


def _path_key(path: str | Path) -> str:
    """A form of ``path`` that compares equal for the same spot on disk.

    Windows ignores case and accepts both slash styles, and a path typed by hand
    may differ from the one the journal stored (which is already resolved).
    """
    text = str(path)
    try:
        text = str(Path(text).resolve())
    except (OSError, RuntimeError, ValueError):
        pass
    return os.path.normcase(os.path.normpath(text))


def find_active_move(path: str | Path, active: list[dict] | None = None) -> dict | None:
    """The newest move that is not undone yet and put a file at ``path``.

    ``active`` is ``get_active_moves(load_history())`` when left out. A name can
    be filed to, put back and filed to again, so the latest record wins.
    """
    if active is None:
        active = get_active_moves(load_history())
    wanted = _path_key(path)
    wanted_name = os.path.basename(wanted)
    for rec in reversed(active):
        dest = rec.get("dest")
        # Names first: resolving a path asks the disk, and the history can hold thousands of moves.
        if dest and os.path.normcase(os.path.basename(str(dest))) == wanted_name and _path_key(dest) == wanted:
            return rec
    return None


def undo_file(path: str | Path, dry_run: bool = False, active: list[dict] | None = None) -> int:
    """Put back the one file at ``path``; every other filed file stays where it is.

    Returns how many files were restored: 1, or 0 when no active move put a
    file there or the file is no longer at that spot.
    """
    rec = find_active_move(path, active)
    if rec is None:
        logging.info("Nothing to undo for %s: no active move put a file there", path)
        return 0
    return undo_moves([rec], dry_run=dry_run)


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
            restored = safe_ops.restore(rec)
            if restored is None:
                logging.warning("Cannot undo %s: file not found at %s", dest_path.name, dest_path)
                continue
            target_file = restored
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
        except safe_ops.Refused as refusal:
            if refusal.reason == "extraction-record":
                logging.warning(
                    "Skipped %s: it was extracted from %s, which is in your Archives folder; "
                    "the extracted folder was left as it is",
                    dest_path.name, src_path.name,
                )
            else:
                logging.warning("Skipped undo of %s (%s); try again when it is no longer in use", dest_path.name, refusal.reason)
        except OSError as exc:
            logging.error("Failed to restore %s: %s", dest_path, exc)

    return restored_count


def _show_toast(title: str, message: str) -> None:
    """A short ribbon for a command that runs without a window (best effort)."""
    try:
        import ribbon_popup
        ribbon_popup.notify_summary(title, message, accent="#FA7183")
    except Exception:
        pass


def _say(text: str) -> None:
    """print() that does not fail on a path the console's code page cannot show (the toast still matters)."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", "backslashreplace").decode("ascii"))


def _undo_single_file(args: argparse.Namespace, active: list[dict]) -> int:
    """``--file PATH``: put back that one file, as the ribbon's Undo button does."""
    wanted = args.file
    toast = bool(getattr(args, "notify", False) or getattr(args, "quiet_toast", False))
    rec = find_active_move(wanted, active)
    if rec is None:
        _say(f"No active move found for file '{wanted}'.")
        if toast:
            _show_toast("Nothing to put back", "No record of moving that file, or it is already back.")
        return 0

    count = undo_file(wanted, dry_run=args.dry_run, active=active)
    action_word = "Would restore" if args.dry_run else "Restored"
    _say(f"{action_word} {count} file(s).")
    if toast and not args.dry_run:
        src = Path(rec["src"])
        if count:
            _show_toast("Put back", f"{src.name} is back in {src.parent.name or 'its folder'}.")
        elif Path(rec["dest"]).exists():
            _show_toast("Could not put it back", "It may be open in another program.")
        else:
            _show_toast("Could not put it back", f"{Path(wanted).name} is no longer where it was filed.")
    return 0


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    parser = argparse.ArgumentParser(description="Undo recent download-sorter moves.")
    parser.add_argument("--version", action="version", version=about.version_string())
    parser.add_argument(
        "-n", "--dry-run", action="store_true", help="Preview undo operations without moving files"
    )
    parser.add_argument(
        "-c", "--count", type=int, default=None, help="Number of files to undo (default: last batch)"
    )
    parser.add_argument(
        "-b", "--batch", action="append", default=None,
        help="Batch ID to undo (repeat to undo several batches at once)"
    )
    parser.add_argument(
        "-a", "--all", action="store_true", help="Undo all active recorded moves"
    )
    parser.add_argument(
        "-f", "--file", metavar="PATH", default=None,
        help="Undo only the move that put the file at PATH (the ribbon's Undo button); "
             "wins over --all, --batch and --count"
    )
    parser.add_argument(
        "-l", "--list", action="store_true", help="List recent active move history"
    )
    parser.add_argument(
        "--notify", action="store_true", help="Display desktop ribbon summary toast upon completion"
    )
    parser.add_argument(
        "--quiet-toast", action="store_true", help="Quietly display desktop ribbon summary toast"
    )
    args = parser.parse_args(argv)

    records = load_history()
    active = get_active_moves(records)

    if args.file and not args.list:
        return _undo_single_file(args, active)

    if not active:
        print("No reversible move history found.")
        if getattr(args, "notify", False) or getattr(args, "quiet_toast", False):
            try:
                import ribbon_popup
                ribbon_popup.notify_summary(
                    "Undo Last Batch",
                    "No reversible move history found.",
                    accent="#FA7183",
                )
            except Exception:
                pass
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
        wanted = set(args.batch)
        targets = [rec for rec in reversed(active) if rec.get("batch_id") in wanted]
        if not targets:
            print(f"No active moves found for batch '{', '.join(args.batch)}'.")
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
    if getattr(args, "notify", False) or getattr(args, "quiet_toast", False):
        try:
            import ribbon_popup
            msg = f"Restored {count} file(s) to Downloads." if count > 0 else "Nothing was restored."
            ribbon_popup.notify_summary("Undo Last Batch", msg, accent="#FA7183")
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
