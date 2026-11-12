"""MakerWorld model kits: one folder per model, for the .3mf and its STL, STEP and guide files.

A MakerWorld download arrives as several files. The .3mf says which model it is (its Title
and DesignModelId); the browser's "downloaded from" note says which MakerWorld model page the
other files came from (the slug in the URL is the model's title). Both name the same folder,
and a folder that already exists with the same name, ignoring case, spaces and punctuation,
is reused so a kit never splits in two.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

# https://makerworld.com/en/models/1234567-articulated-dragon  (the slug is optional)
_MODEL_URL = re.compile(r"makerworld\.com/(?:[a-z]{2}(?:-[a-z]{2})?/)?models/(\d+)(?:-([^/?#\s]+))?", re.IGNORECASE)

# Kinds of file that belong in a model kit when they came from a MakerWorld page.
KIT_EXTENSIONS = frozenset({".stl", ".step", ".stp", ".pdf"})


def normalize(name: str) -> str:
    """Folder names compare equal when they only differ in case, spacing or punctuation."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def title_from_slug(slug: str) -> str:
    """'articulated-dragon_v2' -> 'Articulated Dragon v2'."""
    words = re.split(r"[-_+]+", unquote(slug).strip())
    return " ".join(w[:1].upper() + w[1:] for w in words if w)


def model_from_zone_text(text: str) -> tuple[str, str] | None:
    """(model_id, title from the URL slug) when a download note names a MakerWorld model page."""
    values: dict[str, str] = {}
    for line in (text or "").splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            values[key.strip().lower()] = value.strip()
    for key in ("referrerurl", "hosturl"):
        match = _MODEL_URL.search(values.get(key, ""))
        if match:
            return match.group(1), title_from_slug(match.group(2) or "")
    return None


def model_from_file(path: Path) -> tuple[str, str] | None:
    """The MakerWorld model a loose download came from, read from its Zone.Identifier note."""
    try:
        import learning
        text = learning._read_zone_identifier(Path(path))
    except Exception:
        return None
    return model_from_zone_text(text)


def existing_folder(parent: Path, title: str) -> str:
    """The folder under ``parent`` already holding this model, or ``title`` for a new one."""
    wanted = normalize(title)
    try:
        for child in parent.iterdir():
            if child.is_dir() and wanted and normalize(child.name) == wanted:
                return child.name
    except OSError:
        pass
    return title


def _index_file():
    import sorter
    return Path(sorter.LOG_DIR) / "kits_index.json"


def folder_for_model(model_id: str | None, base_dir: Path) -> str | None:
    """The kit folder already holding this MakerWorld model id, even if the title differs.

    A re-saved multi-plate .3mf often has a different title from the first download but
    the same DesignModelId, so the id is the reliable key.
    """
    import json
    if not model_id:
        return None
    try:
        index = json.loads(_index_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    name = index.get(str(model_id)) if isinstance(index, dict) else None
    if name and (Path(base_dir) / name).is_dir():
        return str(name)
    return None


def remember_model(model_id: str | None, folder_name: str) -> None:
    """Note which kit folder a model id was filed into."""
    import json
    import os
    import uuid
    if not model_id or not folder_name:
        return
    target = _index_file()
    try:
        index = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(index, dict):
            index = {}
    except (OSError, ValueError):
        index = {}
    if index.get(str(model_id)) == folder_name:
        return
    index[str(model_id)] = folder_name
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(f"{target.name}.{uuid.uuid4().hex}.tmp")
        temp.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, target)
    except OSError:
        pass
