"""Interactive multi-tier Folder Architecture model, premade catalog, presets & config sync."""

from __future__ import annotations

from collections.abc import Callable
import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
import uuid

if TYPE_CHECKING:
    from download_sorter.config import Config, Rule


def _clean_exts(values: list[str] | tuple[str, ...]) -> list[str]:
    out: list[str] = []
    for raw in values or ():
        s = str(raw).strip().lower()
        if not s:
            continue
        if not s.startswith("."):
            s = "." + s
        if s not in out:
            out.append(s)
    return out


def _clean_strs(values: list[str] | tuple[str, ...]) -> list[str]:
    out: list[str] = []
    for raw in values or ():
        s = str(raw).strip()
        if s and s not in out:
            out.append(s)
    return out


KIND_COLORS: dict[str, str] = {
    "doc": "#0A84FF",
    "pdf": "#FA7183",
    "sheet": "#30D158",
    "print": "#69E6AD",
    "cad": "#34D399",
    "cnc": "#2DD4BF",
    "image": "#BF5AF2",
    "video": "#FF375F",
    "audio": "#5E5CE6",
    "archive": "#64D2FF",
    "installer": "#FF453A",
    "code": "#FF9F0A",
    "school": "#38BDF8",
    "career": "#A78BFA",
    "finance": "#4ADE80",
    "design": "#F472B6",
    "fonts": "#FBBF24",
    "default": "#0A84FF",
}


