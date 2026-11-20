"""Keep every destination folder inside the watched folder.

Rules, file-type folders and the catch-all folder are stored as paths relative to
the watched folder (``Documents\\School``). Settings carried over from 2.x, or
typed by hand, can instead hold a full path (``C:\\Users\\me\\Documents\\Bank``),
a profile variable (``%USERPROFILE%\\Documents\\Bank``) or ``..``. Joined onto the
watched folder those either escape it or become a folder literally named
``${USERPROFILE}``. ``to_relative_dest`` turns them into a safe relative path.
"""

from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path, PureWindowsPath
import re

DEFAULT_FALLBACK = "Other"

# A path part that is still an unexpanded variable: ${NAME}, $NAME or %NAME%.
_VAR_PART_RE = re.compile(r"^(\$\{[^}]*\}|\$[A-Za-z_][A-Za-z0-9_]*|%[^%]*%)$")
_BAD_CHARS = set('<>:"|?*')
_UNSAFE_TOKEN_CHARS_RE = re.compile(r'[^A-Za-z0-9._-]+')
# Like the above, but keeps letters and digits from every language (for name text).
_UNSAFE_NAME_CHARS_RE = re.compile(r"[^\w.-]+")
_EXIF_DATE_RE = re.compile(rb"(\d{4}):(\d{2}):(\d{2}) (\d{2}):(\d{2}):(\d{2})")


def read_exif_date(path: Path | None) -> datetime | None:
    """Read photo capture date (EXIF DateTimeOriginal) from a JPEG, falling back to mtime."""
    if path is None or not path.is_file():
        return None
    if path.suffix.lower() in (".jpg", ".jpeg", ".tif", ".tiff"):
        try:
            with path.open("rb") as fh:
                head = fh.read(65536)
            m = _EXIF_DATE_RE.search(head)
            if m:
                y, mo, d, hh, mm, ss = (int(g) for g in m.groups())
                return datetime(y, mo, d, hh, mm, ss)
        except (OSError, ValueError):
            pass
    try:
        return datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return None


def resolve_destination_tokens(
    raw: str,
    *,
    host: str = "",
    ext: str = "",
    when: datetime | None = None,
    exif_date: datetime | None = None,
) -> str:
    """Expand safe dynamic tokens (``{year}``, ``{month}``, ``{day}``, ``{host}``, ``{ext}``, ``{exif_year}``, ``{exif_month}``)."""
    text = str(raw or "")
    if "{" not in text:
        return text
    now = when or datetime.now()
    photo_dt = exif_date or now
    clean_host = _UNSAFE_TOKEN_CHARS_RE.sub("-", (host or "").strip().lower()).strip(".-") or "unknown-site"
    clean_ext = _UNSAFE_TOKEN_CHARS_RE.sub("", (ext or "").strip().lower().lstrip(".")) or "other"
    replacements = {
        "{year}": f"{now.year:04d}",
        "{month}": f"{now.month:02d}",
        "{day}": f"{now.day:02d}",
        "{exif_year}": f"{photo_dt.year:04d}",
        "{exif_month}": f"{photo_dt.month:02d}",
        "{host}": clean_host,
        "{ext}": clean_ext,
    }
    for token, value in replacements.items():
        if token in text:
            text = text.replace(token, value)
    return text


def apply_rename_template(
    template: str,
    filename: str,
    *,
    host: str = "",
    when: datetime | None = None,
    exif_date: datetime | None = None,
    regex_groups: dict[str, str] | None = None,
) -> str:
    """Apply a safe filename-only rename template (no path separators allowed)."""
    tmpl = str(template or "").strip()
    if not tmpl:
        return filename
    p = Path(filename)
    stem = p.stem
    ext = p.suffix.lstrip(".")
    now = when or datetime.now()
    photo_dt = exif_date or now
    slug = re.sub(r"[\W_]+", "-", stem.lower()).strip("-") or "file"
    clean_host = _UNSAFE_TOKEN_CHARS_RE.sub("-", (host or "").strip().lower()).strip(".-") or "unknown-site"

    tokens = {
        "{stem}": stem,
        "{stem_lower}": stem.lower(),
        "{stem_slug}": slug,
        "{ext}": ext.lower(),
        "{year}": f"{now.year:04d}",
        "{month}": f"{now.month:02d}",
        "{day}": f"{now.day:02d}",
        "{exif_year}": f"{photo_dt.year:04d}",
        "{exif_month}": f"{photo_dt.month:02d}",
        "{host}": clean_host,
    }
    if regex_groups:
        for k, v in regex_groups.items():
            safe_val = _UNSAFE_NAME_CHARS_RE.sub("_", str(v or "")).strip("_")
            if k:
                tokens[f"{{{k}}}"] = safe_val

    out = tmpl
    for tok, val in tokens.items():
        if tok in out:
            out = out.replace(tok, val)
    # Strip any path separators or Windows-illegal characters
    out = out.replace("/", "_").replace("\\", "_")
    out = "".join(ch for ch in out if ch not in _BAD_CHARS).strip(". ")
    if not out:
        return filename
    if p.suffix and "." not in out:
        out = f"{out}{p.suffix}"
    return out


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
            from download_sorter.paths import default_downloads_dir

            watch_root = default_downloads_dir()
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
