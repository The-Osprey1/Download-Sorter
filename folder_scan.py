"""Set Download Sorter up from the folders already in Downloads.

The welcome steps used to ask "which of these 4 profiles are you?". Instead, this
looks at the folders the user already keeps inside Downloads and suggests a
rule for each one, which the user ticks or unticks:

- A folder named like one of the main categories (``Pictures`` for Images,
  ``Music`` for Audio...) keeps its name: that kind of file goes there.
- Any other folder (``Invoices``, ``Banking``, ``IME 4030``...) gets a rule
  from the words in its name and words that keep coming back in the names of
  the files inside it (``invoice``, ``statement``...), limited to the file types
  it holds.
- Files already in the user's folders are also fed to the learner, so it knows
  those folders from day one (it never moves anything on its own).

Nothing here moves a file or changes the settings; ``apply_scan`` returns a new
config built from what the user ticked.
"""

from __future__ import annotations

from collections import Counter
import copy
import logging
import os
from pathlib import Path
from typing import Any, Iterable

import re

import learning
import sorter

MAX_FILES = 2000          # across all folders, so the welcome window opens quickly
MAX_FILES_PER_FOLDER = 300
MAX_DEPTH = 2             # a folder and the folders inside it
MAX_RULE_WORDS = 5

# Another name people use for one of the main folders.
CATEGORY_ALIASES: dict[str, frozenset[str]] = {
    "Documents": frozenset({"docs", "pdfs", "pdf", "paperwork", "papers"}),
    "Images": frozenset({"pictures", "picture", "photos", "photo", "pics", "screenshots", "wallpapers"}),
    "Videos": frozenset({"video", "movies", "movie", "clips", "recordings"}),
    "Audio": frozenset({"music", "songs", "sounds", "podcasts"}),
    "Archives": frozenset({"zips", "zip", "compressed"}),
    "Installers": frozenset({"install", "installs", "setup", "setups", "programs", "apps", "software"}),
    "3D Models": frozenset({"3d prints", "3d printing", "3d print", "prints", "models", "stl", "stls", "3d", "cad"}),
    "Code": frozenset({"scripts", "source", "programming", "projects code"}),
}

# Folders the app itself makes or uses for something else.
SKIP_FOLDERS = frozenset({"extracted", "makerworld", "other"})

MAKER_EXTENSIONS = frozenset({
    ".stl", ".3mf", ".obj", ".step", ".stp", ".iges", ".igs", ".f3d", ".f3z",
    ".sldprt", ".sldasm", ".ipt", ".iam", ".dwg", ".dxf", ".blend",
    ".gcode", ".bgcode", ".gco", ".nc", ".tap", ".ngc",
})

# Ready-made folders offered when Downloads has no folders yet: (key, label, rule).
STARTER_FOLDERS: list[tuple[str, str, dict[str, Any]]] = [
    ("receipts", "Invoices and receipts", {
        "name": "Invoices & Receipts", "folder": "Documents\\Invoices & Receipts",
        "match": ["invoice", "receipt", "billing", "billing_statement", "payment_confirmation"],
        "extensions": [".pdf", ".png", ".jpg", ".docx"], "enabled": True,
    }),
    ("school", "School: syllabi, notes and assignments", {
        "name": "School", "folder": "Documents\\School",
        "match": ["syllabus", "lecture", "lecture_notes", "class_notes", "homework", "assignment",
                  "lab_report", "problem_set", "study_guide", "midterm", "final_exam"],
        "extensions": [".pdf", ".docx", ".doc", ".pptx", ".txt", ".xlsx"], "enabled": True,
    }),
    ("career", "Resumes and job applications", {
        "name": "Resumes & Career", "folder": "Documents\\Career",
        "match": ["resume", "cv", "cover_letter", "offer_letter", "job_application"],
        "extensions": [".pdf", ".docx", ".doc"], "enabled": True,
    }),
    ("money", "Taxes and bank statements", {
        "name": "Taxes & Bank", "folder": "Documents\\Taxes & Bank",
        "match": ["tax_return", "w-2", "w2", "1099", "1040", "bank_statement", "paystub", "pay_stub"],
        "extensions": [".pdf", ".csv", ".xlsx"], "enabled": True,
    }),
]


# "IME 4030", "ME3210", "CS-101": a course code, kept as one phrase so the number counts.
COURSE_CODE = re.compile(r"^\s*([A-Za-z]{2,5})\s*[-_]?\s*(\d{3,4}[A-Za-z]?)\s*$")


def _plural(word: str) -> str:
    if word.endswith("y") and len(word) > 2 and word[-2] not in "aeiou":
        return word[:-1] + "ies"
    if word.endswith(("x", "ch", "sh", "ss", "z")):
        return word + "es"
    return word + "s"


