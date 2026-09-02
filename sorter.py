"""Classify and move files sitting at the top of Downloads."""

from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
import os
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
import uuid

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
LOG_DIR = Path(os.environ.get("LOCALAPPDATA", ROOT)) / "download-sorter"
LOG_FILE = LOG_DIR / "sorter.log"
HISTORY_FILE = LOG_DIR / "history.jsonl"
PAUSE_FILE = LOG_DIR / "paused"

def split_folder(folder: str) -> tuple[str, ...]:
    return tuple(part for part in folder.replace("/", "\\").split("\\") if part)


def folder_path(root: Path, folder: str) -> Path:
    return root.joinpath(*split_folder(folder))


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            RotatingFileHandler(LOG_FILE, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def is_paused() -> bool:
    return PAUSE_FILE.exists()


def set_paused(paused: bool) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if paused:
        PAUSE_FILE.touch(exist_ok=True)
    else:
        try:
            PAUSE_FILE.unlink(missing_ok=True)
        except OSError:
            pass


def record_history(src: Path, dest: Path, batch_id: str = "") -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        record = {
            "id": uuid.uuid4().hex,
            "batch_id": batch_id or uuid.uuid4().hex[:8],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "src": str(src.resolve()),
            "dest": str(dest.resolve()),
        }
        with HISTORY_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as exc:
        logging.warning("Failed to write history record: %s", exc)


def is_old_enough(path: Path, min_age_minutes: float) -> bool:
    if min_age_minutes <= 0:
        return True
    try:
        age_seconds = time.time() - path.stat().st_mtime
        return age_seconds >= (min_age_minutes * 60)
    except OSError:
        return False


def expand_path(value: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(value))).resolve()


def load_config() -> dict:
    with CONFIG_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def collect_category_folders(config: dict) -> list[str]:
    folders: list[str] = []
    for rule in config.get("keyword_rules", []):
        folders.append(rule["folder"])
    for rule in config.get("extension_rules", []):
        folders.append(rule["folder"])
    folders.append(config.get("default_folder", "Other"))
    # Keep unique order
    seen: set[str] = set()
    ordered: list[str] = []
    for folder in folders:
        key = folder.lower()
        if key not in seen:
            seen.add(key)
            ordered.append(folder)
    return ordered


def watch_root(config: dict) -> Path:
    return expand_path(config["watch"]["folder"])


def document_roots(config: dict) -> list[Path]:
    downloads = watch_root(config)
    seen: set[Path] = set()
    resolved_roots: list[Path] = []
    for raw in config.get("document_sources", []):
        path = expand_path(raw)
        if not path.exists() or not path.is_dir():
            continue
        resolved = path.resolve()
        if resolved in seen:
            continue
        if resolved == downloads or resolved.is_relative_to(downloads):
            continue
        seen.add(resolved)
        resolved_roots.append(resolved)
    return resolved_roots


def is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root)
        return True
    except ValueError:
        return False


# Windows file attribute constants
FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
FILE_ATTRIBUTE_OFFLINE = 0x00001000
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x00040000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x00400000


def is_cloud_placeholder(path: Path) -> bool:
    """Return True if path is an unhydrated cloud-only file on Windows."""
    try:
        attrs = os.stat(path).st_file_attributes
        mask = (
            FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
            | FILE_ATTRIBUTE_RECALL_ON_OPEN
            | FILE_ATTRIBUTE_OFFLINE
        )
        return bool(attrs & mask)
    except (OSError, AttributeError):
        return False


def is_ignored_directory(path: Path, config: dict) -> bool:
    ignore_dirs = [d.lower() for d in config.get("documents_ignore_dirs", [])]
    for part in path.parts:
        part_lower = part.lower()
        if any(ign in part_lower for ign in ignore_dirs):
            return True
    return False


def should_relocate_from_documents(path: Path, config: dict) -> bool:
    if not path.exists() or not path.is_file():
        return False
    if is_incomplete(path, config):
        return False
    if is_cloud_placeholder(path):
        return False
    if is_ignored_directory(path.parent, config):
        return False
    ext = path.suffix.lower()
    relocate_exts = {item.lower() for item in config.get("documents_relocate_extensions", [])}
    if relocate_exts:
        if ext not in relocate_exts:
            return False
    else:
        keep_exts = {item.lower() for item in config.get("documents_keep_extensions", [])}
        if ext in keep_exts:
            return False
    folder = classify(path, config)
    default_folder = config.get("default_folder", "Other")
    if folder == default_folder:
        return False
    return True


