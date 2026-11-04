"""Automatically extract and organize zip archives into dedicated landing subfolders."""

from __future__ import annotations

import argparse
import about
import logging
import math
import os
import shutil
import sys
import time
import uuid
import zipfile
from pathlib import Path

import safe_ops
from sorter import (
    folder_path,
    is_under,
    load_active_project,
    load_config,
    setup_logging,
    unique_destination,
    watch_root,
)

JUNK_PREFIXES = ("__MACOSX/", "._")
JUNK_NAMES = {
    ".ds_store",
    "desktop.ini",
    "thumbs.db",
}


def is_valid_zip(archive_path: Path) -> bool:
    """Check if the path points to an existing, non-empty, readable zip archive.

    Only the archive's directory is read here.  Member contents are not decompressed
    (that used to happen twice, once for testzip() and again while extracting); CRC
    errors are caught while extracting instead.
    """
    try:
        archive_path = archive_path.resolve()
        if not archive_path.exists() or not archive_path.is_file():
            return False
        if archive_path.stat().st_size == 0:
            return False
        if not zipfile.is_zipfile(archive_path):
            return False
        with zipfile.ZipFile(archive_path, "r") as z:
            z.infolist()
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Resource limits (zip bombs, runaway archives)
# ---------------------------------------------------------------------------

DEFAULT_ARCHIVE_LIMITS: dict[str, float] = {
    "max_total_mb": 4096,     # total uncompressed size of the members we extract
    "max_entries": 10000,     # number of members in the archive
    "max_ratio": 200,         # uncompressed / compressed size of any single member over 1 MB
    "min_free_mb": 1024,      # disk space that must remain free after extracting
}
_RATIO_FLOOR_BYTES = 1024 * 1024


class ArchiveLimitError(Exception):
    """The archive exceeds a safety limit; nothing was extracted."""


def archive_limits(config: dict | None) -> dict[str, float]:
    limits = dict(DEFAULT_ARCHIVE_LIMITS)
    overrides = (config or {}).get("archive_limits", {})
    if isinstance(overrides, dict):
        for key in limits:
            try:
                if key in overrides:
                    limits[key] = float(overrides[key])
            except (TypeError, ValueError):
                pass
    return limits


def _nearest_existing(path: Path) -> Path:
    for candidate in (path, *path.parents):
        if candidate.exists():
            return candidate
    return path


def check_archive_limits(
    archive: zipfile.ZipFile,
    members: list[zipfile.ZipInfo],
    dest_parent: Path,
    limits: dict[str, float],
) -> int:
    """Validate the archive's own metadata before anything is decompressed.

    Returns the total uncompressed size of ``members``; raises ArchiveLimitError.
    """
    entry_count = len(archive.infolist())
    if entry_count > limits["max_entries"]:
        raise ArchiveLimitError(f"{entry_count} entries (limit {int(limits['max_entries'])})")
    total = 0
    for info in members:
        size = int(info.file_size)
        packed = int(info.compress_size)
        if size > _RATIO_FLOOR_BYTES:
            if packed <= 0 or size / packed > limits["max_ratio"]:
                raise ArchiveLimitError(
                    f"'{info.filename}' expands {size // max(packed, 1)}x (limit {int(limits['max_ratio'])}x)"
                )
        total += size
    max_total = limits["max_total_mb"] * 1024 * 1024
    if total > max_total:
        raise ArchiveLimitError(
            f"{total / 1048576:.0f} MB uncompressed (limit {limits['max_total_mb']:.0f} MB)"
        )
    try:
        free = shutil.disk_usage(_nearest_existing(dest_parent)).free
    except OSError:
        free = None
    if free is not None and free - total < limits["min_free_mb"] * 1024 * 1024:
        raise ArchiveLimitError(
            f"needs {total / 1048576:.0f} MB but only {free / 1048576:.0f} MB is free"
        )
    return total


def copy_member_limited(archive: zipfile.ZipFile, info: zipfile.ZipInfo, target: Path, budget: list[int]) -> None:
    """Stream one member to ``target``, never writing more than its declared size or the budget."""
    declared = int(info.file_size)
    written = 0
    with archive.open(info) as src, open(target, "wb") as dst:
        while True:
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            budget[0] -= len(chunk)
            if written > declared or budget[0] < 0:
                raise ArchiveLimitError(f"'{info.filename}' produced more data than it declared")
            dst.write(chunk)


