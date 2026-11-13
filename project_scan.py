"""Find project folders, tell what kind they are, and report the ones gone stale (RFC #1).

A project is a folder that holds a recognisable marker: a Git repository or a build file for
software; Fusion, SolidWorks or G-code files for CAD/CAM; SOLIDCast or MAGMASOFT files for
foundry work; KiCad or Arduino files for electronics; a course code in the folder name for
coursework. Nothing here moves or deletes a project. ``archive`` writes a zip copy (leaving out
regenerable folders such as node_modules and .venv) and reports what it did; removing the original
is left to the person.

    python project_scan.py                 # list projects found under Downloads and Documents
    python project_scan.py --archive NAME  # zip one project into Projects\\_Archive\\<year>\
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

STALE_DAYS = 90
SCAN_LIMIT = 4000  # files looked at per project, to keep a scan quick
REGENERABLE = frozenset({"node_modules", ".venv", "venv", "__pycache__", ".pytest_cache",
                         ".mypy_cache", "build", "dist", ".tox", ".gradle"})
COURSE_CODE = re.compile(r"\b[A-Z]{2,4}\s?\d{4}\b", re.IGNORECASE)

KINDS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    # (kind, marker names, marker suffixes)
    ("software", ("pyproject.toml", "package.json", "cargo.toml", "cmakelists.txt", "requirements.txt"), ()),
    ("cad_cam", (), (".f3d", ".f3z", ".sldasm", ".sldprt", ".nc", ".tap", ".ngc")),
    ("foundry", ("flowcast.set",), (".fsimodel9", ".afs", ".magma")),
    ("electronics", (), (".kicad_pro", ".kicad_pcb", ".ino")),
)


@dataclass
class Project:
    path: Path
    kind: str
    last_active: float
    git_clean: bool | None

    @property
    def stale(self) -> bool:
        return (time.time() - self.last_active) > STALE_DAYS * 86400


def _walk_files(folder: Path, limit: int = SCAN_LIMIT):
    seen = 0
    stack = [folder]
    while stack and seen < limit:
        current = stack.pop()
        try:
            children = list(current.iterdir())
        except OSError:
            continue
        for child in children:
            if child.name in REGENERABLE:
                continue
            try:
                is_dir = child.is_dir()
            except OSError:
                continue
            if is_dir:
                stack.append(child)
            else:
                seen += 1
                yield child
                if seen >= limit:
                    return


def fingerprint(folder: Path) -> str | None:
    """The kind of project a folder is, from its markers, or None."""
    try:
        if (folder / ".git").exists():
            return "software"
        names = {p.name.lower() for p in folder.iterdir() if p.is_file()} if folder.is_dir() else set()
    except OSError:  # a folder Windows won't let us list (for example "My Music" in Documents)
        return None
    for kind, marker_names, _suffixes in KINDS:
        if any(n in names for n in marker_names):
            return kind
    for file in _walk_files(folder, limit=400):
        suffix = file.suffix.lower()
        for kind, _names, suffixes in KINDS:
            if suffix in suffixes:
                return kind
    if COURSE_CODE.search(folder.name):
        return "coursework"
    return None


def _git_clean(folder: Path) -> bool | None:
    if not (folder / ".git").exists():
        return None
    try:
        out = subprocess.run(["git", "-C", str(folder), "status", "--porcelain"],
                             capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.returncode == 0 and not out.stdout.strip()


def last_active(folder: Path) -> float:
    latest = 0.0
    for file in _walk_files(folder):
        try:
            latest = max(latest, file.stat().st_mtime)
        except OSError:
            continue
    return latest or folder.stat().st_mtime


def scan(roots: list[Path]) -> list[Project]:
    """Projects that sit directly inside each root (a root's own subfolders are checked)."""
    found: list[Project] = []
    for root in roots:
        try:
            children = sorted(root.iterdir()) if root.is_dir() else []
        except OSError:
            continue
        for child in children:
            # One folder that can't be read (no permission, a drive that went away) is left out;
            # it must not stop the whole scan.
            try:
                if not child.is_dir() or child.name.startswith(("_", ".")) or child.name in REGENERABLE:
                    continue
                kind = fingerprint(child)
                if kind is None:
                    continue
                found.append(Project(child, kind, last_active(child), _git_clean(child)))
            except OSError:
                continue
    return found


def archive(project: Path, archive_root: Path, now: float | None = None) -> Path:
    """Write ``archive_root\\<year>\\<name>.zip`` with the project's files, leaving out regenerable
    folders. The original folder is not touched."""
    when = time.localtime(time.time() if now is None else now)
    target_dir = archive_root / str(when.tm_year)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{project.name}.zip"
    if target.exists():
        raise FileExistsError(f"{target} already exists")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for path in project.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(project)
            if any(part in REGENERABLE for part in rel.parts):
                continue
            z.write(path, Path(project.name, rel).as_posix())
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="List project folders and their state.")
    parser.add_argument("--archive", metavar="NAME", help="zip one project by folder name")
    args = parser.parse_args(argv)
    import sorter
    config = sorter.load_config()
    downloads = sorter.watch_root(config)
    roots = [downloads / "Extracted", Path.home() / "Documents", Path.home() / "Desktop"]
    projects = scan(roots)
    if args.archive:
        match = [p for p in projects if p.path.name == args.archive]
        if not match:
            print(f"No project named {args.archive!r} was found.", file=sys.stderr)
            return 1
        print(f"Wrote {archive(match[0].path, downloads / 'Projects' / '_Archive')}")
        return 0
    if not projects:
        print("No projects found.")
    for p in projects:
        state = "stale" if p.stale else "active"
        git = "" if p.git_clean is None else (", git clean" if p.git_clean else ", git has changes")
        print(f"{p.path.name:<36} {p.kind:<12} {state}{git}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