def keep_in_documents(path: Path, config: dict) -> bool:
    return not should_relocate_from_documents(path, config)


def iter_files(root: Path, config: dict | None = None):
    ignore_dirs = [d.lower() for d in config.get("documents_ignore_dirs", [])] if config else []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        if ignore_dirs:
            dirnames[:] = [
                d for d in dirnames
                if not any(ign in d.lower() for ign in ignore_dirs)
            ]
        for name in filenames:
            yield Path(dirpath) / name


def ensure_tree(config: dict) -> Path:
    root = watch_root(config)
    root.mkdir(parents=True, exist_ok=True)
    for folder in collect_category_folders(config):
        folder_path(root, folder).mkdir(parents=True, exist_ok=True)
    return root


def is_incomplete(path: Path, config: dict) -> bool:
    name = path.name.lower()
    ext = path.suffix.lower()
    if ext in {item.lower() for item in config.get("ignore_extensions", [])}:
        return True
    if name in {item.lower() for item in config.get("ignore_names", [])}:
        return True
    # Office temporary lock files: ~$document.docx
    if name.startswith("~$"):
        return True
    # LibreOffice lock files: .~lock.document.docx#
    if name.startswith(".~lock."):
        return True
    # Hidden dot files: .DS_Store, .git, etc.
    if name.startswith(".") and ext != "":
        return True
    return False


def is_managed_path(path: Path, root: Path, folders: list[str]) -> bool:
    try:
        relative = path.resolve().relative_to(root)
    except ValueError:
        return False
    if not relative.parts:
        return True
    top = relative.parts[0]
    return any(top.lower() == split_folder(folder)[0].lower() for folder in folders)


def matches_keyword(name: str, token: str) -> bool:
    token = token.strip()
    if not token:
        return False
    # Word boundary supporting delimiters: start/end, spaces, -, _, ., [], (), etc.
    pattern = rf"(?:^|[^a-zA-Z0-9]){re.escape(token.lower())}(?:[^a-zA-Z0-9]|$)"
    return bool(re.search(pattern, name.lower()))


def classify(path: Path, config: dict) -> str:
    folder, _reason = explain_classification(path, config)
    return folder


def explain_classification(path: Path, config: dict) -> tuple[str, str]:
    name = path.name
    ext = path.suffix.lower()
    for rule in config.get("keyword_rules", []):
        allowed = [item.lower() for item in rule.get("extensions", [])]
        if allowed and ext not in allowed:
            continue
        for token in rule.get("match", []):
            if matches_keyword(name, token):
                return rule["folder"], f"keyword '{token}'"
    for rule in config.get("extension_rules", []):
        if ext in {item.lower() for item in rule.get("extensions", [])}:
            return rule["folder"], f"extension '{ext}'"
    default = config.get("default_folder", "Other")
    return default, "default rule"


def unique_destination(directory: Path, name: str) -> Path:
    candidate = directory / name
    if not candidate.exists():
        return candidate
    stem = Path(name).stem
    suffix = Path(name).suffix
    index = 2
    while True:
        candidate = directory / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def is_file_locked(path: Path) -> bool:
    """Return True if another process holds an exclusive write/share lock on path."""
    try:
        with open(path, "r+b"):
            return False
    except PermissionError:
        return True
    except OSError:
        return False


def wait_until_stable(path: Path, settle_seconds: float) -> bool:
    if is_cloud_placeholder(path):
        return False
    if is_file_locked(path):
        time.sleep(0.25)
    else:
        try:
            age = time.time() - path.stat().st_mtime
            if age >= settle_seconds:
                return True
        except OSError:
            pass

    last_size = -1
    stable_since: float | None = None
    deadline = time.time() + 120
    while time.time() < deadline:
        if not path.exists() or not path.is_file():
            return False
        if is_cloud_placeholder(path):
            return False
        if is_file_locked(path):
            time.sleep(0.5)
            stable_since = None
            continue
        try:
            size = path.stat().st_size
        except OSError:
            time.sleep(0.25)
            continue
        now = time.time()
        if size == last_size:
            if stable_since is None:
                stable_since = now
            elif now - stable_since >= settle_seconds:
                return True
        else:
            last_size = size
            stable_since = None
        time.sleep(0.25)
    return False