def _rule_words(words: list[str]) -> list[str]:
    """Rule words match whole words only, so "invoice" also needs "invoices"."""
    out: list[str] = []
    for word in words:
        plain = " " not in word and not word[-1:].isdigit() and not word.endswith("ing")
        for form in (word, _plural(word)) if plain else (word,):
            if form not in out:
                out.append(form)
    return out


def _norm_name(name: str) -> str:
    return " ".join(name.lower().replace("_", " ").replace("-", " ").split())


def alias_category(folder_name: str) -> str | None:
    """The main category a folder name stands for ("Pictures" -> "Images"), if any."""
    name = _norm_name(folder_name)
    for category, names in CATEGORY_ALIASES.items():
        if name in names:
            return category
    return None


def _category_folders(config: dict) -> dict[str, str]:
    """{lowercase top-level folder: rule name} for the file-type rules."""
    found: dict[str, str] = {}
    for rule in config.get("extension_rules", []):
        folder = str(rule.get("folder") or "")
        if folder:
            found[sorter.split_folder(folder)[0].lower()] = str(rule.get("name") or folder)
    default = str(config.get("default_folder", "Other"))
    found.setdefault(default.lower(), default)
    return found


def _is_skipped(folder: Path, categories: dict[str, str]) -> bool:
    name = folder.name
    low = name.lower()
    if low.startswith((".", "_", "~")) or low in SKIP_FOLDERS or low in categories:
        return True
    if low in sorter.INTERNAL_DIR_NAMES:
        return True
    try:
        if (folder / ".download-sorter-keep").exists() or (folder / ".git").exists():
            return True
    except OSError:
        return True
    try:
        import remodel
        if remodel.looks_unsorted(name):
            return True
    except Exception:
        pass
    return False


def _files_in(folder: Path, limit: int) -> list[Path]:
    """Files in a folder and the folders inside it (MAX_DEPTH deep), newest code paths skipped."""
    files: list[Path] = []
    base_depth = len(folder.parts)
    try:
        for current, dirs, names in os.walk(folder):
            here = Path(current)
            depth = len(here.parts) - base_depth
            dirs[:] = [d for d in dirs if not d.startswith((".", "_")) and d not in ("node_modules", "venv", ".venv")]
            if depth >= MAX_DEPTH - 1:
                dirs[:] = []
            for name in names:
                if name.startswith((".", "~$")) or name.lower() == "desktop.ini":
                    continue
                files.append(here / name)
                if len(files) >= limit:
                    return files
    except OSError:
        pass
    return files


def _describe(rule: dict[str, Any]) -> str:
    words: list[str] = []
    for raw in rule.get("match", []):
        word = str(raw).replace("_", " ")
        if not any(word == _plural(w) for w in words):
            words.append(word)
    quoted = ", ".join(f"“{w}”" for w in words[:4])
    text = f"Files with {quoted} in the name" if words else "Files like these"
    exts = [str(e).lstrip(".").upper() for e in rule.get("extensions", [])]
    if exts and len(exts) <= 4:
        text += f" ({', '.join(exts)})"
    return text


