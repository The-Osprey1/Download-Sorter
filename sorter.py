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
import threading
from datetime import datetime, timezone
from pathlib import Path
import uuid

ROOT = Path(__file__).resolve().parent
LOG_DIR = Path(os.environ.get("LOCALAPPDATA", ROOT)) / "download-sorter"
DEFAULT_CONFIG_PATH = ROOT / "config.json"
USER_CONFIG_PATH = LOG_DIR / "config.json"
CONFIG_PATH = USER_CONFIG_PATH
LOG_FILE = LOG_DIR / "sorter.log"
HISTORY_FILE = LOG_DIR / "history.jsonl"
PAUSE_FILE = LOG_DIR / "paused"
FAILURE_FILE = LOG_DIR / "failures.json"
ACTIVE_PROJECT_FILE = LOG_DIR / "active_project.json"
RUNTIME_STATE_FILE = LOG_DIR / "runtime_state.json"
SERVICE_DISABLED_FILE = LOG_DIR / "service_disabled"

# History records whose "action" is one of these are bookkeeping, not user-visible moves:
# they are never counted as "files filed today" and Undo never offers to reverse them.
NON_MOVE_ACTIONS = frozenset({
    "undo", "planned", "aborted", "needs_review", "aux_move", "link", "extract",
})

# Default minimum quiet time before a file is considered finished being written.
DEFAULT_SETTLE_SECONDS = 3.0

MAKERWORLD_FOLDER = "3D Models\\MakerWorld"

# Release 2.x feature blocks (schema_version 2).  Every block carries its own
# on/off switch so any feature can be disabled without touching the others.
V2_DEFAULTS: dict = {
    "schema_version": 2,
    "batch_handoff": {
        "enabled": True,
        "gap_seconds": 25,
        "max_span_seconds": 120,
        "max_files": 50,
        "hotkey_latest": "Ctrl+G",
        "hotkey_all": "Ctrl+Shift+G",
        "hotkey_copy": "Ctrl+Shift+C",
        "stage_copy_limit_mb": 200,
        "stage_prune_hours": 24,
    },
    "hold": {
        "enabled": True,
        "extensions": [".3mf"],
        "hold_days": 7,
        "max_held": 15,
        "slicers": ["bambu-studio.exe", "orca-slicer.exe", "prusa-slicer.exe"],
    },
    "mirror": {"enabled": False},
    # Learning from how the user organizes files: it keeps score of its guesses and,
    # after 3 similar moves, asks before adding a rule. It never moves files itself.
    "learning": {"enabled": True, "suggest": True},
    # Optional: move installers and zips nobody needed for a while to the
    # Recycle Bin, once a day (clean_old.py). Off unless the user turns it on.
    "cleanup": {"enabled": False, "days": 30, "folders": ["Installers", "Archives"]},
    # Optional extras, all off unless the user turns them on in Settings:
    # pictures into a folder per month, rule words found inside PDFs, and
    # tidier file names (Undo always puts the old name back).
    "photos": {"by_month": False},
    "pdf_text": {"enabled": False, "max_mb": 10},
    "rename": {"tidy": False, "add_date": False},
    # Once a day, ask GitHub which version is newest (see update_check.py).
    "update_check": {"enabled": True},
    "remodel": {
        "enabled": True,
        "quarantine_days": 30,
        "active_folder_days": 14,
        "desktop_min_age_days": 30,
    },
    "makerworld_destination": MAKERWORLD_FOLDER,
    # Second tier: inside a main folder, each kind of file gets its own folder
    # (Documents\PDF holds only PDFs). "names" changes or adds the folder for an
    # extension ({".pdf": "PDFs"}); an empty name keeps that kind in the main folder.
    "type_folders": {"enabled": True, "names": {}},
    # A folder you make in Downloads, or inside a main folder, collects new files
    # named after it: 3D Models\Flanged Mace takes "flanged_mace_v3.f3d". Nothing
    # is ever taken out of a folder you made. "skip" lists folders that should
    # not collect files ("3D Models\Old").
    "my_folders": {"enabled": True, "skip": []},
}

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

# Documents folders scanned for stray 3D/CAD files when the maker profile is chosen.
MAKER_DOCUMENT_SOURCES: list[str] = [
    "${USERPROFILE}\\Documents",
    "${USERPROFILE}\\OneDrive\\Documents",
]

DEFAULT_CONFIG: dict = {
    "watch": {
        "folder": "${USERPROFILE}\\Downloads",
        "settle_seconds": 3.0,
        "min_age_minutes": 0,
        "auto_unpack_3d_zips": False,
        "auto_unzip": False,
        "extracted_destination": "Extracted",
    },
    "downloads_dir": "${USERPROFILE}\\Downloads",
    "auto_unpack_3d_zips": False,
    "auto_unzip": False,
    "extracted_destination": "Extracted",
    "gcode_sniffer_enabled": True,
    "haas_destination": "3D Models",
    "gcode_destination": "3D Models",
    # Moving 3D/CAD files out of Documents is a maker feature: it is off until the
    # user picks the "3D Printing, CAD & CNC Maker" profile (see MAKER_DOCUMENT_SOURCES).
    "document_sources": [],
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
    # Keyword rules are personal; the welcome wizard adds a starter set for the
    # profile the user picks.  Out of the box, files are sorted by extension only.
    "keyword_rules": [],
    "extension_rules": [
        {
            "name": "Documents",
            "folder": "Documents",
            "extensions": [
                ".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".md",
                ".xls", ".xlsx", ".xlsm", ".csv", ".ods",
                ".ppt", ".pptx", ".odp", ".key",
                ".epub", ".mobi", ".tex", ".bib", ".pkpass",
            ],
            "enabled": True,
        },
        {
            "name": "3D Models & Print Files",
            "folder": "3D Models",
            "extensions": [
                ".stl", ".3mf", ".obj",
                ".3ds", ".fbx", ".blend", ".glb", ".gltf",
                ".gcode", ".bgcode", ".nc", ".tap", ".ngc", ".gco", ".g",
            ],
            "enabled": True,
        },
        {
            "name": "CAD & Engineering",
            "folder": "CAD",
            "extensions": [
                ".step", ".stp", ".iges", ".igs",
                ".f3d", ".f3z", ".ipt", ".iam", ".sldprt", ".sldasm", ".slddrw", ".dwg", ".dxf",
            ],
            "enabled": True,
        },
        {
            "name": "Images",
            "folder": "Images",
            "extensions": [
                ".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".svg", ".bmp", ".tif", ".tiff",
                ".ico", ".ai", ".eps", ".cdr",
            ],
            "enabled": True,
        },
        {
            "name": "Videos",
            "folder": "Videos",
            "extensions": [
                ".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv", ".wmv", ".m4v",
            ],
            "enabled": True,
        },
        {
            "name": "Audio",
            "folder": "Audio",
            "extensions": [
                ".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".aiff", ".wma",
            ],
            "enabled": True,
        },
        {
            "name": "Archives",
            "folder": "Archives",
            "extensions": [
                ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".tgz", ".iso",
            ],
            "enabled": True,
        },
        {
            "name": "Installers",
            "folder": "Installers",
            "extensions": [
                ".exe", ".msi", ".msix",
            ],
            "enabled": True,
        },
        {
            "name": "Code & Data",
            "folder": "Code",
            "extensions": [
                ".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css",
                ".cpp", ".c", ".h", ".hpp", ".cs", ".java", ".rs", ".go",
                ".json", ".yaml", ".yml", ".toml", ".xml", ".ini", ".env",
                ".sql", ".db", ".sqlite", ".parquet", ".feather",
                ".log", ".dmp", ".out",
            ],
            "enabled": True,
        },
    ],
    "default_folder": "Other",
}
DEFAULT_CONFIG.update(copy.deepcopy(V2_DEFAULTS))


