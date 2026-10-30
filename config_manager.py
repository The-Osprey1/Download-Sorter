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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sorter import (
    ACTIVE_PROJECT_FILE,
    CAD_EXTENSIONS as SORTER_CAD_EXTENSIONS,
    CONFIG_PATH,
    DEFAULT_CONFIG,
    DEFAULT_CONFIG_PATH,
    LOG_DIR,
    USER_CONFIG_PATH,
)

CAD_EXTENSIONS: list[str] = sorted(list(SORTER_CAD_EXTENSIONS))
SORTER_CONFIG_FILE: Path = USER_CONFIG_PATH

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
    for r in config["keyword_rules"]:
        if not isinstance(r, dict) or not r.get("name") or not r.get("folder"):
            return False
    for r in config["extension_rules"]:
        if not isinstance(r, dict) or not r.get("name") or not r.get("extensions") or not isinstance(r["extensions"], list):
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

    # 3. Fallback to repo template if user config in AppData doesn't exist yet
    if config_path is None and DEFAULT_CONFIG_PATH.exists():
        try:
            raw_tmpl = DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")
            if raw_tmpl.strip():
                tmpl_data = json.loads(raw_tmpl)
                if isinstance(tmpl_data, dict) and "keyword_rules" in tmpl_data:
                    try:
                        save_sorter_config(tmpl_data, config_path=target)
                    except Exception:
                        pass
                    return tmpl_data
        except Exception:
            pass

    # 4. Safe fallback defaults
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


# =====================================================================
# PUBLIC PRESET LIBRARY & RULE PACKS
# =====================================================================