def get_strip_prefix(namelist: list[str]) -> str:
    """Return common top-level directory prefix to strip if all members are wrapped in a single root folder.

    Avoids double-nesting like Extracted/archive_name/archive_name/...
    """
    valid_names: list[str] = []
    for name in namelist:
        normalized = name.replace("\\", "/")
        if any(normalized.startswith(p) or f"/{p}" in normalized for p in JUNK_PREFIXES):
            continue
        base = Path(normalized).name.lower()
        if base in JUNK_NAMES:
            continue
        valid_names.append(normalized)

    if not valid_names:
        return ""

    candidate: str | None = None
    for name in valid_names:
        parts = [p for p in name.strip("/").split("/") if p]
        if not parts:
            continue
        # If there is a file sitting at root level, no root folder wraps everything
        if len(parts) == 1 and not name.endswith("/"):
            return ""
        root_part = parts[0]
        if candidate is None:
            candidate = root_part
        elif candidate != root_part:
            return ""

    return f"{candidate}/" if candidate else ""


def _publish_staging(staging: Path, extract_dir: Path) -> None:
    """Move a fully written staging folder into place."""
    if not extract_dir.exists():
        extract_dir.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging, extract_dir)
        return
    # Extracting into an existing folder (explicit target_folder): publish file by file.
    for item in sorted(staging.rglob("*")):
        if item.is_file():
            final = extract_dir / item.relative_to(staging)
            final.parent.mkdir(parents=True, exist_ok=True)
            os.replace(item, final)
    shutil.rmtree(staging, ignore_errors=True)


def unpack_archive(
    archive_path: Path,
    config: dict | None = None,
    dry_run: bool = False,
    active_project: dict | None = None,
    target_folder: Path | None = None,
    batch_id: str = "",
) -> Path | None:
    """Extract a zip archive into a dedicated subfolder under Extracted/ (or target_folder)

    and move the original archive to Archives/.
    Returns the extraction directory Path on success, or None on failure (including an
    archive over the safety limits in ``config["archive_limits"]``; nothing is written then).

    Files are extracted into a hidden ``.<name>.partial-<id>`` folder next to the
    destination and only published once every member has been written, so an
    interrupted or rejected extraction never leaves a half-filled folder behind.
    The move of the original zip to Archives is an ordinary, undoable move.
    """
    archive_path = archive_path.resolve()
    if not is_valid_zip(archive_path):
        logging.warning("Archive %s is not a valid zip archive or is corrupted.", archive_path.name)
        return None

    if config is None:
        config = load_config()

    downloads = watch_root(config)

    # Check active project if not explicitly supplied
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

    if active_proj:
        raw_target = (
            active_proj.get("target_directory")
            or active_proj.get("folder_path")
            or active_proj.get("target_folder")
        )
        if not raw_target or not str(raw_target).strip():
            active_proj = None

    extracted_rel = config.get("extracted_destination", "Extracted")

    if target_folder is not None:
        extract_dir = target_folder
        archive_dest_dir = folder_path(downloads, "Archives")
    elif active_proj:
        target_root = Path(str(raw_target).strip())
        extract_parent = target_root / extracted_rel
        extract_dir = unique_destination(extract_parent, archive_path.stem)
        archive_dest_dir = target_root / "Archives"
    else:
        extract_parent = folder_path(downloads, extracted_rel)
        extract_dir = unique_destination(extract_parent, archive_path.stem)
        archive_dest_dir = folder_path(downloads, "Archives")

    dest_display = (
        extract_dir.relative_to(downloads)
        if is_under(extract_dir, downloads)
        else str(extract_dir)
    )

    try:
        with zipfile.ZipFile(archive_path, "r") as z:
            namelist = z.namelist()
            strip_prefix = get_strip_prefix(namelist)
            if strip_prefix:
                logging.debug("Stripping redundant wrapper root folder '%s' for %s", strip_prefix, archive_path.name)

            entries_to_extract: list[tuple[zipfile.ZipInfo, Path]] = []
            for info in z.infolist():
                raw_name = info.filename.replace("\\", "/")
                # Skip junk files
                if any(raw_name.startswith(p) or f"/{p}" in raw_name for p in JUNK_PREFIXES):
                    continue
                if Path(raw_name).name.lower() in JUNK_NAMES:
                    continue

                if strip_prefix and raw_name.startswith(strip_prefix):
                    clean_rel = raw_name[len(strip_prefix):]
                else:
                    clean_rel = raw_name

                if not clean_rel or clean_rel.endswith("/"):
                    continue

                target_file = (extract_dir / clean_rel).resolve()

                # Zip Slip defense: ensure target_file is strictly inside extract_dir
                try:
                    target_file.relative_to(extract_dir.resolve())
                except ValueError:
                    logging.warning("Skipping entry with illegal path traversal '%s' in %s", info.filename, archive_path.name)
                    continue

                entries_to_extract.append((info, target_file))

            if dry_run:
                logging.info("[DRY-RUN] Would extract %d file(s) from %s -> %s", len(entries_to_extract), archive_path.name, dest_display)
                return extract_dir

            limits = archive_limits(config)
            try:
                total = check_archive_limits(z, [info for info, _ in entries_to_extract], extract_dir.parent, limits)
            except ArchiveLimitError as exc:
                logging.warning("Not extracting %s: %s", archive_path.name, exc)
                return None

            staging = extract_dir.parent / f".{extract_dir.name}.partial-{uuid.uuid4().hex[:8]}"
            staging.mkdir(parents=True, exist_ok=False)
            budget = [total]
            try:
                for info, target_file in entries_to_extract:
                    staged_file = staging / target_file.relative_to(extract_dir.resolve())
                    staged_file.parent.mkdir(parents=True, exist_ok=True)
                    copy_member_limited(z, info, staged_file, budget)
                    # Preserve file timestamp if valid
                    try:
                        if info.date_time and info.date_time[0] >= 1980:
                            mtime = time.mktime(info.date_time + (0, 0, -1))
                            os.utime(staged_file, (mtime, mtime))
                    except Exception:
                        pass
                _publish_staging(staging, extract_dir)
            except BaseException:
                shutil.rmtree(staging, ignore_errors=True)
                raise

            logging.info("Extracted %d file(s) to %s", len(entries_to_extract), dest_display)

        # Move original zip to Archives (undoable: Undo brings the zip back)
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
                logging.warning("Could not move original zip %s: %s", archive_path.name, exc)

        return extract_dir

    except Exception as exc:
        logging.error("Failed unpacking archive %s: %s", archive_path.name, exc)
        return None