# ---------------------------------------------------------------------------
# Rich Premade Folder Catalog (46 Premades across Tier 1, Tier 2, Tier 3+)
# ---------------------------------------------------------------------------
PREMADE_FOLDER_CATALOG: list[dict[str, Any]] = [
    # --- TIER 1: Primary Top-Level Categories (16) ---
    {
        "key": "t1_documents",
        "name": "Documents",
        "tier": 1,
        "parent_name": "",
        "kind": "doc",
        "extensions": [".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".md", ".xls", ".xlsx", ".xlsm", ".csv", ".ods", ".ppt", ".pptx", ".odp", ".key", ".epub", ".mobi", ".tex", ".bib", ".pkpass"],
        "keywords": [],
        "domains": [],
        "summary": "PDFs, Word docs, spreadsheets, slides & ebooks",
    },
    {
        "key": "t1_3d_models",
        "name": "3D Models",
        "tier": 1,
        "parent_name": "",
        "kind": "print",
        "extensions": [".stl", ".3mf", ".obj", ".3ds", ".fbx", ".blend", ".glb", ".gltf", ".gcode", ".bgcode", ".nc", ".tap", ".ngc", ".gco", ".g"],
        "keywords": [],
        "domains": [],
        "summary": "3D prints, meshes & CNC/G-code files",
    },
    {
        "key": "t1_cad",
        "name": "CAD",
        "tier": 1,
        "parent_name": "",
        "kind": "cad",
        "extensions": [".step", ".stp", ".iges", ".igs", ".f3d", ".f3z", ".ipt", ".iam", ".sldprt", ".sldasm", ".slddrw", ".dwg", ".dxf"],
        "keywords": [],
        "domains": [],
        "summary": "STEP, IGES, Fusion 360, SolidWorks & DXF/DWG",
    },
    {
        "key": "t1_images",
        "name": "Images",
        "tier": 1,
        "parent_name": "",
        "kind": "image",
        "extensions": [".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".svg", ".bmp", ".tif", ".tiff", ".ico", ".ai", ".eps", ".cdr"],
        "keywords": [],
        "domains": [],
        "summary": "Photos, screenshots, vectors & icons",
    },
    {
        "key": "t1_videos",
        "name": "Videos",
        "tier": 1,
        "parent_name": "",
        "kind": "video",
        "extensions": [".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv", ".wmv", ".m4v"],
        "keywords": [],
        "domains": [],
        "summary": "Screen recordings, clips & movies",
    },
    {
        "key": "t1_audio",
        "name": "Audio",
        "tier": 1,
        "parent_name": "",
        "kind": "audio",
        "extensions": [".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".aiff", ".wma"],
        "keywords": [],
        "domains": [],
        "summary": "Music, podcasts & sound samples",
    },
    {
        "key": "t1_archives",
        "name": "Archives",
        "tier": 1,
        "parent_name": "",
        "kind": "archive",
        "extensions": [".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".tgz", ".iso"],
        "keywords": [],
        "domains": [],
        "summary": "ZIP, 7z, RAR, TAR & ISO disk images",
    },
    {
        "key": "t1_installers",
        "name": "Installers",
        "tier": 1,
        "parent_name": "",
        "kind": "installer",
        "extensions": [".exe", ".msi", ".msix", ".appx", ".dmg", ".pkg"],
        "keywords": [],
        "domains": [],
        "summary": "Windows installers & setup packages",
    },
    {
        "key": "t1_code",
        "name": "Code",
        "tier": 1,
        "parent_name": "",
        "kind": "code",
        "extensions": [".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css", ".cpp", ".c", ".h", ".hpp", ".cs", ".java", ".rs", ".go", ".json", ".yaml", ".yml", ".toml", ".xml", ".ini", ".env", ".sql", ".db", ".sqlite", ".parquet", ".feather", ".log", ".dmp", ".out"],
        "keywords": [],
        "domains": [],
        "summary": "Source code, JSON/YAML configs & data logs",
    },
    {
        "key": "t1_finance",
        "name": "Finance",
        "tier": 1,
        "parent_name": "",
        "kind": "finance",
        "extensions": [".pdf", ".csv", ".xlsx", ".ofx", ".qfx"],
        "keywords": ["invoice", "receipt", "statement", "tax", "w2", "1099", "billing", "payment"],
        "domains": ["stripe.com", "paypal.com", "intuit.com", "turbotax.com", "chase.com"],
        "summary": "Invoices, receipts, bank statements & tax forms",
    },
    {
        "key": "t1_school",
        "name": "School",
        "tier": 1,
        "parent_name": "",
        "kind": "school",
        "extensions": [".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xlsx", ".txt", ".ipynb", ".py", ".zip"],
        "keywords": ["syllabus", "lecture", "homework", "assignment", "lab_report", "study_guide", "midterm", "final_exam"],
        "domains": ["instructure.com", "blackboard.com", "brightspace.com", "classroom.google.com", "gradescope.com", "piazza.com"],
        "summary": "Dedicated top-level School coursework & LMS downloads",
    },
    {
        "key": "t1_career",
        "name": "Career",
        "tier": 1,
        "parent_name": "",
        "kind": "career",
        "extensions": [".pdf", ".doc", ".docx"],
        "keywords": ["resume", "cv", "cover_letter", "offer_letter", "job_application", "scholarship"],
        "domains": ["linkedin.com", "indeed.com", "greenhouse.io", "lever.co"],
        "summary": "Resumes, CVs, cover letters & offers",
    },
    {
        "key": "t1_ebooks",
        "name": "E-Books",
        "tier": 1,
        "parent_name": "",
        "kind": "doc",
        "extensions": [".epub", ".mobi", ".azw3", ".djvu"],
        "keywords": ["ebook", "manual", "datasheet", "handbook"],
        "domains": [],
        "summary": "Kindle, EPUB manuals & reference books",
    },
    {
        "key": "t1_fonts",
        "name": "Fonts",
        "tier": 1,
        "parent_name": "",
        "kind": "fonts",
        "extensions": [".ttf", ".otf", ".woff", ".woff2"],
        "keywords": ["font", "typeface"],
        "domains": ["fonts.google.com", "dafont.com"],
        "summary": "TTF, OTF & WOFF typography families",
    },
    {
        "key": "t1_design",
        "name": "Design",
        "tier": 1,
        "parent_name": "",
        "kind": "design",
        "extensions": [".psd", ".fig", ".xd", ".sketch", ".afdesign", ".ai", ".eps"],
        "keywords": ["mockup", "wireframe", "brand", "logo"],
        "domains": ["figma.com", "canva.com", "dribbble.com"],
        "summary": "Figma, Photoshop, Illustrator & brand assets",
    },
    {
        "key": "t1_spreadsheets",
        "name": "Spreadsheets",
        "tier": 1,
        "parent_name": "",
        "kind": "sheet",
        "extensions": [".csv", ".tsv", ".xls", ".xlsx", ".xlsm", ".ods"],
        "keywords": [],
        "domains": [],
        "summary": "Standalone top-level Excel & CSV datasets",
    },

    # --- TIER 2: Category Subfolders & Smart Packs (20) ---
    {
        "key": "t2_pdf",
        "name": "PDF",
        "tier": 2,
        "parent_name": "Documents",
        "kind": "pdf",
        "extensions": [".pdf"],
        "keywords": [],
        "domains": [],
        "summary": "All standard PDF files",
    },
    {
        "key": "t2_word",
        "name": "Word",
        "tier": 2,
        "parent_name": "Documents",
        "kind": "doc",
        "extensions": [".doc", ".docx"],
        "keywords": [],
        "domains": [],
        "summary": "Microsoft Word documents",
    },
    {
        "key": "t2_spreadsheets",
        "name": "Spreadsheets",
        "tier": 2,
        "parent_name": "Documents",
        "kind": "sheet",
        "extensions": [".xls", ".xlsx", ".xlsm", ".csv", ".ods"],
        "keywords": [],
        "domains": [],
        "summary": "Excel workbooks & CSV tables",
    },
    {
        "key": "t2_presentations",
        "name": "Presentations",
        "tier": 2,
        "parent_name": "Documents",
        "kind": "doc",
        "extensions": [".ppt", ".pptx", ".odp", ".key"],
        "keywords": [],
        "domains": [],
        "summary": "PowerPoint & Keynote decks",
    },
    {
        "key": "t2_text",
        "name": "Text",
        "tier": 2,
        "parent_name": "Documents",
        "kind": "doc",
        "extensions": [".txt", ".md", ".rtf", ".odt"],
        "keywords": [],
        "domains": [],
        "summary": "Plain text, Markdown & RTF notes",
    },
    {
        "key": "t2_ebooks",
        "name": "Ebooks",
        "tier": 2,
        "parent_name": "Documents",
        "kind": "doc",
        "extensions": [".epub", ".mobi"],
        "keywords": [],
        "domains": [],
        "summary": "EPUB & MOBI publications",
    },
    {
        "key": "t2_school",
        "name": "School",
        "tier": 2,
        "parent_name": "Documents",
        "kind": "school",
        "extensions": [".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xlsx", ".txt", ".zip", ".ipynb", ".py"],
        "keywords": ["syllabus", "lecture", "lecture_notes", "class_notes", "homework", "assignment", "lab_report", "problem_set", "study_guide", "midterm", "final_exam"],
        "domains": ["instructure.com", "blackboard.com", "brightspace.com", "d2l.com", "classroom.google.com", "gradescope.com", "piazza.com", "schoology.com", "moodlecloud.com"],
        "summary": "LMS sites + coursework keywords inside Documents",
    },
    {
        "key": "t2_career",
        "name": "Career",
        "tier": 2,
        "parent_name": "Documents",
        "kind": "career",
        "extensions": [".pdf", ".doc", ".docx"],
        "keywords": ["resume", "cv", "cover_letter", "offer_letter", "job_application", "career_statement", "goals_statement", "scholarship"],
        "domains": [],
        "summary": "Resumes, CVs & applications inside Documents",
    },
    {
        "key": "t2_receipts",
        "name": "Receipts & Invoices",
        "tier": 2,
        "parent_name": "Documents",
        "kind": "finance",
        "extensions": [".pdf", ".csv", ".xlsx"],
        "keywords": ["invoice", "receipt", "order_confirmation", "statement", "billing"],
        "domains": ["stripe.com", "paypal.com", "amazon.com"],
        "summary": "Orders, bills, receipts & invoices",
    },
    {
        "key": "t2_contracts",
        "name": "Contracts & Legal",
        "tier": 2,
        "parent_name": "Documents",
        "kind": "doc",
        "extensions": [".pdf", ".doc", ".docx"],
        "keywords": ["contract", "agreement", "nda", "lease", "terms", "policy", "signed"],
        "domains": ["docusign.com", "hellosign.com"],
        "summary": "Agreements, NDAs & DocuSign PDFs",
    },
    {
        "key": "t2_3mf",
        "name": "3MF",
        "tier": 2,
        "parent_name": "3D Models",
        "kind": "print",
        "extensions": [".3mf"],
        "keywords": [],
        "domains": [],
        "summary": "Bambu / Prusa / Orca 3MF projects",
    },
    {
        "key": "t2_stl",
        "name": "STL",
        "tier": 2,
        "parent_name": "3D Models",
        "kind": "print",
        "extensions": [".stl"],
        "keywords": [],
        "domains": [],
        "summary": "STL printable meshes",
    },
    {
        "key": "t2_obj",
        "name": "OBJ",
        "tier": 2,
        "parent_name": "3D Models",
        "kind": "print",
        "extensions": [".obj"],
        "keywords": [],
        "domains": [],
        "summary": "Wavefront OBJ models",
    },
    {
        "key": "t2_gcode",
        "name": "G-Code",
        "tier": 2,
        "parent_name": "3D Models",
        "kind": "cnc",
        "extensions": [".gcode", ".bgcode", ".gco", ".g"],
        "keywords": [],
        "domains": [],
        "summary": "Sliced G-Code toolpaths",
    },
    {
        "key": "t2_cnc",
        "name": "CNC",
        "tier": 2,
        "parent_name": "3D Models",
        "kind": "cnc",
        "extensions": [".nc", ".tap", ".ngc"],
        "keywords": [],
        "domains": [],
        "summary": "Haas & CNC machine toolpaths",
    },
    {
        "key": "t2_makerworld",
        "name": "MakerWorld",
        "tier": 2,
        "parent_name": "3D Models",
        "kind": "print",
        "extensions": [".3mf", ".stl", ".step", ".stp", ".zip"],
        "keywords": [],
        "domains": ["makerworld.com"],
        "summary": "Models downloaded from MakerWorld",
    },
    {
        "key": "t2_downloaded_models",
        "name": "Downloaded Models",
        "tier": 2,
        "parent_name": "3D Models",
        "kind": "print",
        "extensions": [".3mf", ".stl", ".step", ".stp"],
        "keywords": [],
        "domains": ["printables.com", "thingiverse.com", "thangs.com"],
        "summary": "Models from Printables, Thingiverse & Thangs",
    },
    {
        "key": "t2_step",
        "name": "STEP",
        "tier": 2,
        "parent_name": "CAD",
        "kind": "cad",
        "extensions": [".step", ".stp"],
        "keywords": [],
        "domains": [],
        "summary": "Neutral B-Rep STEP assemblies",
    },
    {
        "key": "t2_fusion360",
        "name": "Fusion 360",
        "tier": 2,
        "parent_name": "CAD",
        "kind": "cad",
        "extensions": [".f3d", ".f3z"],
        "keywords": [],
        "domains": [],
        "summary": "Autodesk Fusion 360 archives",
    },
    {
        "key": "t2_solidworks",
        "name": "SolidWorks",
        "tier": 2,
        "parent_name": "CAD",
        "kind": "cad",
        "extensions": [".sldprt", ".sldasm", ".slddrw"],
        "keywords": [],
        "domains": [],
        "summary": "SolidWorks parts, assemblies & drawings",
    },
    {
        "key": "t2_2d_drawings",
        "name": "2D Drawings",
        "tier": 2,
        "parent_name": "CAD",
        "kind": "cad",
        "extensions": [".dwg", ".dxf"],
        "keywords": [],
        "domains": [],
        "summary": "Laser cutting & CAD DWG/DXF drawings",
    },
    {
        "key": "t2_screenshots",
        "name": "Screenshots",
        "tier": 2,
        "parent_name": "Images",
        "kind": "image",
        "extensions": [".png", ".jpg", ".jpeg", ".webp"],
        "keywords": ["screenshot", "screen_shot", "snip", "capture", "cleanshot"],
        "domains": [],
        "summary": "Screen captures matched by filename",
    },
    {
        "key": "t2_png",
        "name": "PNG",
        "tier": 2,
        "parent_name": "Images",
        "kind": "image",
        "extensions": [".png"],
        "keywords": [],
        "domains": [],
        "summary": "PNG images",
    },
    {
        "key": "t2_jpg",
        "name": "JPG",
        "tier": 2,
        "parent_name": "Images",
        "kind": "image",
        "extensions": [".jpg", ".jpeg"],
        "keywords": [],
        "domains": [],
        "summary": "JPEG photos",
    },
    {
        "key": "t2_svg",
        "name": "SVG",
        "tier": 2,
        "parent_name": "Images",
        "kind": "design",
        "extensions": [".svg"],
        "keywords": [],
        "domains": [],
        "summary": "Scalable vector graphics",
    },

    # --- TIER 3+: Deep Workflow Subfolders (10) ---
    {
        "key": "t3_syllabi",
        "name": "Syllabi",
        "tier": 3,
        "parent_name": "School",
        "kind": "school",
        "extensions": [".pdf", ".doc", ".docx"],
        "keywords": ["syllabus", "schedule", "course_outline"],
        "domains": [],
        "summary": "Course syllabi & semester calendars",
    },
    {
        "key": "t3_homework",
        "name": "Homework",
        "tier": 3,
        "parent_name": "School",
        "kind": "school",
        "extensions": [".pdf", ".doc", ".docx", ".ipynb", ".py", ".zip"],
        "keywords": ["homework", "hw", "assignment", "problem_set"],
        "domains": [],
        "summary": "Homework assignments & problem sets",
    },
    {
        "key": "t3_labs",
        "name": "Lab Reports",
        "tier": 3,
        "parent_name": "School",
        "kind": "school",
        "extensions": [".pdf", ".doc", ".docx", ".xlsx", ".csv"],
        "keywords": ["lab", "lab_report", "experiment"],
        "domains": [],
        "summary": "Lab reports & experimental data",
    },
    {
        "key": "t3_exams",
        "name": "Exams & Study Guides",
        "tier": 3,
        "parent_name": "School",
        "kind": "school",
        "extensions": [".pdf", ".doc", ".docx", ".pptx"],
        "keywords": ["exam", "midterm", "final_exam", "quiz", "study_guide", "review"],
        "domains": [],
        "summary": "Study guides, quizzes & exams",
    },
    {
        "key": "t3_resumes",
        "name": "Resumes",
        "tier": 3,
        "parent_name": "Career",
        "kind": "career",
        "extensions": [".pdf", ".doc", ".docx"],
        "keywords": ["resume", "cv"],
        "domains": [],
        "summary": "Targeted Resumes & CVs",
    },
    {
        "key": "t3_offers",
        "name": "Cover Letters & Offers",
        "tier": 3,
        "parent_name": "Career",
        "kind": "career",
        "extensions": [".pdf", ".doc", ".docx"],
        "keywords": ["cover_letter", "offer_letter", "scholarship"],
        "domains": [],
        "summary": "Cover letters, offers & awards",
    },
    {
        "key": "t3_bambu_projects",
        "name": "Bambu Projects",
        "tier": 3,
        "parent_name": "MakerWorld",
        "kind": "print",
        "extensions": [".3mf"],
        "keywords": ["bambu", "ams", "plate", "multicolor"],
        "domains": [],
        "summary": "MakerWorld .3mf Bambu Studio plates",
    },
    {
        "key": "t3_functional_parts",
        "name": "Brackets & Enclosures",
        "tier": 3,
        "parent_name": "MakerWorld",
        "kind": "cad",
        "extensions": [".3mf", ".stl", ".step"],
        "keywords": ["bracket", "mount", "enclosure", "case", "adapter", "jig", "tray"],
        "domains": [],
        "summary": "Mechanical brackets, mounts & cases",
    },
    {
        "key": "t3_tax_forms",
        "name": "Tax Forms",
        "tier": 3,
        "parent_name": "Receipts & Invoices",
        "kind": "finance",
        "extensions": [".pdf"],
        "keywords": ["tax", "w2", "1099", "irs", "1098"],
        "domains": ["turbotax.com", "irs.gov"],
        "summary": "W-2, 1099 & annual tax PDFs",
    },
    {
        "key": "t3_isos",
        "name": "Disk Images",
        "tier": 2,
        "parent_name": "Archives",
        "kind": "archive",
        "extensions": [".iso", ".img"],
        "keywords": [],
        "domains": [],
        "summary": "OS & recovery ISO images",
    },
]


ARCHITECTURE_PRESETS: dict[str, dict[str, Any]] = {
    "basic_starter": {
        "label": "Basic Starter (Clean 2-Tier Foundation)",
        "tier_count": 2,
        "keys": [
            "t1_documents", "t1_3d_models", "t1_cad", "t1_images",
            "t1_videos", "t1_audio", "t1_archives", "t1_installers", "t1_code",
            "t2_pdf", "t2_school", "t2_career",
            "t2_3mf", "t2_stl", "t2_makerworld", "t2_downloaded_models",
        ],
    },
    "standard": {
        "label": "Standard 2-Tier (Balanced Default)",
        "tier_count": 2,
        "keys": [
            "t1_documents", "t1_3d_models", "t1_cad", "t1_images",
            "t1_videos", "t1_audio", "t1_archives", "t1_installers", "t1_code",
            "t2_pdf", "t2_word", "t2_spreadsheets", "t2_presentations",
            "t2_school", "t2_career",
            "t2_3mf", "t2_stl", "t2_gcode", "t2_makerworld", "t2_downloaded_models",
            "t2_step", "t2_fusion360", "t2_2d_drawings",
            "t2_png", "t2_jpg", "t2_svg",
        ],
    },
    "flat": {
        "label": "Minimalist 1-Tier (Flat Categories)",
        "tier_count": 1,
        "keys": [
            "t1_documents", "t1_3d_models", "t1_cad", "t1_images",
            "t1_videos", "t1_audio", "t1_archives", "t1_installers", "t1_code",
        ],
    },
    "maker_pro": {
        "label": "Maker & CAD Pro (3-Tier Workshop)",
        "tier_count": 3,
        "keys": [
            "t1_3d_models", "t1_cad", "t1_documents", "t1_images", "t1_archives", "t1_code", "t1_installers",
            "t2_3mf", "t2_stl", "t2_gcode", "t2_cnc", "t2_makerworld", "t2_downloaded_models",
            "t2_step", "t2_fusion360", "t2_solidworks", "t2_2d_drawings",
            "t2_pdf", "t2_spreadsheets", "t2_screenshots", "t2_svg",
            "t3_bambu_projects", "t3_functional_parts",
        ],
    },
    "student_pro": {
        "label": "Student & Career Pro (3-Tier Academic)",
        "tier_count": 3,
        "keys": [
            "t1_documents", "t1_images", "t1_videos", "t1_archives", "t1_code", "t1_installers",
            "t2_pdf", "t2_word", "t2_spreadsheets", "t2_presentations", "t2_school", "t2_career",
            "t2_screenshots", "t2_png", "t2_jpg",
            "t3_syllabi", "t3_homework", "t3_labs", "t3_exams", "t3_resumes", "t3_offers",
        ],
    },
    "creator_pro": {
        "label": "Creator, Design & Finance (3-Tier Studio)",
        "tier_count": 3,
        "keys": [
            "t1_documents", "t1_design", "t1_images", "t1_fonts", "t1_videos", "t1_audio", "t1_archives",
            "t2_pdf", "t2_spreadsheets", "t2_receipts", "t2_contracts",
            "t2_screenshots", "t2_png", "t2_jpg", "t2_svg",
            "t3_tax_forms",
        ],
    },
}


@dataclass
class ArchNode:
    """Single folder layer node in the interactive Folder Architecture canvas."""

    id: str
    name: str
    tier: int
    parent_id: str | None = None
    kind: str = "default"
    extensions: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    enabled: bool = True
    is_custom: bool = False
    premade_key: str = ""

    def __post_init__(self) -> None:
        self.id = str(self.id or uuid.uuid4().hex[:8])
        self.name = str(self.name).strip().replace("/", "").replace("\\", "") or "Untitled"
        self.tier = max(1, min(5, int(self.tier)))
        self.parent_id = str(self.parent_id) if self.parent_id else None
        if self.tier == 1:
            self.parent_id = None
        self.kind = str(self.kind or "default")
        self.extensions = _clean_exts(self.extensions)
        self.keywords = _clean_strs(self.keywords)
        self.domains = [d.lower() for d in _clean_strs(self.domains)]
        self.enabled = bool(self.enabled)
        self.is_custom = bool(self.is_custom)
        self.premade_key = str(self.premade_key or "")

    @property
    def accent_hex(self) -> str:
        return KIND_COLORS.get(self.kind, KIND_COLORS["default"])

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "tier": self.tier,
            "parent_id": self.parent_id,
            "kind": self.kind,
            "extensions": list(self.extensions),
            "keywords": list(self.keywords),
            "domains": list(self.domains),
            "enabled": self.enabled,
            "is_custom": self.is_custom,
            "premade_key": self.premade_key,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArchNode:
        return cls(
            id=str(data.get("id") or uuid.uuid4().hex[:8]),
            name=str(data.get("name", "Untitled")),
            tier=int(data.get("tier", 1)),
            parent_id=data.get("parent_id"),
            kind=str(data.get("kind", "default")),
            extensions=list(data.get("extensions", [])),
            keywords=list(data.get("keywords", [])),
            domains=list(data.get("domains", [])),
            enabled=bool(data.get("enabled", True)),
            is_custom=bool(data.get("is_custom", False)),
            premade_key=str(data.get("premade_key", "")),
        )


def _find_premade(key: str) -> dict[str, Any] | None:
    for item in PREMADE_FOLDER_CATALOG:
        if item["key"] == key:
            return item
    return None


def _guess_kind(name: str, extensions: list[str]) -> str:
    low = name.lower()
    if "school" in low or "homework" in low or "syllab" in low or "exam" in low or "lab" in low:
        return "school"
    if "career" in low or "resume" in low or "offer" in low:
        return "career"
    if "finance" in low or "tax" in low or "receipt" in low or "invoice" in low:
        return "finance"
    if "pdf" in low:
        return "pdf"
    if "3d" in low or "maker" in low or "print" in low or ".3mf" in extensions or ".stl" in extensions:
        return "print"
    if "cnc" in low or "gcode" in low:
        return "cnc"
    if "cad" in low or "step" in low or "fusion" in low or "solid" in low:
        return "cad"
    if "image" in low or "photo" in low or "screen" in low or ".png" in extensions or ".jpg" in extensions:
        return "image"
    if "video" in low or ".mp4" in extensions:
        return "video"
    if "audio" in low or "music" in low or ".mp3" in extensions:
        return "audio"
    if "archive" in low or "iso" in low or ".zip" in extensions:
        return "archive"
    if "install" in low or "setup" in low or ".exe" in extensions:
        return "installer"
    if "code" in low or ".py" in extensions:
        return "code"
    if "sheet" in low or "excel" in low or ".csv" in extensions or ".xlsx" in extensions:
        return "sheet"
    if "doc" in low or "word" in low or "text" in low:
        return "doc"
    if "font" in low:
        return "fonts"
    if "design" in low or "svg" in low:
        return "design"
    return "default"


class FolderArchitecture:
    """Editable multi-tier folder architecture model backing the interactive canvas."""

    MIN_TIERS = 1
    MAX_TIERS = 5

    def __init__(self, tier_count: int = 2, nodes: list[ArchNode] | None = None) -> None:
        self.tier_count = max(self.MIN_TIERS, min(self.MAX_TIERS, int(tier_count)))
        self.nodes: list[ArchNode] = list(nodes or [])

    def clone(self) -> FolderArchitecture:
        return FolderArchitecture(
            tier_count=self.tier_count,
            nodes=[ArchNode.from_dict(n.to_dict()) for n in self.nodes],
        )

    def add_tier(self) -> int:
        if self.tier_count < self.MAX_TIERS:
            self.tier_count += 1
        return self.tier_count

    def remove_last_tier(self) -> int:
        if self.tier_count <= self.MIN_TIERS:
            return self.tier_count
        removed_tier = self.tier_count
        self.tier_count -= 1
        self.nodes = [n for n in self.nodes if n.tier <= self.tier_count]
        return removed_tier

    def get_node(self, node_id: str | None) -> ArchNode | None:
        if not node_id:
            return None
        for n in self.nodes:
            if n.id == node_id:
                return n
        return None

    def nodes_in_tier(self, tier: int) -> list[ArchNode]:
        return [n for n in self.nodes if n.tier == tier]

    def children_of(self, parent_id: str | None) -> list[ArchNode]:
        return [n for n in self.nodes if n.parent_id == parent_id]

    def is_descendant_of(self, candidate_id: str, ancestor_id: str) -> bool:
        """Return True if ``candidate_id`` is a strict descendant of ``ancestor_id``."""
        cur = self.get_node(candidate_id)
        visited: set[str] = set()
        while cur is not None and cur.parent_id and cur.id not in visited:
            visited.add(cur.id)
            if cur.parent_id == ancestor_id:
                return True
            cur = self.get_node(cur.parent_id)
        return False

    def subtree_depth(self, node_id: str, _visited: set[str] | None = None) -> int:
        """Return max depth below ``node_id`` (0 for a leaf node)."""
        visited = set(_visited or ())
        if node_id in visited:
            return 0
        visited.add(node_id)
        children = self.children_of(node_id)
        if not children:
            return 0
        return 1 + max(self.subtree_depth(c.id, visited) for c in children)

    def can_reparent(self, node_id: str, new_parent_id: str | None) -> bool:
        """Validate moving ``node_id`` under ``new_parent_id`` (None = Tier 1 root)."""
        node = self.get_node(node_id)
        if node is None:
            return False
        sub_depth = self.subtree_depth(node_id)
        if new_parent_id is None:
            return (1 + sub_depth) <= self.MAX_TIERS
        if new_parent_id == node_id:
            return False
        target = self.get_node(new_parent_id)
        if target is None:
            return False
        if self.is_descendant_of(new_parent_id, node_id):
            return False
        new_tier = target.tier + 1
        return (new_tier + sub_depth) <= self.MAX_TIERS

    def reparent_node(self, node_id: str, new_parent_id: str | None) -> bool:
        """Move ``node_id`` (and its subtree) under ``new_parent_id`` (None = Tier 1 root)."""
        if not self.can_reparent(node_id, new_parent_id):
            return False
        node = self.get_node(node_id)
        if node is None:
            return False
        if new_parent_id is None:
            node.parent_id = None
            new_tier = 1
        else:
            target = self.get_node(new_parent_id)
            if target is None:
                return False
            node.parent_id = target.id
            new_tier = target.tier + 1

        def _apply_tier(cur: ArchNode, tier: int, seen: set[str]) -> None:
            if cur.id in seen:
                return
            seen.add(cur.id)
            cur.tier = max(1, min(self.MAX_TIERS, tier))
            if cur.tier == 1:
                cur.parent_id = None
            for child in self.children_of(cur.id):
                _apply_tier(child, cur.tier + 1, seen)

        _apply_tier(node, new_tier, set())
        max_used = max((n.tier for n in self.nodes), default=1)
        self.tier_count = max(self.tier_count, min(self.MAX_TIERS, max_used))
        return True

    def node_relative_path(self, node_id: str) -> str:
        parts: list[str] = []
        seen: set[str] = set()
        cur = self.get_node(node_id)
        while cur is not None and cur.id not in seen:
            seen.add(cur.id)
            parts.append(cur.name)
            cur = self.get_node(cur.parent_id)
        parts.reverse()
        return "\\".join(parts)

    def all_target_folders(self) -> list[str]:
        out: list[str] = []
        for n in self.nodes:
            if n.enabled and n.tier <= self.tier_count:
                rel = self.node_relative_path(n.id)
                if rel and rel not in out:
                    out.append(rel)
        return out

    def find_by_name_and_tier(self, name: str, tier: int, parent_id: str | None = None) -> ArchNode | None:
        low = name.strip().lower()
        for n in self.nodes:
            if n.tier == tier and n.name.lower() == low:
                if parent_id is None or n.parent_id == parent_id:
                    return n
        return None

    def ensure_path_chain(
        self,
        rel_dest: str,
        *,
        extensions: list[str] | None = None,
        keywords: list[str] | None = None,
        domains: list[str] | None = None,
        enabled: bool = True,
        is_custom: bool = True,
    ) -> ArchNode | None:
        parts = [p.strip() for p in rel_dest.replace("/", "\\").split("\\") if p.strip()]
        if not parts:
            return None
        self.tier_count = max(self.tier_count, min(self.MAX_TIERS, len(parts)))
        parent_id: str | None = None
        last_node: ArchNode | None = None
        for idx, part in enumerate(parts[: self.MAX_TIERS], start=1):
            is_leaf = idx == len(parts[: self.MAX_TIERS])
            existing = self.find_by_name_and_tier(part, idx, parent_id)
            if existing is not None:
                if is_leaf:
                    existing.extensions = _clean_exts([*existing.extensions, *(extensions or [])])
                    existing.keywords = _clean_strs([*existing.keywords, *(keywords or [])])
                    existing.domains = _clean_strs([*existing.domains, *(domains or [])])
                    existing.enabled = enabled
                parent_id = existing.id
                last_node = existing
                continue

            leaf_exts = list(extensions or []) if is_leaf else []
            leaf_kws = list(keywords or []) if is_leaf else []
            leaf_doms = list(domains or []) if is_leaf else []
            node = ArchNode(
                id=f"n-{uuid.uuid4().hex[:6]}",
                name=part,
                tier=idx,
                parent_id=parent_id,
                kind=_guess_kind(part, leaf_exts),
                extensions=leaf_exts,
                keywords=leaf_kws,
                domains=leaf_doms,
                enabled=enabled,
                is_custom=is_custom,
            )
            self.nodes.append(node)
            parent_id = node.id
            last_node = node
        return last_node

    def add_premade_node(
        self,
        premade_key: str,
        *,
        tier: int | None = None,
        parent_id: str | None = None,
    ) -> ArchNode | None:
        tpl = _find_premade(premade_key)
        if tpl is None:
            return None
        use_tier = max(1, min(self.MAX_TIERS, int(tier if tier is not None else tpl["tier"])))
        if use_tier > self.tier_count:
            self.tier_count = use_tier

        resolved_parent_id = parent_id if use_tier > 1 else None
        if use_tier > 1 and not resolved_parent_id:
            parent_name = str(tpl.get("parent_name", "")).strip()
            if parent_name:
                parent_node = self.find_by_name_and_tier(parent_name, use_tier - 1)
                if parent_node is None and use_tier == 2:
                    # Auto-create missing Tier 1 parent from catalog
                    for cand in PREMADE_FOLDER_CATALOG:
                        if cand["tier"] == 1 and cand["name"].lower() == parent_name.lower():
                            parent_node = self.add_premade_node(cand["key"], tier=1)
                            break
                if parent_node is not None:
                    resolved_parent_id = parent_node.id
            if not resolved_parent_id:
                prev_nodes = self.nodes_in_tier(use_tier - 1)
                if prev_nodes:
                    resolved_parent_id = prev_nodes[0].id

        existing = self.find_by_name_and_tier(tpl["name"], use_tier, resolved_parent_id)
        if existing is not None:
            existing.extensions = _clean_exts([*existing.extensions, *tpl["extensions"]])
            existing.keywords = _clean_strs([*existing.keywords, *tpl["keywords"]])
            existing.domains = _clean_strs([*existing.domains, *tpl["domains"]])
            return existing

        node = ArchNode(
            id=f"pm-{uuid.uuid4().hex[:6]}",
            name=str(tpl["name"]),
            tier=use_tier,
            parent_id=resolved_parent_id,
            kind=str(tpl["kind"]),
            extensions=list(tpl["extensions"]),
            keywords=list(tpl["keywords"]),
            domains=list(tpl["domains"]),
            enabled=True,
            is_custom=False,
            premade_key=premade_key,
        )
        self.nodes.append(node)
        return node

    def add_custom_node(
        self,
        *,
        name: str,
        tier: int,
        parent_id: str | None = None,
        kind: str = "default",
        extensions: list[str] | None = None,
        keywords: list[str] | None = None,
        domains: list[str] | None = None,
        enabled: bool = True,
    ) -> ArchNode:
        use_tier = max(1, min(self.MAX_TIERS, int(tier)))
        if use_tier > self.tier_count:
            self.tier_count = use_tier
        use_parent = parent_id if use_tier > 1 else None
        if use_tier > 1 and not use_parent:
            prev_nodes = self.nodes_in_tier(use_tier - 1)
            if prev_nodes:
                use_parent = prev_nodes[0].id
        node = ArchNode(
            id=f"cust-{uuid.uuid4().hex[:6]}",
            name=name,
            tier=use_tier,
            parent_id=use_parent,
            kind=kind or _guess_kind(name, extensions or []),
            extensions=list(extensions or []),
            keywords=list(keywords or []),
            domains=list(domains or []),
            enabled=enabled,
            is_custom=True,
        )
        self.nodes.append(node)
        return node

    def update_node(
        self,
        node_id: str,
        *,
        name: str | None = None,
        tier: int | None = None,
        parent_id: str | None = None,
        kind: str | None = None,
        extensions: list[str] | None = None,
        keywords: list[str] | None = None,
        domains: list[str] | None = None,
        enabled: bool | None = None,
    ) -> ArchNode | None:
        node = self.get_node(node_id)
        if node is None:
            return None
        if name is not None:
            node.name = name.strip().replace("/", "").replace("\\", "") or node.name
        if tier is not None:
            node.tier = max(1, min(self.MAX_TIERS, int(tier)))
            if node.tier > self.tier_count:
                self.tier_count = node.tier
        if node.tier == 1:
            node.parent_id = None
        elif parent_id is not None:
            node.parent_id = parent_id
        if kind is not None:
            node.kind = kind
        if extensions is not None:
            node.extensions = _clean_exts(extensions)
        if keywords is not None:
            node.keywords = _clean_strs(keywords)
        if domains is not None:
            node.domains = [d.lower() for d in _clean_strs(domains)]
        if enabled is not None:
            node.enabled = bool(enabled)
        return node

    def remove_node(self, node_id: str) -> None:
        to_remove = {node_id}
        changed = True
        while changed:
            changed = False
            for n in self.nodes:
                if n.parent_id in to_remove and n.id not in to_remove:
                    to_remove.add(n.id)
                    changed = True
        self.nodes = [n for n in self.nodes if n.id not in to_remove]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier_count": self.tier_count,
            "nodes": [n.to_dict() for n in self.nodes],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FolderArchitecture:
        tier_count = int(data.get("tier_count", 2))
        nodes = [ArchNode.from_dict(item) for item in data.get("nodes", []) if isinstance(item, dict)]
        return cls(tier_count=tier_count, nodes=nodes)

    @classmethod
    def from_preset(cls, preset_key: str) -> FolderArchitecture:
        preset = ARCHITECTURE_PRESETS.get(preset_key, ARCHITECTURE_PRESETS["standard"])
        arch = cls(tier_count=int(preset["tier_count"]))
        for key in preset["keys"]:
            arch.add_premade_node(key)
        return arch

    @classmethod
    def auto_create_basic(cls, watch_root: Path | None = None) -> FolderArchitecture:
        """Auto-generate a clean, standard 2-tier basic foundation to build upon."""
        arch = cls.from_preset("basic_starter")
        if watch_root is not None:
            root = Path(watch_root)
            if root.is_dir():
                ext_to_premade = {
                    ".step": "t2_step",
                    ".stp": "t2_step",
                    ".iges": "t2_step",
                    ".igs": "t2_step",
                    ".gcode": "t2_gcode",
                    ".bgcode": "t2_gcode",
                    ".gco": "t2_gcode",
                    ".g": "t2_gcode",
                    ".nc": "t2_cnc",
                    ".tap": "t2_cnc",
                    ".ngc": "t2_cnc",
                    ".xlsx": "t2_spreadsheets",
                    ".xls": "t2_spreadsheets",
                    ".xlsm": "t2_spreadsheets",
                    ".csv": "t2_spreadsheets",
                    ".ods": "t2_spreadsheets",
                    ".doc": "t2_word",
                    ".docx": "t2_word",
                    ".ppt": "t2_presentations",
                    ".pptx": "t2_presentations",
                    ".key": "t2_presentations",
                    ".png": "t2_png",
                    ".jpg": "t2_jpg",
                    ".jpeg": "t2_jpg",
                    ".svg": "t2_svg",
                }
                seen_keys: set[str] = set()
                try:
                    for item in root.iterdir():
                        if not item.is_file():
                            continue
                        pm_key = ext_to_premade.get(item.suffix.lower())
                        if pm_key and pm_key not in seen_keys:
                            seen_keys.add(pm_key)
                            arch.add_premade_node(pm_key)
                except OSError:
                    pass
        return arch


_CORE_DEFAULT_TIER2_NAMES: frozenset[str] = frozenset(
    {"PDF", "Word", "Spreadsheets", "Presentations", "3MF", "STL", "G-Code", "STEP"}
)


def architecture_from_config(cfg: Config) -> FolderArchitecture:
    """Build or restore a ``FolderArchitecture`` accurately representing ``cfg``."""
    from download_sorter.config import DEFAULT_TYPE_SUBFOLDERS

    if isinstance(cfg.architecture, dict) and cfg.architecture.get("nodes"):
        arch = FolderArchitecture.from_dict(cfg.architecture)
        if arch.nodes:
            return arch

    tier_count = 2 if cfg.use_type_subfolders else 1
    arch = FolderArchitecture(tier_count=tier_count)

    # 1. Populate Tier 1 from cfg.category_map
    by_cat: dict[str, list[str]] = {}
    for ext, cat in cfg.category_map.items():
        top = cat.split("\\")[0].strip() or cat
        by_cat.setdefault(top, []).append(ext)

    t1_by_name: dict[str, ArchNode] = {}
    for cat_name, exts in by_cat.items():
        node = ArchNode(
            id=f"t1-{uuid.uuid4().hex[:6]}",
            name=cat_name,
            tier=1,
            parent_id=None,
            kind=_guess_kind(cat_name, exts),
            extensions=exts,
            enabled=True,
        )
        arch.nodes.append(node)
        t1_by_name[cat_name.lower()] = node

    # 2. Populate clean Tier 2 starter subfolders + any user-customized type_subfolders
    if cfg.use_type_subfolders:
        sub_groups: dict[tuple[str, str], list[str]] = {}
        for ext, sub in cfg.type_subfolders.items():
            is_custom_sub = sub != DEFAULT_TYPE_SUBFOLDERS.get(ext)
            if sub not in _CORE_DEFAULT_TIER2_NAMES and not is_custom_sub:
                continue
            parent_cat = cfg.category_map.get(ext, "")
            top = parent_cat.split("\\")[0].strip() if parent_cat else ""
            if top and sub:
                sub_groups.setdefault((top, sub), []).append(ext)
        for (top, sub), exts in sub_groups.items():
            p_node = t1_by_name.get(top.lower())
            if p_node is None:
                p_node = ArchNode(
                    id=f"t1-{uuid.uuid4().hex[:6]}",
                    name=top,
                    tier=1,
                    kind=_guess_kind(top, exts),
                    extensions=exts,
                )
                arch.nodes.append(p_node)
                t1_by_name[top.lower()] = p_node
            if not arch.find_by_name_and_tier(sub, 2, p_node.id):
                arch.nodes.append(
                    ArchNode(
                        id=f"t2-{uuid.uuid4().hex[:6]}",
                        name=sub,
                        tier=2,
                        parent_id=p_node.id,
                        kind=_guess_kind(sub, exts),
                        extensions=exts,
                        enabled=True,
                    )
                )

    # 3. Populate Rules (Tier 1..5)
    for rule in cfg.rules:
        arch.ensure_path_chain(
            rule.destination,
            extensions=rule.extensions,
            keywords=rule.keywords,
            domains=rule.domains,
            enabled=rule.enabled,
            is_custom=True,
        )

    return arch


_GENERATED_RULE_PREFIX = "arch-"


def _merge_user_and_generated_rules(user_rules: list[Rule], generated: list[Rule]) -> list[Rule]:
    """Keep the user's own rules, in their order, ahead of the folder map's rules.

    Rules made on the Custom Rules tab (or carried over from 2.x, or added from a
    starter set) carry settings the folder map cannot show: regex, rename template,
    exclude words, size and age limits, PDF content words, ALL/ANY and auto-unzip.
    They are kept exactly as they are. Rules this function generated last time
    (``arch-`` ids) are dropped and generated again from the map.

    A folder card that a user rule already feeds holds that rule's words and sites,
    so its generated rule would only repeat it. It is left out, unless words or
    sites were added on the card, in which case a rule for just those is added.
    """
    kept = [copy.deepcopy(r) for r in user_rules if not str(r.id).startswith(_GENERATED_RULE_PREFIX)]
    by_dest: dict[str, list[Rule]] = {}
    for rule in kept:
        by_dest.setdefault(rule.destination.lower(), []).append(rule)

    extra: list[Rule] = []
    for gen in generated:
        same_dest = by_dest.get(gen.destination.lower())
        if not same_dest:
            extra.append(gen)
            continue
        if gen.keywords or gen.domains:
            known_kw = {k.lower() for r in same_dest for k in r.keywords}
            known_dom = {d.lower() for r in same_dest for d in r.domains}
            new_kw = [k for k in gen.keywords if k.lower() not in known_kw]
            new_dom = [d for d in gen.domains if d.lower() not in known_dom]
            if new_kw or new_dom:
                gen.keywords = new_kw
                gen.domains = new_dom
                extra.append(gen)
            continue
        # Extension-only folder: covered when a user rule there already files
        # every file of those types (no words, sites or other conditions).
        plain_exts = {
            e
            for r in same_dest
            if not (r.keywords or r.domains or r.regex_pattern or r.content_keywords)
            for e in r.extensions
        }
        new_ext = [e for e in gen.extensions if e not in plain_exts]
        if new_ext:
            gen.extensions = new_ext
            extra.append(gen)
    return kept + extra


def apply_architecture_to_config(arch: FolderArchitecture, base_cfg: Config) -> Config:
    """Compile a ``FolderArchitecture`` into a complete, deterministic ``Config``."""
    from download_sorter.config import DEFAULT_TYPE_SUBFOLDERS, Rule

    cfg = copy.deepcopy(base_cfg)
    cfg.architecture = arch.to_dict()
    cfg.use_type_subfolders = arch.tier_count >= 2

    new_cat_map: dict[str, str] = {}
    new_type_subs: dict[str, str] = dict(DEFAULT_TYPE_SUBFOLDERS) if arch.tier_count >= 2 else {}
    generated_rules: list[Rule] = []

    # Step A: Tier 1 top-level category mappings & rule-based Tier 1 nodes
    for node in arch.nodes_in_tier(1):
        if not node.enabled:
            continue
        if node.keywords or node.domains:
            generated_rules.append(
                Rule(
                    id=f"arch-{node.id}",
                    name=f"{node.name}",
                    destination=node.name,
                    keywords=list(node.keywords),
                    domains=list(node.domains),
                    extensions=list(node.extensions),
                    enabled=True,
                )
            )
        else:
            for ext in node.extensions:
                new_cat_map[ext] = node.name

    # Step B: Tier 2 & Tier 3+ child folders
    deep_nodes = sorted(
        [n for n in arch.nodes if n.enabled and 2 <= n.tier <= arch.tier_count],
        key=lambda n: (-n.tier, 0 if (n.keywords or n.domains) else 1),
    )

    for node in deep_nodes:
        rel_path = arch.node_relative_path(node.id)
        if not rel_path:
            continue
        parent = arch.get_node(node.parent_id)

        has_matcher = bool(node.keywords or node.domains)
        if not has_matcher and not node.extensions:
            # An empty folder has nothing to match on. As a rule it would have no
            # conditions at all and catch every file, so it stays a plain folder.
            continue
        if has_matcher or node.tier >= 3:
            # Explicit top-to-bottom rule (deepest tier first so Tier 3 beats Tier 2)
            generated_rules.append(
                Rule(
                    id=f"arch-{node.id}",
                    name=f"{rel_path}",
                    destination=rel_path,
                    keywords=list(node.keywords),
                    domains=list(node.domains),
                    extensions=list(node.extensions),
                    enabled=True,
                )
            )
        elif node.tier == 2 and node.extensions and parent is not None and parent.tier == 1:
            # Tier 2 extension-based folder under Tier 1 category
            for ext in node.extensions:
                parent_cat = new_cat_map.get(ext)
                if parent_cat is None or parent_cat == parent.name:
                    new_cat_map[ext] = parent.name
                    new_type_subs[ext] = node.name
                else:
                    # User moved this extension under a different Tier 1 parent -> make explicit rule so it wins cleanly
                    generated_rules.append(
                        Rule(
                            id=f"arch-{node.id}",
                            name=f"{rel_path}",
                            destination=rel_path,
                            extensions=list(node.extensions),
                            enabled=True,
                        )
                    )
                    break

    # Deduplicate rules by (destination.lower(), keywords, domains, extensions) while keeping deepest/keyword-specific first
    generated_rules.sort(
        key=lambda r: (
            0 if (r.keywords or r.domains) else 1,
            -len(r.destination.split("\\")),
        )
    )
    cfg.rules = _merge_user_and_generated_rules(base_cfg.rules, generated_rules)

    if new_cat_map:
        cfg.category_map = new_cat_map
    cfg.type_subfolders = new_type_subs
    return cfg


_SCAN_SKIP_DIRS = {"_duplicates", "_extracted", "__pycache__", ".git", ".venv"}
_SCAN_STOPWORDS = {
    "copy",
    "final",
    "new",
    "download",
    "downloads",
    "img",
    "dsc",
    "file",
    "doc",
    "document",
    "untitled",
    "version",
    "rev",
    "draft",
    "scan",
    "screenshot",
    "screen",
    "shot",
}


def scan_folder_architecture(
    watch_root: Path,
    *,
    max_depth: int = 5,
    max_files_per_dir: int = 300,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    """Scan ``watch_root`` on disk and build an interactive ``FolderArchitecture`` root map."""
    from collections import Counter
    import os

    from download_sorter.router import _normalize_tokens, extract_host, read_zone_url

    root = watch_root.resolve()
    max_tiers = max(1, min(FolderArchitecture.MAX_TIERS, int(max_depth)))
    if not root.is_dir():
        return {"architecture": FolderArchitecture(tier_count=2, nodes=[]), "folder_count": 0, "file_count": 0}

    if on_progress is not None:
        on_progress(0, 0, f"Scanning folders inside {root.name}...")

    dir_records: list[dict[str, Any]] = []
    global_token_counts: Counter[str] = Counter()
    total_files = 0
    max_tier_seen = 1

    # Walk top-down up to max_tiers
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        cur = Path(dirpath)
        rel = cur.relative_to(root)
        depth = len(rel.parts)
        dirnames[:] = sorted(
            [d for d in dirnames if not d.startswith(".") and d.lower() not in _SCAN_SKIP_DIRS],
            key=str.lower,
        )
        if depth >= max_tiers:
            dirnames[:] = []
        if depth == 0:
            continue

        max_tier_seen = max(max_tier_seen, depth)
        ext_counts: Counter[str] = Counter()
        token_counts: Counter[str] = Counter()
        domain_counts: Counter[str] = Counter()

        sampled_files = [f for f in filenames if not f.startswith(".")][:max_files_per_dir]
        total_files += len(sampled_files)
        for idx, fname in enumerate(sampled_files):
            fp = cur / fname
            ext = fp.suffix.lower()
            if ext:
                ext_counts[ext] += 1
            norm = _normalize_tokens(fp.stem)
            seen_in_file: set[str] = set()
            for tok in norm.split():
                if len(tok) >= 3 and not tok.isdigit() and tok not in _SCAN_STOPWORDS and tok not in seen_in_file:
                    seen_in_file.add(tok)
                    token_counts[tok] += 1
                    global_token_counts[tok] += 1
            if idx < 40:
                host = extract_host(read_zone_url(fp))
                if host:
                    domain_counts[host] += 1

        dir_records.append(
            {
                "rel_parts": rel.parts,
                "name": cur.name,
                "tier": depth,
                "file_count": len(sampled_files),
                "ext_counts": ext_counts,
                "token_counts": token_counts,
                "domain_counts": domain_counts,
            }
        )

    nodes: list[ArchNode] = []
    path_to_id: dict[tuple[str, ...], str] = {}

    # Sort by tier so parents are always created before children
    dir_records.sort(key=lambda r: (r["tier"], tuple(p.lower() for p in r["rel_parts"])))
    total_dirs = len(dir_records)
    for idx, rec in enumerate(dir_records, start=1):
        if on_progress is not None:
            on_progress(idx, max(1, total_dirs), f"Analyzing {rec['name']} ({idx}/{total_dirs})...")
        parts: tuple[str, ...] = rec["rel_parts"]
        parent_id = path_to_id.get(parts[:-1]) if len(parts) > 1 else None
        fc = max(1, int(rec["file_count"]))

        inferred_exts = [
            ext
            for ext, cnt in rec["ext_counts"].most_common(8)
            if cnt >= 2 or (cnt / fc) >= 0.25 or len(rec["ext_counts"]) == 1
        ]
        # Distinctive keywords: >=2 occurrences in this folder and >=50% purity across whole tree
        inferred_kws = [
            tok
            for tok, cnt in rec["token_counts"].most_common(12)
            if cnt >= 2 and (cnt / max(1, global_token_counts[tok])) >= 0.5
        ][:5]
        inferred_domains = [host for host, _cnt in rec["domain_counts"].most_common(3)]

        node_id = uuid.uuid4().hex[:8]
        path_to_id[parts] = node_id
        nodes.append(
            ArchNode(
                id=node_id,
                name=rec["name"],
                tier=rec["tier"],
                parent_id=parent_id,
                kind=_guess_kind(rec["name"], inferred_exts),
                extensions=inferred_exts,
                keywords=inferred_kws,
                domains=inferred_domains,
                enabled=True,
                is_custom=True,
            )
        )

    arch = FolderArchitecture(tier_count=max(2, max_tier_seen), nodes=nodes)
    if on_progress is not None:
        on_progress(max(1, total_dirs), max(1, total_dirs), f"Scanned {len(nodes)} folder(s)")
    return {
        "architecture": arch,
        "folder_count": len(nodes),
        "file_count": total_files,
        "max_tier": max_tier_seen,
    }


def merge_architectures(
    existing: FolderArchitecture,
    scanned: FolderArchitecture,
) -> FolderArchitecture:
    """Merge ``scanned`` folders into ``existing`` without overwriting existing nodes."""
    merged = existing.clone()
    merged.tier_count = max(merged.tier_count, scanned.tier_count)

    # Map existing (parent_id, name.lower()) -> node_id
    existing_key_to_id: dict[tuple[str | None, str], str] = {
        (n.parent_id, n.name.lower()): n.id for n in merged.nodes
    }
    scanned_to_merged_id: dict[str, str] = {}

    for node in sorted(scanned.nodes, key=lambda n: (n.tier, n.name.lower())):
        mapped_parent = scanned_to_merged_id.get(node.parent_id or "") if node.parent_id else None
        key = (mapped_parent, node.name.lower())
        if key in existing_key_to_id:
            scanned_to_merged_id[node.id] = existing_key_to_id[key]
            continue
        new_node = ArchNode.from_dict(node.to_dict())
        new_node.id = uuid.uuid4().hex[:8]
        new_node.parent_id = mapped_parent
        merged.nodes.append(new_node)
        existing_key_to_id[key] = new_node.id
        scanned_to_merged_id[node.id] = new_node.id

    return merged


def _snapshots_file(data_dir: Path) -> Path:
    return data_dir / "architecture_snapshots.json"


def list_architecture_snapshots(data_dir: Path) -> list[dict[str, Any]]:
    """Return saved Folder Architecture snapshots (newest first)."""
    import json

    path = _snapshots_file(data_dir)
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            return sorted(
                [item for item in raw if isinstance(item, dict)],
                key=lambda item: float(item.get("created_at") or 0.0),
                reverse=True,
            )
    except (OSError, json.JSONDecodeError):
        pass
    return []


def save_architecture_snapshot(
    arch: FolderArchitecture,
    name: str,
    data_dir: Path,
    *,
    note: str = "",
) -> dict[str, Any]:
    """Save a named snapshot of ``arch`` to ``architecture_snapshots.json``."""
    import json
    import time

    clean_name = str(name or "").strip() or f"Snapshot {time.strftime('%Y-%m-%d %H:%M')}"
    snapshots = list_architecture_snapshots(data_dir)
    entry = {
        "id": uuid.uuid4().hex[:8],
        "name": clean_name,
        "note": str(note or "").strip(),
        "created_at": time.time(),
        "tier_count": arch.tier_count,
        "node_count": len(arch.nodes),
        "architecture": arch.to_dict(),
    }
    snapshots.insert(0, entry)
    path = _snapshots_file(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshots[:30], indent=2), encoding="utf-8")
    return entry


def load_architecture_snapshot(snapshot_id: str, data_dir: Path) -> FolderArchitecture | None:
    """Restore a ``FolderArchitecture`` from a saved snapshot ID."""
    for item in list_architecture_snapshots(data_dir):
        if item.get("id") == snapshot_id and isinstance(item.get("architecture"), dict):
            return FolderArchitecture.from_dict(item["architecture"])
    return None


def delete_architecture_snapshot(snapshot_id: str, data_dir: Path) -> bool:
    """Delete a saved snapshot by ID."""
    import json

    snapshots = list_architecture_snapshots(data_dir)
    kept = [s for s in snapshots if s.get("id") != snapshot_id]
    if len(kept) == len(snapshots):
        return False
    path = _snapshots_file(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(kept, indent=2), encoding="utf-8")
    return True