BUILTIN_PRESETS: list[dict[str, Any]] = [
    {
        "id": "office_admin",
        "name": "Office, Invoices & Finance",
        "category": "Office & Finance",
        "description": "Essential organizational rules for invoices, receipts, tax documents, and careers.",
        "rules": [
            {
                "name": "Invoices & Receipts",
                "folder": "Documents\\Invoices & Receipts",
                "match": [
                    "invoice", "receipt", "billing", "statement", "payment",
                    "order_confirmation", "tax_invoice", "purchase_order", "remittance",
                ],
                "extensions": [".pdf", ".png", ".jpg", ".docx", ".xlsx", ".csv"],
                "enabled": True,
            },
            {
                "name": "Financial & Taxes",
                "folder": "Documents\\Financial & Tax",
                "match": [
                    "tax", "w2", "w-2", "1099", "1040", "tax_return",
                    "bank_statement", "paystub", "w9", "w-9", "dividend",
                ],
                "extensions": [".pdf", ".xlsx", ".xls", ".csv", ".doc", ".docx"],
                "enabled": True,
            },
            {
                "name": "Resumes & Career",
                "folder": "Documents\\Career",
                "match": [
                    "resume", "curriculum_vitae", "cv", "cover_letter",
                    "portfolio", "offer_letter", "recommendation",
                ],
                "extensions": [".pdf", ".docx", ".doc", ".odt"],
                "enabled": True,
            },
            {
                "name": "Manuals & Guides",
                "folder": "Documents\\Manuals & Guides",
                "match": [
                    "manual", "userguide", "datasheet", "quickstart",
                    "handbook", "documentation", "spec_sheet",
                ],
                "extensions": [".pdf", ".doc", ".docx", ".txt"],
                "enabled": True,
            },
            {
                "name": "Travel & Bookings",
                "folder": "Documents\\Travel",
                "match": [
                    "boarding_pass", "flight", "itinerary", "e-ticket",
                    "hotel_reservation", "booking_confirmation", "airline",
                ],
                "extensions": [".pdf", ".pkpass", ".png", ".jpg"],
                "enabled": True,
            },
        ],
    },
    {
        "id": "academic_student",
        "name": "Student & Academic Coursework",
        "category": "Student & Academic",
        "description": "Streamlined sorting for courses, syllabi, assignments, and academic research.",
        "rules": [
            {
                "name": "Syllabi & Notes",
                "folder": "Documents\\School\\Notes",
                "match": [
                    "syllabus", "lecture", "lecture_notes", "course_outline",
                    "class_notes", "handout", "reading",
                ],
                "extensions": [".pdf", ".docx", ".pptx", ".txt"],
                "enabled": True,
            },
            {
                "name": "Homework & Assignments",
                "folder": "Documents\\School\\Assignments",
                "match": [
                    "homework", "assignment", "lab_report", "problem_set",
                    "submission", "rubric", "worksheet",
                ],
                "extensions": [".pdf", ".docx", ".doc", ".xlsx", ".py", ".zip"],
                "enabled": True,
            },
            {
                "name": "Exams & Study Guides",
                "folder": "Documents\\School\\Exams",
                "match": [
                    "midterm", "final_exam", "quiz", "study_guide",
                    "exam_review", "cheat_sheet", "practice_exam",
                ],
                "extensions": [".pdf", ".docx", ".doc"],
                "enabled": True,
            },
            {
                "name": "Research Papers & Theses",
                "folder": "Documents\\School\\Research",
                "match": [
                    "thesis", "dissertation", "arxiv", "journal_article",
                    "manuscript", "abstract", "whitepaper",
                ],
                "extensions": [".pdf", ".bib", ".tex"],
                "enabled": True,
            },
        ],
    },
    {
        "id": "maker_3d",
        "name": "3D Printing & Digital Fabrication",
        "category": "3D Printing & Maker",
        "description": "Dedicated rules for makers running Bambu, Prusa, Creality, Haas CNC, and CAD.",
        "rules": [
            {
                "name": "3MF Slicer Projects",
                "folder": "3D Models\\3MF",
                "match": [
                    "bambu", "orca", "prusa", "plate", "multicolor", "ams",
                ],
                "extensions": [".3mf"],
                "enabled": True,
            },
            {
                "name": "CNC & Slicer G-Code",
                "folder": "Code\\Gcode",
                "match": [
                    "slice", "layer", "toolpath", "contour", "gcode", "haas",
                ],
                "extensions": [".gcode", ".bgcode", ".nc", ".tap", ".ngc"],
                "enabled": True,
            },
            {
                "name": "CAD & Mechanical Parts",
                "folder": "3D Models\\CAD",
                "match": [
                    "bracket", "mount", "enclosure", "adapter", "gear",
                    "spacer", "chassis", "flange", "bushing",
                ],
                "extensions": [".step", ".stp", ".iges", ".f3d", ".sldprt", ".ipt", ".stl"],
                "enabled": True,
            },
        ],
    },
    {
        "id": "software_dev",
        "name": "Software Development & Ops",
        "category": "Software Developer",
        "description": "Developer rules for configs, databases, container definitions, and diagnostics.",
        "rules": [
            {
                "name": "Configs & Infrastructure",
                "folder": "Code\\Configs",
                "match": [
                    "docker-compose", "k8s", "kubernetes", "settings",
                    "config", "manifest", "env_example",
                ],
                "extensions": [".json", ".yaml", ".yml", ".toml", ".ini", ".env"],
                "enabled": True,
            },
            {
                "name": "Datasets & Data",
                "folder": "Data",
                "match": [
                    "dataset", "benchmark", "sample_data", "kaggle",
                    "telemetry", "metrics",
                ],
                "extensions": [".csv", ".json", ".parquet", ".sqlite", ".db", ".feather"],
                "enabled": True,
            },
            {
                "name": "Logs & Crash Dumps",
                "folder": "Code\\Logs",
                "match": [
                    "crashdump", "stacktrace", "error_log", "diagnostics",
                    "dmp", "syslog",
                ],
                "extensions": [".log", ".txt", ".dmp", ".out"],
                "enabled": True,
            },
        ],
    },
    {
        "id": "creative_media",
        "name": "Creative Media & Design",
        "category": "Creative & Media",
        "description": "Rules for designers, video editors, music producers, and audio artists.",
        "rules": [
            {
                "name": "Audio Stems & Samples",
                "folder": "Audio\\Samples",
                "match": [
                    "stem", "multitrack", "sample_pack", "drum_kit",
                    "loop", "acapella", "one_shot", "soundfx",
                ],
                "extensions": [".wav", ".flac", ".mp3", ".aiff"],
                "enabled": True,
            },
            {
                "name": "Vector Graphics & Logos",
                "folder": "Images\\Vector",
                "match": [
                    "vector", "icon", "logo", "illustration",
                    "sprite", "badge", "banner",
                ],
                "extensions": [".svg", ".eps", ".ai", ".cdr"],
                "enabled": True,
            },
        ],
    },
]


