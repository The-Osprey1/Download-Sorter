"""Rules for which entries of a zip are real content, shared by the two unzip tools (RFC #2).

unzip_3d and unzip_archive used to each carry their own copy of the macOS-metadata and
path-safety checks. Both now ask this module. Each tool keeps its own list of extra junk names
(unzip_3d also skips readmes and licences), because those are policy, not safety.
"""

from __future__ import annotations

from pathlib import PurePosixPath

MAC_METADATA_PREFIXES = ("__MACOSX/",)
MAC_METADATA_NAME_PREFIX = "._"
SYSTEM_JUNK_NAMES = frozenset({".ds_store", "thumbs.db", "desktop.ini"})


def is_directory_entry(name: str) -> bool:
    return name.endswith("/")


def is_system_junk(name: str) -> bool:
    """macOS resource forks and Finder/Explorer housekeeping files."""
    normalized = name.replace("\\", "/")
    if any(normalized.startswith(p) or f"/{p}" in normalized for p in MAC_METADATA_PREFIXES):
        return True
    base = PurePosixPath(normalized).name
    return base.startswith(MAC_METADATA_NAME_PREFIX) or base.lower() in SYSTEM_JUNK_NAMES


def safe_relative_parts(name: str) -> tuple[str, ...] | None:
    """The folder parts of an entry, with '.', '..' and empty parts removed. None when the entry
    is absolute or names a drive, so it must not be placed at all."""
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or (len(normalized) > 1 and normalized[1] == ":"):
        return None
    return tuple(p for p in normalized.split("/") if p not in ("", ".", ".."))
