"""Automatically flatten and extract 3D model files from zip archives (Printables, MakerWorld, Thingiverse)."""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
import zipfile
from pathlib import Path

from sorter import (
    classify,
    folder_path,
    load_config,
    setup_logging,
    unique_destination,
    watch_root,
)

TARGET_3D_EXTENSIONS = {
    ".stl",
    ".3mf",
    ".step",
    ".stp",
    ".obj",
    ".iges",
    ".igs",
    ".gcode",
    ".bgcode",
    ".f3d",
    ".blend",
}

JUNK_NAMES = {
    "license.txt",
    "license.pdf",
    "readme.txt",
    "readme.md",
    ".ds_store",
    "desktop.ini",
    "thumbs.db",
}


def is_3d_archive(archive_path: Path) -> list[str]:
    """Return list of 3D file paths inside zip if archive contains 3D models."""
    try:
        with zipfile.ZipFile(archive_path, "r") as z:
            results: list[str] = []
            for name in z.namelist():
                if name.endswith("/") or name.startswith("__MACOSX/"):
                    continue
                p = Path(name)
                if p.name.lower() in JUNK_NAMES:
                    continue
                if p.suffix.lower() in TARGET_3D_EXTENSIONS:
                    results.append(name)
            return results
    except Exception:
        return []


def unpack_3d_archive(archive_path: Path, config: dict, dry_run: bool = False) -> list[Path]:
    """Extract 3D models from zip archive into 3D Models directory and move zip to Archives."""
    archive_path = archive_path.resolve()
    if not archive_path.exists() or not archive_path.is_file():
        return []

    model_files = is_3d_archive(archive_path)
    if not model_files:
        logging.info("Archive %s does not contain recognized 3D model files.", archive_path.name)
        return []

    downloads = watch_root(config)
    archive_dest_dir = folder_path(downloads, "Archives")
    extracted_paths: list[Path] = []

    model_folder_name = archive_path.stem
    # If archive has multiple 3D files, place them in a dedicated folder under 3D Models
    # If archive only has 1 3D file, place it in the specific category (STL, 3MF, etc.)
    multi_file = len(model_files) > 1

    try:
        with zipfile.ZipFile(archive_path, "r") as z:
            for zip_internal_path in model_files:
                file_name = Path(zip_internal_path).name
                ext = Path(file_name).suffix.lower()

                if multi_file:
                    dest_dir = folder_path(downloads, f"3D Models\\{model_folder_name}")
                else:
                    dest_folder = classify(Path(file_name), config)
                    dest_dir = folder_path(downloads, dest_folder)

                target_file = unique_destination(dest_dir, file_name)

                if dry_run:
                    logging.info("[DRY-RUN] Would extract %s -> %s", file_name, target_file)
                    extracted_paths.append(target_file)
                    continue

                dest_dir.mkdir(parents=True, exist_ok=True)
                with z.open(zip_internal_path) as src, open(target_file, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                logging.info("Extracted %s -> %s", file_name, target_file.relative_to(downloads))
                extracted_paths.append(target_file)

        # Move original zip to Archives
        if not dry_run:
            archive_dest_dir.mkdir(parents=True, exist_ok=True)
            zip_destination = unique_destination(archive_dest_dir, archive_path.name)
            try:
                shutil.move(str(archive_path), str(zip_destination))
                logging.info("Moved original zip -> %s", zip_destination.relative_to(downloads))
            except OSError as exc:
                logging.warning("Could not move zip %s: %s", archive_path.name, exc)

    except Exception as exc:
        logging.error("Failed unpacking %s: %s", archive_path.name, exc)

    return extracted_paths


def main() -> int:
    setup_logging()
    parser = argparse.ArgumentParser(description="Extract 3D models from zip archives (flattens nested folders).")
    parser.add_argument("archive", nargs="?", type=str, help="Path to .zip archive to unpack")
    parser.add_argument("-n", "--dry-run", action="store_true", help="Simulate extraction without writing files")
    parser.add_argument("-a", "--all", action="store_true", help="Scan top of Downloads for 3D zip archives and unpack all")
    args = parser.parse_args()

    config = load_config()
    downloads = watch_root(config)

    if args.all:
        zips = list(downloads.glob("*.zip"))
        if not zips:
            print(f"No .zip files found at top level of {downloads}.")
            return 0
        total_extracted = 0
        for z in zips:
            extracted = unpack_3d_archive(z, config, dry_run=args.dry_run)
            total_extracted += len(extracted)
        print(f"Unpacked {total_extracted} 3D model file(s) across {len(zips)} archive(s).")
        return 0

    if not args.archive:
        parser.print_help()
        return 1

    target = Path(args.archive)
    if not target.exists():
        print(f"Archive not found: {target}")
        return 1

    extracted = unpack_3d_archive(target, config, dry_run=args.dry_run)
    action = "Would extract" if args.dry_run else "Extracted"
    print(f"{action} {len(extracted)} 3D model file(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
