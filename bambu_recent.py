"""Keep Bambu Studio's "Recently Opened" list pointing at files that were sorted.

Bambu Studio remembers the .3mf projects it opened in ``recent_projects`` inside
``%APPDATA%\\BambuStudio\\BambuStudio.conf``. When Download Sorter moves a .3mf to its model
folder, that entry would point at a file that no longer exists. This module records each
such move, and once Bambu Studio is closed (it rewrites the file on exit, so writing while
it runs would be lost) it replaces the old path with the new one.

The conf file ends with ``# MD5 checksum <hex>``: the MD5 of the body with line endings
normalised to LF and the final newline removed. That footer is recomputed after each edit.
A backup is kept beside the conf file the first time it is changed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sys
import uuid
from pathlib import Path

import sorter

CONF_NAME = "BambuStudio.conf"
BACKUP_NAME = "BambuStudio.conf.download-sorter.bak"
PENDING_NAME = "bambu_recent_pending.json"
BAMBU_PROCESS = "BambuStudio.exe"
_FOOTER = re.compile(r"# MD5 checksum ([0-9A-Fa-f]{32})")


def conf_path() -> Path | None:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return Path(appdata) / "BambuStudio" / CONF_NAME


def _pending_file() -> Path:
    return Path(sorter.LOG_DIR) / PENDING_NAME


def _load_pending() -> list[dict]:
    try:
        data = json.loads(_pending_file().read_text(encoding="utf-8"))
        return [item for item in data if isinstance(item, dict)]
    except (OSError, ValueError):
        return []


def _save_pending(items: list[dict]) -> None:
    target = _pending_file()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if not items:
            target.unlink(missing_ok=True)
            return
        temp = target.with_name(f"{target.name}.{uuid.uuid4().hex}.tmp")
        temp.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, target)
    except OSError as exc:
        logging.debug("Could not save Bambu recent-file queue: %s", exc)


def is_enabled(config: dict | None) -> bool:
    """True only when the user has opted in to updating BambuStudio.conf."""
    if not isinstance(config, dict):
        return False
    block = config.get("bambu_recent")
    return isinstance(block, dict) and bool(block.get("enabled", False))


def has_asked(config: dict | None) -> bool:
    """True once the user has answered the one-time Bambu recent-list prompt."""
    if not isinstance(config, dict):
        return False
    block = config.get("bambu_recent")
    return isinstance(block, dict) and bool(block.get("asked", False))


def should_ask(config: dict | None) -> bool:
    """True when we should offer to enable Bambu recent-list updates once."""
    return not is_enabled(config) and not has_asked(config) and bambu_exe() is not None


def save_preference(enabled: bool, asked: bool = True) -> bool:
    """Persist the user's choice for ``bambu_recent`` into sorter config."""
    try:
        import config_manager
        cfg = config_manager.load_sorter_config()
        block = cfg.get("bambu_recent") if isinstance(cfg.get("bambu_recent"), dict) else {}
        block["enabled"] = bool(enabled)
        block["asked"] = bool(asked)
        cfg["bambu_recent"] = block
        return bool(config_manager.save_sorter_config(cfg))
    except Exception as exc:
        logging.warning("Could not save Bambu recent-list preference: %s", exc)
        return False


def has_backup(path: Path | None = None) -> bool:
    """True when a pre-edit backup of BambuStudio.conf exists on disk."""
    target = path or conf_path()
    if target is None:
        return False
    return target.with_name(BACKUP_NAME).is_file()


def restore_backup(path: Path | None = None) -> bool:
    """Restore BambuStudio.conf from its backup when Bambu Studio is closed."""
    target = path or conf_path()
    if target is None:
        return False
    backup = target.with_name(BACKUP_NAME)
    if not backup.is_file():
        return False
    import hold_manager
    if hold_manager.is_slicer_running([BAMBU_PROCESS]):
        logging.info("Cannot restore %s while Bambu Studio is running", CONF_NAME)
        return False
    try:
        temp = target.with_name(f"{target.name}.{uuid.uuid4().hex}.tmp")
        shutil.copy2(backup, temp)
        os.replace(temp, target)
        _save_pending([])
        logging.info("Restored %s from backup", CONF_NAME)
        return True
    except OSError as exc:
        logging.warning("Could not restore %s from backup: %s", CONF_NAME, exc)
        return False


