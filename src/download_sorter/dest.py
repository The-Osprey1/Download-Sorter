"""Keep every destination folder inside the watched folder.

Rules, file-type folders and the catch-all folder are stored as paths relative to
the watched folder (``Documents\\School``). Settings carried over from 2.x, or
typed by hand, can instead hold a full path (``C:\\Users\\me\\Documents\\Bank``),
a profile variable (``%USERPROFILE%\\Documents\\Bank``) or ``..``. Joined onto the
watched folder those either escape it or become a folder literally named
``${USERPROFILE}``. ``to_relative_dest`` turns them into a safe relative path.
"""

from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath
import re

DEFAULT_FALLBACK = "Other"

# A path part that is still an unexpanded variable: ${NAME}, $NAME or %NAME%.
_VAR_PART_RE = re.compile(r"^(\$\{[^}]*\}|\$[A-Za-z_][A-Za-z0-9_]*|%[^%]*%)$")
_BAD_CHARS = set('<>:"|?*')


def _expand(text: str) -> str:
    # ntpath.expandvars handles %NAME%, $NAME and ${NAME} alike on Windows;
    # USERPROFILE is spelled out so the same works where it is not set.
    home = str(Path.home())
    for spelling in ("${USERPROFILE}", "%USERPROFILE%", "$USERPROFILE"):
        text = re.sub(re.escape(spelling), lambda _m: home, text, flags=re.IGNORECASE)
    if text.startswith("~"):
        text = home + text[1:]
    return os.path.expandvars(text)


def _relative_to(path: PureWindowsPath, base: Path | None) -> PureWindowsPath | None:
    if base is None:
        return None
    base_win = PureWindowsPath(str(base))
    if [p.lower() for p in path.parts[: len(base_win.parts)]] != [p.lower() for p in base_win.parts]:
        return None
    return PureWindowsPath(*path.parts[len(base_win.parts):]) if len(path.parts) > len(base_win.parts) else PureWindowsPath()


def _clean_parts(parts: tuple[str, ...] | list[str]) -> list[str] | None:
    """Return the usable folder names, or None when a part is unsafe."""
    out: list[str] = []
    for raw in parts:
        name = raw.strip()
        if name == "..":
            return None
        name = name.rstrip(".").strip()  # Windows drops trailing dots and spaces
        if not name:
            continue
        if _BAD_CHARS & set(name):
            return None
        if _VAR_PART_RE.match(name):
            continue  # a variable this PC does not have: drop it, keep the rest
        out.append(name)
    return out


def to_relative_dest(
    raw: str,
    *,
    watch_root: Path | None = None,
    fallback: str = DEFAULT_FALLBACK,
) -> str:
    """Return ``raw`` as a folder path relative to the watched folder.

    - Already relative and safe: returned tidied (``Documents/School/`` -> ``Documents\\School``).
    - A full path (after expanding ``%USERPROFILE%`` and the like) inside the watched
      folder: the part below it. Inside the user's profile: the part below the profile
      (``...\\me\\Documents\\Bank`` -> ``Documents\\Bank``). Anywhere else: its last folder name.
    - Anything with ``..`` or characters Windows does not allow: ``fallback``.
    """
    text = str(raw or "").strip().replace("/", "\\")
    if not text:
        return fallback
    text = _expand(text)
    path = PureWindowsPath(text)

    if path.drive or path.root:  # C:\..., \\server\share\..., or \...
        if watch_root is None:
            watch_root = Path.home() / "Downloads"
        rel = _relative_to(path, watch_root)
        if rel is None:
            rel = _relative_to(path, Path.home())
        if rel is None:
            parts = _clean_parts([p for p in path.parts if p not in (path.anchor,)])
            if not parts:
                return fallback
            return parts[-1]
        parts = _clean_parts(rel.parts)
    else:
        parts = _clean_parts(path.parts)

    if not parts:
        return fallback
    return "\\".join(parts)


def is_under_watch_root(path: Path, watch_root: Path) -> bool:
    """True when ``path`` resolves inside ``watch_root`` (not beside it via ``..``)."""
    try:
        resolved = path.resolve()
        root = watch_root.resolve()
    except OSError:
        return False
    try:
        resolved.relative_to(root)
    except ValueError:
        return False
    return True