def scan_downloads(config: dict, root: Path | None = None, max_files: int = MAX_FILES) -> dict[str, Any]:
    """Look at the folders in Downloads and suggest a rule for each one.

    Returns {"root", "folders": [...], "files_scanned", "maker_files",
    "empty", "examples": [(file name, folder)]}. Each folder is a dict with
    "name", "files", "kind" ("category" or "custom"), "checked", "summary", and
    either "category" (for a renamed main folder) or "rule" (a keyword rule),
    or neither when no pattern could be found yet.
    """
    root = Path(root) if root else sorter.watch_root(config)
    categories = _category_folders(config)
    result: dict[str, Any] = {
        "root": str(root), "folders": [], "files_scanned": 0, "maker_files": 0,
        "empty": True, "examples": [],
    }
    try:
        subfolders = sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name.lower())
    except OSError:
        return result

    collected: list[tuple[Path, list[Path]]] = []
    context: list[Path] = []   # files elsewhere in Downloads, only for word counts
    budget = max_files
    for folder in subfolders:
        if budget <= 0:
            break
        if _is_skipped(folder, categories):
            if folder.name.lower() in categories and not folder.name.lower().startswith("_"):
                more = _files_in(folder, min(MAX_FILES_PER_FOLDER, budget))
                budget -= len(more)
                context.extend(more)
            continue
        files = _files_in(folder, min(MAX_FILES_PER_FOLDER, budget))
        budget -= len(files)
        collected.append((folder, files))
    try:
        context.extend(p for p in list(root.iterdir())[:max(0, budget)] if p.is_file())
    except OSError:
        pass

    # How many files across all of Downloads contain each word, so a word that
    # shows up everywhere ("scan", "copy", or "statement" in a personal
    # statement in Documents) isn't taken as one folder's own.
    word_files: Counter = Counter()
    folder_words: dict[str, Counter] = {}
    for folder, files in collected:
        counts: Counter = Counter()
        for path in files:
            counts.update(set(learning.name_words(path.name)))
        folder_words[folder.name] = counts
        word_files.update(counts)
    for path in context:
        word_files.update(set(learning.name_words(path.name)))

    taken_categories = {c.lower() for c in categories}
    for folder, files in collected:
        exts = Counter(p.suffix.lower() for p in files if p.suffix)
        result["files_scanned"] += len(files)
        result["maker_files"] += sum(n for ext, n in exts.items() if ext in MAKER_EXTENSIONS)
        entry: dict[str, Any] = {"name": folder.name, "files": len(files), "kind": "custom", "checked": True}

        category = alias_category(folder.name)
        if category and category.lower() in taken_categories and not (root / category).is_dir():
            entry.update(kind="category", category=category,
                         summary=f"Use this folder for {category.lower()} instead of “{category}”")
        elif category:
            entry["checked"] = False
            entry["summary"] = (f"“{category}” already holds these. Files you move here "
                                "yourself stay here.")
        else:
            code = COURSE_CODE.match(folder.name)
            if code and re.fullmatch(r"(19|20)\d\d", code.group(2)):
                code = None  # "Taxes 2025" is a year, not a course
            words: list[str] = (
                [f"{code.group(1).lower()} {code.group(2).lower()}"] if code
                else [w for w in learning.name_words(folder.name)]
            )
            counts = folder_words.get(folder.name, Counter())
            for word, n in counts.most_common():
                if len(words) >= MAX_RULE_WORDS:
                    break
                if word in words or n < 3:
                    continue
                if n / max(1, len(files)) >= 0.3 and n / max(1, word_files[word]) >= 0.75:
                    words.append(word)
            if words:
                rule: dict[str, Any] = {
                    "name": folder.name, "folder": folder.name, "match": _rule_words(words),
                    "enabled": True, "origin": "scan",
                }
                top = [ext for ext, _ in exts.most_common(4)]
                if files and top and sum(exts[e] for e in top) >= 0.9 * len(files):
                    rule["extensions"] = sorted(top)
                entry["rule"] = rule
                entry["summary"] = _describe(rule)
            else:
                entry["checked"] = False
                entry["summary"] = "No pattern yet. Move files here and Download Sorter will learn it."
        result["folders"].append(entry)
        result["examples"].extend((p.name, folder.name) for p in files)

    result["empty"] = not result["folders"]
    return result


def seed_learning(scan: dict[str, Any], config: dict) -> int:
    """Teach the learner which kinds of files live in which folders. Returns how many."""
    if not learning.is_enabled(config):
        return 0
    examples = [(name, folder) for name, folder in scan.get("examples", []) if name and folder]
    if not examples:
        return 0
    try:
        return learning.seed_examples(examples)
    except Exception:
        logging.debug("Could not seed the learner from the folder scan", exc_info=True)
        return 0


def apply_scan(
    config: dict,
    scan: dict[str, Any],
    chosen_folders: Iterable[str] = (),
    categories_off: Iterable[str] = (),
    starters: Iterable[str] = (),
) -> dict:
    """A copy of ``config`` set up from what the user ticked.

    - chosen_folders: names of scanned folders to use;
    - categories_off: main categories (extension rule names or folders) to switch
      off; files of those types then go to "Other";
    - starters: keys of STARTER_FOLDERS to add.
    """
    import config_manager

    updated = config_manager.apply_persona_profile(config, "custom")
    chosen = {str(n).lower() for n in chosen_folders}
    off = {str(n).lower() for n in categories_off}

    rules: list[dict[str, Any]] = []
    for key, _label, rule in STARTER_FOLDERS:
        if key in set(starters):
            rules.append(copy.deepcopy(rule))
    for entry in scan.get("folders", []):
        if entry["name"].lower() not in chosen:
            continue
        if entry.get("kind") == "category":
            for ext_rule in updated.get("extension_rules", []):
                top = sorter.split_folder(str(ext_rule.get("folder") or ""))
                if top and top[0].lower() == str(entry["category"]).lower():
                    ext_rule["folder"] = entry["name"]
            if str(entry["category"]).lower() == "3d models":
                for key in ("haas_destination", "gcode_destination"):
                    if str(updated.get(key, "")).lower() == "3d models":
                        updated[key] = entry["name"]
        elif entry.get("rule"):
            rules.append(copy.deepcopy(entry["rule"]))

    names = {str(r.get("name") or "").lower() for r in rules}
    for rule in updated.get("keyword_rules", []):
        if str(rule.get("name") or "").lower() not in names:
            rules.append(rule)
    updated["keyword_rules"] = rules

    for ext_rule in updated.get("extension_rules", []):
        name = str(ext_rule.get("name") or "").lower()
        top = sorter.split_folder(str(ext_rule.get("folder") or ""))
        if name in off or (top and top[0].lower() in off):
            ext_rule["enabled"] = False

    if scan.get("maker_files", 0) >= 3:
        updated["show_maker_features"] = True
    updated["user_persona"] = "custom"
    return updated
