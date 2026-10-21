"""Classify and move files sitting at the top of Downloads."""

from __future__ import annotations

import copy
from collections import deque
from dataclasses import dataclass
import json
import logging
from logging.handlers import RotatingFileHandler
import math
import os
import re
import shutil
import sys
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
FAILURE_FILE = LOG_DIR / "failures.json"
ACTIVE_PROJECT_FILE = LOG_DIR / "active_project.json"
RUNTIME_STATE_FILE = LOG_DIR / "runtime_state.json"
SERVICE_DISABLED_FILE = LOG_DIR / "service_disabled"

# All 27 CAD / 3D / CNC extensions captured by Active Project catch-all
CAD_EXTENSIONS: set[str] = {
    ".stl", ".3mf", ".obj", ".step", ".stp", ".iges", ".igs",
    ".f3d", ".f3z", ".sldprt", ".sldasm", ".ipt", ".iam",
    ".dwg", ".dxf", ".blend", ".fbx", ".glb", ".gltf", ".3ds",
    ".nc", ".tap", ".ngc", ".gcode", ".bgcode", ".gco", ".g",
}

# Ambiguous toolpath extensions evaluated by Smart G-code Machine Sniffer
GCODE_SNIFFER_EXTENSIONS: set[str] = {
    ".nc", ".tap", ".ngc", ".gcode", ".bgcode", ".gco", ".g",
}