def get_available_presets() -> list[dict[str, Any]]:
    """Return all available built-in rule presets."""
    return copy.deepcopy(BUILTIN_PRESETS)


def apply_preset(config: dict[str, Any], preset_id: str) -> tuple[dict[str, Any], int]:
    """Apply a preset's rules into the given configuration without duplicating existing rules.

    Returns (updated_config, count_of_added_rules).
    """
    preset = next((p for p in BUILTIN_PRESETS if p["id"] == preset_id), None)
    if not preset:
        raise ValueError(f"Unknown preset ID: {preset_id}")

    existing_keyword_names = {
        (r.get("name") or "").lower()
        for r in config.get("keyword_rules", [])
        if isinstance(r, dict)
    }
    new_rules: list[dict[str, Any]] = []
    added_count = 0
    for rule in preset["rules"]:
        r_name = (rule.get("name") or "").lower()
        if r_name and r_name not in existing_keyword_names:
            new_rules.append(copy.deepcopy(rule))
            existing_keyword_names.add(r_name)
            added_count += 1

    updated = copy.deepcopy(config)
    updated.setdefault("keyword_rules", []).extend(new_rules)
    return updated, added_count


def export_rules_to_file(file_path: Path | str, config: dict[str, Any] | None = None) -> bool:
    """Export keyword_rules and extension_rules to a standalone JSON file."""
    cfg = config if config is not None else load_sorter_config()
    payload = {
        "version": "1.0",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "keyword_rules": cfg.get("keyword_rules", []),
        "extension_rules": cfg.get("extension_rules", []),
    }
    target = Path(file_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return True


def import_rules_from_file(file_path: Path | str) -> dict[str, Any]:
    """Import and validate rules from an exported JSON file.

    Returns a dict with 'keyword_rules' and/or 'extension_rules'.
    """
    target = Path(file_path)
    if not target.exists():
        raise FileNotFoundError(f"Import file does not exist: {target}")
    raw = target.read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Imported file must contain a JSON object")
    if "keyword_rules" not in data and "extension_rules" not in data:
        raise ValueError("Import file missing 'keyword_rules' and 'extension_rules'")

    if "keyword_rules" in data:
        if not isinstance(data["keyword_rules"], list):
            raise ValueError("'keyword_rules' must be a list")
        for idx, r in enumerate(data["keyword_rules"]):
            if not isinstance(r, dict) or not r.get("name") or not r.get("folder"):
                raise ValueError(f"Malformed keyword rule at index {idx}: must be an object with 'name' and 'folder'")

    if "extension_rules" in data:
        if not isinstance(data["extension_rules"], list):
            raise ValueError("'extension_rules' must be a list")
        for idx, r in enumerate(data["extension_rules"]):
            if not isinstance(r, dict) or not r.get("name") or not r.get("extensions") or not isinstance(r["extensions"], list):
                raise ValueError(f"Malformed extension rule at index {idx}: must be an object with 'name' and 'extensions'")

    return data


def reset_sorter_config_to_defaults(config_path: Path | str | None = None) -> dict[str, Any]:
    """Reset configuration to factory defaults and persist atomically."""
    default_cfg = copy.deepcopy(DEFAULT_SORTER_CONFIG)
    save_sorter_config(default_cfg, config_path=config_path)
    return default_cfg