def main() -> int:
    setup_logging()
    parser = argparse.ArgumentParser(description="Extract zip archives into dedicated landing folders and preserve archives.")
    parser.add_argument("--version", action="version", version=about.version_string())
    parser.add_argument("archive", nargs="?", type=str, help="Path to .zip archive to unpack")
    parser.add_argument("-n", "--dry-run", action="store_true", help="Simulate extraction without writing files")
    parser.add_argument("-a", "--all", action="store_true", help="Scan top of Downloads for all .zip archives and unpack all")
    parser.add_argument("-d", "--dest", type=str, default=None, help="Custom landing folder to extract into")
    args = parser.parse_args()

    config = load_config()
    downloads = watch_root(config)

    custom_dest = Path(args.dest).resolve() if args.dest else None

    if args.all:
        zips = list(downloads.glob("*.zip"))
        if not zips:
            print(f"No .zip files found at top level of {downloads}.")
            return 0
        total_extracted = 0
        for z in zips:
            landing = unpack_archive(z, config, dry_run=args.dry_run, target_folder=custom_dest)
            if landing is not None:
                total_extracted += 1
        action = "Would unpack" if args.dry_run else "Unpacked"
        print(f"{action} {total_extracted} of {len(zips)} archive(s).")
        return 0

    if not args.archive:
        parser.print_help()
        return 1

    target = Path(args.archive)
    if not target.exists():
        print(f"Archive not found: {target}")
        return 1

    landing = unpack_archive(target, config, dry_run=args.dry_run, target_folder=custom_dest)
    if landing is None:
        print(f"Failed to extract {target.name}.")
        return 1

    action = "Would extract to" if args.dry_run else "Extracted to"
    print(f"{action}: {landing}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
