"""Typed configuration schema, starter templates, atomic persistence, and v2 importer."""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import shutil
import time
import uuid
from typing import Any

from download_sorter.paths import AppPaths

DEFAULT_IGNORE_EXTENSIONS: list[str] = [
    ".crdownload",
    ".part",
    ".partial",
    ".tmp",
    ".download",
    ".opdownload",
]

DEFAULT_IGNORE_NAMES: list[str] = [
    "desktop.ini",
    "thumbs.db",
]

DEFAULT_CATEGORY_MAP: dict[str, str] = {
    # Documents
    ".pdf": "Documents",
    ".doc": "Documents",
    ".docx": "Documents",
    ".odt": "Documents",
    ".rtf": "Documents",
    ".txt": "Documents",
    ".md": "Documents",
    ".xls": "Documents",
    ".xlsx": "Documents",
    ".xlsm": "Documents",
    ".csv": "Documents",
    ".ods": "Documents",
    ".ppt": "Documents",
    ".pptx": "Documents",
    ".odp": "Documents",
    ".key": "Documents",
    ".epub": "Documents",
    ".mobi": "Documents",
    ".tex": "Documents",
    ".bib": "Documents",
    ".pkpass": "Documents",
    # 3D Models
    ".stl": "3D Models",
    ".3mf": "3D Models",
    ".obj": "3D Models",
    ".3ds": "3D Models",
    ".fbx": "3D Models",
    ".blend": "3D Models",
    ".glb": "3D Models",
    ".gltf": "3D Models",
    ".gcode": "3D Models",
    ".bgcode": "3D Models",
    ".nc": "3D Models",
    ".tap": "3D Models",
    ".ngc": "3D Models",
    ".gco": "3D Models",
    ".g": "3D Models",
    # CAD
    ".step": "CAD",
    ".stp": "CAD",
    ".iges": "CAD",
    ".igs": "CAD",
    ".f3d": "CAD",
    ".f3z": "CAD",
    ".ipt": "CAD",
    ".iam": "CAD",
    ".sldprt": "CAD",
    ".sldasm": "CAD",
    ".slddrw": "CAD",
    ".dwg": "CAD",
    ".dxf": "CAD",
    # Images
    ".jpg": "Images",
    ".jpeg": "Images",
    ".png": "Images",
    ".gif": "Images",
    ".webp": "Images",
    ".heic": "Images",
    ".svg": "Images",
    ".bmp": "Images",
    ".tif": "Images",
    ".tiff": "Images",
    ".ico": "Images",
    ".ai": "Images",
    ".eps": "Images",
    ".cdr": "Images",
    # Videos
    ".mp4": "Videos",
    ".mov": "Videos",
    ".mkv": "Videos",
    ".avi": "Videos",
    ".webm": "Videos",
    ".flv": "Videos",
    ".wmv": "Videos",
    ".m4v": "Videos",
    # Audio
    ".mp3": "Audio",
    ".wav": "Audio",
    ".flac": "Audio",
    ".m4a": "Audio",
    ".aac": "Audio",
    ".ogg": "Audio",
    ".aiff": "Audio",
    ".wma": "Audio",
    # Archives
    ".zip": "Archives",
    ".rar": "Archives",
    ".7z": "Archives",
    ".tar": "Archives",
    ".gz": "Archives",
    ".bz2": "Archives",
    ".xz": "Archives",
    ".tgz": "Archives",
    ".iso": "Archives",
    # Installers
    ".exe": "Installers",
    ".msi": "Installers",
    ".msix": "Installers",
    # Code & Data
    ".py": "Code",
    ".js": "Code",
    ".ts": "Code",
    ".jsx": "Code",
    ".tsx": "Code",
    ".html": "Code",
    ".css": "Code",
    ".cpp": "Code",
    ".c": "Code",
    ".h": "Code",
    ".hpp": "Code",
    ".cs": "Code",
    ".java": "Code",
    ".rs": "Code",
    ".go": "Code",
    ".json": "Code",
    ".yaml": "Code",
    ".yml": "Code",
    ".toml": "Code",
    ".xml": "Code",
    ".ini": "Code",
    ".env": "Code",
    ".sql": "Code",
    ".db": "Code",
    ".sqlite": "Code",
    ".parquet": "Code",
    ".feather": "Code",
    ".log": "Code",
    ".dmp": "Code",
    ".out": "Code",
}

