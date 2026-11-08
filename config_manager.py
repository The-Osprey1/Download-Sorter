"""Configuration persistence, active project state, and runtime diagnostics for Download Sorter."""
from __future__ import annotations

import copy
import json
import logging
import os
import re
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
    MAKER_DOCUMENT_SOURCES,
    USER_CONFIG_PATH,
    apply_v2_defaults,
    matches_keyword as _matches_keyword,
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
                    return apply_v2_defaults(data)
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
                    return apply_v2_defaults(bak_data)
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
                    return apply_v2_defaults(tmpl_data)
        except Exception:
            pass

    # 4. Safe fallback defaults
    return copy.deepcopy(DEFAULT_SORTER_CONFIG)


def _backup_v1_config(target: Path) -> None:
    """Keep a one-time pristine copy (config.v1.json) before the first schema-v2 write."""
    backup = target.with_name("config.v1.json")
    if backup.exists() or not target.exists():
        return
    try:
        existing = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if isinstance(existing, dict) and existing.get("schema_version") is None:
        try:
            shutil.copy2(target, backup)
        except OSError:
            pass


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
    # Persist schema v2 (unknown user keys are kept; caller's dict is not mutated).
    payload = apply_v2_defaults(copy.deepcopy(config))
    content = json.dumps(payload, indent=2, ensure_ascii=False)

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
        _backup_v1_config(target)
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


# =====================================================================
# USER PERSONAS & ONBOARDING PROFILES
# =====================================================================

