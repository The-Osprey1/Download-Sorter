"""Consolidate nested subfolders into official flat categories.

Flattens legacy nested subfolders (e.g. Downloads/Documents/PDFs,
Downloads/Documents/Word, Downloads/3D Models/STL, Downloads/Spreadsheets)
directly into official flat categories (Downloads/Documents,
Downloads/3D Models, etc.) using safe_ops with atomic collision resolution
and reversible history journaling.
"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import sys
import time
import uuid
from typing import Any

import about
import safe_ops
import sorter
from sorter import (
    are_files_identical,
    collect_category_folders,
    duplicates_folder,
    evaluate_routing,
    folder_path,
    is_incomplete,
    is_internal_path,
    load_config,
    setup_logging,
    unique_destination,
    watch_root,
)

PROTECTED_NAMES = {
    ".git",
    ".svn",
    ".hg",
    ".venv",
    "venv",
    "node_modules",
    ".cargo",
    ".rustup",
    ".obsidian",
    "__pycache__",
    ".idea",
    ".vscode",
    "build",
    "dist",
    "_internal",
    ".batch_stage",
    "_Remodel Quarantine",
    "_Duplicates",
    "Games",
    "Extracted",
    "Installers",
}

# Categories whose historical subfolders should be flattened into the category root
FLAT_CATEGORIES_TO_CONSOLIDATE = {
    "Documents",
    "3D Models",
    "Images",
    "Audio",
    "Videos",
    "Archives",
}

# Legacy top-level folders whose contents should be routed to official categories
LEGACY_FOLDERS_TO_CONSOLIDATE = {
    "spreadsheets",
    "finance",
    "xyzfiles",
    "downloadsorter-v2.0.2-artifacts",
    "downloadsorter-v2.0.2-public",
    "downloadsorter-v2.1.0-artifacts",
}

# Specific legacy subfolders in otherwise protected directories (e.g. Code)
LEGACY_SUBFOLDERS_TO_CONSOLIDATE = [
    ("code", "gcode"),
    ("code", "haas"),
]


def resolve_category_dir(root: Path, cat_name: str) -> Path:
    """Find the directory for cat_name case-insensitively, or fall back to folder_path."""
    target_lower = cat_name.lower()
    try:
        for entry in root.iterdir():
            if entry.is_dir() and entry.name.lower() == target_lower:
                return entry
    except OSError:
        pass
    return folder_path(root, cat_name)


def is_protected_directory(path: Path) -> bool:
    """Check if a directory is a repository, virtualenv, or explicitly protected."""
    name = path.name
    if name in PROTECTED_NAMES or name.startswith("_Remodel Quarantine"):
        return True
    if (path / ".git").exists() or (path / ".svn").exists() or (path / ".hg").exists():
        return True
    if (path / ".download-sorter-keep").exists():
        return True
    if (path / ".venv").exists() or (path / "venv").exists() or (path / "pyvenv.cfg").exists():
        return True
    return False


def consolidate_downloads(
    target_root: Path | str | None = None,
    config: dict | None = None,
    dry_run: bool = False,
    batch_id: str = "",
    include_held: bool = True,
    remove_empty: bool = True,
) -> dict[str, Any]:
    """Consolidate nested subfolders and legacy categories into flat official categories.

    All moves are executed via safe_ops and journaled in history.jsonl so the
    entire operation can be completely and safely undone using `sort_undo`.
    """
    if config is None:
        config = load_config()

    root = Path(target_root).resolve() if target_root else watch_root(config).resolve()
    if not root.exists():
        return {"moved": 0, "duplicates": 0, "dirs_removed": 0, "moves": []}

    batch = batch_id or f"consolidate_{uuid.uuid4().hex[:8]}"

    candidates: list[Path] = []

    # 1. Root loose files (e.g. held .3mf files or previously undone files)
    if include_held:
        for entry in root.iterdir():
            if entry.is_file():
                candidates.append(entry)

    # 2. Subfolders inside target flat categories (e.g. Documents\PDFs, 3D Models\STL, Images\Photos)
    for cat_name in FLAT_CATEGORIES_TO_CONSOLIDATE:
        cat_dir = resolve_category_dir(root, cat_name)
        if not cat_dir.exists() or not cat_dir.is_dir():
            continue
        for sub_root, dirs, files in os.walk(cat_dir):
            sub_path = Path(sub_root)
            if sub_path.resolve() == cat_dir.resolve():
                continue  # Files directly in the root of the category are already flat!
            dirs[:] = [d for d in dirs if not is_protected_directory(sub_path / d)]
            for f in files:
                candidates.append(sub_path / f)

    # 3. Known legacy folders (e.g. Spreadsheets, Finance, etc.)
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        if entry.name.lower() in LEGACY_FOLDERS_TO_CONSOLIDATE:
            for sub_root, dirs, files in os.walk(entry):
                sub_path = Path(sub_root)
                dirs[:] = [d for d in dirs if not is_protected_directory(sub_path / d)]
                for f in files:
                    candidates.append(sub_path / f)

    # 4. Known legacy subfolders (e.g. Code\Gcode, Code\Haas)
    for parent_sub, child_sub in LEGACY_SUBFOLDERS_TO_CONSOLIDATE:
        leg_dir = root / parent_sub / child_sub
        if leg_dir.exists() and leg_dir.is_dir():
            for sub_root, dirs, files in os.walk(leg_dir):
                sub_path = Path(sub_root)
                dirs[:] = [d for d in dirs if not is_protected_directory(sub_path / d)]
                for f in files:
                    candidates.append(sub_path / f)

    moved_count = 0
    dup_count = 0
    executed_moves: list[dict[str, Any]] = []

    # 0. Reconcile any interrupted moves from prior consolidation batches where
    # destination was already fully written and is identical to source
    if not dry_run:
        for rec in safe_ops.pending_planned(min_age_seconds=0.0):
            if not str(rec.get("batch_id", "")).startswith("consolidate"):
                continue
            s = Path(str(rec.get("src", "")))
            d = Path(str(rec.get("dest", "")))
            if s.exists() and d.exists() and are_files_identical(s, d):
                for attempt in range(4):
                    try:
                        s.unlink()
                        safe_ops.append_journal(safe_ops._done_record(
                            str(rec["id"]), str(rec.get("batch_id", "")), str(s), str(d), "move", True
                        ))
                        moved_count += 1
                        executed_moves.append({
                            "src": str(s),
                            "dest": str(d),
                            "duplicate": False,
                        })
                        break
                    except OSError:
                        if attempt < 3:
                            time.sleep(0.05 * (attempt + 1))

    for file_path in candidates:
        if not file_path.exists() or not file_path.is_file():
            continue
        if is_incomplete(file_path, config):
            continue
        if is_internal_path(file_path, root):
            continue

        # Evaluate official routing destination
        routed = evaluate_routing(file_path.name, config, file_path=file_path)
        if routed.get("match_type") == "ignored":
            continue

        dest_rel = routed.get("destination", config.get("default_folder", "Other"))
        dest_dir = resolve_category_dir(root, dest_rel)

        # Skip if already directly in the target category folder
        if file_path.parent.resolve() == dest_dir.resolve():
            continue

        dest_dir.mkdir(parents=True, exist_ok=True)
        primary_target = dest_dir / file_path.name
        is_dup = primary_target.exists() and are_files_identical(file_path, primary_target)

        if dry_run:
            if is_dup:
                dup_count += 1
                logging.info("[DRY-RUN] Duplicate: %s matches %s", file_path, primary_target)
            else:
                moved_count += 1
                logging.info("[DRY-RUN] Would consolidate: %s -> %s", file_path, primary_target)
            executed_moves.append({
                "src": str(file_path),
                "dest": str(primary_target),
                "duplicate": is_dup,
            })
            continue

        # Execute safe move
        try:
            if is_dup:
                dup_dir = duplicates_folder(root)
                parked = safe_ops.quarantine(file_path, dup_dir, batch_id=batch)
                dup_count += 1
                executed_moves.append({
                    "src": str(file_path),
                    "dest": str(parked),
                    "duplicate": True,
                })
            else:
                final_dest = safe_ops.move(file_path, primary_target, batch_id=batch, settle_seconds=0.0)
                moved_count += 1
                executed_moves.append({
                    "src": str(file_path),
                    "dest": str(final_dest),
                    "duplicate": False,
                })

            # Clear hold or restored flag if the file had one
            try:
                import held_files
                held_files.release(file_path)
            except Exception:
                pass
            try:
                import restored_files
                restored_files.remove_restored(file_path)
            except Exception:
                pass

        except Exception as exc:
            logging.warning("Could not consolidate %s: %s", file_path.name, exc)

    # Clean up empty directories
    dirs_removed = 0
    if remove_empty and not dry_run:
        category_set = {c.lower() for c in collect_category_folders(config)}

        # 1. Clean subfolders inside flat categories
        for cat_name in FLAT_CATEGORIES_TO_CONSOLIDATE:
            cat_dir = resolve_category_dir(root, cat_name)
            if not cat_dir.exists() or not cat_dir.is_dir():
                continue
            for sub_root, dirs, _ in os.walk(cat_dir, topdown=False):
                p = Path(sub_root)
                if p.resolve() == cat_dir.resolve() or is_protected_directory(p):
                    continue
                try:
                    if not any(p.iterdir()):
                        p.rmdir()
                        dirs_removed += 1
                except (OSError, PermissionError):
                    pass

        # 2. Clean legacy folders and legacy subfolders
        legacy_paths_to_clean = [root / name for name in LEGACY_FOLDERS_TO_CONSOLIDATE]
        legacy_paths_to_clean.extend([root / p / c for p, c in LEGACY_SUBFOLDERS_TO_CONSOLIDATE])

        for leg_path in legacy_paths_to_clean:
            if not leg_path.exists() or not leg_path.is_dir():
                continue
            for sub_root, dirs, _ in os.walk(leg_path, topdown=False):
                p = Path(sub_root)
                if is_protected_directory(p):
                    continue
                try:
                    if not any(p.iterdir()):
                        p.rmdir()
                        dirs_removed += 1
                except (OSError, PermissionError):
                    pass
            try:
                if leg_path.exists() and leg_path.name.lower() not in category_set and not any(leg_path.iterdir()):
                    leg_path.rmdir()
                    dirs_removed += 1
            except (OSError, PermissionError):
                pass

    return {
        "moved": moved_count,
        "duplicates": dup_count,
        "dirs_removed": dirs_removed,
        "batch_id": batch,
        "moves": executed_moves,
    }


def main(argv: list[str] | None = None) -> int:
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
            handlers=[logging.StreamHandler()],
        )
    parser = argparse.ArgumentParser(
        description="Consolidate nested subfolders and legacy categories into official flat categories."
    )
    parser.add_argument("--version", action="version", version=about.version_string())
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Simulate consolidation without moving any files on disk",
    )
    parser.add_argument(
        "-t",
        "--target",
        type=str,
        default=None,
        help="Target folder root to consolidate (defaults to configured Downloads folder)",
    )
    parser.add_argument(
        "--no-held",
        action="store_true",
        help="Do not consolidate loose/held files at the root of Downloads",
    )
    parser.add_argument(
        "--keep-empty-dirs",
        action="store_true",
        help="Do not remove emptied subdirectories after moving files",
    )
    parser.add_argument(
        "--notify",
        action="store_true",
        help="Display desktop notification summary toast upon completion",
    )

    args = parser.parse_args(argv)
    config = load_config()
    target_root = Path(args.target) if args.target else None

    result = consolidate_downloads(
        target_root=target_root,
        config=config,
        dry_run=args.dry_run,
        include_held=not args.no_held,
        remove_empty=not args.keep_empty_dirs,
    )

    moved = result["moved"]
    dups = result["duplicates"]
    dirs = result["dirs_removed"]
    batch = result.get("batch_id", "")

    if args.dry_run:
        print(f"\n[DRY-RUN] Simulated consolidation: Would move {moved} file(s), quarantine {dups} duplicate(s).")
    else:
        print(f"Consolidated {moved} file(s) into flat categories, quarantined {dups} duplicate(s), cleaned {dirs} empty folder(s). [Batch: {batch}]")
        if moved > 0 or dups > 0:
            print("Tip: To undo this consolidation, run: DownloadSorter --undo")

    if args.notify:
        try:
            import ribbon_popup
            msg = f"Consolidated {moved} file(s) into flat categories." if moved > 0 else "Downloads is already consolidated."
            ribbon_popup.notify_summary("Downloads Consolidated", msg, accent="#69E6AD")
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
