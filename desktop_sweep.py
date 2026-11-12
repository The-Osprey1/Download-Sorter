"""Optionally sweep loose files off the Desktop once they have sat there for a while (#25).

Off by default (Settings: "desktop_sweep": {"enabled": true, "days": 7}). A file that has been on
the Desktop for that long, unchanged, is moved into Downloads, where the normal sorter files it
like any download, so the rules and undo history apply. Shortcuts, folders and system files are
left alone. The Desktop is looked at once a day.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path

SKIP_SUFFIXES = frozenset({".lnk", ".url", ".ini", ".sys", ".tmp", ".crdownload", ".part"})
STATE_NAME = "desktop_sweep.json"
EVERY_SECONDS = 24 * 3600


def desktop_folder() -> Path:
    if sys.platform == "win32":
        profile = os.environ.get("USERPROFILE") or str(Path.home())
        return Path(profile) / "Desktop"
    return Path.home() / "Desktop"


def _arrival(stat: os.stat_result) -> float:
    import clean_old
    return clean_old.arrival_time(stat)


def sweep(config: dict, desktop: Path | None = None, now: float | None = None, force: bool = False) -> int:
    """Move the stale loose Desktop files into Downloads. Returns how many were moved."""
    import safe_ops
    import sorter

    block = config.get("desktop_sweep") if isinstance(config.get("desktop_sweep"), dict) else {}
    if not block.get("enabled"):
        return 0
    now = time.time() if now is None else now
    state_file = Path(sorter.LOG_DIR) / STATE_NAME
    if not force:
        try:
            last = float(json.loads(state_file.read_text(encoding="utf-8")).get("last_run", 0))
        except (OSError, ValueError, AttributeError):
            last = 0.0
        if 0 <= now - last < EVERY_SECONDS:
            return 0

    days = max(1.0, float(block.get("days", 7) or 7))
    source = desktop or desktop_folder()
    downloads = sorter.watch_root(config)
    moved = 0
    if source.is_dir():
        for path in source.iterdir():
            try:
                if not path.is_file() or path.suffix.lower() in SKIP_SUFFIXES or path.name.startswith("~"):
                    continue
                if (now - _arrival(path.stat())) < days * 86400:
                    continue
                target = sorter.unique_destination(downloads, path.name)
                safe_ops.move(path, target, batch_id="desktop_sweep", settle_seconds=0.0,
                              honor_keep=False, skip_preflight=True)
                moved += 1
            except (OSError, safe_ops.Refused) as exc:
                logging.info("Left %s on the Desktop: %s", path.name, exc)
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps({"last_run": now, "moved": moved}), encoding="utf-8")
    except OSError:
        pass
    return moved
