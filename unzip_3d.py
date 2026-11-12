"""Automatically flatten and extract 3D model files from zip archives (Printables, MakerWorld, Thingiverse)."""

from __future__ import annotations

import argparse
import about
import logging
import os
import math
import sys
import time
import zipfile

import zip_members
from pathlib import Path

import safe_ops
from sorter import (
    classify,
    folder_path,
    is_under,
    load_active_project,
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
                if zip_members.is_directory_entry(name) or zip_members.is_system_junk(name):
                    continue
                p = Path(name)
                if p.name.lower() in JUNK_NAMES:
                    continue
                if p.suffix.lower() in TARGET_3D_EXTENSIONS:
                    results.append(name)
            return results
    except Exception:
        return []


GUIDE_EXTENSIONS = {".pdf", ".txt", ".md", ".jpg", ".jpeg", ".png", ".webp", ".gif"}


def _makerworld_kit_dir(archive_path: Path, downloads: Path, config: dict) -> Path | None:
    """The model kit folder for a zip downloaded from a MakerWorld model page, or None."""
    import kits
    from sorter import MAKERWORLD_FOLDER, folder_path, sanitize_folder_segment

    found = kits.model_from_file(archive_path)
    if not found:
        return None
    _model_id, slug_title = found
    base = folder_path(downloads, str(config.get("makerworld_destination") or MAKERWORLD_FOLDER))
    return base / kits.existing_folder(base, sanitize_folder_segment(slug_title))


def _kit_members(archive_path: Path, model_files: list[str]) -> list[str]:
    """The 3D files plus the build guides and reference photos a MakerWorld kit keeps."""
    members = list(model_files)
    try:
        with zipfile.ZipFile(archive_path, "r") as z:
            for name in z.namelist():
                if zip_members.is_directory_entry(name) or zip_members.is_system_junk(name):
                    continue
                if Path(name).suffix.lower() not in GUIDE_EXTENSIONS:
                    continue
                if name not in members:
                    members.append(name)
    except Exception:
        pass
    return members


def _safe_parent(zip_internal_path: str) -> Path:
    """The folder a member sat in inside the zip, kept (its own sub-folders stay together),
    but never allowed to climb out of the kit folder."""
    parts = zip_members.safe_relative_parts(zip_internal_path)
    folders = parts[:-1] if parts else ()
    return Path(*folders) if folders else Path(".")


def unpack_3d_archive(
    archive_path: Path,
    config: dict,
    dry_run: bool = False,
    active_project: dict | None = None,
    batch_id: str = "",
) -> list[Path]:
    """Extract 3D models from zip archive into 3D Models directory or active project and move zip to Archives.

    The archive is checked against ``config["archive_limits"]`` before anything is
    decompressed; if a limit is exceeded, or extraction fails part-way, the files this
    call already wrote are removed again and an empty list is returned.  The move of
    the original zip to Archives is an ordinary, undoable move.
    """
    archive_path = archive_path.resolve()
    if not archive_path.exists() or not archive_path.is_file():
        return []

    model_files = is_3d_archive(archive_path)
    if not model_files:
        logging.info("Archive %s does not contain recognized 3D model files.", archive_path.name)
        return []

    # Check active project
    active_proj = None
    if active_project is not None:
        if isinstance(active_project, dict):
            is_en = active_project.get("enabled", active_project.get("active", False))
            exp = active_project.get("expires_at")
            if is_en:
                is_expired = False
                if exp is not None:
                    try:
                        exp_val = float(exp)
                        if math.isnan(exp_val) or exp_val < 0 or (exp_val > 0 and time.time() >= exp_val):
                            is_expired = True
                    except (ValueError, TypeError):
                        is_expired = True
                if not is_expired:
                    active_proj = active_project
    else:
        active_proj = load_active_project()

    downloads = watch_root(config)
    kit_dir: Path | None = None
    if not active_proj:
        kit_dir = _makerworld_kit_dir(archive_path, downloads, config)
        if kit_dir is not None:
            # A MakerWorld kit keeps its build guide and reference photos with the models.
            model_files = _kit_members(archive_path, model_files)
    extracted_paths: list[Path] = []
    model_folder_name = archive_path.stem
    multi_file = len(model_files) > 1

    if active_proj:
        raw_target = (
            active_proj.get("target_directory")
            or active_proj.get("folder_path")
            or active_proj.get("target_folder")
        )
        if not raw_target or not str(raw_target).strip():
            active_proj = None

    if active_proj:
        target_root = Path(str(raw_target).strip())
        archive_dest_dir = target_root / "Archives"
    else:
        target_root = downloads
        archive_dest_dir = folder_path(downloads, "Archives")

    try:
        with zipfile.ZipFile(archive_path, "r") as z:
            budget = [0]
            if not dry_run:
                from unzip_archive import ArchiveLimitError, archive_limits, check_archive_limits
                members = [z.getinfo(name) for name in model_files]
                try:
                    budget = [check_archive_limits(z, members, target_root, archive_limits(config))]
                except ArchiveLimitError as exc:
                    logging.warning("Not unpacking %s: %s", archive_path.name, exc)
                    return []
            for zip_internal_path in model_files:
                file_name = Path(zip_internal_path).name

                if kit_dir is not None:
                    dest_dir = kit_dir / _safe_parent(zip_internal_path)
                elif active_proj:
                    if multi_file:
                        dest_dir = target_root / model_folder_name
                    else:
                        dest_dir = target_root
                else:
                    if multi_file:
                        dest_dir = folder_path(downloads, f"3D Models\\{model_folder_name}")
                    else:
                        dest_folder = classify(Path(file_name), config)
                        dest_dir = folder_path(downloads, dest_folder)

                target_file = unique_destination(dest_dir, file_name)

                dest_display = (
                    target_file.relative_to(downloads)
                    if is_under(target_file, downloads)
                    else str(target_file)
                )

                if dry_run:
                    logging.info("[DRY-RUN] Would extract %s -> %s", file_name, dest_display)
                    extracted_paths.append(target_file)
                    continue

                dest_dir.mkdir(parents=True, exist_ok=True)
                from unzip_archive import copy_member_limited
                extracted_paths.append(target_file)
                copy_member_limited(z, z.getinfo(zip_internal_path), target_file, budget)
                logging.info("Extracted %s -> %s", file_name, dest_display)

        # Move original zip to Archives
        if not dry_run:
            archive_dest_dir.mkdir(parents=True, exist_ok=True)
            zip_destination = unique_destination(archive_dest_dir, archive_path.name)
            zip_display = (
                zip_destination.relative_to(downloads)
                if is_under(zip_destination, downloads)
                else str(zip_destination)
            )
            try:
                safe_ops.move(archive_path, zip_destination, batch_id=batch_id, settle_seconds=0.0, honor_keep=False, skip_preflight=True)
                logging.info("Moved original zip -> %s", zip_display)
            except (OSError, safe_ops.Refused) as exc:
                logging.warning("Could not move zip %s: %s", archive_path.name, exc)

    except Exception as exc:
        logging.error("Failed unpacking %s: %s", archive_path.name, exc)
        if not dry_run:
            # Roll back the files this call created; the zip itself was not touched.
            for created in extracted_paths:
                try:
                    created.unlink()
                except OSError:
                    pass
            return []

    return extracted_paths


def main() -> int:
    setup_logging()
    parser = argparse.ArgumentParser(description="Extract 3D models from zip archives (flattens nested folders).")
    parser.add_argument("--version", action="version", version=about.version_string())
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