def record_move(old: Path | str, new: Path | str, config: dict | None = None) -> None:
    """Remember that a .3mf moved, so Bambu's Recently Opened can follow it later."""
    if config is not None and not is_enabled(config):
        return
    old_s, new_s = str(old), str(new)
    if not old_s.lower().endswith(".3mf") or old_s.lower() == new_s.lower():
        return
    items = [i for i in _load_pending() if i.get("old", "").lower() != old_s.lower()]
    items.append({"old": old_s, "new": new_s})
    _save_pending(items)


def checksum_of(text: str) -> str:
    """The footer checksum for a conf body (text before the footer line)."""
    body = text.replace("\r\n", "\n").rstrip("\n")
    return hashlib.md5(body.encode("utf-8")).hexdigest().upper()


def _escaped(path: str) -> str:
    """How the path is written inside the conf's JSON (backslashes doubled, UTF-8 kept)."""
    return json.dumps(path, ensure_ascii=False)[1:-1]


def _replace_in_recent(text: str, old: str, new: str) -> tuple[str, int]:
    start = text.find('"recent_projects"')
    if start < 0:
        return text, 0
    end = text.find("}", start)
    if end < 0:
        return text, 0
    block = text[start:end]
    pattern = re.compile(re.escape(_escaped(old)), re.IGNORECASE)
    new_block, count = pattern.subn(lambda _m: _escaped(new), block)
    return text[:start] + new_block + text[end:], count


def apply_pending(path: Path | None = None, config: dict | None = None) -> int:
    """Update Bambu's recent list for the queued moves. Returns how many entries changed.

    Does nothing when disabled in config, while Bambu Studio is running, and when the conf
    file cannot be read or would not parse after the edit. Queued moves are dropped once they
    are applied or once Bambu has no entry for them.
    """
    if config is not None and not is_enabled(config):
        return 0
    items = _load_pending()
    if not items or sys.platform != "win32":
        return 0
    target = path or conf_path()
    if target is None or not target.exists():
        return 0
    import hold_manager
    if hold_manager.is_slicer_running([BAMBU_PROCESS]):
        return 0

    try:
        raw = target.read_bytes()
    except OSError:
        return 0
    text = raw.decode("utf-8")
    footer = _FOOTER.search(text)
    if not footer:
        logging.info("BambuStudio.conf has no checksum footer; recent list left alone")
        return 0
    body = text[:footer.start()]

    changed = 0
    remaining: list[dict] = []
    for item in items:
        body, count = _replace_in_recent(body, item["old"], item["new"])
        changed += count
        if count == 0:
            # Bambu has no entry for this file (or none yet): nothing to fix, drop it.
            continue
    if not changed:
        _save_pending(remaining)
        return 0

    try:
        json.loads(body.replace("\r\n", "\n"))
    except ValueError as exc:
        logging.warning("BambuStudio.conf would not parse after the edit (%s); left unchanged", exc)
        return 0

    new_text = body + text[footer.start():].replace(footer.group(1), checksum_of(body), 1)
    backup = target.with_name(BACKUP_NAME)
    try:
        if not backup.exists():
            shutil.copy2(target, backup)
        temp = target.with_name(f"{target.name}.{uuid.uuid4().hex}.tmp")
        temp.write_bytes(new_text.encode("utf-8"))
        os.replace(temp, target)
    except OSError as exc:
        logging.warning("Could not update BambuStudio.conf: %s", exc)
        return 0
    _save_pending(remaining)
    logging.info("Bambu Studio's recent list now points at %d moved .3mf file(s)", changed)
    return changed


def bambu_exe() -> Path | None:
    """Where Bambu Studio is installed, if it is (the usual install folders)."""
    if sys.platform != "win32":
        return None
    roots = [os.environ.get(k) for k in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
    names = (r"Bambu Studio\bambu-studio.exe", r"BambuStudio\bambu-studio.exe",
             r"Bambu Studio\BambuStudio.exe", r"Programs\Bambu Studio\bambu-studio.exe")
    for root in roots:
        if not root:
            continue
        for name in names:
            candidate = Path(root) / name
            if candidate.is_file():
                return candidate
    return None


def open_in_bambu(path: Path | str) -> bool:
    """Open a sorted .3mf in Bambu Studio, so its Recently Opened entry is the sorted path."""
    exe = bambu_exe()
    if exe is None or not Path(path).is_file():
        return False
    import subprocess
    flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    subprocess.Popen([str(exe), str(path)], creationflags=flags, close_fds=True)
    return True
