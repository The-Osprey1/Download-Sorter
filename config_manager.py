"""Configuration persistence, active project state, and runtime diagnostics for Download Sorter."""
from __future__ import annotations

import copy
import json
import logging
import os
import shutil
import sqlite3
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from sorter import (
    ACTIVE_PROJECT_FILE,
    CAD_EXTENSIONS as SORTER_CAD_EXTENSIONS,
    CONFIG_PATH,
    DEFAULT_CONFIG,
    LOG_DIR,
)

CAD_EXTENSIONS: list[str] = sorted(list(SORTER_CAD_EXTENSIONS))
SORTER_CONFIG_FILE: Path = CONFIG_PATH

_sorter_config_write_lock = threading.RLock()
_active_project_write_lock = threading.RLock()

DEFAULT_SORTER_CONFIG: dict[str, Any] = copy.deepcopy(DEFAULT_CONFIG)


def validate_sorter_config(config: Any) -> bool:
    """Validate sorter configuration structure before persistence."""
    if not isinstance(config, dict):
        return False
    if "keyword_rules" not in config or not isinstance(config["keyword_rules"], list):
        return False
    if "extension_rules" not in config or not isinstance(config["extension_rules"], list):
        return False
    return True


def load_sorter_config(config_path: Path | str | None = None) -> dict[str, Any]:
    """Load sorter configuration with fallback to .bak and safe defaults on corruption."""
    target = Path(config_path) if config_path else SORTER_CONFIG_FILE

    # 1. Primary config attempt
    if target.exists():
        try:
            raw = target.read_text(encoding="utf-8")
            if raw.strip():
                data = json.loads(raw)
                if isinstance(data, dict) and "keyword_rules" in data and "extension_rules" in data:
                    return data
        except Exception:
            pass

    # 2. Backup .bak fallback
    bak_path = target.with_name(target.name + ".bak")
    if bak_path.exists():
        try:
            raw_bak = bak_path.read_text(encoding="utf-8")
            if raw_bak.strip():
                bak_data = json.loads(raw_bak)
                if isinstance(bak_data, dict) and "keyword_rules" in bak_data:
                    return bak_data
        except Exception:
            pass

    # 3. Safe fallback defaults
    return copy.deepcopy(DEFAULT_SORTER_CONFIG)


def save_sorter_config(config: dict[str, Any], config_path: Path | str | None = None) -> bool:
    """Safely persist sorter configuration using a 5-stage atomic commit.

    1. Schema validation (keyword_rules list, extension_rules list, dict type).
    2. Write payload to temporary file with UTF-8 encoding.
    3. Flush and call os.fsync() on file descriptor.
    4. Create backup copy (config.json.bak) if target exists.
    5. Atomic replace (os.replace) with retry loop for transient file locks.
    """
    if not isinstance(config, dict):
        raise TypeError("Configuration must be a dictionary")
    if not validate_sorter_config(config):
        raise ValueError("Configuration failed schema validation: keyword_rules and extension_rules must be lists")

    target = Path(config_path) if config_path else SORTER_CONFIG_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target.parent / f"{target.name}.tmp.{uuid.uuid4().hex}"
    content = json.dumps(config, indent=2, ensure_ascii=False)

    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
    except OSError:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        raise

    with _sorter_config_write_lock:
        # 4. Backup existing config
        bak_path = target.with_name(target.name + ".bak")
        if target.exists():
            try:
                shutil.copy2(target, bak_path)
            except OSError:
                pass

        # 5. Atomic rename with Windows retry loop
        max_attempts = 5
        delay = 0.05
        for attempt in range(max_attempts):
            try:
                os.replace(temp_path, target)
                return True
            except OSError:
                if attempt == max_attempts - 1:
                    if temp_path.exists():
                        try:
                            temp_path.unlink()
                        except OSError:
                            pass
                    raise
                time.sleep(delay)
    return True


def get_active_project(path: Path | str | None = None) -> dict[str, Any] | None:
    """Read active project state from %LOCALAPPDATA%\\download-sorter\\active_project.json.
    Returns None if missing, disabled, or expired.
    """
    target = Path(path) if path else ACTIVE_PROJECT_FILE
    if not target.exists():
        return None
    try:
        raw = target.read_text(encoding="utf-8")
        if not raw.strip():
            return None
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        is_enabled = data.get("enabled", data.get("active", False))
        if not is_enabled:
            return None
        expires_at = data.get("expires_at")
        if expires_at is not None:
            try:
                exp_val = float(expires_at)
                if exp_val > 0 and time.time() >= exp_val:
                    data["enabled"] = False
                    data["active"] = False
                    try:
                        target.write_text(json.dumps(data, indent=2), encoding="utf-8")
                    except OSError:
                        pass
                    return None
            except (ValueError, TypeError):
                return None
        return data
    except Exception:
        return None


def save_active_project(data: dict[str, Any], path: Path | str | None = None) -> bool:
    """Save active project state to %LOCALAPPDATA%\\download-sorter\\active_project.json."""
    if not isinstance(data, dict):
        raise TypeError("Active project data must be a dictionary")
    target = Path(path) if path else ACTIVE_PROJECT_FILE
    with _active_project_write_lock:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            temp_path = target.parent / f"{target.name}.tmp.{uuid.uuid4().hex}"
            content = json.dumps(data, indent=2, ensure_ascii=False)
            with open(temp_path, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            for attempt in range(15):
                try:
                    os.replace(temp_path, target)
                    return True
                except OSError:
                    if attempt == 14:
                        if temp_path.exists():
                            try:
                                temp_path.unlink()
                            except OSError:
                                pass
                        raise
                    time.sleep(0.01 * (attempt + 1))
            return True
        except OSError:
            return False


def is_sorter_running() -> bool:
    """Check if download-sorter is running using pure ctypes (zero subprocesses, zero console flashing)."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        SYNCHRONIZE = 0x00100000
        h = kernel32.OpenMutexW(SYNCHRONIZE, False, "Local\\DownloadSorter_SingleInstance")
        if h:
            kernel32.CloseHandle(h)
            return True
        return False
    except Exception:
        return False


def get_maker_projects(db_path: Path | str | None = None) -> list[dict[str, Any]]:
    """Query Maker / 3D projects from local database or project directory."""
    if db_path:
        target = Path(db_path)
    else:
        target = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "print-library" / "library.db"

    if target.exists():
        try:
            conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT id, name, managed_folder, stage, machine_type FROM projects
                   WHERE archived_at IS NULL
                   ORDER BY updated_at DESC"""
            ).fetchall()
            projects: list[dict[str, Any]] = []
            for r in rows:
                name = r["name"]
                folder = r["managed_folder"] or str(Path.home() / "Documents" / "Maker Projects" / name)
                projects.append({
                    "id": str(r["id"]),
                    "name": name,
                    "target_directory": folder,
                    "folder": folder,
                    "managed_folder": r["managed_folder"] or "",
                    "stage": r["stage"],
                    "machine_type": r["machine_type"],
                })
            conn.close()
            return projects
        except Exception:
            pass

    # Generic fallback: scan Documents / 3D Projects
    doc_projects = Path.home() / "Documents" / "3D Projects"
    if doc_projects.exists():
        try:
            return [
                {
                    "id": p.name,
                    "name": p.name,
                    "target_directory": str(p),
                    "folder": str(p),
                    "managed_folder": str(p),
                    "stage": "Active",
                    "machine_type": "3D",
                }
                for p in doc_projects.iterdir() if p.is_dir()
            ]
        except Exception:
            pass

    return []
