"""A searchable record of 3D print files: printer, print time, plates, filament (#25).

Each .3mf or G-code file that is filed gets one line in ``prints.jsonl`` (next to the other
history files). The Home list uses it so that searching for "PLA" or "X1C" finds the files
that were sliced with it, not only the ones whose name says so.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

PRINT_EXTENSIONS = frozenset({".3mf", ".gcode", ".bgcode"})
_MAX_LINES = 5000


def _log_file() -> Path:
    import sorter
    return Path(sorter.LOG_DIR) / "prints.jsonl"


def record_print(dest: Path | str, meta: dict) -> None:
    """Note the print details of a file that was just filed (a no-op for other files)."""
    dest = Path(dest)
    if dest.suffix.lower() not in PRINT_EXTENSIONS or not meta:
        return
    mw = meta.get("makerworld_meta") or {}
    row = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "file": dest.name,
        "path": str(dest),
        "slicer": meta.get("slicer"),
        "printer": meta.get("printer") or meta.get("machine") or None,
        "print_time": meta.get("print_time"),
        "plates": meta.get("plate_count"),
        "filament": meta.get("filament_type"),
        "filament_color": meta.get("filament_color"),
        "filament_weight_g": meta.get("filament_weight_g"),
        "nozzle": meta.get("nozzle_diameter"),
        "layer_height": meta.get("layer_height"),
        "designer": mw.get("designer"),
        "title": mw.get("title"),
    }
    target = _log_file()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({k: v for k, v in row.items() if v not in (None, "")}, ensure_ascii=False) + "\n")
        _trim(target)
    except OSError:
        pass


def _trim(target: Path) -> None:
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    if len(lines) <= _MAX_LINES:
        return
    temp = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    temp.write_text("\n".join(lines[-_MAX_LINES:]) + "\n", encoding="utf-8")
    os.replace(temp, target)


def rows() -> list[dict]:
    out: list[dict] = []
    try:
        for line in _log_file().read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict):
                out.append(item)
    except OSError:
        pass
    return out


_SEARCHABLE = ("file", "slicer", "printer", "filament", "filament_color", "designer", "title", "nozzle")


def matching_files(query: str) -> set[str]:
    """Names of the filed print files whose print details contain ``query`` (any case)."""
    needle = query.strip().lower()
    if not needle:
        return set()
    names: set[str] = set()
    for row in rows():
        haystack = " ".join(str(row.get(k, "")) for k in _SEARCHABLE).lower()
        if needle in haystack:
            names.add(str(row.get("file", "")))
    return names


def describe(row: dict) -> str:
    """One line for a print: "X1C · PLA · 2 plates · 3h 12m"."""
    parts = []
    for value in (row.get("printer"), row.get("filament"),
                  f"{row['plates']} plates" if row.get("plates") else None, row.get("print_time")):
        if value:
            parts.append(str(value))
    return " · ".join(parts)


def latest_for(file_name: str) -> dict | None:
    found = None
    for row in rows():
        if row.get("file") == file_name:
            found = row
    return found