def move_file(
    path: Path,
    config: dict,
    dry_run: bool = False,
    batch_id: str = "",
    check_age: bool = True,
    source_root: Path | None = None,
) -> Path | None:
    downloads = watch_root(config)
    from_root = source_root or downloads
    folders = collect_category_folders(config)
    if not path.exists() or not path.is_file():
        return None
    if is_incomplete(path, config):
        return None
    if is_cloud_placeholder(path):
        return None
    if is_managed_path(path, from_root, folders) and path.parent != from_root:
        return None
    if path.parent != from_root:
        return None

    if check_age:
        min_age = float(config.get("watch", {}).get("min_age_minutes", 0))
        if not is_old_enough(path, min_age):
            return None

    settle = float(config.get("watch", {}).get("settle_seconds", 2))
    if not dry_run and not wait_until_stable(path, settle):
        logging.warning("Gave up waiting for %s to finish writing", path.name)
        return None

    folder, reason = explain_classification(path, config)
    destination_dir = folder_path(downloads, folder)
    destination = unique_destination(destination_dir, path.name)

    if dry_run:
        logging.info("[DRY-RUN] Would move %s -> %s (%s)", path.name, destination.relative_to(downloads), reason)
        return destination

    destination_dir.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(path), str(destination))
    except OSError as exc:
        logging.warning("Could not move %s: %s", path.name, exc)
        return None
    record_history(path, destination, batch_id)
    logging.info("Moved %s -> %s", path.name, destination.relative_to(downloads))
    return destination


def relocate_from_documents(
    path: Path,
    config: dict,
    dry_run: bool = False,
    batch_id: str = "",
) -> Path | None:
    downloads = watch_root(config)
    if not should_relocate_from_documents(path, config):
        return None
    if is_under(path, downloads):
        return None
    if not any(is_under(path, source) for source in document_roots(config)):
        return None

    settle = float(config.get("watch", {}).get("settle_seconds", 2))
    if not dry_run and not wait_until_stable(path, settle):
        logging.warning("Gave up waiting for %s to finish writing", path.name)
        return None

    folder, reason = explain_classification(path, config)
    if folder == config.get("default_folder", "Other"):
        return None
    destination_dir = folder_path(downloads, folder)
    destination = unique_destination(destination_dir, path.name)

    if dry_run:
        logging.info(
            "[DRY-RUN] Would move from Documents %s -> %s (%s)",
            path.name,
            destination.relative_to(downloads),
            reason,
        )
        return destination

    destination_dir.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(path), str(destination))
    except OSError as exc:
        logging.warning("Could not move %s: %s", path.name, exc)
        return None
    record_history(path, destination, batch_id)
    logging.info(
        "Moved from Documents %s -> %s",
        path.name,
        destination.relative_to(downloads),
    )
    return destination


def sort_existing(
    config: dict,
    dry_run: bool = False,
    batch_id: str = "",
    target_dir: Path | None = None,
) -> int:
    downloads = watch_root(config)
    if not dry_run:
        ensure_tree(config)
    root = target_dir.resolve() if target_dir else downloads
    if not root.exists():
        return 0
    moved = 0
    for item in list(root.iterdir()):
        if item.is_file():
            if move_file(item, config, dry_run=dry_run, batch_id=batch_id, check_age=False, source_root=root):
                moved += 1
    return moved


def sort_existing_documents(config: dict, dry_run: bool = False, batch_id: str = "") -> int:
    if not dry_run:
        ensure_tree(config)
    moved = 0
    for source in document_roots(config):
        logging.info("Scanning Documents source %s", source)
        for item in iter_files(source, config):
            if relocate_from_documents(item, config, dry_run=dry_run, batch_id=batch_id):
                moved += 1
    return moved