DEFAULT_TYPE_SUBFOLDERS: dict[str, str] = {
    ".pdf": "PDF",
    ".doc": "Word",
    ".docx": "Word",
    ".xls": "Spreadsheets",
    ".xlsx": "Spreadsheets",
    ".xlsm": "Spreadsheets",
    ".csv": "Spreadsheets",
    ".ods": "Spreadsheets",
    ".ppt": "Presentations",
    ".pptx": "Presentations",
    ".odp": "Presentations",
    ".key": "Presentations",
    ".txt": "Text",
    ".md": "Text",
    ".rtf": "Text",
    ".odt": "Text",
    ".epub": "Ebooks",
    ".mobi": "Ebooks",
    ".3mf": "3MF",
    ".stl": "STL",
    ".obj": "OBJ",
    ".gcode": "G-Code",
    ".bgcode": "G-Code",
    ".nc": "CNC",
    ".tap": "CNC",
    ".ngc": "CNC",
    ".gco": "G-Code",
    ".g": "G-Code",
    ".blend": "Meshes",
    ".fbx": "Meshes",
    ".glb": "Meshes",
    ".gltf": "Meshes",
    ".3ds": "Meshes",
    ".step": "STEP",
    ".stp": "STEP",
    ".iges": "IGES",
    ".igs": "IGES",
    ".f3d": "Fusion 360",
    ".f3z": "Fusion 360",
    ".sldprt": "SolidWorks",
    ".sldasm": "SolidWorks",
    ".slddrw": "SolidWorks",
    ".ipt": "Inventor",
    ".iam": "Inventor",
    ".dwg": "2D Drawings",
    ".dxf": "2D Drawings",
    ".jpg": "JPG",
    ".jpeg": "JPG",
    ".png": "PNG",
    ".gif": "GIF",
    ".webp": "WEBP",
    ".heic": "HEIC",
    ".svg": "SVG",
    ".bmp": "BMP",
    ".tif": "TIFF",
    ".tiff": "TIFF",
}


def _normalize_ext_list(values: Any) -> list[str]:
    result: list[str] = []
    for raw in values or []:
        text = str(raw).strip().lower()
        if not text:
            continue
        if not text.startswith("."):
            text = "." + text
        if text not in result:
            result.append(text)
    return result


def _normalize_str_list(values: Any) -> list[str]:
    result: list[str] = []
    for raw in values or []:
        text = str(raw).strip()
        if text and text not in result:
            result.append(text)
    return result


@dataclass
class Rule:
    """An explicit top-to-bottom sorting rule."""

    id: str
    name: str
    destination: str
    keywords: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    extensions: list[str] = field(default_factory=list)
    enabled: bool = True

    def __post_init__(self) -> None:
        self.id = str(self.id or uuid.uuid4().hex[:8])
        self.name = str(self.name).strip()
        self.destination = str(self.destination).strip().replace("/", "\\").strip("\\")
        self.keywords = _normalize_str_list(self.keywords)
        self.domains = [d.lower() for d in _normalize_str_list(self.domains)]
        self.extensions = _normalize_ext_list(self.extensions)
        self.enabled = bool(self.enabled)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Rule:
        return cls(
            id=str(data.get("id") or uuid.uuid4().hex[:8]),
            name=str(data.get("name", "Untitled Rule")),
            destination=str(data.get("destination") or data.get("folder") or "Other"),
            keywords=list(data.get("keywords") if "keywords" in data else data.get("match", [])),
            domains=list(data.get("domains") if "domains" in data else data.get("sites", [])),
            extensions=list(data.get("extensions", [])),
            enabled=bool(data.get("enabled", True)),
        )