def apply_v2_defaults(config: dict) -> dict:
    """Fill any missing schema-v2 blocks in place; never overwrites user values.

    Unknown keys are preserved untouched.  A legacy config that still carries the old
    1.5 s default settle time is moved to the new 3 s default.
    """
    if not isinstance(config, dict):
        return config
    legacy = config.get("schema_version") is None
    # Learning used to be an off-by-default preview. Settings saved before it
    # could suggest rules (no "suggest" key yet) get it switched on once; after
    # that the user's choice in Settings is kept.
    learning_block = config.get("learning")
    if isinstance(learning_block, dict) and "suggest" not in learning_block:
        learning_block["enabled"] = True
    for key, default in V2_DEFAULTS.items():
        if key not in config:
            config[key] = copy.deepcopy(default)
        elif isinstance(default, dict) and isinstance(config[key], dict):
            for sub_key, sub_default in default.items():
                config[key].setdefault(sub_key, copy.deepcopy(sub_default))
    if legacy:
        config["schema_version"] = 2
        watch_block = config.get("watch")
        if isinstance(watch_block, dict) and watch_block.get("settle_seconds") in (None, 1.5):
            watch_block["settle_seconds"] = DEFAULT_SETTLE_SECONDS
    return config


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
            folder.replace("/", "\\").lower() for folder in collect_managed_folders(config)
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


def record_extraction(archive: Path, outputs: list[Path], batch_id: str = "") -> None:
    """Note what an archive was extracted to.

    This is bookkeeping, not a move: the zip's own trip to Archives is recorded as a
    normal (undoable) move by safe_ops, and Undo restores the zip from there.  The
    extracted copies are left in place for the user to keep or delete.
    """
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        record = {
            "id": uuid.uuid4().hex,
            "action": "extract",
            "batch_id": batch_id or uuid.uuid4().hex[:8],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "archive": str(Path(archive)),
            "outputs": [str(Path(o)) for o in outputs],
        }
        with HISTORY_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as exc:
        logging.warning("Failed to write extraction record: %s", exc)


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
            if record.get("action") in NON_MOVE_ACTIONS:
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


# ---------------------------------------------------------------------------
# Files put back by Undo
# ---------------------------------------------------------------------------
# When Undo restores a file to Downloads (or Documents), the watcher sees it arrive
# and would sort it straight back out again.  A restored file is therefore left
# where it is until it changes (edited, renamed or replaced), exactly like the
# move-failure list above.

def _restored_file() -> Path:
    return Path(LOG_DIR) / "restored_files.json"