PERSONA_PROFILES: list[dict[str, Any]] = [
    {
        "id": "everyday_home",
        "name": "Everyday & Home PC",
        "icon": "🏠",
        "description": "Keep Downloads tidy by automatically filing PDFs, photos, videos, music, installers, and zip files into clear folders.",
        "presets": ["office_admin"],
        "show_maker_features": False,
        "default_rules": [
            {
                "name": "Invoices & Receipts",
                "folder": "Documents\\Invoices & Receipts",
                "match": ["invoice", "receipt", "billing", "statement", "payment"],
                "extensions": [".pdf", ".png", ".jpg", ".docx"],
                "enabled": True,
            },
            {
                "name": "Manuals & Guides",
                "folder": "Documents\\Manuals & Guides",
                "match": ["manual", "userguide", "datasheet", "quickstart", "handbook"],
                "extensions": [".pdf", ".txt"],
                "enabled": True,
            },
            {
                "name": "Travel & Bookings",
                "folder": "Documents\\Travel",
                "match": ["boarding_pass", "flight", "itinerary", "hotel_reservation"],
                "extensions": [".pdf", ".png", ".jpg"],
                "enabled": True,
            },
        ],
    },
    {
        "id": "academic_student",
        "name": "Student & Schoolwork",
        "icon": "🎓",
        "description": "Organize syllabi, homework assignments, lecture notes, study guides, and research papers alongside everyday downloads.",
        "presets": ["academic_student", "office_admin"],
        "show_maker_features": False,
        "default_rules": [
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
        "id": "office_admin",
        "name": "Office, Invoices & Work",
        "icon": "💼",
        "description": "Automatically sort invoices, receipts, tax forms, spreadsheets, resumes, and travel documents.",
        "presets": ["office_admin"],
        "show_maker_features": False,
        "default_rules": [
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
        "id": "maker_3d",
        "name": "3D Printing, CAD & CNC Maker",
        "icon": "🛠️",
        "description": "Full engineering suite: MakerWorld 3MF grace-period holds, Bambu/Orca slicer awareness, CAD assemblies, and Haas CNC toolpaths.",
        "presets": ["maker_3d", "office_admin"],
        "show_maker_features": True,
        "default_rules": [
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
]


def get_persona_profiles() -> list[dict[str, Any]]:
    """Return a deep copy of all available user persona profiles."""
    return copy.deepcopy(PERSONA_PROFILES)


def apply_persona_profile(config: dict[str, Any], persona_id: str) -> dict[str, Any]:
    """Apply a persona profile to the configuration without mutating DEFAULT_CONFIG.

    Validates persona_id, sets user_persona and onboarding_completed = True,
    and merges keyword rules tailored to the persona without duplicates.
    """
    profile = next((p for p in PERSONA_PROFILES if p["id"] == persona_id), None)
    if not profile:
        raise ValueError(f"Unknown persona ID: {persona_id}")

    updated = copy.deepcopy(config)
    updated["user_persona"] = persona_id
    updated["onboarding_completed"] = True
    if "show_maker_features" in profile:
        updated["show_maker_features"] = profile["show_maker_features"]
        # Pulling 3D/CAD files out of Documents only makes sense for makers; for
        # everyone else it would break projects (Blender, SolidWorks) kept there.
        if profile["show_maker_features"]:
            if not updated.get("document_sources"):
                updated["document_sources"] = list(MAKER_DOCUMENT_SOURCES)
        else:
            updated["document_sources"] = []

    # Populate default rules for the persona
    new_rules: list[dict[str, Any]] = copy.deepcopy(profile.get("default_rules", []))

    # Merge rules from referenced presets without duplicates
    for preset_id in profile.get("presets", []):
        preset = next((p for p in BUILTIN_PRESETS if p["id"] == preset_id), None)
        if preset:
            for rule in preset.get("rules", []):
                r_name = (rule.get("name") or "").lower()
                if r_name and not any((r.get("name") or "").lower() == r_name for r in new_rules):
                    new_rules.append(copy.deepcopy(rule))

    # Retain existing rules that don't collide with the persona's rules
    existing = updated.get("keyword_rules", [])
    for r in existing:
        if isinstance(r, dict) and r.get("name"):
            r_name = r["name"].lower()
            if not any((nr.get("name") or "").lower() == r_name for nr in new_rules):
                new_rules.append(copy.deepcopy(r))

    updated["keyword_rules"] = new_rules
    return updated


# =====================================================================
# SMART PERSONA PROFILE RECOMMENDATION
# =====================================================================

_MAKER_RECOMMEND_EXTENSIONS: frozenset[str] = frozenset({
    ".stl", ".3mf", ".obj", ".step", ".stp", ".iges", ".igs",
    ".f3d", ".f3z", ".sldprt", ".sldasm", ".ipt", ".iam",
    ".dwg", ".dxf", ".blend", ".fbx", ".glb", ".gltf", ".3ds",
    ".nc", ".tap", ".ngc", ".gcode", ".bgcode", ".gco", ".g",
})

# Names of 3D-printing sites, slicers and printers: a signal on any file type.
_MAKER_RECOMMEND_KEYWORDS: tuple[str, ...] = (
    "bambu", "bambulab", "orcaslicer", "prusa", "prusaslicer", "multicolor",
    "slicer", "sliced", "gcode", "haas", "toolpath", "3dprint", "3d_print",
    "3d print", "voron", "creality", "makerworld", "thingiverse", "printables", "cad",
)

# Part-shape words ("bracket", "gear", ...) are common in everyday filenames too
# (a USB adapter driver, a gear list), so they only count for downloaded archives,
# which is how model packs usually arrive.
_MAKER_PART_KEYWORDS: tuple[str, ...] = (
    "bracket", "mount", "enclosure", "adapter", "gear", "spacer",
    "chassis", "flange", "bushing",
)

_MAKER_ARCHIVE_EXTENSIONS: frozenset[str] = frozenset({".zip", ".7z", ".rar"})

_ACADEMIC_RECOMMEND_KEYWORDS: tuple[str, ...] = (
    "syllabus", "homework", "assignment", "lecture", "exam",
    "midterm", "quiz", "lab_report", "thesis", "dissertation",
    "arxiv", "problem_set", "study_guide", "course_outline",
    "class_notes", "handout", "reading", "textbook", "semester",
    "curriculum", "rubric", "worksheet",
)

_ACADEMIC_COURSE_CODE_RE = re.compile(r"(?:^|[^a-z0-9])[a-z]{2,4}\s*[-_]?\s*\d{3,4}[a-z]?(?:[^a-z0-9]|$)")

# Course codes ("ME 3210", "CS101") only count on document files: camera and
# screenshot names such as IMG_1234.jpg or DSC_0042.jpg have the same shape.
_ACADEMIC_COURSE_CODE_EXTENSIONS: frozenset[str] = frozenset({
    ".pdf", ".doc", ".docx", ".ppt", ".pptx", ".odt", ".odp", ".rtf", ".txt", ".md",
})

_OFFICE_RECOMMEND_KEYWORDS: tuple[str, ...] = (
    "invoice", "receipt", "billing", "statement", "tax", "w2",
    "w-2", "1099", "1040", "paystub", "pay_stub", "resume",
    "curriculum_vitae", "cv", "itinerary", "boarding_pass",
    "purchase_order", "remittance", "dividend", "financial",
    "expense", "budget", "balance_sheet", "flight", "hotel_reservation",
    "offer_letter", "cover_letter",
)

_OFFICE_SPREADSHEET_EXTS: frozenset[str] = frozenset({
    ".xlsx", ".xls", ".csv", ".xlsm",
})


def _has_word(name: str, keywords: tuple[str, ...]) -> bool:
    return any(_matches_keyword(name, kw) for kw in keywords)


def recommend_persona_profile(
    scan_dirs: list[Path] | Path | str | None = None,
) -> dict[str, Any]:
    """Perform a fast, 100% local scan (<50ms, <=500 files) to recommend a persona profile.

    Scores signals for maker_3d, academic_student, and office_admin, falling back to
    everyday_home when no specialized signals dominate or when 0 files are scanned.
    Never mutates sorter.DEFAULT_CONFIG.
    """
    target_dirs: list[Path] = []
    if scan_dirs is None:
        try:
            import sorter
            if hasattr(sorter, "get_downloads_dir"):
                d = sorter.get_downloads_dir()
                if isinstance(d, (Path, str)):
                    target_dirs.append(Path(d))
            elif hasattr(sorter, "watch_root"):
                target_dirs.append(sorter.watch_root({}))
        except Exception:
            pass
        if not target_dirs:
            target_dirs.append(Path.home() / "Downloads")
    elif isinstance(scan_dirs, (Path, str)):
        target_dirs.append(Path(scan_dirs))
    elif isinstance(scan_dirs, (list, tuple, set)):
        for item in scan_dirs:
            if isinstance(item, (Path, str)):
                target_dirs.append(Path(item))

    scores: dict[str, int] = {
        "maker_3d": 0,
        "academic_student": 0,
        "office_admin": 0,
        "everyday_home": 0,
    }
    match_counts: dict[str, int] = {
        "maker_3d": 0,
        "academic_student": 0,
        "office_admin": 0,
    }

    files_scanned = 0
    max_scan_files = 500

    for d in target_dirs:
        try:
            if not d.exists() or not d.is_dir():
                continue
            for entry in d.iterdir():
                if files_scanned >= max_scan_files:
                    break
                try:
                    if not entry.is_file():
                        continue
                except OSError:
                    continue

                files_scanned += 1
                name_lower = entry.name.lower()
                ext_lower = entry.suffix.lower()

                # Ignore temporary downloads
                if ext_lower in {".tmp", ".crdownload", ".part", ".partial", ".download"}:
                    continue

                # Match whole words only ("tax" must not fire on "syntax", "mount"
                # on "amount", "cad" on "decade").
                stem_lower = entry.stem.lower()

                maker_hit = False
                if ext_lower in _MAKER_RECOMMEND_EXTENSIONS:
                    scores["maker_3d"] += 3
                    maker_hit = True
                elif _has_word(stem_lower, _MAKER_RECOMMEND_KEYWORDS) or (
                    ext_lower in _MAKER_ARCHIVE_EXTENSIONS and _has_word(stem_lower, _MAKER_PART_KEYWORDS)
                ):
                    scores["maker_3d"] += 2
                    maker_hit = True
                if maker_hit:
                    match_counts["maker_3d"] += 1

                academic_hit = False
                if _has_word(stem_lower, _ACADEMIC_RECOMMEND_KEYWORDS):
                    scores["academic_student"] += 3
                    academic_hit = True
                elif (
                    ext_lower in _ACADEMIC_COURSE_CODE_EXTENSIONS
                    and _ACADEMIC_COURSE_CODE_RE.search(stem_lower)
                ):
                    scores["academic_student"] += 3
                    academic_hit = True
                if academic_hit:
                    match_counts["academic_student"] += 1

                office_hit = False
                if _has_word(stem_lower, _OFFICE_RECOMMEND_KEYWORDS):
                    scores["office_admin"] += 3
                    office_hit = True
                elif ext_lower in _OFFICE_SPREADSHEET_EXTS:
                    scores["office_admin"] += 1
                    office_hit = True
                if office_hit:
                    match_counts["office_admin"] += 1

        except (PermissionError, OSError):
            continue

    specialized = ["maker_3d", "academic_student", "office_admin"]
    max_score = max(scores[k] for k in specialized)

    if max_score <= 0 or files_scanned == 0:
        recommended_id = "everyday_home"
        reason = "Everyday & Home PC is recommended to keep your photos, music, documents, and downloads tidy!"
    else:
        # Highest score wins; ties broken by specialized order
        recommended_id = max(specialized, key=lambda k: scores[k])
        if recommended_id == "maker_3d":
            count = match_counts["maker_3d"]
            file_word = "file" if count == 1 else "files"
            reason = f"We spotted {count} 3D/CAD or Maker {file_word} in your Downloads — 3D Printing, CAD & CNC Maker is recommended for your PC!"
        elif recommended_id == "academic_student":
            count = match_counts["academic_student"]
            file_word = "file" if count == 1 else "files"
            reason = f"We spotted {count} coursework or study {file_word} in your Downloads — Student & Schoolwork is recommended for your PC!"
        elif recommended_id == "office_admin":
            count = match_counts["office_admin"]
            file_word = "file" if count == 1 else "files"
            reason = f"We spotted {count} invoices, receipts, or work documents in your Downloads — Office, Invoices & Work is recommended for your PC!"
        else:
            reason = "Everyday & Home PC is recommended to keep your photos, music, documents, and downloads tidy!"

    return {
        "recommended_id": recommended_id,
        "reason": reason,
        "scores": scores,
        "files_scanned": files_scanned,
    }


# =====================================================================
# RECENT MOVE ACTIVITY EXTRACTION
# =====================================================================

def get_recent_activity(
    limit: int = 25,
    history_path: Path | str | None = None,
    include_undone: bool = False,
) -> list[dict[str, Any]]:
    """Read recent user-visible file moves from history.jsonl.

    Returns up to `limit` records, newest first, excluding bookkeeping and undone moves.
    """
    if limit <= 0:
        return []

    from sorter import HISTORY_FILE, NON_MOVE_ACTIONS

    target = Path(history_path) if history_path else HISTORY_FILE
    if not target.exists():
        return []

    records: list[dict[str, Any]] = []
    undone_ids: set[str] = set()

    try:
        with target.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if isinstance(rec, dict):
                        if rec.get("action") == "undo" and "ref_id" in rec:
                            undone_ids.add(str(rec["ref_id"]))
                        records.append(rec)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []

    activity: list[dict[str, Any]] = []
    for rec in reversed(records):
        action = rec.get("action")
        if action in NON_MOVE_ACTIONS:
            continue
        dest_str = rec.get("dest")
        if not dest_str or not isinstance(dest_str, str):
            continue
        rec_id = str(rec.get("id", ""))
        is_undone = rec_id in undone_ids
        if is_undone and not include_undone:
            continue

        dest_path = Path(dest_str)
        try:
            exists = dest_path.exists()
        except Exception:
            exists = False

        folder_name = dest_path.parent.name if dest_path.parent.name else "Downloads"

        activity.append({
            "id": rec_id,
            "batch_id": str(rec.get("batch_id", "")),
            "filename": dest_path.name,
            "src": str(rec.get("src", "")),
            "dest": dest_str,
            "folder": folder_name,
            "timestamp": str(rec.get("timestamp", "")),
            "exists": exists,
            "undone": is_undone,
        })
        if len(activity) >= limit:
            break

    return activity


# =====================================================================
# WINDOWS STARTUP & FILE EXPLORER CONTEXT MENU INTEGRATION
# =====================================================================

def _get_launch_command(args: list[str]) -> str:
    """Construct an appropriate windowless launch command for frozen or dev execution."""
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve()
        arg_str = " ".join(args)
        return f'"{exe}" {arg_str}'.strip()

    # Development mode: prefer pythonw.exe if available to prevent console popup
    python = Path(sys.executable).resolve()
    if python.name.lower() == "python.exe":
        pythonw = python.with_name("pythonw.exe")
        if pythonw.exists():
            python = pythonw
    main_py = Path(__file__).resolve().parent / "main.py"
    arg_str = " ".join(args)
    return f'"{python}" "{main_py}" {arg_str}'.strip()


def _delete_reg_key_tree(root: Any, subkey: str) -> None:
    """Recursively delete a Windows registry key and all child subkeys safely."""
    if sys.platform != "win32":
        return
    import winreg
    try:
        # Enumerate all child subkey names first with KEY_READ
        children: list[str] = []
        try:
            with winreg.OpenKey(root, subkey, 0, winreg.KEY_READ) as key:
                index = 0
                while True:
                    try:
                        child = winreg.EnumKey(key, index)
                        children.append(child)
                        index += 1
                    except OSError:
                        break
        except (FileNotFoundError, OSError):
            return

        # Recursively delete each child
        for child in children:
            _delete_reg_key_tree(root, f"{subkey}\\{child}")

        # Delete the key itself
        try:
            winreg.DeleteKey(root, subkey)
        except OSError:
            pass
    except Exception:
        pass


def is_startup_enabled() -> bool:
    """Return True if Download Sorter is registered in HKCU Run key."""
    if sys.platform != "win32":
        return False
    import winreg
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_READ,
        ) as key:
            val, _ = winreg.QueryValueEx(key, "DownloadSorter")
            return bool(val)
    except (FileNotFoundError, OSError):
        return False


def set_startup_enabled(enabled: bool) -> bool:
    """Enable or disable startup at Windows login via HKCU Run key."""
    if sys.platform != "win32":
        return False
    import winreg
    try:
        if enabled:
            with winreg.CreateKeyEx(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE,
            ) as key:
                cmd = _get_launch_command(["--tray"])
                winreg.SetValueEx(key, "DownloadSorter", 0, winreg.REG_SZ, cmd)
        else:
            try:
                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run",
                    0,
                    winreg.KEY_SET_VALUE,
                ) as key:
                    winreg.DeleteValue(key, "DownloadSorter")
            except (FileNotFoundError, OSError):
                pass
        return True
    except OSError:
        return False


def is_context_menu_enabled() -> bool:
    """Return True if Explorer context menu integration is active in HKCU Classes."""
    if sys.platform != "win32":
        return False
    import winreg
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Classes\Directory\shell\DownloadSorter",
            0,
            winreg.KEY_READ,
        ) as key:
            return True
    except (FileNotFoundError, OSError):
        return False


def set_context_menu_enabled(enabled: bool) -> bool:
    """Enable or disable File Explorer context menus without console flashing."""
    if sys.platform != "win32":
        return False
    import winreg

    folder_sort_key = r"Software\Classes\Directory\shell\DownloadSorter"
    bg_undo_key = r"Software\Classes\Directory\Background\shell\DownloadSorterUndo"

    if not enabled:
        _delete_reg_key_tree(winreg.HKEY_CURRENT_USER, folder_sort_key)
        _delete_reg_key_tree(winreg.HKEY_CURRENT_USER, bg_undo_key)
        return True

    try:
        sort_cmd = _get_launch_command(["--sort", '--target "%1"', "--notify"])
        undo_cmd = _get_launch_command(["--undo", "--notify"])

        # 1. Directory sort menu
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, folder_sort_key) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "Sort with DownloadSorter")
            winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, "imageres.dll,3")
            with winreg.CreateKey(key, "command") as cmd_key:
                winreg.SetValueEx(cmd_key, "", 0, winreg.REG_SZ, sort_cmd)

        # 2. Directory Background undo menu
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, bg_undo_key) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "Undo Last DownloadSorter Batch")
            winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, "imageres.dll,102")
            with winreg.CreateKey(key, "command") as cmd_key:
                winreg.SetValueEx(cmd_key, "", 0, winreg.REG_SZ, undo_cmd)

        return True
    except OSError:
        return False