STARTER_TEMPLATES: dict[str, list[Rule]] = {
    "school": [
        Rule(
            id="tpl-school-sites",
            name="School websites",
            destination=r"Documents\School",
            domains=[
                "instructure.com",
                "blackboard.com",
                "brightspace.com",
                "d2l.com",
                "classroom.google.com",
                "gradescope.com",
                "piazza.com",
                "schoology.com",
                "moodlecloud.com",
            ],
            extensions=[".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".txt", ".zip", ".ipynb", ".py"],
        ),
        Rule(
            id="tpl-school-keywords",
            name="School coursework",
            destination=r"Documents\School",
            keywords=[
                "syllabus",
                "lecture",
                "lecture_notes",
                "class_notes",
                "homework",
                "assignment",
                "lab_report",
                "problem_set",
                "study_guide",
                "midterm",
                "final_exam",
            ],
            extensions=[".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xlsx", ".txt"],
        ),
    ],
    "career": [
        Rule(
            id="tpl-career-keywords",
            name="Resumes & Career",
            destination=r"Documents\Career",
            keywords=[
                "resume",
                "cv",
                "cover_letter",
                "offer_letter",
                "job_application",
                "career_statement",
                "goals_statement",
                "scholarship",
            ],
            extensions=[".pdf", ".doc", ".docx"],
        ),
    ],
    "3d_printing": [
        Rule(
            id="tpl-makerworld",
            name="MakerWorld Downloads",
            destination=r"3D Models\MakerWorld",
            domains=["makerworld.com"],
            extensions=[".3mf", ".stl", ".step", ".stp", ".zip"],
        ),
        Rule(
            id="tpl-3d-sites",
            name="Printables & Thingiverse",
            destination=r"3D Models\Downloaded Models",
            domains=["printables.com", "thingiverse.com", "thangs.com"],
            extensions=[".3mf", ".stl", ".step", ".stp"],
        ),
    ],
}


@dataclass
class Config:
    """Validated Download Sorter v3 configuration."""

    schema_version: int = 3
    watch_folder: str = r"${USERPROFILE}\Downloads"
    settle_seconds: float = 3.0
    fallback_mode: str = "other"  # "other" or "leave"
    default_folder: str = "Other"
    use_type_subfolders: bool = True
    notifications_enabled: bool = True
    ribbon_timeout_seconds: float = 6.0
    hold_3mf_enabled: bool = True
    hold_3mf_days: int = 7
    quick_switcher_enabled: bool = True
    quick_switcher_timeout_seconds: float = 300.0
    quick_switcher_3d_timeout_hours: float = 12.0
    quick_switcher_hotkey: str = "Ctrl+G"
    quick_switcher_hotkey_all: str = "Ctrl+Shift+G"
    quick_switcher_hotkey_copy: str = "Ctrl+Shift+C"
    paused: bool = False
    tutorial_completed: bool = False
    check_for_updates: bool = True
    rules: list[Rule] = field(default_factory=list)
    category_map: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_CATEGORY_MAP))
    type_subfolders: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_TYPE_SUBFOLDERS))
    ignore_extensions: list[str] = field(default_factory=lambda: list(DEFAULT_IGNORE_EXTENSIONS))
    ignore_names: list[str] = field(default_factory=lambda: list(DEFAULT_IGNORE_NAMES))
    architecture: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.schema_version = 3
        self.watch_folder = str(self.watch_folder or r"${USERPROFILE}\Downloads")
        self.settle_seconds = max(0.0, float(self.settle_seconds))
        self.fallback_mode = self.fallback_mode if self.fallback_mode in ("other", "leave") else "other"
        self.default_folder = str(self.default_folder or "Other").strip().strip("\\") or "Other"
        self.use_type_subfolders = bool(self.use_type_subfolders)
        self.notifications_enabled = bool(self.notifications_enabled)
        self.ribbon_timeout_seconds = max(1.0, min(60.0, float(self.ribbon_timeout_seconds)))
        self.hold_3mf_enabled = bool(self.hold_3mf_enabled)
        self.hold_3mf_days = max(1, int(self.hold_3mf_days))
        self.quick_switcher_enabled = bool(self.quick_switcher_enabled)
        self.quick_switcher_timeout_seconds = max(5.0, float(self.quick_switcher_timeout_seconds))
        self.quick_switcher_3d_timeout_hours = max(0.1, float(self.quick_switcher_3d_timeout_hours))
        self.quick_switcher_hotkey = str(self.quick_switcher_hotkey or "Ctrl+G").strip()
        self.quick_switcher_hotkey_all = str(self.quick_switcher_hotkey_all or "Ctrl+Shift+G").strip()
        self.quick_switcher_hotkey_copy = str(self.quick_switcher_hotkey_copy or "Ctrl+Shift+C").strip()
        self.paused = bool(self.paused)
        self.tutorial_completed = bool(self.tutorial_completed)
        self.check_for_updates = bool(self.check_for_updates)
        self.rules = [r if isinstance(r, Rule) else Rule.from_dict(r) for r in self.rules]
        self.category_map = {
            (k if k.startswith(".") else "." + k).lower(): str(v).strip().replace("/", "\\").strip("\\")
            for k, v in (self.category_map or DEFAULT_CATEGORY_MAP).items()
            if str(k).strip() and str(v).strip()
        }
        self.type_subfolders = {
            (k if k.startswith(".") else "." + k).lower(): str(v).strip().replace("/", "\\").strip("\\")
            for k, v in (self.type_subfolders or DEFAULT_TYPE_SUBFOLDERS).items()
            if str(k).strip() and str(v).strip()
        }
        self.ignore_extensions = _normalize_ext_list(self.ignore_extensions or DEFAULT_IGNORE_EXTENSIONS)
        self.ignore_names = [n.lower() for n in _normalize_str_list(self.ignore_names or DEFAULT_IGNORE_NAMES)]
        self.architecture = dict(self.architecture) if isinstance(self.architecture, dict) else {}

    @classmethod
    def with_starter_defaults(cls, templates: tuple[str, ...] = ("school", "career", "3d_printing")) -> Config:
        rules: list[Rule] = []
        for key in templates:
            for tpl in STARTER_TEMPLATES.get(key, ()):
                rules.append(Rule.from_dict(tpl.to_dict()))
        return cls(rules=rules)

    def resolved_watch_folder(self, paths: AppPaths | None = None) -> Path:
        if paths is not None and paths.downloads_dir is not None:
            return paths.downloads_dir.resolve()
        expanded = os.path.expandvars(os.path.expanduser(self.watch_folder))
        return Path(expanded).resolve()

    def apply_template(self, template_key: str) -> int:
        """Append rules from ``STARTER_TEMPLATES`` if not already present by name. Returns count added."""
        existing_names = {r.name.lower() for r in self.rules}
        added = 0
        for tpl in STARTER_TEMPLATES.get(template_key, ()):
            if tpl.name.lower() not in existing_names:
                self.rules.append(Rule.from_dict(tpl.to_dict()))
                existing_names.add(tpl.name.lower())
                added += 1
        return added

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "watch_folder": self.watch_folder,
            "settle_seconds": self.settle_seconds,
            "fallback_mode": self.fallback_mode,
            "default_folder": self.default_folder,
            "use_type_subfolders": self.use_type_subfolders,
            "notifications_enabled": self.notifications_enabled,
            "ribbon_timeout_seconds": self.ribbon_timeout_seconds,
            "hold_3mf_enabled": self.hold_3mf_enabled,
            "hold_3mf_days": self.hold_3mf_days,
            "quick_switcher_enabled": self.quick_switcher_enabled,
            "quick_switcher_timeout_seconds": self.quick_switcher_timeout_seconds,
            "quick_switcher_3d_timeout_hours": self.quick_switcher_3d_timeout_hours,
            "quick_switcher_hotkey": self.quick_switcher_hotkey,
            "quick_switcher_hotkey_all": self.quick_switcher_hotkey_all,
            "quick_switcher_hotkey_copy": self.quick_switcher_hotkey_copy,
            "paused": self.paused,
            "tutorial_completed": self.tutorial_completed,
            "check_for_updates": self.check_for_updates,
            "rules": [r.to_dict() for r in self.rules],
            "category_map": dict(self.category_map),
            "type_subfolders": dict(self.type_subfolders),
            "ignore_extensions": list(self.ignore_extensions),
            "ignore_names": list(self.ignore_names),
            "architecture": dict(self.architecture),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Config:
        if not isinstance(data, dict):
            raise TypeError("Config payload must be a JSON object")
        return cls(
            watch_folder=data.get("watch_folder", r"${USERPROFILE}\Downloads"),
            settle_seconds=data.get("settle_seconds", 3.0),
            fallback_mode=data.get("fallback_mode", "other"),
            default_folder=data.get("default_folder", "Other"),
            use_type_subfolders=data.get("use_type_subfolders", True),
            notifications_enabled=data.get("notifications_enabled", True),
            ribbon_timeout_seconds=data.get("ribbon_timeout_seconds", 6.0),
            hold_3mf_enabled=data.get("hold_3mf_enabled", True),
            hold_3mf_days=data.get("hold_3mf_days", 7),
            quick_switcher_enabled=data.get("quick_switcher_enabled", True),
            quick_switcher_timeout_seconds=data.get("quick_switcher_timeout_seconds", 300.0),
            quick_switcher_3d_timeout_hours=data.get("quick_switcher_3d_timeout_hours", 12.0),
            quick_switcher_hotkey=data.get("quick_switcher_hotkey", "Ctrl+G"),
            quick_switcher_hotkey_all=data.get("quick_switcher_hotkey_all", "Ctrl+Shift+G"),
            quick_switcher_hotkey_copy=data.get("quick_switcher_hotkey_copy", "Ctrl+Shift+C"),
            paused=data.get("paused", False),
            tutorial_completed=data.get("tutorial_completed", False),
            check_for_updates=data.get("check_for_updates", True),
            rules=[Rule.from_dict(item) for item in data.get("rules", []) if isinstance(item, dict)],
            category_map=data.get("category_map") or dict(DEFAULT_CATEGORY_MAP),
            type_subfolders=data.get("type_subfolders") or dict(DEFAULT_TYPE_SUBFOLDERS),
            ignore_extensions=data.get("ignore_extensions") or list(DEFAULT_IGNORE_EXTENSIONS),
            ignore_names=data.get("ignore_names") or list(DEFAULT_IGNORE_NAMES),
            architecture=data.get("architecture") or {},
        )