def _load_restored() -> dict:
    try:
        with _restored_file().open(encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_restored(data: dict) -> None:
    target = _restored_file()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(f"{target.name}.{uuid.uuid4().hex}.tmp")
        temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(temp, target)
    except OSError as exc:
        logging.debug("Could not save restored-file list: %s", exc)


def remember_restored(path: Path) -> None:
    """Mark a file Undo just put back so the sorter leaves it alone until it changes."""
    path = Path(path)
    data = {k: v for k, v in _load_restored().items() if Path(v.get("path", "")).exists()}
    if path.is_file():
        data[failure_key(path)] = {
            "path": str(path),
            "signature": file_signature(path),
            "restored_at": datetime.now(timezone.utc).isoformat(),
        }
    elif path.is_dir():
        for child in path.rglob("*"):
            if child.is_file():
                data[failure_key(child)] = {
                    "path": str(child),
                    "signature": file_signature(child),
                    "restored_at": datetime.now(timezone.utc).isoformat(),
                }
    _save_restored(data)


def remember_user_placed(path: Path) -> None:
    """Mark a file the user moved into a folder themselves; the sorter won't move it again.

    Unlike a file Undo put back, this stays in force when the file is edited: the
    user chose the folder, not the contents. It ends when the file is moved away.
    """
    path = Path(path)
    if not path.is_file():
        return
    data = {k: v for k, v in _load_restored().items() if Path(v.get("path", "")).exists()}
    data[failure_key(path)] = {
        "path": str(path),
        "signature": file_signature(path),
        "reason": "placed_by_you",
        "restored_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_restored(data)


def is_restored_by_undo(path: Path) -> bool:
    """True while the sorter must leave ``path`` alone.

    That's a file Undo put back (until it changes), or a file the user moved into
    a folder themselves (for as long as it stays there).
    """
    data = _load_restored()
    if not data:
        return False
    key = failure_key(Path(path))
    entry = data.get(key)
    if not entry:
        return False
    if entry.get("reason") == "placed_by_you" and Path(path).is_file():
        return True
    if entry.get("signature") == file_signature(Path(path)):
        return True
    data.pop(key, None)
    _save_restored(data)
    return False


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
    target = CONFIG_PATH
    if not target.exists():
        if target == USER_CONFIG_PATH and DEFAULT_CONFIG_PATH.exists():
            try:
                with DEFAULT_CONFIG_PATH.open(encoding="utf-8") as handle:
                    data = json.load(handle)
                if isinstance(data, dict):
                    try:
                        LOG_DIR.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(DEFAULT_CONFIG_PATH, USER_CONFIG_PATH)
                        logging.info("Initialized user config from %s to %s", DEFAULT_CONFIG_PATH, USER_CONFIG_PATH)
                    except OSError:
                        pass
                    return apply_v2_defaults(data)
            except Exception as exc:
                logging.warning("Failed loading template config from %s (%s)", DEFAULT_CONFIG_PATH, exc)
        logging.info("Config file %s does not exist; using default config", target)
        return get_default_config()
    try:
        with target.open(encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            return apply_v2_defaults(data)
        logging.warning("Config at %s is not a dictionary; using default config", target)
        return get_default_config()
    except (OSError, json.JSONDecodeError, Exception) as exc:
        logging.warning("Failed loading config from %s (%s); using default config", target, exc)
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
    folders.append(config.get("makerworld_destination") or MAKERWORLD_FOLDER)
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
    # Slicer / editor temp saves (e.g. model.3mf.tmp) - shared with safe_ops
    import safe_ops
    if safe_ops.is_temp_save(name):
        return True
    return False


def is_keep_protected(path: Path) -> bool:
    """True when path sits under a folder carrying a .download-sorter-keep marker."""
    import safe_ops
    return safe_ops.keep_marker_dir(path) is not None


def same_folder(a: Path | str, b: Path | str) -> bool:
    """True when ``a`` and ``b`` are the same folder, however each is spelled.

    A folder can be reached by more than one spelling: a short 8.3 name
    (C:\\Users\\RUNNER~1) and its long name, a linked folder, a mapped drive,
    different letter case on Windows. Comparing the paths as written treats those
    as different folders.
    """
    try:
        return os.path.samefile(a, b)
    except (OSError, ValueError):
        pass
    try:
        return Path(a).resolve() == Path(b).resolve()
    except (OSError, ValueError):
        return Path(a) == Path(b)


def is_managed_path(path: Path, root: Path, folders: list[str]) -> bool:
    try:
        relative = path.resolve().relative_to(Path(root).resolve())
    except (ValueError, OSError):
        return False
    if not relative.parts:
        return True
    top = relative.parts[0]
    return any(top.lower() == split_folder(folder)[0].lower() for folder in folders)


def sanitize_folder_segment(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]', '_', str(name or "")).strip(" .")
    return cleaned if cleaned else "Unknown"


def matches_keyword(name: str, token: str) -> bool:
    token = token.strip()
    if not token:
        return False
    # Whole words only: the keyword must sit between the start/end of the name or
    # a non-alphanumeric character (space, -, _, ., brackets...).  Inside a
    # multi-word keyword any separator works, or none, so "bank_statement"
    # matches "Bank Statement.pdf", "bank-statement.pdf" and "BankStatement.pdf".
    parts = [part for part in re.split(r"[\s_\-.]+", token.lower()) if part]
    if not parts:
        return False
    body = r"[^a-z0-9]*".join(re.escape(part) for part in parts)
    pattern = rf"(?:^|[^a-z0-9]){body}(?:[^a-z0-9]|$)"
    return bool(re.search(pattern, name.lower()))


# ---------------------------------------------------------------------------
# Optional extras (Settings > Extra options), all off by default
# ---------------------------------------------------------------------------

MONTH_FOLDER = re.compile(r"^\d{4}-\d{2}$")
_COPY_MARK = re.compile(r"(?:\s*-\s*copy)?(?:\s*\(\d{1,3}\))+$|\s*-\s*copy$", re.IGNORECASE)
_DATE_IN_FRONT = re.compile(r"^\d{4}-\d{2}-\d{2}[ _-]")
# Never renamed: other files point to these by name (CAD assemblies, code
# imports, slicer projects, web pages), or the name is the program's own.
NO_RENAME_EXTENSIONS = frozenset({
    ".stl", ".3mf", ".obj", ".step", ".stp", ".iges", ".igs", ".f3d", ".f3z", ".sldprt",
    ".sldasm", ".slddrw", ".ipt", ".iam", ".dwg", ".dxf", ".blend", ".gcode", ".bgcode",
    ".gco", ".nc", ".tap", ".ngc", ".py", ".ipynb", ".js", ".ts", ".html", ".htm", ".css",
    ".json", ".xml", ".yml", ".yaml", ".toml", ".c", ".cpp", ".h", ".hpp", ".java", ".cs",
    ".go", ".rs", ".sh", ".bat", ".ps1", ".exe", ".msi", ".msix", ".appx", ".dll", ".torrent",
})


def _option(config: dict, block: str, key: str) -> bool:
    value = config.get(block) if isinstance(config, dict) else None
    return isinstance(value, dict) and bool(value.get(key))


# ---------------------------------------------------------------------------
# Folder layout: main folder > kind of file, plus the folders you make yourself
# ---------------------------------------------------------------------------
# Downloads
#   Documents
#     PDF              <- second tier: only PDFs
#   3D Models
#     STL
#     Flanged Mace     <- a folder you made: files named after it land here
#   Flanged Mace       <- or keep the project at the top of Downloads

# Second tier. Kinds of file that are rare stay in the main folder.
DEFAULT_TYPE_FOLDERS: dict[str, tuple[str, ...]] = {
    # Documents
    "PDF": (".pdf",),
    "Word": (".doc", ".docx", ".odt", ".rtf"),
    "Spreadsheets": (".xls", ".xlsx", ".xlsm", ".csv", ".ods"),
    "Presentations": (".ppt", ".pptx", ".odp", ".key"),
    "Text": (".txt", ".md", ".tex", ".bib"),
    "Ebooks": (".epub", ".mobi"),
    # 3D Models
    "STL": (".stl",),
    "3MF": (".3mf",),
    "OBJ": (".obj",),
    "Blender": (".blend",),
    "G-code": (".gcode", ".bgcode", ".gco", ".g", ".nc", ".tap", ".ngc"),
    # CAD
    "STEP": (".step", ".stp"),
    "IGES": (".iges", ".igs"),
    "Fusion 360": (".f3d", ".f3z"),
    "Inventor": (".ipt", ".iam"),
    "SolidWorks": (".sldprt", ".sldasm", ".slddrw"),
    "AutoCAD": (".dwg", ".dxf"),
    # Images
    "JPG": (".jpg", ".jpeg"),
    "PNG": (".png",),
    "GIF": (".gif",),
    "WEBP": (".webp",),
    "HEIC": (".heic", ".heif"),
    "TIFF": (".tif", ".tiff"),
    "Vector": (".svg", ".ai", ".eps", ".cdr"),
    # Videos
    "MP4": (".mp4", ".m4v"),
    "MOV": (".mov",),
    "MKV": (".mkv",),
    # Audio
    "MP3": (".mp3",),
    "M4A": (".m4a",),
    "WAV": (".wav",),
    "FLAC": (".flac",),
    # Code
    "Python": (".py",),
    "Web": (".js", ".ts", ".jsx", ".tsx", ".html", ".css"),
    "C and C++": (".c", ".cpp", ".h", ".hpp"),
    "Data": (".json", ".yaml", ".yml", ".toml", ".xml", ".sql", ".db", ".sqlite", ".parquet", ".feather"),
    "Logs": (".log", ".dmp", ".out"),
}
_DEFAULT_TYPE_BY_EXT: dict[str, str] = {
    ext: name for name, exts in DEFAULT_TYPE_FOLDERS.items() for ext in exts
}
# Toolpaths the G-code sniffer recognises as Haas CNC programs.
HAAS_TYPE_FOLDER = "CNC"

# A folder with one of these names says nothing about which files belong in it,
# so it never collects files by name ("New folder", "Old", "PDFs", "Parts").
GENERIC_FOLDER_WORDS = frozenset({
    "new", "folder", "old", "misc", "stuff", "temp", "tmp", "other", "others", "file",
    "files", "download", "downloads", "backup", "backups", "copy", "copies", "untitled",
    "test", "tests", "sorted", "unsorted", "random", "various", "junk", "final", "draft",
    "drafts", "export", "exports", "import", "imports", "project", "projects", "work",
    "part", "parts", "print", "prints", "printed", "model", "models", "doc", "docs",
    "document", "documents", "image", "images", "picture", "pictures", "photo", "photos",
    "video", "videos", "music", "audio", "zip", "zips", "archive", "archives", "installer",
    "installers", "data", "code", "pdf", "pdfs", "word", "stl", "stls", "3mf", "step",
    "cad", "gcode", "spreadsheet", "spreadsheets", "presentation", "presentations", "text",
    "ebook", "ebooks", "book", "books", "song", "songs", "movie", "movies", "game", "games",
    "the", "and", "my",
})
MIN_FOLDER_NAME_LETTERS = 4


def type_folders_enabled(config: dict) -> bool:
    """On unless switched off: settings saved before this option existed get the new layout."""
    block = config.get("type_folders") if isinstance(config, dict) else None
    return not isinstance(block, dict) or bool(block.get("enabled", True))


def type_folder_for(ext: str, config: dict) -> str:
    """The second-tier folder for a file extension ("PDF" for ".pdf"), or ''."""
    ext = str(ext or "").lower()
    block = config.get("type_folders") if isinstance(config, dict) else None
    names = block.get("names") if isinstance(block, dict) else None
    if isinstance(names, dict) and names:
        for key, value in names.items():
            if str(key).lower() == ext:
                value = str(value or "").strip()
                return sanitize_folder_segment(value) if value else ""
    return _DEFAULT_TYPE_BY_EXT.get(ext, "")


def with_type_folder(folder: str, ext: str, config: dict, name: str | None = None) -> str:
    """``folder`` plus its second tier: "Documents" -> "Documents\\PDF" for a PDF.

    Only a main folder (one name, inside Downloads) gets a second tier. A rule
    that already points deeper ("Documents\\Invoices"), or at a folder outside
    Downloads, is used exactly as written.
    """
    folder = str(folder or "")
    if not folder or not type_folders_enabled(config):
        return folder
    if len(split_folder(folder)) != 1 or Path(folder).is_absolute() or ":" in folder:
        return folder
    sub = name or type_folder_for(ext, config)
    return f"{split_folder(folder)[0]}\\{sub}" if sub else folder


def collect_managed_folders(config: dict) -> list[str]:
    """Every folder Download Sorter itself files into: the main folders and rule
    folders, plus the type folders inside the main folders.

    These are the only folders it ever corrects a misplaced file in. Any other
    folder was made by the user and is left as it is.
    """
    folders = list(collect_category_folders(config))
    if type_folders_enabled(config):
        for rule in config.get("extension_rules", []):
            if not isinstance(rule, dict):
                continue
            base = str(rule.get("folder") or "")
            for ext in rule.get("extensions", []) or []:
                folders.append(with_type_folder(base, str(ext), config))
        gcode_dest = str(config.get("gcode_destination") or "")
        for ext in GCODE_SNIFFER_EXTENSIONS:
            folders.append(with_type_folder(gcode_dest, ext, config))
        folders.append(with_type_folder(str(config.get("haas_destination") or ""), "", config, HAAS_TYPE_FOLDER))
    seen: set[str] = set()
    ordered: list[str] = []
    for folder in folders:
        key = str(folder).replace("/", "\\").strip("\\").lower()
        if key and key not in seen:
            seen.add(key)
            ordered.append(str(folder))
    return ordered


def my_folders_enabled(config: dict) -> bool:
    block = config.get("my_folders") if isinstance(config, dict) else None
    return not isinstance(block, dict) or bool(block.get("enabled", True))


def folder_collects_by_name(name: str) -> bool:
    """True when a folder's name is specific enough to collect files named after it."""
    words = [w for w in re.split(r"[^a-z0-9]+", str(name).lower()) if w]
    letters = "".join(words)
    if len(letters) < MIN_FOLDER_NAME_LETTERS or not any(c.isalpha() for c in letters):
        return False
    if MONTH_FOLDER.match(str(name).strip()):
        return False
    return not all(word in GENERIC_FOLDER_WORDS or word.isdigit() for word in words)


def _subfolders(folder: Path) -> list[os.DirEntry]:
    try:
        with os.scandir(folder) as entries:
            return [e for e in entries if e.is_dir(follow_symlinks=False)]
    except OSError:
        return []


def _has_only_subfolders(folder: str) -> bool:
    """True for a folder holding folders but no files of its own."""
    try:
        with os.scandir(folder) as entries:
            kinds = [e.is_dir(follow_symlinks=False) for e in entries]
    except OSError:
        return False
    return bool(kinds) and all(kinds)


_folder_snapshot = threading.local()


class reuse_my_folders:
    """Read the user's folders once for a whole sweep instead of once per file.

    ``with reuse_my_folders(): ...`` around a loop that sorts many files.
    """

    def __enter__(self) -> "reuse_my_folders":
        self._outer = getattr(_folder_snapshot, "cache", None)
        if self._outer is None:
            _folder_snapshot.cache = {}
        return self

    def __exit__(self, *_exc) -> None:
        if self._outer is None:
            _folder_snapshot.cache = None


def my_folders(config: dict, root: Path | None = None) -> list[dict]:
    """The folders the user made: at the top of Downloads, and directly inside a main folder.

    Each is {"label": "3D Models\\Flanged Mace", "name": "Flanged Mace",
    "category": "3D Models" or None, "extensions": frozenset or None}. A folder
    inside a main folder only takes that main folder's kinds of file (and a
    folder in 3D Models or CAD takes both, so one project keeps its .f3d, .step
    and .stl together); a folder at the top of Downloads takes any kind.
    """
    if not isinstance(config, dict) or not my_folders_enabled(config):
        return []
    try:
        base = Path(root) if root is not None else watch_root(config)
    except (OSError, ValueError, TypeError):
        return []
    cache = getattr(_folder_snapshot, "cache", None)
    cache_key = (str(base), id(config))
    if cache is not None and cache_key in cache:
        return cache[cache_key]

    def key_of(label: str) -> str:
        return str(label).replace("/", "\\").strip("\\").lower()

    try:
        managed = {key_of(f) for f in collect_managed_folders(config)}
    except (KeyError, AttributeError, TypeError):
        managed = set()
    managed_tops = {key.split("\\")[0] for key in managed if key and ":" not in key}
    extracted = key_of(str(config.get("extracted_destination") or "Extracted"))
    block = config.get("my_folders") if isinstance(config.get("my_folders"), dict) else {}
    skipped = {key_of(item) for item in (block.get("skip") or [])}
    makerworld = key_of(str(config.get("makerworld_destination") or MAKERWORLD_FOLDER))

    def usable(entry: os.DirEntry, label: str) -> bool:
        name = entry.name
        if name.startswith((".", "_", "~")) or key_of(label) in skipped:
            return False
        if not folder_collects_by_name(name):
            return False
        try:
            return not os.path.exists(os.path.join(entry.path, ".download-sorter-keep"))
        except OSError:
            return False

    # Main folders: which kinds of file each one holds.
    category_exts: dict[str, set[str]] = {}
    category_names: dict[str, str] = {}
    for rule in config.get("extension_rules", []):
        if not isinstance(rule, dict) or not rule.get("enabled", True):
            continue
        parts = split_folder(str(rule.get("folder") or ""))
        if len(parts) != 1 or ":" in parts[0]:
            continue
        top = parts[0].lower()
        category_names.setdefault(top, parts[0])
        category_exts.setdefault(top, set()).update(str(e).lower() for e in rule.get("extensions", []) or [])
    for top, exts in category_exts.items():
        if exts & CAD_EXTENSIONS:
            exts.update(CAD_EXTENSIONS)
            exts.update({".slddrw"})

    found: list[dict] = []
    for entry in _subfolders(base):
        low = entry.name.lower()
        if low in managed_tops or low in INTERNAL_DIR_NAMES or low == extracted.split("\\")[0]:
            continue
        if usable(entry, entry.name):
            found.append({"label": entry.name, "name": entry.name, "category": None, "extensions": None})
    for top, exts in category_exts.items():
        category = category_names[top]
        for entry in _subfolders(base / category):
            label = f"{category}\\{entry.name}"
            key = key_of(label)
            if key in managed or any(m.startswith(key + "\\") for m in managed):
                continue  # a type folder, a rule's folder, or the parent of one
            if not usable(entry, label):
                continue
            if makerworld == top and _has_only_subfolders(entry.path):
                continue  # MakerWorld designer folders (designer\model) are the sorter's own
            found.append({"label": label, "name": entry.name, "category": category, "extensions": frozenset(exts)})

    if cache is not None:
        cache[cache_key] = found
    return found


def match_my_folder(filename: str, config: dict, root: Path | None = None) -> dict | None:
    """The user's folder a file is named after, if any. The longest folder name wins."""
    ext = Path(filename).suffix.lower()
    best: dict | None = None
    best_rank: tuple[int, int] = (0, 0)
    for folder in my_folders(config, root):
        exts = folder["extensions"]
        if exts is not None and ext not in exts:
            continue
        if not matches_keyword(filename, folder["name"]):
            continue
        rank = (sum(c.isalnum() for c in folder["name"]), 1 if folder["category"] else 0)
        if rank > best_rank:
            best, best_rank = folder, rank
    return best


def photo_month(file_path: Path | None) -> str:
    """"2024-11" from when a photo was taken (its EXIF date), else the file's date, else today."""
    when: datetime | None = None
    if file_path is not None:
        try:
            from PIL import Image
            with Image.open(file_path) as image:
                exif = image.getexif()
                raw = exif.get_ifd(0x8769).get(36867) or exif.get(306)  # DateTimeOriginal, DateTime
            if raw:
                when = datetime.strptime(str(raw).strip()[:19], "%Y:%m:%d %H:%M:%S")
        except Exception:
            when = None
        if when is None:
            try:
                when = datetime.fromtimestamp(Path(file_path).stat().st_mtime)
            except OSError:
                when = None
    return (when or datetime.now()).strftime("%Y-%m")


def pdf_head_text(file_path: Path, config: dict) -> str:
    """The start of a PDF's first page, lowercased ('' when it can't be read).

    Only the top of the first page is used: an invoice says "Invoice" there,
    while a paper that mentions invoices somewhere in its text does not.
    """
    block = config.get("pdf_text") if isinstance(config, dict) else None
    max_mb = float((block or {}).get("max_mb", 10) or 10)
    try:
        if Path(file_path).stat().st_size > max_mb * 1024 * 1024:
            return ""
        from pypdf import PdfReader
        reader = PdfReader(str(file_path))
        if reader.is_encrypted or not reader.pages:
            return ""
        text = reader.pages[0].extract_text() or ""
    except Exception:
        return ""
    return " ".join(text.split())[:600].lower()


def filed_name(path: Path, config: dict) -> str:
    """The name a file is filed under: its own, unless tidy names or the date are turned on.

    Tidy: "My_File (1).pdf" -> "My File.pdf". Date: "2026-11-09 My File.pdf",
    using the day the download finished. Undo restores the original name.
    """
    name = path.name
    tidy, add_date = _option(config, "rename", "tidy"), _option(config, "rename", "add_date")
    if not (tidy or add_date) or path.suffix.lower() in NO_RENAME_EXTENSIONS:
        return name
    stem, suffix = path.stem, path.suffix
    if tidy:
        cleaned = _COPY_MARK.sub("", stem)
        cleaned = re.sub(r"_+", " ", cleaned)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" .-")
        stem = cleaned or stem
    if add_date and not _DATE_IN_FRONT.match(stem):
        try:
            when = datetime.fromtimestamp(path.stat().st_mtime)
        except OSError:
            when = datetime.now()
        stem = f"{when:%Y-%m-%d} {stem}"
    return stem + suffix


def evaluate_routing(
    filename: str,
    config: dict,
    active_project: dict | None = None,
    file_path: Path | None = None,
    content_sample: str | None = None,
    routing_context: RoutingContext | None = None,
    use_my_folders: bool = True,
) -> dict:
    """Evaluate routing chain for a file according to strict precedence:
    0. Incomplete / ignored check
    1. Active Project catch-all (R3): matches 27 CAD/3D/CNC extensions to target_directory;
       non-CAD extensions (e.g. university documents, spreadsheets) bypass active project
    2. Keyword rules (R1): checks enabled flag, extension whitelist, word boundary token match
    2.5 Your own folders: a folder you made in Downloads or inside a main folder takes
       files named after it ("Flanged Mace" takes "flanged_mace_v3.f3d")
    3. Smart G-code Machine Sniffer (R4): inspects content of .nc, .tap, .gcode, .bgcode to
       route Haas CNC to haas_destination and 3D printer slicers to gcode_destination
    4. Extension rules (R1): checks enabled flag, extension match; inside a main folder the
       file goes into the folder for its kind (Documents\\PDF)
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
    # A rule can also require the website a file came from ("sites"), read
    # once per file from the note Windows keeps beside a download.
    file_site: list[str | None] = []

    pdf_head: list[str] = []

    def head_of_pdf() -> str:
        if not pdf_head:
            look = (file_path is not None and ext == ".pdf" and _option(config, "pdf_text", "enabled"))
            pdf_head.append(pdf_head_text(Path(file_path), config) if look else "")
        return pdf_head[0]

    def site_of_file() -> str | None:
        if not file_site:
            site = None
            if file_path is not None:
                try:
                    import learning
                    site = learning.source_site(Path(file_path))
                except Exception:
                    site = None
            file_site.append(site)
        return file_site[0]

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

        sites = [str(item).lower() for item in rule.get("sites", []) if item]
        if sites:
            site = site_of_file()
            if not site or not any(site == wanted or site.endswith("." + wanted) for wanted in sites):
                rule_trace.append({"tier": "keyword", "rule": rule_name, "status": "site_mismatch"})
                continue

        matched_token: str | None = None
        for token in rule.get("match", []):
            if matches_keyword(filename, token):
                matched_token = token
                break
        trigger = f"keyword '{matched_token}'" if matched_token else ""
        if not matched_token and rule.get("match") and head_of_pdf():
            for token in rule.get("match", []):
                if matches_keyword(head_of_pdf(), token):
                    matched_token = token
                    trigger = f"the word '{token}' at the top of the PDF"
                    break
        if sites and matched_token:
            trigger += f" from {site_of_file()}"
        elif sites and not rule.get("match"):
            matched_token = site_of_file()
            trigger = f"downloaded from {matched_token}"

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
                "matched_trigger": trigger,
                "is_active_project": False,
                "sniffer_details": None,
                "rule_trace": rule_trace,
            }
        else:
            rule_trace.append({"tier": "keyword", "rule": rule_name, "status": "no_keyword_match"})

    # --- Tier 2.5: Your own folders ---
    own_folder = None
    if use_my_folders:
        try:
            own_folder = match_my_folder(p.name, config)
        except Exception as exc:  # a folder that can't be read must never stop sorting
            logging.debug("Could not check your own folders for %s: %s", p.name, exc)
    if own_folder:
        rule_trace.append({
            "tier": "my_folder",
            "rule": "Your folders",
            "status": "matched",
            "folder": own_folder["label"],
            "destination": own_folder["label"],
        })
        return {
            "destination": own_folder["label"],
            "match_type": "my_folder",
            "winning_rule": f"Your folder ({own_folder['name']})",
            "matched_trigger": f"named after your folder '{own_folder['name']}'",
            "is_active_project": False,
            "sniffer_details": None,
            "rule_trace": rule_trace,
        }
    rule_trace.append({
        "tier": "my_folder",
        "rule": "Your folders",
        "status": "no_match" if use_my_folders else "not_checked",
    })

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
                    haas_dest = with_type_folder(haas_dest, ext, config, HAAS_TYPE_FOLDER)
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
                    gcode_dest = with_type_folder(gcode_dest, ext, config)
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

    # --- Tier 3.5: MakerWorld 3MF Sniffer ---
    if ext == ".3mf" and file_path is not None:
        p_file = Path(file_path)
        if p_file.exists() and p_file.is_file():
            try:
                from gcode_peek import parse_3mf
                meta_3mf = parse_3mf(p_file)
                if meta_3mf.get("is_makerworld"):
                    mw = meta_3mf.get("makerworld_meta", {})
                    author = sanitize_folder_segment(mw.get("designer") or "MakerWorld")
                    model_title = sanitize_folder_segment(mw.get("title") or p_file.stem)
                    base_mw = config.get("makerworld_destination") or MAKERWORLD_FOLDER
                    mw_dest = f"{base_mw}\\{author}\\{model_title}"
                    rule_trace.append({
                        "tier": "makerworld",
                        "status": "matched",
                        "designer": author,
                        "title": model_title,
                        "destination": mw_dest,
                    })
                    return {
                        "destination": mw_dest,
                        "match_type": "makerworld",
                        "winning_rule": f"MakerWorld ({author})",
                        "matched_trigger": f"MakerWorld model '{model_title}' by {author}",
                        "is_active_project": False,
                        "sniffer_details": meta_3mf,
                        "rule_trace": rule_trace,
                    }
            except Exception as exc:
                logging.debug("MakerWorld 3MF check error: %s", exc)

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
            if str(rule.get("name", "")).lower() == "images" and _option(config, "photos", "by_month"):
                dest_folder = f"{dest_folder}\\{photo_month(Path(file_path) if file_path else None)}"
            else:
                dest_folder = with_type_folder(dest_folder, ext, config)
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
    while index < 10000:
        candidate = directory / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate
        index += 1
    import uuid
    return directory / f"{stem}_{uuid.uuid4().hex[:6]}{suffix}"


def sort_destination(path: Path | str, config: dict | None = None) -> Path:
    """Compute the planned destination path for a file according to active rules."""
    p = Path(path)
    cfg = config if config is not None else load_config()
    file_path = p if p.is_absolute() and p.exists() else None
    routing = evaluate_routing(p.name, cfg, file_path=file_path)
    if routing.get("is_active_project") or Path(routing.get("destination", "")).is_absolute():
        dest_dir = Path(routing["destination"])
    else:
        dest_folder = routing.get("destination", "")
        downloads = watch_root(cfg)
        dest_dir = folder_path(downloads, dest_folder) if dest_folder else downloads
    return unique_destination(dest_dir, p.name)


def is_file_locked(path: Path) -> bool:
    """Return True if another process holds an exclusive write/share lock on path."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        GENERIC_READ = 0x80000000
        GENERIC_WRITE = 0x40000000
        OPEN_EXISTING = 3
        FILE_SHARE_NONE = 0
        INVALID_HANDLE_VAL = ctypes.c_void_p(-1).value
        try:
            kernel32 = ctypes.windll.kernel32
            kernel32.CreateFileW.argtypes = [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.LPVOID,
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.HANDLE,
            ]
            kernel32.CreateFileW.restype = wintypes.HANDLE
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL

            handle = kernel32.CreateFileW(
                str(path.resolve()),
                GENERIC_READ | GENERIC_WRITE,
                FILE_SHARE_NONE,
                None,
                OPEN_EXISTING,
                0,
                None,
            )
            if handle in (INVALID_HANDLE_VAL, -1, None, 0):
                err = kernel32.GetLastError()
                if err == 5:
                    # ERROR_ACCESS_DENIED: May be a read-only file rather than an active write lock.
                    # Retry with GENERIC_READ and FILE_SHARE_NONE to verify if the file is truly locked.
                    ro_handle = kernel32.CreateFileW(
                        str(path.resolve()),
                        GENERIC_READ,
                        FILE_SHARE_NONE,
                        None,
                        OPEN_EXISTING,
                        0,
                        None,
                    )
                    if ro_handle not in (INVALID_HANDLE_VAL, -1, None, 0):
                        kernel32.CloseHandle(ro_handle)
                        return False
                    ro_err = kernel32.GetLastError()
                    if ro_err in (5, 32, 33):
                        return True
                    return False
                if err in (32, 33):  # Sharing violation, Lock violation
                    return True
                return False
            kernel32.CloseHandle(handle)
            return False
        except Exception:
            return False
    try:
        with open(path, "r+b"):
            return False
    except PermissionError:
        try:
            with open(path, "rb"):
                return False
        except PermissionError:
            return True
        except OSError:
            return False
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


DUPLICATES_DIR_NAME = "_Duplicates"

# Download Sorter's own working folders inside Downloads; never sorted or rescued.
INTERNAL_DIR_NAMES = frozenset({DUPLICATES_DIR_NAME.lower(), "_remodel quarantine", ".batch_stage"})


def is_internal_path(path: Path, root: Path) -> bool:
    """True for files inside Download Sorter's own working folders under ``root``."""
    try:
        rel = Path(path).resolve().relative_to(Path(root).resolve())
    except (ValueError, OSError):
        return False
    return bool(rel.parts) and rel.parts[0].lower() in INTERNAL_DIR_NAMES


def hold_applies(path: Path, config: dict, batch_id: str = "") -> bool:
    """True when the grace-period hold covers this file (and this is not a release)."""
    hold_cfg = config.get("hold", {})
    hold_exts = [e.lower() for e in hold_cfg.get("extensions", [".3mf"])]
    return (
        bool(hold_cfg.get("enabled", True))
        and path.suffix.lower() in hold_exts
        and batch_id not in ("file_now", "hold_release")
    )


def hold_in_place(
    path: Path,
    config: dict,
    destination: Path,
    batch_id: str = "",
    dry_run: bool = False,
    downloads: Path | None = None,
) -> bool:
    """Decide whether the hold keeps ``path`` where it is.

    Returns True when the file must stay put (marked "Keep here", or newly held).
    A dry run only reports what would happen: it never registers or resets a hold
    and never shows a notification.
    """
    if not hold_applies(path, config, batch_id):
        return False
    import hold_manager
    if hold_manager.is_keep_here(path):
        logging.debug("3MF file %s is marked keep_here; skipping move", path.name)
        return True
    if dry_run:
        if hold_manager.is_held(path):
            logging.info("[DRY-RUN] %s is already held in place (planned: %s)", path.name, destination)
        elif hold_manager.hold_period_over(path, config):
            return False  # it has waited long enough: the dry run goes on to show where it would be filed
        else:
            logging.info("[DRY-RUN] Would hold %s in place (planned: %s)", path.name, destination)
        return True
    meta_3mf = None
    try:
        from gcode_peek import parse_3mf
        meta_3mf = parse_3mf(path)
    except Exception:
        pass
    if not hold_manager.hold_file(path, config, destination, meta=meta_3mf):
        return False
    logging.info("3MF file held in place: %s (planned: %s)", path.name, destination)
    if config.get("ribbon_enabled", True) and config.get("notifications", True):
        try:
            from ribbon_popup import notify_file_held
            if downloads is not None:
                notify_file_held(path, destination, meta_3mf, downloads_root=downloads)
            else:
                notify_file_held(path, destination, meta_3mf)
        except Exception as err:
            logging.debug("Ribbon hold notification skipped: %s", err)
    return True


def duplicates_folder(downloads: Path) -> Path:
    """Where exact duplicates go instead of being deleted: Downloads\\_Duplicates\\YYYY-MM."""
    return Path(downloads) / DUPLICATES_DIR_NAME / datetime.now().strftime("%Y-%m")


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
    if is_restored_by_undo(path):
        return None
    if is_quarantined(path):
        return None
    if is_keep_protected(path):
        return None
    # Only files directly in the folder being sorted are moved; anything in a
    # sub-folder (including the category folders) is left where it is.
    if not same_folder(path.parent, from_root):
        return None

    if check_age:
        min_age = float(config.get("watch", {}).get("min_age_minutes", 0))
        if not is_old_enough(path, min_age):
            return None

    settle = float(config.get("watch", {}).get("settle_seconds", DEFAULT_SETTLE_SECONDS))
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
    name = filed_name(path, config)
    primary_target = destination_dir / name
    destination = unique_destination(destination_dir, name)

    # Retention first: "Keep here" and the grace-period hold win over everything
    # else, including duplicate cleanup (a slicer may have the held copy open).
    if hold_in_place(path, config, destination, batch_id, dry_run, downloads=downloads):
        return None

    # Exact duplicate of a file already filed: quarantine it (journaled, so Undo can
    # bring it back) instead of deleting it.
    if primary_target.exists() and are_files_identical(path, primary_target):
        dup_dir = duplicates_folder(downloads)
        if dry_run:
            logging.info(
                "[DRY-RUN] Exact duplicate already exists at %s; would move %s to %s",
                primary_target, path.name, dup_dir,
            )
            return primary_target
        import safe_ops
        try:
            parked = safe_ops.quarantine(path, dup_dir, batch_id=batch_id)
            logging.info(
                "Exact duplicate already exists at %s; moved download %s to %s",
                primary_target, path.name, parked,
            )
            return primary_target
        except safe_ops.Refused as refusal:
            logging.debug("Left duplicate %s alone: %s", path.name, refusal.reason)
            return None
        except OSError as exc:
            record_move_failure(path, exc)
            return None


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
                extracted = unpack_3d_archive(path, config, active_project=active_proj_arg, batch_id=batch_id)
                if extracted:
                    logging.info("Auto-unpacked %d 3D model file(s) from %s", len(extracted), path.name)
                    record_extraction(path, extracted, batch_id)
                    return extracted[0]
        except Exception as exc:
            logging.warning("Auto-unpack check failed for %s: %s", path.name, exc)

    auto_unzip = config.get("watch", {}).get("auto_unzip", config.get("auto_unzip", False))
    if path.suffix.lower() == ".zip" and auto_unzip:
        try:
            from unzip_archive import unpack_archive
            if dry_run:
                extracted_dir_name = config.get("extracted_destination", "Extracted")
                sim_dest = folder_path(downloads, f"{extracted_dir_name}\\{path.stem}")
                logging.info("[DRY-RUN] Would auto-unzip archive %s into %s", path.name, sim_dest)
                return sim_dest
            active_proj_arg = load_active_project() if routing["is_active_project"] else None
            landing_folder = unpack_archive(path, config, active_project=active_proj_arg, batch_id=batch_id)
            if landing_folder:
                logging.info("Auto-unzipped archive %s -> %s", path.name, landing_folder)
                record_extraction(path, [landing_folder], batch_id)
                return landing_folder
        except Exception as exc:
            logging.warning("Auto-unzip failed for %s: %s", path.name, exc)

    dest_display = destination.relative_to(downloads) if is_under(destination, downloads) else str(destination)
    if dry_run:
        logging.info("[DRY-RUN] Would move %s -> %s (%s)", path.name, dest_display, reason)
        return destination

    import safe_ops
    destination_dir.mkdir(parents=True, exist_ok=True)
    source = path.resolve()
    try:
        destination = safe_ops.move(path, destination, batch_id=batch_id, settle_seconds=settle)
    except safe_ops.Refused as refusal:
        logging.debug("Left %s alone: %s", path.name, refusal.reason)
        return None
    except OSError as exc:
        record_move_failure(path, exc)
        return None
    clear_failure(path)
    dest_display = destination.relative_to(downloads) if is_under(destination, downloads) else str(destination)
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
    if is_restored_by_undo(path):
        return None
    if is_under(path, downloads):
        return None
    if not any(is_under(path, source) for source in document_roots(config)):
        return None

    settle = float(config.get("watch", {}).get("settle_seconds", DEFAULT_SETTLE_SECONDS))
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
    # Retention first; a dry run never writes hold state or notifies.
    if hold_in_place(path, config, destination, batch_id, dry_run):
        return None


    dest_display = destination.relative_to(downloads) if is_under(destination, downloads) else str(destination)
    if dry_run:
        logging.info(
            "[DRY-RUN] Would move from Documents %s -> %s (%s)",
            path.name,
            dest_display,
            reason,
        )
        return destination

    import safe_ops
    destination_dir.mkdir(parents=True, exist_ok=True)
    source = path.resolve()
    try:
        destination = safe_ops.move(path, destination, batch_id=batch_id, settle_seconds=settle)
    except safe_ops.Refused as refusal:
        logging.debug("Left %s alone: %s", path.name, refusal.reason)
        return None
    except OSError as exc:
        record_move_failure(path, exc)
        return None
    clear_failure(path)
    dest_display = destination.relative_to(downloads) if is_under(destination, downloads) else str(destination)
    logging.info(
        "Moved from Documents %s -> %s (%s)",
        source,
        dest_display,
        reason,
    )
    return destination


def existing_files_cutoff(config: dict) -> float | None:
    """Time before which files are left alone by automatic sweeps, or None.

    The welcome wizard stores ``skip_files_before`` when the user unticks
    "Organize existing files": the files already in Downloads at that moment are
    theirs to keep where they are.  Explicit "Sort now" actions ignore it.
    """
    value = config.get("skip_files_before")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def arrival_time(path: Path) -> float | None:
    """When ``path`` landed where it is: the newer of its modified and created times.

    Some browsers stamp a download with the server's modified date, but its
    creation time on Windows is when it landed in Downloads. None when the file
    cannot be read.
    """
    try:
        st = path.stat()
    except OSError:
        return None
    newest = st.st_mtime
    created = getattr(st, "st_birthtime", None)
    if created is None and sys.platform == "win32":
        created = st.st_ctime
    if created is not None:
        newest = max(newest, created)
    return newest


def arrived_before(path: Path, cutoff: float | None) -> bool:
    """True when path was created and last changed before cutoff (see ``arrival_time``)."""
    if cutoff is None:
        return False
    arrived = arrival_time(path)
    return arrived is not None and arrived < cutoff


def sort_existing(
    config: dict,
    dry_run: bool = False,
    batch_id: str = "",
    target_dir: Path | None = None,
    routing_context: RoutingContext | None = None,
    skip_before: float | None = None,
) -> int:
    downloads = watch_root(config)
    if not dry_run:
        ensure_tree(config)
    root = target_dir.resolve() if target_dir else downloads
    if not root.exists():
        return 0
    moved = 0
    with reuse_my_folders():
        for item in list(root.iterdir()):
            if item.is_file():
                if arrived_before(item, skip_before):
                    continue
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


class _DropThisThreadsDryRunLogs(logging.Filter):
    """Hide "[DRY-RUN]" lines written by one thread, so a preview stays quiet."""

    def __init__(self, thread_id: int) -> None:
        super().__init__()
        self.thread_id = thread_id

    def filter(self, record: logging.LogRecord) -> bool:
        return not (record.thread == self.thread_id and str(record.msg).startswith("[DRY-RUN]"))


def preview_existing(config: dict, limit: int = 1000) -> list[tuple[Path, Path]]:
    """Files directly in Downloads that "Sort Downloads now" would move, with
    where each would go. Nothing is moved, held or written to the log."""
    root = watch_root(config)
    if not root.exists():
        return []
    quiet = _DropThisThreadsDryRunLogs(threading.get_ident())
    root_logger = logging.getLogger()
    root_logger.addFilter(quiet)
    handlers = list(root_logger.handlers)
    for handler in handlers:
        handler.addFilter(quiet)
    found: list[tuple[Path, Path]] = []
    try:
        with reuse_my_folders():
            for item in sorted(root.iterdir(), key=lambda p: p.name.lower()):
                if len(found) >= limit:
                    break
                if not item.is_file():
                    continue
                try:
                    dest = move_file(item, config, dry_run=True, check_age=False, source_root=root)
                except Exception:
                    dest = None
                if dest is not None:
                    found.append((item, Path(dest)))
    finally:
        root_logger.removeFilter(quiet)
        for handler in handlers:
            handler.removeFilter(quiet)
    return found


def sort_existing_documents(
    config: dict,
    dry_run: bool = False,
    batch_id: str = "",
    routing_context: RoutingContext | None = None,
    skip_before: float | None = None,
) -> int:
    if not dry_run:
        ensure_tree(config)
    moved = 0
    with reuse_my_folders():
        for source in document_roots(config):
            logging.info("Scanning Documents source %s", source)
            for item in iter_files(source, config, routing_context):
                if arrived_before(item, skip_before):
                    continue
                if relocate_from_documents(
                    item,
                    config,
                    dry_run=dry_run,
                    batch_id=batch_id,
                    routing_context=routing_context,
                ):
                    moved += 1
    return moved


def _changed_within_hold(path: Path, config: dict) -> bool:
    """True when ``path`` changed more recently than the hold's length (7 days by default)."""
    try:
        days = float(config.get("hold", {}).get("hold_days", 7))
        return time.time() - path.stat().st_mtime < days * 86400.0
    except (OSError, TypeError, ValueError, AttributeError):
        return True


def reclassify_misplaced_file(
    path: Path,
    config: dict,
    dry_run: bool = False,
    batch_id: str = "",
    routing_context: RoutingContext | None = None,
) -> Path | None:
    """Auto-correct a file sitting directly in one of the sorter's own folders.

    That is a main folder (Documents), a type folder (Documents\\PDF) or a rule's
    folder. A folder the user made is never touched: nothing is taken out of it.
    """
    downloads = watch_root(config)
    if not path.exists() or not path.is_file():
        return None
    if is_restored_by_undo(path) or is_internal_path(path, downloads):
        return None
    if is_incomplete(path, config, routing_context):
        return None
    if is_cloud_placeholder(path):
        return None
    if is_quarantined(path):
        return None

    try:
        parent_rel = path.parent.resolve().relative_to(downloads)
    except ValueError:
        return None

    # Current folder relative to Downloads
    current_folder = str(parent_rel).replace("/", "\\")

    # Only the sorter's own folders are corrected. Any other folder was made by
    # the user, and a file in it stays there.
    managed_folders = (
        routing_context.category_folders
        if routing_context is not None
        else {f.replace("/", "\\").strip("\\").lower() for f in collect_managed_folders(config)}
    )
    if current_folder.lower() not in managed_folders:
        return None

    # An active project collects new downloads. It never empties the folders
    # that are already sorted, so it is left out here.
    routing = evaluate_routing(
        path.name,
        config,
        active_project={"enabled": False},
        file_path=path,
        routing_context=routing_context,
    )
    if routing["match_type"] == "ignored":
        return None
    target_folder, reason = routing["destination"], routing["matched_trigger"]
    target_folder = target_folder.replace("/", "\\")

    # If already in the right folder, nothing to do
    if current_folder.lower() == target_folder.lower().strip("\\"):
        return None

    settle = float(config.get("watch", {}).get("settle_seconds", DEFAULT_SETTLE_SECONDS))
    if not dry_run and not is_file_ready_to_move(path, settle):
        return None

    if Path(target_folder).is_absolute():
        destination_dir = Path(target_folder)
    else:
        destination_dir = folder_path(downloads, target_folder)
    destination = unique_destination(destination_dir, path.name)
    # Retention first; a dry run never writes hold state or notifies. The hold
    # is for files still being worked on: one that hasn't changed for longer
    # than the hold lasts is simply put in its folder.
    if _changed_within_hold(path, config) and hold_in_place(path, config, destination, batch_id, dry_run):
        return None


    dest_display = destination.relative_to(downloads) if is_under(destination, downloads) else str(destination)
    if dry_run:
        logging.info("[DRY-RUN] Would reclassify %s -> %s (%s)", path.name, dest_display, reason)
        return destination

    import safe_ops
    destination_dir.mkdir(parents=True, exist_ok=True)
    source = path.resolve()
    try:
        destination = safe_ops.move(source, destination, batch_id=batch_id, settle_seconds=settle)
    except safe_ops.Refused as refusal:
        logging.debug("Left %s alone: %s", path.name, refusal.reason)
        return None
    except OSError as exc:
        record_move_failure(path, exc)
        return None

    clear_failure(path)
    dest_display = destination.relative_to(downloads) if is_under(destination, downloads) else str(destination)
    logging.info("Auto-corrected misplaced file: %s from '%s' -> '%s'", source.name, current_folder, dest_display)
    return destination


def sort_existing_misplaced(
    config: dict,
    dry_run: bool = False,
    batch_id: str = "",
    routing_context: RoutingContext | None = None,
    skip_before: float | None = None,
) -> int:
    """Sweep the files sitting directly in the sorter's own folders for misplaced ones.

    This is also what moves files from a main folder into its type folders
    (Documents\\report.pdf -> Documents\\PDF) the first time the two-tier layout
    is used. Folders the user made are not looked into.
    """
    downloads = watch_root(config)
    moved = 0
    with reuse_my_folders():
        for rel_folder in collect_managed_folders(config):
            if Path(rel_folder).is_absolute() or ":" in rel_folder:
                continue
            folder = folder_path(downloads, rel_folder)
            if folder.exists() and folder.is_dir():
                for item in list(folder.iterdir()):
                    if item.is_file():
                        if arrived_before(item, skip_before):
                            continue
                        if reclassify_misplaced_file(
                            item,
                            config,
                            dry_run=dry_run,
                            batch_id=batch_id,
                            routing_context=routing_context,
                        ):
                            moved += 1
    return moved


def consolidate_downloads(
    target_root: Path | str | None = None,
    config: dict | None = None,
    dry_run: bool = False,
    batch_id: str = "",
    include_held: bool = True,
    remove_empty: bool = True,
) -> dict:
    """Consolidate nested subfolders and legacy categories into flat official categories."""
    import consolidate
    return consolidate.consolidate_downloads(
        target_root=target_root,
        config=config,
        dry_run=dry_run,
        batch_id=batch_id,
        include_held=include_held,
        remove_empty=remove_empty,
    )


