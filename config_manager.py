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
    """Load the settings: the same loader the watcher uses (sorter.load_config_from), so the
    window and the sorter always agree, including falling back to the .bak copy."""
    import sorter

    target = Path(config_path) if config_path else SORTER_CONFIG_FILE
    return sorter.load_config_from(target, DEFAULT_CONFIG_PATH if config_path is None else None)


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


def current_sorter_config(fallback: dict[str, Any]) -> dict[str, Any]:
    """The settings as saved right now, or ``fallback`` when there is no settings file yet.

    Pages that change one thing (a Settings option, the rules) start from this
    instead of the copy they loaded earlier, so they don't undo a change made
    meanwhile somewhere else: another page, or a rule added from a learning
    ribbon (which runs in its own process).
    """
    if not Path(SORTER_CONFIG_FILE).exists():
        return fallback
    try:
        return load_sorter_config()
    except Exception:
        return fallback


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
    """Maker / 3D projects for the Active Project picker.

    Optional: reads the Print Library app's database when that app is
    installed, otherwise folders in Documents\\3D Projects, otherwise nothing.
    """
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
                    "invoice", "receipt", "billing", "billing_statement", "account_statement",
                    "card_statement", "payment_confirmation", "payment_receipt",
                    "order_confirmation", "tax_invoice", "purchase_order", "remittance",
                ],
                "extensions": [".pdf", ".png", ".jpg", ".docx", ".xlsx", ".csv"],
                "enabled": True,
            },
            {
                "name": "Financial & Taxes",
                "folder": "Documents\\Financial & Tax",
                "match": [
                    "tax_return", "tax_form", "tax_summary", "tax_documents", "w-2", "1099", "1040",
                    "bank_statement", "paystub", "pay_stub", "w-9", "dividend",
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
                    "class_notes", "handout", "reading_list", "required_reading",
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
                    "slice", "layer", "toolpath", "contour", "gcode",
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
                "extensions": [".step", ".stp", ".iges", ".f3d", ".sldprt", ".ipt"],
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
        "name": "Everyday",
        "icon": "profile_home",
        "description": "Documents, photos, videos, music, installers and zip files each go into their own folder, and so do receipts, manuals and travel bookings.",
        "presets": [],
        "show_maker_features": False,
        "default_rules": [
            {
                "name": "Invoices & Receipts",
                "folder": "Documents\\Invoices & Receipts",
                "match": ["invoice", "receipt", "billing", "billing_statement", "payment_confirmation"],
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
        "name": "Student",
        "icon": "profile_school",
        "description": "Everything in Everyday, plus folders for syllabi, notes, assignments, exams and research papers.",
        "presets": ["academic_student"],
        "includes": "everyday_home",
        "show_maker_features": False,
        "default_rules": [
            {
                "name": "Syllabi & Notes",
                "folder": "Documents\\School\\Notes",
                "match": [
                    "syllabus", "lecture", "lecture_notes", "course_outline",
                    "class_notes", "handout", "reading_list", "required_reading",
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
        "name": "Office and work",
        "icon": "profile_office",
        "description": "Everything in Everyday, plus folders for invoices, tax forms, bank statements and résumés.",
        "presets": ["office_admin"],
        "includes": "everyday_home",
        "show_maker_features": False,
        "default_rules": [
            {
                "name": "Invoices & Receipts",
                "folder": "Documents\\Invoices & Receipts",
                "match": [
                    "invoice", "receipt", "billing", "billing_statement", "account_statement",
                    "card_statement", "payment_confirmation", "payment_receipt",
                    "order_confirmation", "tax_invoice", "purchase_order", "remittance",
                ],
                "extensions": [".pdf", ".png", ".jpg", ".docx", ".xlsx", ".csv"],
                "enabled": True,
            },
            {
                "name": "Financial & Taxes",
                "folder": "Documents\\Financial & Tax",
                "match": [
                    "tax_return", "tax_form", "tax_summary", "tax_documents", "w-2", "1099", "1040",
                    "bank_statement", "paystub", "pay_stub", "w-9", "dividend",
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
        "name": "3D printing, CAD and CNC",
        "icon": "profile_maker",
        "description": "Everything in Everyday, plus 3D models, slicer projects and G-code. Also moves stray CAD and 3D files out of Documents.",
        "presets": [],
        "includes": "everyday_home",
        "show_maker_features": True,
        # No keyword rules of its own: the file-type rules and the 3MF, MakerWorld
        # and G-code detectors already file maker downloads, and name rules here
        # fought them ("haas" beat the Haas detector, "gear.stl" left 3D Models\\STL).
        "default_rules": [],
    },
]


# Set up from the user's own folders (folder_scan.py), with no starter rules.
CUSTOM_PROFILE_ID = "custom"


def get_persona_profiles() -> list[dict[str, Any]]:
    """Return a deep copy of all available user persona profiles."""
    return copy.deepcopy(PERSONA_PROFILES)


def profile_rules(persona_id: str) -> list[dict[str, Any]]:
    """The keyword rules a profile sets up: its own, its presets', and those of
    the profile it builds on ("Everything in Everyday, plus...")."""
    profile = next((p for p in PERSONA_PROFILES if p["id"] == persona_id), None)
    if not profile:
        raise ValueError(f"Unknown persona ID: {persona_id}")
    rules: list[dict[str, Any]] = []

    def add(rule: dict[str, Any]) -> None:
        name = (rule.get("name") or "").lower()
        if name and not any((r.get("name") or "").lower() == name for r in rules):
            rules.append(copy.deepcopy(rule))

    for rule in profile.get("default_rules", []):
        add(rule)
    for preset_id in profile.get("presets", []):
        preset = next((p for p in BUILTIN_PRESETS if p["id"] == preset_id), None)
        for rule in (preset or {}).get("rules", []):
            add(rule)
    if profile.get("includes"):
        for rule in profile_rules(profile["includes"]):
            add(rule)
    return rules


# Words earlier versions put in the built-in rules, so rules saved by an older
# version still count as untouched.
_LEGACY_PROFILE_RULE_WORDS: frozenset[str] = frozenset({
    "statement", "payment", "tax", "w-2", "w-9", "reading", "haas",
})


def _builtin_rule_words() -> dict[tuple[str, str], set[str]]:
    words: dict[tuple[str, str], set[str]] = {}
    sources = [p.get("default_rules", []) for p in PERSONA_PROFILES]
    sources += [p.get("rules", []) for p in BUILTIN_PRESETS]
    for rules in sources:
        for rule in rules:
            key = ((rule.get("name") or "").lower(), (rule.get("folder") or "").lower())
            words.setdefault(key, set()).update(str(w).lower() for w in rule.get("match", []))
    return words


def is_untouched_profile_rule(rule: dict[str, Any]) -> bool:
    """True when a rule is a built-in profile rule the user has not added words to."""
    key = ((rule.get("name") or "").lower(), (rule.get("folder") or "").lower())
    known = _builtin_rule_words().get(key)
    if known is None:
        return False
    words = {str(w).lower() for w in rule.get("match", [])}
    return words <= (known | _LEGACY_PROFILE_RULE_WORDS)


def apply_persona_profile(config: dict[str, Any], persona_id: str) -> dict[str, Any]:
    """Apply a persona profile to the configuration without mutating DEFAULT_CONFIG.

    Validates persona_id, sets user_persona and onboarding_completed = True,
    and merges keyword rules tailored to the persona without duplicates.
    """
    if persona_id == CUSTOM_PROFILE_ID:
        # "Just my folders": no starter rules. Built-in rules nobody added words
        # to are dropped; the user's own, scanned and learned rules stay.
        updated = copy.deepcopy(config)
        updated["user_persona"] = CUSTOM_PROFILE_ID
        updated["onboarding_completed"] = True
        updated["keyword_rules"] = [
            copy.deepcopy(r) for r in updated.get("keyword_rules", [])
            if isinstance(r, dict) and r.get("name") and not is_untouched_profile_rule(r)
        ]
        return updated

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

    new_rules = profile_rules(persona_id)
    new_names = {(r.get("name") or "").lower() for r in new_rules}

    # Keep the user's own rules. A rule an earlier profile added (same name and
    # folder as a built-in rule, no words the user typed in) is dropped, so
    # switching from 3D printing to Student does not leave CAD rules behind.
    for r in updated.get("keyword_rules", []):
        if not isinstance(r, dict) or not r.get("name"):
            continue
        if r["name"].lower() in new_names or is_untouched_profile_rule(r):
            continue
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
    "invoice", "receipt", "billing", "bank_statement", "tax_return", "w2",
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
        reason = "Everyday is a good fit for most PCs."
    else:
        # Highest score wins; ties broken by specialized order
        recommended_id = max(specialized, key=lambda k: scores[k])
        if recommended_id == "maker_3d":
            count = match_counts["maker_3d"]
            file_word = "file" if count == 1 else "files"
            reason = f"I found {count} 3D or CAD {file_word} in your Downloads, so I'd suggest the 3D printing profile."
        elif recommended_id == "academic_student":
            count = match_counts["academic_student"]
            file_word = "file" if count == 1 else "files"
            reason = f"I found {count} coursework {file_word} in your Downloads, so I'd suggest the Student profile."
        elif recommended_id == "office_admin":
            count = match_counts["office_admin"]
            file_word = "file" if count == 1 else "files"
            reason = f"I found {count} invoices, receipts or work {file_word} in your Downloads, so I'd suggest the Office profile."
        else:
            reason = "Everyday is a good fit for most PCs."

    return {
        "recommended_id": recommended_id,
        "reason": reason,
        "scores": scores,
        "files_scanned": files_scanned,
    }


# =====================================================================
# RECENT MOVE ACTIVITY EXTRACTION
# =====================================================================

_WHY_TEXT = {
    "extension": "Filed by its file type",
    "my_folder": "Filed into a folder you made",
    "gcode_sniffer": "Filed by what is inside the file",
    "makerworld": "Filed as a MakerWorld model",
    "makerworld_kit": "Filed with the rest of its MakerWorld model",
    "pdf_content": "Filed by the words on its first page",
    "photo_kind": "Filed by the kind of picture it is",
    "fallback": "No rule matched, so it went to the catch-all folder",
    "learned": "Filed where you put files like it",
}


def describe_why(record: dict[str, Any]) -> str:
    """One or two plain lines on why a file was filed where it was ("" for a move from
    before this was recorded): 'Filed by your rule "Banking"' / 'Downloaded from yourbank.com'."""
    why = str(record.get("why") or "")
    rule = str(record.get("rule") or "")
    lines: list[str] = []
    if why == "keyword" and rule:
        lines.append(f'Filed by your rule "{rule}"')
    elif why == "active_project" and rule:
        lines.append(f'Filed into the active project "{rule}"')
    elif why in _WHY_TEXT:
        lines.append(_WHY_TEXT[why])
    site = str(record.get("site") or "")
    if site:
        lines.append(f"Downloaded from {site}")
    return "\n".join(lines)


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

    def path_key(value: str) -> str:
        return os.path.normcase(os.path.normpath(value))

    # Newest first, so by the time an older record is looked at we know every place a
    # newer move took a file *from*. A file that was moved on (by a later sort, or by
    # you from Home's menu) shows once, where it is now, not also where it used to be.
    moved_on: set[str] = set()
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

        superseded = path_key(dest_str) in moved_on
        src_value = rec.get("src")
        if not is_undone and isinstance(src_value, str) and src_value:
            moved_on.add(path_key(src_value))
        if superseded:
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
            "why": str(rec.get("why", "")),
            "rule": str(rec.get("rule", "")),
            "site": str(rec.get("site", "")),
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



# =====================================================================
# WINDOWS STORAGE SENSE
# =====================================================================

def storage_sense_downloads_days() -> int | None:
    """Days after which Windows Storage Sense deletes files in Downloads, or None.

    Storage Sense can be set to "Delete files in my Downloads folder if they
    haven't been opened for more than N days". The folders Download Sorter
    files into are inside Downloads, so that would delete sorted files too.
    Read-only: a school or company policy wins over the user's own setting.
    """
    if sys.platform != "win32":
        return None
    import winreg

    def read(root: Any, subkey: str, name: str) -> int | None:
        try:
            with winreg.OpenKey(root, subkey, 0, winreg.KEY_READ) as key:
                value, _ = winreg.QueryValueEx(key, name)
                return int(value)
        except (OSError, ValueError, TypeError):
            return None

    policy = r"SOFTWARE\Policies\Microsoft\Windows\StorageSense"
    if read(winreg.HKEY_LOCAL_MACHINE, policy, "AllowStorageSenseGlobal") == 0:
        return None
    days = read(winreg.HKEY_LOCAL_MACHINE, policy, "ConfigStorageSenseDownloadsCleanupThreshold")
    if days is not None:
        return days or None
    user = r"Software\Microsoft\Windows\CurrentVersion\StorageSense\Parameters\StoragePolicy"
    if read(winreg.HKEY_CURRENT_USER, user, "01") != 1:
        return None  # Storage Sense is off
    days = read(winreg.HKEY_CURRENT_USER, user, "512")
    return days or None
