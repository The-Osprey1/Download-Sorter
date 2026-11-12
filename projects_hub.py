"""Projects hub (RFC #1): your projects in one place, without moving them.

Moving a project folder breaks Git worktrees, virtual environments and CAD links, so the hub
leaves every project where it is. Instead it keeps a list of the projects you chose
(``projects_registry.json``) and, on Windows, an NTFS directory junction for each one inside a
hub folder (``%USERPROFILE%\\Projects Hub\\<kind>\\<name>``). Opening the junction opens the real
folder. Removing a junction never touches the project itself.

The hub sits outside Downloads on purpose, so the sorter never walks into a project through it.
The registered projects are also the "Route to" targets on the moved-file ribbon.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

REGISTRY_NAME = "projects_registry.json"
HUB_FOLDER_NAME = "Projects Hub"
KIND_FOLDERS = {
    "software": "Software",
    "cad_cam": "CAD and CAM",
    "foundry": "Foundry",
    "electronics": "Electronics",
    "coursework": "Coursework",
}


def _registry_file() -> Path:
    import sorter
    return Path(sorter.LOG_DIR) / REGISTRY_NAME


def hub_dir() -> Path:
    profile = os.environ.get("USERPROFILE") or str(Path.home())
    return Path(profile) / HUB_FOLDER_NAME


def load() -> list[dict]:
    """Registered projects: [{"name", "path", "kind"}], in the order they were added."""
    try:
        data = json.loads(_registry_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = data.get("projects") if isinstance(data, dict) else None
    return [p for p in items or [] if isinstance(p, dict) and p.get("path")]


def _save(items: list[dict]) -> None:
    target = _registry_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f"{target.name}.{uuid.uuid4().hex}.tmp")
    temp.write_text(json.dumps({"projects": items}, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, target)


def _key(path: str | Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def is_registered(path: str | Path) -> bool:
    return any(_key(p["path"]) == _key(path) for p in load())


def junction_path(project: dict, hub: Path | None = None) -> Path:
    folder = KIND_FOLDERS.get(str(project.get("kind")), "Other")
    return (hub or hub_dir()) / folder / str(project.get("name") or Path(project["path"]).name)


def is_junction(path: Path) -> bool:
    checker = getattr(os.path, "isjunction", None)
    if checker is not None:
        return bool(checker(path))
    try:
        return bool(os.lstat(path).st_file_attributes & 0x400) and path.is_dir()  # REPARSE_POINT
    except (OSError, AttributeError):
        return False


def _make_junction(link: Path, target: Path) -> None:
    if sys.platform != "win32":
        os.symlink(target, link, target_is_directory=True)
        return
    import _winapi
    _winapi.CreateJunction(str(target), str(link))


def add(path: str | Path, kind: str, name: str | None = None, hub: Path | None = None,
        make_link: bool = True) -> dict:
    """Register a project (and give it a junction in the hub). Returns its entry.

    The project folder itself is not moved, renamed or written to.
    """
    project_path = Path(path).resolve()
    if not project_path.is_dir():
        raise NotADirectoryError(str(project_path))
    items = [p for p in load() if _key(p["path"]) != _key(project_path)]
    entry = {"name": name or project_path.name, "path": str(project_path), "kind": kind}
    if make_link:
        link = junction_path(entry, hub)
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.exists() or is_junction(link):
            if not is_junction(link):
                raise FileExistsError(f"{link} exists and is not a hub link")
        else:
            _make_junction(link, project_path)
        entry["link"] = str(link)
    items.append(entry)
    _save(items)
    return entry


def remove(path: str | Path) -> bool:
    """Forget a project and remove its hub junction (only the junction; never the project)."""
    items = load()
    keep, gone = [], None
    for item in items:
        if _key(item["path"]) == _key(path):
            gone = item
        else:
            keep.append(item)
    if gone is None:
        return False
    link = Path(gone.get("link") or "")
    if gone.get("link") and is_junction(link):
        os.rmdir(link)  # removes the junction itself; the target folder is untouched
    _save(keep)
    return True


def route_targets() -> list[dict]:
    """Projects a downloaded file can be routed to from the ribbon (folders that still exist)."""
    return [p for p in load() if Path(p["path"]).is_dir()]


def route_file(file_path: str | Path, project: dict, batch_id: str = "quick_route") -> Path:
    """Move a filed download into a project's folder (journaled, so Undo brings it back)."""
    import safe_ops
    import sorter

    source = Path(file_path)
    target = sorter.unique_destination(Path(project["path"]), source.name)
    safe_ops.move(source, target, batch_id=batch_id, settle_seconds=0.0, honor_keep=False, skip_preflight=True)
    return target