def save_config(config: Config, paths: AppPaths) -> None:
    """Validate and atomically persist ``config`` to ``paths.config_file``."""
    paths.ensure_dirs()
    payload = json.dumps(config.to_dict(), indent=2, ensure_ascii=False) + "\n"
    target = paths.config_file
    tmp = target.parent / f"{target.name}.tmp.{uuid.uuid4().hex}"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
    except OSError:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise

    if target.exists():
        try:
            shutil.copy2(target, paths.backup_config_file)
        except OSError:
            pass

    last_err: OSError | None = None
    for attempt in range(5):
        try:
            os.replace(tmp, target)
            return
        except OSError as exc:
            last_err = exc
            time.sleep(0.05 * (attempt + 1))
    if tmp.exists():
        tmp.unlink(missing_ok=True)
    assert last_err is not None
    raise last_err


def load_config(paths: AppPaths) -> Config:
    """Load config from disk (falling back to `.bak`, or importing v2 defaults on first start)."""
    for candidate in (paths.config_file, paths.backup_config_file):
        if not candidate.exists():
            continue
        try:
            raw = json.loads(candidate.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return Config.from_dict(raw)
        except (OSError, ValueError, TypeError):
            continue
    v2_path = AppPaths.v2_config_file()
    if paths == AppPaths.default() and v2_path.exists():
        try:
            cfg = import_v2_config(v2_path)
            for tpl in ("school", "career", "3d_printing"):
                cfg.apply_template(tpl)
        except Exception:
            cfg = Config.with_starter_defaults()
    else:
        cfg = Config.with_starter_defaults()
    save_config(cfg, paths)
    return cfg


def _normalize_profile_vars(text: str) -> str:
    return text.replace("%USERPROFILE%", "${USERPROFILE}").replace("%userprofile%", "${USERPROFILE}")


def _portable_path_str(text: str) -> str:
    norm = _normalize_profile_vars(text)
    home = str(Path.home())
    if home and norm.lower().startswith(home.lower()):
        return "${USERPROFILE}" + norm[len(home):]
    return norm


def import_v2_config(v2_path: Path) -> Config:
    """Convert a Download Sorter v2.x (or v3/exported) ``config.json`` into a complete v3 ``Config``."""
    raw = json.loads(Path(v2_path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Invalid config at {v2_path}: expected JSON object")

    schema_ver = int(raw.get("schema_version") or 2)
    if schema_ver >= 3:
        fixed = copy.deepcopy(raw)
        if "watch_folder" in fixed:
            fixed["watch_folder"] = _normalize_profile_vars(str(fixed["watch_folder"]))
        cfg = Config.from_dict(fixed)
        if not cfg.architecture:
            from download_sorter.architecture import architecture_from_config

            cfg.architecture = architecture_from_config(cfg).to_dict()
        return cfg

    watch_block = raw.get("watch") if isinstance(raw.get("watch"), dict) else {}
    watch_folder = _normalize_profile_vars(
        str(watch_block.get("folder") or raw.get("downloads_dir") or r"${USERPROFILE}\Downloads")
    )
    settle_seconds = float(watch_block.get("settle_seconds", 3.0))
    type_folders = raw.get("type_folders") if isinstance(raw.get("type_folders"), dict) else {}
    use_type_subfolders = bool(type_folders.get("enabled", True))
    hold_block = raw.get("hold") if isinstance(raw.get("hold"), dict) else {}
    hold_3mf_enabled = bool(hold_block.get("enabled", True))
    hold_3mf_days = max(1, int(hold_block.get("hold_days", 7)))

    rules: list[Rule] = []
    for idx, item in enumerate(raw.get("keyword_rules", []), start=1):
        if not isinstance(item, dict) or not item.get("name"):
            continue
        dest = _normalize_profile_vars(str(item.get("folder") or item.get("destination") or "Other"))
        rules.append(
            Rule(
                id=f"v2-kw-{idx}",
                name=str(item["name"]),
                destination=dest,
                keywords=list(item.get("match") if "match" in item else item.get("keywords", [])),
                domains=list(item.get("sites") if "sites" in item else item.get("domains", [])),
                extensions=list(item.get("extensions", [])),
                enabled=bool(item.get("enabled", True)),
            )
        )

    cat_map = dict(DEFAULT_CATEGORY_MAP)
    disabled_exts: set[str] = set()
    enabled_override_exts: set[str] = set()
    for item in raw.get("extension_rules", []):
        if not isinstance(item, dict):
            continue
        folder = str(item.get("folder", "")).strip()
        exts = _normalize_ext_list(item.get("extensions", []))
        if not item.get("enabled", True):
            disabled_exts.update(exts)
            continue
        if not folder:
            continue
        for ext in exts:
            cat_map[ext] = folder
            enabled_override_exts.add(ext)
    for ext in disabled_exts - enabled_override_exts:
        cat_map.pop(ext, None)

    gcode_dest = str(raw.get("gcode_destination") or "").strip()
    if gcode_dest:
        for ext in (".gcode", ".bgcode", ".gco", ".g"):
            cat_map[ext] = gcode_dest
    haas_dest = str(raw.get("haas_destination") or "").strip()
    if haas_dest:
        for ext in (".nc", ".tap", ".ngc"):
            cat_map[ext] = haas_dest

    mw_dest = str(raw.get("makerworld_destination") or "").strip()
    if mw_dest:
        has_mw = any("makerworld.com" in [d.lower() for d in r.domains] for r in rules)
        if not has_mw:
            rules.append(
                Rule(
                    id="v2-makerworld",
                    name="MakerWorld Downloads",
                    destination=mw_dest,
                    domains=["makerworld.com"],
                    extensions=[".3mf", ".stl", ".step", ".stp", ".zip"],
                    enabled=True,
                )
            )

    type_subs = dict(DEFAULT_TYPE_SUBFOLDERS)
    custom_names = type_folders.get("names") if isinstance(type_folders.get("names"), dict) else {}
    for ext, name in custom_names.items():
        norm_ext = (ext if str(ext).startswith(".") else "." + str(ext)).lower()
        name_str = str(name).strip()
        if name_str:
            type_subs[norm_ext] = name_str
        else:
            type_subs.pop(norm_ext, None)

    cfg = Config(
        watch_folder=watch_folder,
        settle_seconds=settle_seconds,
        default_folder=str(raw.get("default_folder") or "Other"),
        use_type_subfolders=use_type_subfolders,
        notifications_enabled=bool(raw.get("notifications_enabled", raw.get("notifications", True))),
        ribbon_timeout_seconds=float(
            raw.get("ribbon_timeout_seconds") or raw.get("notification_timeout_seconds") or 6.0
        ),
        hold_3mf_enabled=hold_3mf_enabled,
        hold_3mf_days=hold_3mf_days,
        quick_switcher_enabled=bool(raw.get("quick_switcher_enabled", raw.get("batch_handoff", {}).get("enabled", True) if isinstance(raw.get("batch_handoff"), dict) else True)),
        quick_switcher_timeout_seconds=float(raw.get("quick_switcher_timeout_seconds", raw.get("batch_handoff", {}).get("timeout_seconds", 300.0) if isinstance(raw.get("batch_handoff"), dict) else 300.0)),
        quick_switcher_3d_timeout_hours=float(raw.get("quick_switcher_3d_timeout_hours", raw.get("batch_handoff", {}).get("3d_timeout_hours", 12.0) if isinstance(raw.get("batch_handoff"), dict) else 12.0)),
        quick_switcher_hotkey=str(raw.get("quick_switcher_hotkey", raw.get("batch_handoff", {}).get("hotkey_latest", "Ctrl+G") if isinstance(raw.get("batch_handoff"), dict) else "Ctrl+G")),
        quick_switcher_hotkey_all=str(raw.get("quick_switcher_hotkey_all", raw.get("batch_handoff", {}).get("hotkey_all", "Ctrl+Shift+G") if isinstance(raw.get("batch_handoff"), dict) else "Ctrl+Shift+G")),
        quick_switcher_hotkey_copy=str(raw.get("quick_switcher_hotkey_copy", raw.get("batch_handoff", {}).get("hotkey_copy", "Ctrl+Shift+C") if isinstance(raw.get("batch_handoff"), dict) else "Ctrl+Shift+C")),
        rules=rules,
        category_map=cat_map,
        type_subfolders=type_subs,
        ignore_extensions=list(raw.get("ignore_extensions") or DEFAULT_IGNORE_EXTENSIONS),
        ignore_names=list(raw.get("ignore_names") or DEFAULT_IGNORE_NAMES),
    )
    from download_sorter.architecture import architecture_from_config

    cfg.architecture = architecture_from_config(cfg).to_dict()
    return cfg


def carry_over_setup(
    source_path: Path,
    current_config: Config | None = None,
    *,
    merge_rules_only: bool = False,
) -> tuple[Config, dict[str, Any]]:
    """Carry over an existing v2.x/v3 setup & rules cleanly and rebuild Folder Architecture.

    Returns ``(new_config, summary_dict)``.
    """
    from download_sorter.architecture import architecture_from_config

    raw = json.loads(Path(source_path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Invalid setup file at {source_path}: expected JSON object")

    source_schema = int(raw.get("schema_version") or 2)
    imported = import_v2_config(source_path)

    if merge_rules_only and current_config is not None:
        merged = copy.deepcopy(current_config)
        existing_keys = {(r.name.lower(), r.destination.lower()) for r in merged.rules}
        added_rules = 0
        for rule in imported.rules:
            key = (rule.name.lower(), rule.destination.lower())
            if key not in existing_keys:
                merged.rules.append(Rule.from_dict(rule.to_dict()))
                existing_keys.add(key)
                added_rules += 1
        merged.category_map.update(imported.category_map)
        merged.type_subfolders.update(imported.type_subfolders)
        arch = architecture_from_config(merged)
        merged.architecture = arch.to_dict()
        summary = {
            "source_schema": source_schema,
            "mode": "merge_rules",
            "rules_imported": added_rules,
            "total_rules": len(merged.rules),
            "categories_imported": len(merged.category_map),
            "subfolders_imported": len(merged.type_subfolders),
            "hold_3mf_enabled": merged.hold_3mf_enabled,
            "hold_3mf_days": merged.hold_3mf_days,
            "watch_folder": merged.watch_folder,
            "tier_count": arch.tier_count,
            "arch_nodes": len(arch.nodes),
        }
        return merged, summary

    if current_config is not None:
        imported.tutorial_completed = current_config.tutorial_completed
    arch = architecture_from_config(imported)
    imported.architecture = arch.to_dict()
    summary = {
        "source_schema": source_schema,
        "mode": "full_carry_over",
        "rules_imported": len(imported.rules),
        "total_rules": len(imported.rules),
        "categories_imported": len(imported.category_map),
        "subfolders_imported": len(imported.type_subfolders),
        "hold_3mf_enabled": imported.hold_3mf_enabled,
        "hold_3mf_days": imported.hold_3mf_days,
        "watch_folder": imported.watch_folder,
        "tier_count": arch.tier_count,
        "arch_nodes": len(arch.nodes),
    }
    return imported, summary


def export_setup_file(config: Config, dest_path: Path) -> Path:
    """Export the full setup, rules, and Folder Architecture as a portable JSON file."""
    data = config.to_dict()
    data["watch_folder"] = _portable_path_str(config.watch_folder)
    data["exported_from"] = "Download Sorter v3"
    target = Path(dest_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target