DEFAULT_CONFIG: dict = {
    "watch": {
        "folder": "${USERPROFILE}\\Downloads",
        "settle_seconds": 1.5,
        "min_age_minutes": 0,
        "auto_unpack_3d_zips": False,
        "auto_unzip": True,
        "extracted_destination": "Extracted",
    },
    "downloads_dir": "${USERPROFILE}\\Downloads",
    "auto_unpack_3d_zips": False,
    "auto_unzip": True,
    "extracted_destination": "Extracted",
    "gcode_sniffer_enabled": True,
    "haas_destination": "Code\\Haas",
    "gcode_destination": "Code\\Gcode",
    "document_sources": [
        "${USERPROFILE}\\Documents",
        "${USERPROFILE}\\OneDrive\\Documents",
    ],
    "documents_keep_extensions": [
        ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".xlsm", ".csv", ".odt", ".ods", ".rtf",
    ],
    "documents_relocate_extensions": [
        ".stl", ".3mf", ".obj", ".step", ".stp", ".iges", ".igs",
        ".gcode", ".bgcode", ".3ds", ".fbx", ".blend", ".glb", ".gltf",
        ".f3d", ".f3z", ".ipt", ".iam", ".sldprt", ".sldasm", ".slddrw",
        ".dwg", ".dxf", ".nc", ".tap", ".ngc",
    ],
    "documents_ignore_dirs": [
        "Sorter Recovery", "CAD & 3D Projects", "Arduino", "KiCad", "MAXON",
        ".git", "node_modules", "My Games", "Saved Games", "Steam",
        "Electronic Arts", "EA Games", "Paradox Interactive", "Rockstar Games",
        "Adobe", "PowerToys", "SOLIDCast", "SOLIDCast Projects",
    ],
    "ignore_extensions": [
        ".crdownload", ".part", ".partial", ".tmp", ".download", ".opdownload",
    ],
    "ignore_names": [
        "desktop.ini", "thumbs.db",
    ],
    "keyword_rules": [
        {
            "name": "IME 3311 Coursework",
            "folder": "Documents\\School\\IME 3311",
            "match": [
                "ime3311", "ime 3311", "ime-3311", "solidcast",
                "casting congress", "metal casting", "foundry",
            ],
            "extensions": [".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".txt", ".rtf", ".odt"],
            "enabled": True,
        },
        {
            "name": "School Documents",
            "folder": "Documents\\School",
            "match": [
                "transcript", "homework", "assignment", "syllabus",
                "calpoly", "cal poly", "pomona",
                "me3210", "me 3210", "exam", "review",
            ],
            "extensions": [".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt"],
            "enabled": True,
        },
    ],
    "extension_rules": [
        {"name": "3D STL", "folder": "3D Models\\STL", "extensions": [".stl"], "enabled": True},
        {"name": "3D 3MF", "folder": "3D Models\\3MF", "extensions": [".3mf"], "enabled": True},
        {"name": "3D OBJ", "folder": "3D Models\\OBJ", "extensions": [".obj"], "enabled": True},
        {"name": "3D STEP", "folder": "3D Models\\STEP", "extensions": [".step", ".stp", ".iges", ".igs"], "enabled": True},
        {"name": "3D Other", "folder": "3D Models\\Other", "extensions": [".3ds", ".fbx", ".blend", ".glb", ".gltf"], "enabled": True},
        {"name": "3D CAD", "folder": "3D Models\\CAD", "extensions": [".f3d", ".f3z", ".ipt", ".iam", ".sldprt", ".sldasm", ".slddrw", ".dwg", ".dxf"], "enabled": True},
        {"name": "Haas / G-Code", "folder": "Code\\Gcode", "extensions": [".nc", ".tap", ".ngc", ".gcode", ".bgcode"], "enabled": True},
        {"name": "PDF Documents", "folder": "Documents\\PDFs", "extensions": [".pdf"], "enabled": True},
        {"name": "Word Documents", "folder": "Documents\\Word", "extensions": [".doc", ".docx", ".odt", ".rtf"], "enabled": True},
        {"name": "Text Documents", "folder": "Documents\\Text", "extensions": [".txt", ".md"], "enabled": True},
        {"name": "Spreadsheets", "folder": "Spreadsheets", "extensions": [".xls", ".xlsx", ".csv", ".ods"], "enabled": True},
        {"name": "Images", "folder": "Images", "extensions": [".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".svg", ".bmp", ".tif", ".tiff"], "enabled": True},
        {"name": "Videos", "folder": "Videos", "extensions": [".mp4", ".mov", ".mkv", ".avi", ".webm"], "enabled": True},
        {"name": "Audio", "folder": "Audio", "extensions": [".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg"], "enabled": True},
        {"name": "Archives", "folder": "Archives", "extensions": [".zip", ".rar", ".7z", ".tar", ".gz"], "enabled": True},
        {"name": "Installers", "folder": "Installers", "extensions": [".exe", ".msi", ".msix"], "enabled": True},
        {"name": "Code", "folder": "Code", "extensions": [".py", ".js", ".ts", ".cpp", ".h", ".cs", ".json", ".html", ".css"], "enabled": True},
    ],
    "default_folder": "Other",
}


@dataclass(frozen=True)
class RoutingContext:
    """Normalized routing data reused by the long-running watcher."""

    ignore_extensions: frozenset[str]
    ignore_names: frozenset[str]
    document_relocate_extensions: frozenset[str]
    document_keep_extensions: frozenset[str]
    document_ignore_dirs: tuple[str, ...]
    category_folders: frozenset[str]
    keyword_extensions: tuple[frozenset[str], ...]
    extension_sets: tuple[frozenset[str], ...]
    active_project: dict | None = None


def compile_routing(config: dict, active_project: dict | None = None) -> RoutingContext:
    """Compile case-normalized rule data once for repeated classifications."""
    keyword_rules = config.get("keyword_rules", []) if isinstance(config, dict) else []
    extension_rules = config.get("extension_rules", []) if isinstance(config, dict) else []
    return RoutingContext(
        ignore_extensions=frozenset(str(item).lower() for item in config.get("ignore_extensions", [])),
        ignore_names=frozenset(str(item).lower() for item in config.get("ignore_names", [])),
        document_relocate_extensions=frozenset(
            str(item).lower() for item in config.get("documents_relocate_extensions", [])
        ),
        document_keep_extensions=frozenset(
            str(item).lower() for item in config.get("documents_keep_extensions", [])
        ),
        document_ignore_dirs=tuple(str(item).lower() for item in config.get("documents_ignore_dirs", [])),
        category_folders=frozenset(
            folder.replace("/", "\\").lower() for folder in collect_category_folders(config)
        ),
        keyword_extensions=tuple(
            frozenset(str(item).lower() for item in rule.get("extensions", []))
            for rule in keyword_rules
        ),
        extension_sets=tuple(
            frozenset(str(item).lower() for item in rule.get("extensions", []))
            for rule in extension_rules
        ),
        active_project=copy.deepcopy(active_project) if active_project else None,
    )


def get_default_config() -> dict:
    """Return a fresh copy of the default configuration dictionary."""
    return copy.deepcopy(DEFAULT_CONFIG)


def load_active_project(path: Path | None = None) -> dict | None:
    """Read and validate active project state from %LOCALAPPDATA%\\download-sorter\\active_project.json.
    Returns None if absent, disabled, or expired.
    """
    target = path or ACTIVE_PROJECT_FILE
    if not target.exists():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        is_enabled = data.get("enabled", data.get("active", False))
        if not is_enabled:
            return None

        target_dir = (
            data.get("target_directory")
            or data.get("folder_path")
            or data.get("target_folder")
        )
        if not target_dir or not str(target_dir).strip():
            return None

        expires_at = data.get("expires_at")
        if expires_at is not None:
            try:
                exp_val = float(expires_at)
                if math.isnan(exp_val) or exp_val < 0 or (exp_val > 0 and time.time() >= exp_val):
                    # Auto-expired: de-activate and persist
                    try:
                        data["enabled"] = False
                        data["active"] = False
                        target.write_text(json.dumps(data, indent=2), encoding="utf-8")
                    except OSError:
                        pass
                    return None
            except (ValueError, TypeError):
                return None
        return data
    except Exception:
        return None


# How many times a single file may fail to move before it is left alone.
MAX_MOVE_ATTEMPTS = 2

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


def write_runtime_state(status: str, detail: str = "") -> None:
    """Atomically persist a low-frequency lifecycle/heartbeat snapshot."""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc).isoformat()
        started_at = now
        if RUNTIME_STATE_FILE.exists():
            try:
                previous = json.loads(RUNTIME_STATE_FILE.read_text(encoding="utf-8"))
                if previous.get("pid") == os.getpid() and previous.get("started_at"):
                    started_at = previous["started_at"]
            except (OSError, json.JSONDecodeError, AttributeError):
                pass
        record = {
            "status": status,
            "pid": os.getpid(),
            "started_at": started_at,
            "updated_at": now,
            "detail": detail,
        }
        temporary = RUNTIME_STATE_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(record, indent=2), encoding="utf-8")
        os.replace(temporary, RUNTIME_STATE_FILE)
    except OSError as exc:
        logging.debug("Could not write runtime state: %s", exc)


class IncrementalHistoryReader:
    """Read only newly appended JSONL records after the initial scan."""

    def __init__(self, path: Path, recent_capacity: int = 100) -> None:
        self.path = path
        self.recent_capacity = recent_capacity
        self._offset = 0
        self._mtime_ns = 0
        self._local_date = datetime.now().astimezone().date()
        self._moves_today = 0
        self._recent: deque[dict] = deque(maxlen=recent_capacity)

    def _reset(self) -> None:
        self._offset = 0
        self._moves_today = 0
        self._recent.clear()

    def refresh(self) -> None:
        today = datetime.now().astimezone().date()
        if today != self._local_date:
            self._local_date = today
            self._reset()
        try:
            stat = self.path.stat()
        except OSError:
            self._reset()
            self._mtime_ns = 0
            return

        if stat.st_size < self._offset or (
            stat.st_size == self._offset
            and self._offset > 0
            and self._mtime_ns
            and stat.st_mtime_ns != self._mtime_ns
        ):
            self._reset()
        if stat.st_size == self._offset:
            self._mtime_ns = stat.st_mtime_ns
            return

        try:
            with self.path.open("rb") as handle:
                handle.seek(self._offset)
                data = handle.read()
        except OSError:
            return

        complete_end = data.rfind(b"\n")
        if complete_end < 0:
            return
        complete = data[: complete_end + 1]
        self._offset += len(complete)
        self._mtime_ns = stat.st_mtime_ns
        for raw_line in complete.splitlines():
            try:
                record = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(record, dict):
                continue
            self._recent.append(record)
            if record.get("action") == "undo":
                continue
            try:
                when = datetime.fromisoformat(str(record.get("timestamp", ""))).astimezone().date()
            except (TypeError, ValueError):
                continue
            if when == self._local_date:
                self._moves_today += 1

    def moves_today(self) -> int:
        self.refresh()
        return self._moves_today

    def recent(self, limit: int = 5) -> list[dict]:
        self.refresh()
        if limit <= 0:
            return []
        return list(reversed(self._recent))[:limit]


_failure_cache: dict | None = None


def load_failures() -> dict:
    """Files that could not be moved, so we stop retrying them every sweep."""
    global _failure_cache
    if _failure_cache is None:
        try:
            with FAILURE_FILE.open(encoding="utf-8") as handle:
                data = json.load(handle)
            _failure_cache = data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            _failure_cache = {}
    return _failure_cache


def save_failures() -> None:
    if _failure_cache is None:
        return
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with FAILURE_FILE.open("w", encoding="utf-8") as handle:
            json.dump(_failure_cache, handle, indent=2)
    except OSError as exc:
        logging.debug("Could not save failure list: %s", exc)


def failure_key(path: Path) -> str:
    try:
        return str(path.resolve()).lower()
    except OSError:
        return str(path).lower()


def file_signature(path: Path) -> str:
    """Cheap fingerprint so an edited file gets a fresh set of attempts."""
    try:
        stat = path.stat()
        return f"{stat.st_size}:{int(stat.st_mtime)}"
    except OSError:
        return ""


def is_quarantined(path: Path) -> bool:
    entry = load_failures().get(failure_key(path))
    if not entry:
        return False
    if entry.get("signature") != file_signature(path):
        clear_failure(path)
        return False
    return int(entry.get("count", 0)) >= MAX_MOVE_ATTEMPTS


def note_failure(path: Path, exc: Exception) -> int:
    failures = load_failures()
    key = failure_key(path)
    entry = failures.get(key) or {}
    if entry.get("signature") != file_signature(path):
        entry = {}
    count = int(entry.get("count", 0)) + 1
    failures[key] = {
        "path": str(path),
        "count": count,
        "error": str(exc),
        "signature": file_signature(path),
        "last_seen": datetime.now(timezone.utc).isoformat(),
    }
    save_failures()
    return count


def clear_failure(path: Path) -> None:
    failures = load_failures()
    if failures.pop(failure_key(path), None) is not None:
        save_failures()


def record_move_failure(path: Path, exc: Exception) -> None:
    """Log a failed move once, then stop retrying it on every future sweep."""
    count = note_failure(path, exc)
    if isinstance(exc, OSError) and is_cloud_error(exc):
        reason = "OneDrive owns this file and is not running"
    else:
        reason = str(exc)
    if count >= MAX_MOVE_ATTEMPTS:
        logging.warning(
            "Could not move %s: %s -- leaving it alone until it changes", path, reason
        )
    else:
        logging.warning("Could not move %s: %s", path, reason)


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
    if not CONFIG_PATH.exists():
        logging.info("Config file %s does not exist; using default config", CONFIG_PATH)
        return get_default_config()
    try:
        with CONFIG_PATH.open(encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            return data
        logging.warning("Config at %s is not a dictionary; using default config", CONFIG_PATH)
        return get_default_config()
    except (OSError, json.JSONDecodeError, Exception) as exc:
        logging.warning("Failed loading config from %s (%s); using default config", CONFIG_PATH, exc)
        return get_default_config()


def collect_category_folders(config: dict) -> list[str]:
    folders: list[str] = []
    for rule in config.get("keyword_rules", []):
        folders.append(rule["folder"])
    for rule in config.get("extension_rules", []):
        folders.append(rule["folder"])
    if "haas_destination" in config:
        folders.append(config["haas_destination"])
    if "gcode_destination" in config:
        folders.append(config["gcode_destination"])
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
    folder = ""
    if isinstance(config, dict):
        watch_sec = config.get("watch")
        if isinstance(watch_sec, dict):
            folder = watch_sec.get("folder", "")
        if not folder:
            folder = config.get("downloads_dir", "")
    if not folder:
        folder = "${USERPROFILE}\\Downloads" if sys.platform == "win32" else "~/Downloads"
    resolved = expand_path(folder)
    if not resolved.exists() and sys.platform == "win32":
        try:
            import winreg
            sub_key = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub_key) as key:
                val, _ = winreg.QueryValueEx(key, "{374DE290-123F-4565-9164-39C4925E467B}")
                reg_path = Path(os.path.expandvars(val)).resolve()
                if reg_path.exists():
                    return reg_path
        except Exception:
            pass
    return resolved


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


# Errors Windows raises for files owned by a cloud sync provider (OneDrive) that
# is stopped, paused, or otherwise unable to hydrate the file right now.
CLOUD_PROVIDER_ERRNOS = {362, 363, 364, 365, 366, 367, 368, 369, 370, 371, 372,
                         373, 374, 375, 376, 377, 378, 379, 380, 381, 382, 383,
                         384, 385, 386, 387, 388, 389, 390, 391, 392, 393, 394,
                         395, 396, 397, 398, 399}


def is_cloud_error(exc: OSError) -> bool:
    """Return True if exc is Windows telling us a cloud provider owns the file."""
    return getattr(exc, "winerror", None) in CLOUD_PROVIDER_ERRNOS


def is_cloud_placeholder(path: Path) -> bool:
    """Return True if path is an unhydrated cloud-only file on Windows.

    When OneDrive is paused or not running, os.stat raises instead of reporting
    attributes. Anything we cannot inspect is treated as untouchable: guessing
    wrong means moving a file the sync engine still owns.
    """
    try:
        attrs = os.stat(path).st_file_attributes
    except AttributeError:
        return False
    except OSError:
        return True
    mask = (
        FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
        | FILE_ATTRIBUTE_RECALL_ON_OPEN
        | FILE_ATTRIBUTE_OFFLINE
    )
    return bool(attrs & mask)


def is_ignored_directory(
    path: Path,
    config: dict,
    routing_context: RoutingContext | None = None,
) -> bool:
    ignore_dirs = (
        routing_context.document_ignore_dirs
        if routing_context is not None
        else tuple(str(d).lower() for d in config.get("documents_ignore_dirs", []))
    )
    for part in path.parts:
        part_lower = part.lower()
        if any(ign in part_lower for ign in ignore_dirs):
            return True
    return False


def should_relocate_from_documents(
    path: Path,
    config: dict,
    routing_context: RoutingContext | None = None,
) -> bool:
    if not path.exists() or not path.is_file():
        return False
    if is_incomplete(path, config, routing_context):
        return False
    if is_cloud_placeholder(path):
        return False
    if is_quarantined(path):
        return False
    if is_ignored_directory(path.parent, config, routing_context):
        return False
    ext = path.suffix.lower()
    relocate_exts = (
        routing_context.document_relocate_extensions
        if routing_context is not None
        else {str(item).lower() for item in config.get("documents_relocate_extensions", [])}
    )
    if relocate_exts:
        if ext not in relocate_exts:
            return False
    else:
        keep_exts = (
            routing_context.document_keep_extensions
            if routing_context is not None
            else {str(item).lower() for item in config.get("documents_keep_extensions", [])}
        )
        if ext in keep_exts:
            return False
    folder = classify(path, config, routing_context=routing_context)
    default_folder = config.get("default_folder", "Other")
    if folder == default_folder:
        return False
    return True


def keep_in_documents(path: Path, config: dict) -> bool:
    return not should_relocate_from_documents(path, config)


def iter_files(
    root: Path,
    config: dict | None = None,
    routing_context: RoutingContext | None = None,
):
    ignore_dirs = (
        routing_context.document_ignore_dirs
        if routing_context is not None
        else tuple(str(d).lower() for d in config.get("documents_ignore_dirs", [])) if config else ()
    )
    relocate_exts = (
        routing_context.document_relocate_extensions
        if routing_context is not None
        else frozenset(str(item).lower() for item in config.get("documents_relocate_extensions", []))
        if config else frozenset()
    )
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        if ignore_dirs:
            dirnames[:] = [
                d for d in dirnames
                if not any(ign in d.lower() for ign in ignore_dirs)
            ]
        for name in filenames:
            # A non-empty relocation allowlist lets us reject the overwhelming
            # majority of Documents entries without constructing Paths or
            # touching file metadata/cloud state.
            if relocate_exts and Path(name).suffix.lower() not in relocate_exts:
                continue
            yield Path(dirpath) / name


def ensure_tree(config: dict) -> Path:
    root = watch_root(config)
    root.mkdir(parents=True, exist_ok=True)
    for folder in collect_category_folders(config):
        folder_path(root, folder).mkdir(parents=True, exist_ok=True)
    return root


def is_incomplete(
    path: Path,
    config: dict,
    routing_context: RoutingContext | None = None,
) -> bool:
    name = path.name.lower()
    ext = path.suffix.lower()
    ignore_extensions = (
        routing_context.ignore_extensions
        if routing_context is not None
        else {str(item).lower() for item in config.get("ignore_extensions", [])}
    )
    ignore_names = (
        routing_context.ignore_names
        if routing_context is not None
        else {str(item).lower() for item in config.get("ignore_names", [])}
    )
    if ext in ignore_extensions:
        return True
    if name in ignore_names:
        return True
    # Office temporary lock files: ~$document.docx or ~document.docx
    if name.startswith("~$") or name.startswith("~"):
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


def evaluate_routing(
    filename: str,
    config: dict,
    active_project: dict | None = None,
    file_path: Path | None = None,
    content_sample: str | None = None,
    routing_context: RoutingContext | None = None,
) -> dict:
    """Evaluate routing chain for a file according to strict precedence:
    0. Incomplete / ignored check
    1. Active Project catch-all (R3): matches 27 CAD/3D/CNC extensions to target_directory;
       non-CAD extensions (e.g. university documents, spreadsheets) bypass active project
    2. Keyword rules (R1): checks enabled flag, extension whitelist, word boundary token match
    3. Smart G-code Machine Sniffer (R4): inspects content of .nc, .tap, .gcode, .bgcode to
       route Haas CNC to haas_destination and 3D printer slicers to gcode_destination
    4. Extension rules (R1): checks enabled flag, extension match
    5. Fallback default

    Returns dict matching contract:
    {
        "destination": str,
        "match_type": str,
        "winning_rule": str,
        "matched_trigger": str,
        "is_active_project": bool,
        "sniffer_details": dict | None,
        "rule_trace": list
    }
    """
    rule_trace: list[dict] = []
    p = Path(filename)
    ext = p.suffix.lower()
    name_lower = p.name.lower()

    # --- Tier 0: Incomplete / Ignored Check ---
    ignore_exts = (
        routing_context.ignore_extensions
        if routing_context is not None
        else {str(e).lower() for e in config.get("ignore_extensions", [])}
    )
    ignore_names = (
        routing_context.ignore_names
        if routing_context is not None
        else {str(n).lower() for n in config.get("ignore_names", [])}
    )
    if ext in ignore_exts:
        rule_trace.append({"tier": "ignore_check", "status": "matched", "reason": f"Extension '{ext}' in ignore_extensions"})
        return {
            "destination": "",
            "match_type": "ignored",
            "winning_rule": "Ignore Rule (Extension)",
            "matched_trigger": f"ignored extension '{ext}'",
            "is_active_project": False,
            "sniffer_details": None,
            "rule_trace": rule_trace,
        }
    if name_lower in ignore_names:
        rule_trace.append({"tier": "ignore_check", "status": "matched", "reason": f"Filename '{p.name}' in ignore_names"})
        return {
            "destination": "",
            "match_type": "ignored",
            "winning_rule": "Ignore Rule (Name)",
            "matched_trigger": f"ignored filename '{p.name}'",
            "is_active_project": False,
            "sniffer_details": None,
            "rule_trace": rule_trace,
        }
    if name_lower.startswith("~$") or name_lower.startswith("~") or name_lower.startswith(".~lock.") or (name_lower.startswith(".") and ext != ""):
        rule_trace.append({"tier": "ignore_check", "status": "matched", "reason": "Temporary/hidden lock file"})
        return {
            "destination": "",
            "match_type": "ignored",
            "winning_rule": "Ignore Rule (Lock File)",
            "matched_trigger": "temporary/hidden lock file",
            "is_active_project": False,
            "sniffer_details": None,
            "rule_trace": rule_trace,
        }

    # --- Tier 1: Active Project Catch-All (R3) ---
    active_proj: dict | None = None
    if active_project is not None:
        if isinstance(active_project, dict):
            is_en = active_project.get("enabled", active_project.get("active", False))
            exp = active_project.get("expires_at")
            is_expired = False
            if exp is not None:
                try:
                    exp_val = float(exp)
                    if math.isnan(exp_val) or exp_val < 0 or (exp_val > 0 and time.time() >= exp_val):
                        is_expired = True
                except (ValueError, TypeError):
                    is_expired = True
            if is_en and not is_expired:
                active_proj = active_project
            else:
                rule_trace.append({"tier": "active_project", "status": "inactive_or_expired"})
        else:
            rule_trace.append({"tier": "active_project", "status": "invalid_type"})
    elif routing_context is not None:
        active_proj = routing_context.active_project
    else:
        active_proj = load_active_project()

    if active_proj:
        target_dir = (
            active_proj.get("target_directory")
            or active_proj.get("folder_path")
            or active_proj.get("target_folder")
            or ""
        )
        if not target_dir or not str(target_dir).strip():
            rule_trace.append({
                "tier": "active_project",
                "status": "invalid_target_directory",
                "reason": "Missing or empty target directory",
            })
            active_proj = None

    if active_proj:
        raw_cad_exts = active_proj.get("cad_extensions")
        if raw_cad_exts is None or not isinstance(raw_cad_exts, (list, tuple, set)):
            project_cad_exts = set(CAD_EXTENSIONS)
        elif not all(isinstance(e, str) for e in raw_cad_exts):
            project_cad_exts = set(CAD_EXTENSIONS)
        else:
            project_cad_exts = {e.lower() for e in raw_cad_exts}

        proj_name = active_proj.get("project_name") or active_proj.get("name", "Active Project")
        clean_target_dir = str(target_dir).strip()
        if ext in project_cad_exts:
            rule_trace.append({
                "tier": "active_project",
                "status": "matched",
                "project_name": proj_name,
                "extension": ext,
                "destination": clean_target_dir,
            })
            return {
                "destination": clean_target_dir,
                "match_type": "active_project",
                "winning_rule": proj_name,
                "matched_trigger": f"active project '{proj_name}' (cad extension '{ext}')",
                "is_active_project": True,
                "sniffer_details": None,
                "rule_trace": rule_trace,
            }
        else:
            rule_trace.append({
                "tier": "active_project",
                "status": "bypassed",
                "reason": f"Non-CAD extension '{ext}' bypassed active project",
            })
    else:
        rule_trace.append({"tier": "active_project", "status": "disabled_or_none"})

    # --- Tier 2: Keyword Rules (R1) ---
    for rule_index, rule in enumerate(config.get("keyword_rules", [])):
        rule_name = rule.get("name") or rule.get("folder", "Keyword Rule")
        if not rule.get("enabled", True):
            rule_trace.append({"tier": "keyword", "rule": rule_name, "status": "skipped_disabled"})
            continue

        allowed = (
            routing_context.keyword_extensions[rule_index]
            if routing_context is not None and rule_index < len(routing_context.keyword_extensions)
            else [str(item).lower() for item in rule.get("extensions", [])]
        )
        if allowed and ext not in allowed:
            rule_trace.append({"tier": "keyword", "rule": rule_name, "status": "extension_mismatch"})
            continue

        matched_token: str | None = None
        for token in rule.get("match", []):
            if matches_keyword(filename, token):
                matched_token = token
                break

        if matched_token:
            dest_folder = rule.get("folder", "")
            rule_trace.append({
                "tier": "keyword",
                "rule": rule_name,
                "status": "matched",
                "token": matched_token,
                "destination": dest_folder,
            })
            return {
                "destination": dest_folder,
                "match_type": "keyword",
                "winning_rule": rule_name,
                "matched_trigger": f"keyword '{matched_token}'",
                "is_active_project": False,
                "sniffer_details": None,
                "rule_trace": rule_trace,
            }
        else:
            rule_trace.append({"tier": "keyword", "rule": rule_name, "status": "no_keyword_match"})

    # --- Tier 3: Smart G-code Machine Sniffer (R4) ---
    sniffer_enabled = config.get("gcode_sniffer_enabled", config.get("sniffer", {}).get("enabled", True))
    sniffer_details: dict | None = None

    if sniffer_enabled and ext in GCODE_SNIFFER_EXTENSIONS:
        sample_to_sniff = None
        if content_sample is not None:
            sample_to_sniff = content_sample
        elif file_path is not None:
            p_file = Path(file_path)
            if p_file.exists() and p_file.is_file():
                sample_to_sniff = p_file

        if sample_to_sniff is not None:
            try:
                from gcode_peek import sniff_machine_type
                sniff_res = sniff_machine_type(sample_to_sniff)
                sniffer_details = sniff_res
                if sniff_res.get("is_haas"):
                    haas_dest = config.get("haas_destination", config.get("sniffer", {}).get("haas_folder", "Code\\Haas"))
                    rule_trace.append({
                        "tier": "gcode_sniffer",
                        "status": "matched",
                        "machine_type": "haas_cnc",
                        "signals": sniff_res.get("matched_signals", []),
                        "destination": haas_dest,
                    })
                    return {
                        "destination": haas_dest,
                        "match_type": "gcode_sniffer",
                        "winning_rule": "Smart G-code Sniffer (Haas CNC)",
                        "matched_trigger": f"Haas CNC ({sniff_res.get('description', '')})",
                        "is_active_project": False,
                        "sniffer_details": sniffer_details,
                        "rule_trace": rule_trace,
                    }
                elif sniff_res.get("is_3d_printer"):
                    gcode_dest = config.get("gcode_destination", config.get("sniffer", {}).get("printer_folder", "Code\\Gcode"))
                    rule_trace.append({
                        "tier": "gcode_sniffer",
                        "status": "matched",
                        "machine_type": "3d_printer",
                        "signals": sniff_res.get("matched_signals", []),
                        "destination": gcode_dest,
                    })
                    return {
                        "destination": gcode_dest,
                        "match_type": "gcode_sniffer",
                        "winning_rule": "Smart G-code Sniffer (3D Printer)",
                        "matched_trigger": f"3D Printer ({sniff_res.get('description', '')})",
                        "is_active_project": False,
                        "sniffer_details": sniffer_details,
                        "rule_trace": rule_trace,
                    }
                else:
                    rule_trace.append({"tier": "gcode_sniffer", "status": "ambiguous_or_generic", "detail": sniff_res.get("description", "")})
            except Exception as exc:
                logging.warning("G-code sniffer error: %s", exc)
                rule_trace.append({
                    "tier": "gcode_sniffer",
                    "status": "error_fallback",
                    "detail": f"Sniffer error: {exc}",
                })
        else:
            rule_trace.append({"tier": "gcode_sniffer", "status": "skipped_no_sample"})
    else:
        if not sniffer_enabled:
            rule_trace.append({"tier": "gcode_sniffer", "status": "disabled"})
        else:
            rule_trace.append({"tier": "gcode_sniffer", "status": "extension_not_gcode"})

    # --- Tier 4: Extension Rules (R1) ---
    for rule_index, rule in enumerate(config.get("extension_rules", [])):
        rule_name = rule.get("name") or rule.get("folder", "Extension Rule")
        if not rule.get("enabled", True):
            rule_trace.append({"tier": "extension", "rule": rule_name, "status": "skipped_disabled"})
            continue

        rule_exts = (
            routing_context.extension_sets[rule_index]
            if routing_context is not None and rule_index < len(routing_context.extension_sets)
            else {str(item).lower() for item in rule.get("extensions", [])}
        )
        if ext in rule_exts:
            dest_folder = rule.get("folder", "")
            rule_trace.append({
                "tier": "extension",
                "rule": rule_name,
                "status": "matched",
                "extension": ext,
                "destination": dest_folder,
            })
            return {
                "destination": dest_folder,
                "match_type": "extension",
                "winning_rule": rule_name,
                "matched_trigger": f"extension '{ext}'",
                "is_active_project": False,
                "sniffer_details": sniffer_details,
                "rule_trace": rule_trace,
            }
        else:
            rule_trace.append({"tier": "extension", "rule": rule_name, "status": "mismatch"})

    # --- Tier 5: Fallback Default ---
    default = config.get("default_folder", "Other")
    rule_trace.append({"tier": "fallback", "status": "matched", "destination": default})
    return {
        "destination": default,
        "match_type": "fallback",
        "winning_rule": "Default Fallback",
        "matched_trigger": "default rule",
        "is_active_project": False,
        "sniffer_details": sniffer_details,
        "rule_trace": rule_trace,
    }


def classify(
    path: Path,
    config: dict,
    routing_context: RoutingContext | None = None,
) -> str:
    routing = evaluate_routing(
        path.name,
        config,
        file_path=path,
        routing_context=routing_context,
    )
    return routing["destination"]


def explain_classification(
    path: Path,
    config: dict,
    routing_context: RoutingContext | None = None,
) -> tuple[str, str]:
    routing = evaluate_routing(
        path.name,
        config,
        file_path=path,
        routing_context=routing_context,
    )
    return routing["destination"], routing["matched_trigger"]


def are_files_identical(src: Path, target: Path) -> bool:
    """Compare file size and content to detect exact duplicate downloads."""
    try:
        if not src.is_file() or not target.is_file():
            return False
        if src.stat().st_size != target.stat().st_size:
            return False
        with open(src, "rb") as f1, open(target, "rb") as f2:
            while chunk1 := f1.read(65536):
                chunk2 = f2.read(65536)
                if chunk1 != chunk2:
                    return False
        return True
    except OSError:
        return False


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
    if sys.platform == "win32":
        import ctypes
        GENERIC_READ = 0x80000000
        GENERIC_WRITE = 0x40000000
        OPEN_EXISTING = 3
        FILE_SHARE_NONE = 0
        INVALID_HANDLE = ctypes.c_void_p(-1).value
        try:
            handle = ctypes.windll.kernel32.CreateFileW(
                str(path.resolve()),
                GENERIC_READ | GENERIC_WRITE,
                FILE_SHARE_NONE,
                None,
                OPEN_EXISTING,
                0,
                None,
            )
            if handle == INVALID_HANDLE or handle == -1:
                err = ctypes.windll.kernel32.GetLastError()
                if err in (5, 32, 33):  # Access denied, Sharing violation, Lock violation
                    return True
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return False
        except Exception:
            return False
    try:
        with open(path, "r+b"):
            return False
    except PermissionError:
        return True
    except OSError:
        return False


def is_file_ready_to_move(path: Path, settle_seconds: float) -> bool:
    """Non-blocking check: True only if file is stable and not actively being written."""
    if not path.exists() or not path.is_file():
        return False
    if is_cloud_placeholder(path):
        return False
    if is_file_locked(path):
        return False
    try:
        age = time.time() - path.stat().st_mtime
        return age >= settle_seconds
    except OSError:
        return False


def move_file(
    path: Path,
    config: dict,
    dry_run: bool = False,
    batch_id: str = "",
    check_age: bool = True,
    source_root: Path | None = None,
    routing_context: RoutingContext | None = None,
) -> Path | None:
    downloads = watch_root(config)
    from_root = source_root or downloads
    folders = collect_category_folders(config)
    if not path.exists() or not path.is_file():
        return None
    if is_incomplete(path, config, routing_context):
        return None
    if is_cloud_placeholder(path):
        return None
    if is_quarantined(path):
        return None
    if is_managed_path(path, from_root, folders) and path.parent != from_root:
        return None
    if path.parent != from_root:
        return None

    if check_age:
        min_age = float(config.get("watch", {}).get("min_age_minutes", 0))
        if not is_old_enough(path, min_age):
            return None

    settle = float(config.get("watch", {}).get("settle_seconds", 1.5))
    if not dry_run and not is_file_ready_to_move(path, settle):
        return None

    routing = evaluate_routing(
        path.name,
        config,
        file_path=path,
        routing_context=routing_context,
    )
    if routing["match_type"] == "ignored":
        return None

    if routing["is_active_project"] or Path(routing["destination"]).is_absolute():
        destination_dir = Path(routing["destination"])
    else:
        destination_dir = folder_path(downloads, routing["destination"])

    reason = routing["matched_trigger"]
    primary_target = destination_dir / path.name

    # Intelligent Deduplication: if exact duplicate already exists in target folder, prune redundant download
    if primary_target.exists() and are_files_identical(path, primary_target):
        if dry_run:
            logging.info("[DRY-RUN] Exact duplicate already exists in %s; would delete download %s", destination_dir, path.name)
            return primary_target
        try:
            path.unlink()
            logging.info("Exact duplicate already exists at %s; removed download %s", primary_target, path.name)
            return primary_target
        except OSError as exc:
            logging.warning("Could not remove redundant duplicate %s: %s", path.name, exc)

    destination = unique_destination(destination_dir, path.name)

    auto_unpack = config.get("watch", {}).get("auto_unpack_3d_zips", config.get("auto_unpack_3d_zips", False))
    if path.suffix.lower() == ".zip" and auto_unpack:
        try:
            from unzip_3d import is_3d_archive, unpack_3d_archive
            model_files = is_3d_archive(path)
            if model_files:
                if dry_run:
                    logging.info("[DRY-RUN] Would auto-unpack %d 3D model(s) from %s into %s", len(model_files), path.name, destination_dir)
                    return destination
                active_proj_arg = load_active_project() if routing["is_active_project"] else None
                extracted = unpack_3d_archive(path, config, active_project=active_proj_arg)
                if extracted:
                    logging.info("Auto-unpacked %d 3D model file(s) from %s", len(extracted), path.name)
                    record_history(path, extracted[0], batch_id)
                    return extracted[0]
        except Exception as exc:
            logging.warning("Auto-unpack check failed for %s: %s", path.name, exc)

    auto_unzip = config.get("watch", {}).get("auto_unzip", config.get("auto_unzip", True))
    if path.suffix.lower() == ".zip" and auto_unzip:
        try:
            from unzip_archive import unpack_archive
            if dry_run:
                extracted_dir_name = config.get("extracted_destination", "Extracted")
                sim_dest = folder_path(downloads, f"{extracted_dir_name}\\{path.stem}")
                logging.info("[DRY-RUN] Would auto-unzip archive %s into %s", path.name, sim_dest)
                return sim_dest
            active_proj_arg = load_active_project() if routing["is_active_project"] else None
            landing_folder = unpack_archive(path, config, active_project=active_proj_arg)
            if landing_folder:
                logging.info("Auto-unzipped archive %s -> %s", path.name, landing_folder)
                record_history(path, landing_folder, batch_id)
                return landing_folder
        except Exception as exc:
            logging.warning("Auto-unzip failed for %s: %s", path.name, exc)

    dest_display = destination.relative_to(downloads) if is_under(destination, downloads) else str(destination)
    if dry_run:
        logging.info("[DRY-RUN] Would move %s -> %s (%s)", path.name, dest_display, reason)
        return destination

    destination_dir.mkdir(parents=True, exist_ok=True)
    source = path.resolve()
    try:
        shutil.move(str(path), str(destination))
    except OSError as exc:
        record_move_failure(path, exc)
        return None
    clear_failure(path)
    record_history(source, destination, batch_id)
    logging.info("Moved %s -> %s (%s)", source, dest_display, reason)
    return destination


def relocate_from_documents(
    path: Path,
    config: dict,
    dry_run: bool = False,
    batch_id: str = "",
    routing_context: RoutingContext | None = None,
) -> Path | None:
    downloads = watch_root(config)
    if not should_relocate_from_documents(path, config, routing_context):
        return None
    if is_under(path, downloads):
        return None
    if not any(is_under(path, source) for source in document_roots(config)):
        return None

    settle = float(config.get("watch", {}).get("settle_seconds", 1.5))
    if not dry_run and not is_file_ready_to_move(path, settle):
        return None

    folder, reason = explain_classification(path, config, routing_context)
    if folder == config.get("default_folder", "Other"):
        return None
    if Path(folder).is_absolute():
        destination_dir = Path(folder)
    else:
        destination_dir = folder_path(downloads, folder)
    destination = unique_destination(destination_dir, path.name)

    dest_display = destination.relative_to(downloads) if is_under(destination, downloads) else str(destination)
    if dry_run:
        logging.info(
            "[DRY-RUN] Would move from Documents %s -> %s (%s)",
            path.name,
            dest_display,
            reason,
        )
        return destination

    destination_dir.mkdir(parents=True, exist_ok=True)
    source = path.resolve()
    try:
        shutil.move(str(path), str(destination))
    except OSError as exc:
        record_move_failure(path, exc)
        return None
    clear_failure(path)
    record_history(source, destination, batch_id)
    logging.info(
        "Moved from Documents %s -> %s (%s)",
        source,
        dest_display,
        reason,
    )
    return destination


def sort_existing(
    config: dict,
    dry_run: bool = False,
    batch_id: str = "",
    target_dir: Path | None = None,
    routing_context: RoutingContext | None = None,
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
            if move_file(
                item,
                config,
                dry_run=dry_run,
                batch_id=batch_id,
                check_age=False,
                source_root=root,
                routing_context=routing_context,
            ):
                moved += 1
    return moved


def sort_existing_documents(
    config: dict,
    dry_run: bool = False,
    batch_id: str = "",
    routing_context: RoutingContext | None = None,
) -> int:
    if not dry_run:
        ensure_tree(config)
    moved = 0
    for source in document_roots(config):
        logging.info("Scanning Documents source %s", source)
        for item in iter_files(source, config, routing_context):
            if relocate_from_documents(
                item,
                config,
                dry_run=dry_run,
                batch_id=batch_id,
                routing_context=routing_context,
            ):
                moved += 1
    return moved


def reclassify_misplaced_file(
    path: Path,
    config: dict,
    dry_run: bool = False,
    batch_id: str = "",
    routing_context: RoutingContext | None = None,
) -> Path | None:
    """Auto-correct a file saved or exported directly into the wrong managed category bucket."""
    downloads = watch_root(config)
    if not path.exists() or not path.is_file():
        return None
    if is_incomplete(path, config, routing_context):
        return None
    if is_cloud_placeholder(path):
        return None
    if is_quarantined(path):
        return None

    try:
        rel = path.resolve().relative_to(downloads)
    except ValueError:
        return None

    # Determine current folder relative to Downloads
    parent_rel = path.parent.resolve().relative_to(downloads)
    current_folder = str(parent_rel).replace("/", "\\")

    target_folder, reason = explain_classification(path, config, routing_context)
    target_folder = target_folder.replace("/", "\\")

    # Check if current parent is a managed category folder
    category_folders = (
        routing_context.category_folders
        if routing_context is not None
        else [f.replace("/", "\\").lower() for f in collect_category_folders(config)]
    )
    default_folder = config.get("default_folder", "Other").lower()

    if current_folder.lower() not in category_folders:
        # If a recognized file landed directly in an unmanaged subfolder (e.g. browser remembered an old folder), rescue it!
        if len(rel.parts) == 2 and target_folder.lower() != default_folder:
            pass
        else:
            return None

    # If already in the right folder, nothing to do
    if current_folder.lower() == target_folder.lower():
        return None

    settle = float(config.get("watch", {}).get("settle_seconds", 1.5))
    if not dry_run and not is_file_ready_to_move(path, settle):
        return None

    if Path(target_folder).is_absolute():
        destination_dir = Path(target_folder)
    else:
        destination_dir = folder_path(downloads, target_folder)
    destination = unique_destination(destination_dir, path.name)

    dest_display = destination.relative_to(downloads) if is_under(destination, downloads) else str(destination)
    if dry_run:
        logging.info("[DRY-RUN] Would reclassify %s -> %s (%s)", path.name, dest_display, reason)
        return destination

    destination_dir.mkdir(parents=True, exist_ok=True)
    source = path.resolve()
    try:
        shutil.move(str(source), str(destination))
    except OSError as exc:
        record_move_failure(path, exc)
        return None

    clear_failure(path)
    record_history(source, destination, batch_id)
    logging.info("Auto-corrected misplaced file: %s from '%s' -> '%s'", source.name, current_folder, dest_display)
    return destination


def sort_existing_misplaced(
    config: dict,
    dry_run: bool = False,
    batch_id: str = "",
    routing_context: RoutingContext | None = None,
) -> int:
    """Sweep direct children of all managed category folders for misplaced files."""
    downloads = watch_root(config)
    category_folders = collect_category_folders(config)
    moved = 0
    for rel_folder in category_folders:
        folder = folder_path(downloads, rel_folder)
        if folder.exists() and folder.is_dir():
            for item in list(folder.iterdir()):
                if item.is_file():
                    if reclassify_misplaced_file(
                        item,
                        config,
                        dry_run=dry_run,
                        batch_id=batch_id,
                        routing_context=routing_context,
                    ):
                        moved += 1
    return moved


