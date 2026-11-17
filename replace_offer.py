"""Offer to replace an older file when a new download has the same name.

The sorter never overwrites. When a download's name matches a *different* file
already at its destination, the new file is filed as ``name (2).ext`` and an
offer is saved here. A ribbon then asks "Replace older copy?". Replace sends the
old file to the Recycle Bin and gives the new file the original name (journaled,
so Undo renames it back). Keep both, closing or timing out changes nothing.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import sorter

PENDING_FILE = sorter.LOG_DIR / "pending_replacements.json"
MAX_AGE_SECONDS = 24 * 3600
_lock = threading.Lock()

# "report (1).pdf" -> "report.pdf"; handles "archive (3).tar.gz" too.
_BROWSER_COPY = re.compile(r"^(?P<stem>.+?) \((?P<n>\d{1,4})\)(?P<ext>(?:\.[^.\s()]{1,8}){0,2})$")


def is_enabled(config: dict | None) -> bool:
    block = (config or {}).get("replace")
    if isinstance(block, dict):
        return bool(block.get("offer", True))
    return True


def original_name(name: str) -> str:
    """Strip the ' (N)' suffix a browser adds when a name is already taken."""
    m = _BROWSER_COPY.match(name)
    if not m:
        return name
    return f"{m.group('stem')}{m.group('ext')}"


def _read() -> dict[str, Any]:
    try:
        data = json.loads(PENDING_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(data: dict[str, Any]) -> None:
    PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PENDING_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, PENDING_FILE)


def _prune(data: dict[str, Any]) -> dict[str, Any]:
    now = time.time()
    return {k: v for k, v in data.items() if now - float(v.get("created", 0)) < MAX_AGE_SECONDS}


def _stamp(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
        return st.st_size, st.st_mtime_ns
    except OSError:
        return None


def record_offer(new_path: Path | str, old_path: Path | str) -> str:
    new_path, old_path = Path(new_path), Path(old_path)
    offer_id = uuid.uuid4().hex[:12]
    entry = {
        "id": offer_id,
        "new": str(new_path),
        "old": str(old_path),
        "new_stamp": list(_stamp(new_path) or ()),
        "old_stamp": list(_stamp(old_path) or ()),
        "created": time.time(),
    }
    with _lock:
        data = _prune(_read())
        data[offer_id] = entry
        _write(data)
    return offer_id


def pending_offer(offer_id: str) -> dict[str, Any] | None:
    with _lock:
        return _prune(_read()).get(offer_id)


def _pop(offer_id: str) -> dict[str, Any] | None:
    with _lock:
        data = _prune(_read())
        entry = data.pop(offer_id, None)
        _write(data)
    return entry


def decline(offer_id: str) -> None:
    _pop(offer_id)


def describe(entry: dict[str, Any]) -> tuple[str, str]:
    old, new = Path(entry["old"]), Path(entry["new"])
    return "Replace older copy?", f"{old.name} already in {old.parent.name} — new one saved as {new.name}"


def _recycle(path: Path) -> bool:
    from clean_old import send_to_recycle_bin
    return send_to_recycle_bin(path)


def accept(offer_id: str) -> Path | None:
    """Recycle the old file and rename the new file to the old name. None if refused."""
    import safe_ops

    entry = _pop(offer_id)
    if not entry:
        return None
    old, new = Path(entry["old"]), Path(entry["new"])
    if not (old.is_file() and new.is_file()):
        logging.info("Replace skipped: a file is gone (%s / %s)", old, new)
        return None
    if list(_stamp(old) or ()) != entry.get("old_stamp") or list(_stamp(new) or ()) != entry.get("new_stamp"):
        logging.info("Replace skipped: %s or %s changed since the offer", old.name, new.name)
        return None
    if not (safe_ops.can_open_exclusive(old) and safe_ops.can_open_exclusive(new)):
        logging.info("Replace skipped: %s or %s is open in another program", old.name, new.name)
        return None
    if not _recycle(old):
        logging.warning("Replace skipped: could not send %s to the Recycle Bin", old)
        return None
    try:
        final = safe_ops.move(new, old, skip_preflight=True, op="replace")
    except (safe_ops.Refused, OSError) as exc:
        logging.warning("Replace: old copy recycled but rename of %s failed: %s", new.name, exc)
        return None
    logging.info("Replaced %s with newer download %s (old copy in Recycle Bin)", old, new.name)
    return final
