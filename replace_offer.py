"""Offer to replace an older file when a new download has the same name.

The sorter never overwrites. When a download's name matches a *different* file
already at its destination, the new file is filed as ``name (2).ext`` (or keeps
its own name if free) and an offer is saved as ``pending_replacements/<id>.json``.
A ribbon then asks "Replace older copy?". Replace quarantines the old file into
``_Duplicates/<YYYY-MM>`` via ``safe_ops.quarantine`` and renames the new file to
the original name via ``safe_ops.move`` (both journaled under the same batch_id,
so Undo restores both). Keep both, closing or timing out changes nothing.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

import sorter

PENDING_DIR = sorter.LOG_DIR / "pending_replacements"
MAX_AGE_SECONDS = 24 * 3600

# Matches Chrome "report (1).pdf" and Firefox "report(1).pdf" (1..999, no leading 0,
# no 4-digit years). Two-part extensions limited to .tar.<gz|xz|bz2|zst>.
_BROWSER_COPY = re.compile(
    r"^(?P<stem>.+?) ?\((?P<n>[1-9]\d{0,2})\)"
    r"(?P<ext>(?:\.tar\.(?:gz|xz|bz2|zst)|\.[^.\s()]{1,8})?)$"
)


def is_enabled(config: dict | None) -> bool:
    block = (config or {}).get("replace")
    if isinstance(block, dict):
        return bool(block.get("offer", True))
    return True


def original_name(name: str) -> str:
    """Strip the ' (N)' or '(N)' suffix a browser adds when a name is already taken."""
    m = _BROWSER_COPY.match(name)
    if not m:
        return name
    return f"{m.group('stem')}{m.group('ext')}"


def _offer_path(offer_id: str) -> Path:
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "", offer_id)
    return PENDING_DIR / f"{safe_id}.json"


def _prune_stale() -> None:
    if not PENDING_DIR.exists():
        return
    now = time.time()
    for p in PENDING_DIR.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if now - float(data.get("created", 0)) >= MAX_AGE_SECONDS:
                p.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass


def _stamp(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
        return st.st_size, st.st_mtime_ns
    except OSError:
        return None


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def record_offer(
    new_path: Path | str,
    old_path: Path | str,
    *,
    downloads_root: Path | str | None = None,
    batch_id: str = "",
) -> str:
    new_path, old_path = Path(new_path), Path(old_path)
    offer_id = uuid.uuid4().hex[:12]
    entry = {
        "id": offer_id,
        "new": str(new_path),
        "old": str(old_path),
        "downloads": str(Path(downloads_root)) if downloads_root else "",
        "batch_id": batch_id or f"replace_{offer_id}",
        "new_stamp": list(_stamp(new_path) or ()),
        "old_stamp": list(_stamp(old_path) or ()),
        "created": time.time(),
    }
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    _prune_stale()
    target = _offer_path(offer_id)
    tmp = target.with_suffix(f".tmp.{os.getpid()}.{uuid.uuid4().hex[:6]}")
    tmp.write_text(json.dumps(entry, indent=2), encoding="utf-8")
    os.replace(tmp, target)
    return offer_id


def pending_offer(offer_id: str) -> dict[str, Any] | None:
    p = _offer_path(offer_id)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if time.time() - float(data.get("created", 0)) >= MAX_AGE_SECONDS:
            p.unlink(missing_ok=True)
            return None
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def decline(offer_id: str) -> None:
    _offer_path(offer_id).unlink(missing_ok=True)


def describe(entry: dict[str, Any]) -> tuple[str, str]:
    old, new = Path(entry["old"]), Path(entry["new"])
    old_sz = (entry.get("old_stamp") or [None])[0]
    new_sz = (entry.get("new_stamp") or [None])[0]
    if old_sz is not None and new_sz is not None:
        detail = f" (older: {_fmt_size(old_sz)} · new: {_fmt_size(new_sz)})"
    else:
        detail = ""
    return "Replace older copy?", f"{old.name} already in {old.parent.name}{detail}"


def accept(offer_id: str) -> tuple[Path | None, str]:
    """Quarantine the old file and rename the new file to the old name.

    Returns ``(final_path, status)`` where status is one of:
    ``"replaced"``, ``"missing"``, ``"changed"``, ``"locked"``,
    ``"quarantine_failed"``, ``"still_exists"``, ``"rename_failed"``.
    """
    import safe_ops

    entry = pending_offer(offer_id)
    if not entry:
        return None, "missing"
    old, new = Path(entry["old"]), Path(entry["new"])
    if not (old.is_file() and new.is_file()):
        logging.info("Replace skipped: a file is gone (%s / %s)", old, new)
        decline(offer_id)
        return None, "missing"
    if list(_stamp(old) or ()) != entry.get("old_stamp") or list(_stamp(new) or ()) != entry.get("new_stamp"):
        logging.info("Replace skipped: %s or %s changed since the offer", old.name, new.name)
        decline(offer_id)
        return None, "changed"
    if not (safe_ops.can_open_exclusive(old) and safe_ops.can_open_exclusive(new)):
        logging.info("Replace skipped: %s or %s is open in another program", old.name, new.name)
        return None, "locked"

    dl_root = Path(entry["downloads"]) if entry.get("downloads") else old.parent
    dup_dir = sorter.duplicates_folder(dl_root)
    batch_id = entry.get("batch_id") or f"replace_{offer_id}"

    try:
        safe_ops.quarantine(old, dup_dir, batch_id=batch_id)
    except (safe_ops.Refused, OSError) as exc:
        logging.warning("Replace skipped: could not quarantine %s: %s", old, exc)
        return None, "quarantine_failed"

    if old.exists():
        logging.warning("Replace stopped: %s still exists after quarantine", old)
        return None, "still_exists"

    try:
        final = safe_ops.move(new, old, batch_id=batch_id, skip_preflight=True, op="replace")
    except (safe_ops.Refused, OSError) as exc:
        logging.warning("Replace: old copy quarantined but rename of %s failed: %s", new.name, exc)
        decline(offer_id)
        return None, "rename_failed"

    decline(offer_id)
    logging.info("Replaced %s with newer download %s (old copy in %s)", old, new.name, dup_dir)
    return final, "replaced"
