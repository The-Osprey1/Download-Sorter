"""Single disk-operations layer for Download Sorter.

Every operation that moves, links, quarantines or restores a user file goes
through this module so that all of them share the same safety rules:

* **Pre-flight checks** - the file must exist, must not look like an in-progress
  save (``~$*``, ``*.3mf.tmp``, ``*.tmp``, ``*.part``, ``*.crdownload`` ...), must not
  be an un-hydrated cloud placeholder, must not sit under a ``.download-sorter-keep``
  marker, must have been quiet for ``settle_seconds`` (default 3 s), and must open
  exclusively (nothing else is writing to it).
* **Never overwrite** - an occupied destination is always resolved to a fresh
  ``name (2).ext`` instead.
* **Write-ahead journal** - a ``planned`` record is appended to ``history.jsonl`` and
  fsynced *before* the filesystem is touched, and a ``done`` record (the classic move
  record that Undo understands) is appended and fsynced afterwards.  If the process
  dies in between, :func:`recover_pending_journal` finds the orphaned ``planned``
  record at the next start and reconciles it with what is actually on disk.

History paths are read from the :mod:`sorter` module at call time (never cached) so
tests and alternate state directories keep working.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import shutil
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import sorter

DEFAULT_SETTLE_SECONDS = 3.0
KEEP_MARKER = ".download-sorter-keep"

# Names that mean "an application is still writing this file".
TEMP_SAVE_PATTERNS: tuple[str, ...] = (
    "~$*",
    "*.3mf.tmp",
    "*.tmp",
    "*.part",
    "*.partial",
    "*.crdownload",
    "*.download",
    "*.opdownload",
    ".~lock.*",
)

_JOURNAL_LOCK = threading.RLock()

# Archive types the sorter extracts; used to recognise old extraction records.
ARCHIVE_SUFFIXES: frozenset[str] = frozenset({".zip"})


class Refused(Exception):
    """A pre-flight safety check declined the operation (nothing was touched)."""

    def __init__(self, reason: str, path: Path | str = "") -> None:
        super().__init__(f"{reason}: {path}")
        self.reason = reason
        self.path = Path(path) if path else None


class CrossVolumeError(OSError):
    """Raised when a hardlink would have to cross NTFS volumes."""


# ---------------------------------------------------------------------------
# journal
# ---------------------------------------------------------------------------

def history_file() -> Path:
    return Path(sorter.HISTORY_FILE)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_journal(record: dict[str, Any]) -> None:
    """Append one JSON line to history.jsonl and force it to disk."""
    with _JOURNAL_LOCK:
        target = history_file()
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:  # pragma: no cover - fsync unsupported on odd filesystems
                pass


def read_journal() -> list[dict[str, Any]]:
    target = history_file()
    records: list[dict[str, Any]] = []
    try:
        with target.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict):
                    records.append(rec)
    except OSError:
        pass
    return records


def _plan(op: str, src: Path, dest: Path, batch_id: str) -> str:
    op_id = uuid.uuid4().hex
    append_journal({
        "id": op_id,
        "action": "planned",
        "op": op,
        "batch_id": batch_id or "",
        "timestamp": _now(),
        "src": str(src),
        "dest": str(dest),
    })
    return op_id


def _abort(op_id: str, detail: str) -> None:
    append_journal({
        "id": uuid.uuid4().hex,
        "action": "aborted",
        "planned_id": op_id,
        "timestamp": _now(),
        "detail": detail,
    })


# ---------------------------------------------------------------------------
# pre-flight
# ---------------------------------------------------------------------------

def is_temp_save(name: str, extra_patterns: Iterable[str] = ()) -> bool:
    lowered = name.lower()
    return any(
        fnmatch.fnmatchcase(lowered, str(pattern).lower())
        for pattern in (*TEMP_SAVE_PATTERNS, *extra_patterns)
    )


def keep_marker_dir(path: Path) -> Path | None:
    """Return the nearest ancestor folder carrying a ``.download-sorter-keep`` marker."""
    try:
        resolved = Path(path).resolve()
    except OSError:
        return None
    start = resolved if resolved.is_dir() else resolved.parent
    for folder in (start, *start.parents):
        try:
            if (folder / KEEP_MARKER).exists():
                return folder
        except OSError:
            continue
    return None


def snapshot(path: Path) -> tuple[int, int] | None:
    try:
        st = Path(path).stat()
    except OSError:
        return None
    return st.st_size, st.st_mtime_ns


def preflight(
    src: Path,
    settle_seconds: float | None = None,
    honor_keep: bool = True,
    extra_patterns: Iterable[str] = (),
) -> str | None:
    """Return a refusal reason, or ``None`` when ``src`` is safe to touch."""
    src = Path(src)
    settle = DEFAULT_SETTLE_SECONDS if settle_seconds is None else max(0.0, float(settle_seconds))
    if not src.exists() or not src.is_file():
        return "missing"
    if is_temp_save(src.name, extra_patterns):
        return "temp-save"
    if sorter.is_cloud_placeholder(src):
        return "cloud-placeholder"
    if honor_keep and keep_marker_dir(src) is not None:
        return "keep-marker"
    try:
        age = time.time() - src.stat().st_mtime
    except OSError:
        return "missing"
    if settle > 0.0 and age < settle:
        return "not-settled"
    if sorter.is_file_locked(src):
        return "locked"
    return None


def can_open_exclusive(path: Path | str) -> bool:
    """Return True if the file can be opened exclusively (not locked by another process)."""
    return not sorter.is_file_locked(Path(path))


# ---------------------------------------------------------------------------
# volumes / attributes
# ---------------------------------------------------------------------------

def _nearest_existing(path: Path) -> Path:
    path = Path(path)
    for candidate in (path, *path.parents):
        if candidate.exists():
            return candidate
    return path


def same_volume(a: Path, b: Path) -> bool:
    try:
        return os.stat(_nearest_existing(a)).st_dev == os.stat(_nearest_existing(b)).st_dev
    except OSError:
        return False


def make_hidden(path: Path) -> None:
    """Set the hidden attribute on a *directory* (never on hardlinked files)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        FILE_ATTRIBUTE_HIDDEN = 0x2
        attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
        if attrs != 0xFFFFFFFF:
            ctypes.windll.kernel32.SetFileAttributesW(str(path), attrs | FILE_ATTRIBUTE_HIDDEN)
    except Exception:  # pragma: no cover - cosmetic only
        pass


# ---------------------------------------------------------------------------
# operations
# ---------------------------------------------------------------------------

def _resolve_dest(dst: Path) -> Path:
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        return sorter.unique_destination(dst.parent, dst.name)
    return dst


def _done_record(op_id: str, batch_id: str, src: str, dest: str, op: str, undoable: bool) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": uuid.uuid4().hex,
        "batch_id": batch_id or uuid.uuid4().hex[:8],
        "timestamp": _now(),
        "src": src,
        "dest": dest,
        "planned_id": op_id,
    }
    if op != "move":
        record["op"] = op
    if not undoable:
        record["action"] = "aux_move"
    return record


def move(
    src: Path | str,
    dst: Path | str,
    *,
    batch_id: str = "",
    settle_seconds: float | None = None,
    undoable: bool = True,
    honor_keep: bool = True,
    skip_preflight: bool = False,
    op: str = "move",
    extra_patterns: Iterable[str] = (),
) -> Path:
    """Journaled, never-overwriting move of one file.  Returns the final path.

    Raises :class:`Refused` when a pre-flight check declines (nothing touched) and
    lets ``OSError`` from the filesystem propagate (journal is marked aborted).
    """
    src = Path(src)
    if not skip_preflight:
        why = preflight(src, settle_seconds, honor_keep, extra_patterns)
        if why:
            raise Refused(why, src)
    before = snapshot(src)
    final = _resolve_dest(Path(dst))
    src_abs = str(src.resolve())
    dest_abs = str(final.resolve())
    op_id = _plan(op, Path(src_abs), Path(dest_abs), batch_id)

    if snapshot(src) != before:
        _abort(op_id, "changed-before-move")
        raise Refused("changed-during-check", src)
    last_exc: BaseException | None = None
    for attempt in range(4):
        try:
            # If a prior attempt copied the file to final and they match, finish unlinking src
            if final.exists() and src.exists() and sorter.are_files_identical(src, final):
                try:
                    os.unlink(src)
                    last_exc = None
                    break
                except OSError as unlink_exc:
                    last_exc = unlink_exc
                    if attempt < 3:
                        time.sleep(0.05 * (attempt + 1))
                    continue
            shutil.move(str(src), str(final))
            last_exc = None
            break
        except (PermissionError, OSError) as exc:
            last_exc = exc
            if attempt < 3:
                time.sleep(0.05 * (attempt + 1))
    if last_exc is not None:
        if src.exists() and not final.exists():
            _abort(op_id, f"{type(last_exc).__name__}: {last_exc}")
        raise last_exc
    append_journal(_done_record(op_id, batch_id, src_abs, dest_abs, op, undoable))
    return final


def link(src: Path | str, dst: Path | str, *, batch_id: str = "") -> Path:
    """Journaled NTFS hardlink (same volume only).  Never overwrites."""
    src = Path(src)
    if not src.exists() or not src.is_file():
        raise Refused("missing", src)
    final_probe = Path(dst)
    if not same_volume(src, final_probe.parent):
        raise CrossVolumeError(f"{src} and {final_probe.parent} are on different volumes")
    final = _resolve_dest(final_probe)
    src_abs = str(src.resolve())
    dest_abs = str(final.resolve())
    op_id = _plan("link", Path(src_abs), Path(dest_abs), batch_id)
    try:
        os.link(src_abs, str(final))
    except BaseException as exc:
        if not final.exists():
            _abort(op_id, f"{type(exc).__name__}: {exc}")
        raise
    record = _done_record(op_id, batch_id, src_abs, dest_abs, "link", False)
    record["action"] = "link"
    append_journal(record)
    return final


def quarantine(
    path: Path | str,
    quarantine_root: Path | str,
    *,
    batch_id: str = "",
    rel_root: Path | str | None = None,
    settle_seconds: float = 0.0,
    honor_keep: bool = False,
) -> Path:
    """Move ``path`` into ``quarantine_root`` preserving its path relative to ``rel_root``."""
    path = Path(path)
    rel: Path
    if rel_root is not None:
        try:
            rel = path.resolve().relative_to(Path(rel_root).resolve())
        except ValueError:
            rel = Path(path.name)
    else:
        rel = Path(path.name)
    return move(
        path,
        Path(quarantine_root) / rel,
        batch_id=batch_id,
        settle_seconds=settle_seconds,
        honor_keep=honor_keep,
        op="quarantine",
    )


def restore(entry: dict[str, Any], *, settle_seconds: float = 0.0) -> Path | None:
    """Undo one recorded move: bring ``entry['dest']`` back to ``entry['src']``.

    Never overwrites: an occupied original location yields a ``name (2).ext`` copy.
    Returns the restored path, or ``None`` when the file is gone/refused.
    """
    dest_path = Path(entry["dest"])
    src_path = Path(entry["src"])
    if not dest_path.exists():
        return None
    if dest_path.is_dir() and src_path.suffix.lower() in ARCHIVE_SUFFIXES:
        # Pre-2.0.2 extraction record ("archive.zip -> extracted folder").  Moving the
        # folder back would leave a directory named like the zip; the zip itself is in
        # Archives.  Refuse instead of producing that.
        raise Refused("extraction-record", dest_path)
    if dest_path.is_file():
        why = preflight(dest_path, settle_seconds, honor_keep=False)
        if why:
            raise Refused(why, dest_path)
    target_dir = src_path.parent
    target_dir.mkdir(parents=True, exist_ok=True)
    final = sorter.unique_destination(target_dir, src_path.name)
    op_id = _plan("restore", dest_path, final, str(entry.get("batch_id", "")))
    try:
        shutil.move(str(dest_path), str(final))
    except BaseException as exc:
        if dest_path.exists() and not final.exists():
            _abort(op_id, f"{type(exc).__name__}: {exc}")
        raise
    try:
        sorter.remember_restored(final)
    except Exception as exc:  # pragma: no cover - bookkeeping only
        logging.debug("Could not mark %s as restored: %s", final, exc)
    append_journal({
        "id": uuid.uuid4().hex,
        "action": "undo",
        "ref_id": entry.get("id", ""),
        "planned_id": op_id,
        "timestamp": _now(),
        "restored_from": str(dest_path),
        "restored_to": str(final),
    })
    return final


# ---------------------------------------------------------------------------
# crash recovery
# ---------------------------------------------------------------------------

def pending_planned(min_age_seconds: float = 30.0) -> list[dict[str, Any]]:
    """``planned`` records that never received a done/aborted/undo companion."""
    records = read_journal()
    finished: set[str] = set()
    for rec in records:
        planned_id = rec.get("planned_id")
        if planned_id:
            finished.add(str(planned_id))
    now = time.time()
    pending: list[dict[str, Any]] = []
    for rec in records:
        if rec.get("action") != "planned" or str(rec.get("id")) in finished:
            continue
        try:
            age = now - datetime.fromisoformat(str(rec.get("timestamp"))).timestamp()
        except (TypeError, ValueError):
            age = float("inf")
        if age >= min_age_seconds:
            pending.append(rec)
    return pending


def recover_pending_journal(min_age_seconds: float = 30.0) -> list[dict[str, Any]]:
    """Reconcile orphaned ``planned`` records with the real disk state.

    * source present, destination absent  -> the operation never happened: mark aborted
    * destination present, source absent  -> it did complete: write the missing ``done``
    * both present                        -> ambiguous (interrupted cross-volume copy):
      nothing is deleted; flagged ``needs_review``
    * neither present                     -> marked aborted (nothing left to protect)
    """
    outcomes: list[dict[str, Any]] = []
    for rec in pending_planned(min_age_seconds):
        op = str(rec.get("op", "move"))
        src = Path(str(rec.get("src", "")))
        dest = Path(str(rec.get("dest", "")))
        op_id = str(rec["id"])
        src_there, dest_there = src.exists(), dest.exists()
        if dest_there and not src_there:
            if op == "restore":
                append_journal({
                    "id": uuid.uuid4().hex, "action": "aborted", "planned_id": op_id,
                    "timestamp": _now(), "detail": "restore completed before crash",
                })
                outcome = "completed"
            elif op == "link":
                record = _done_record(op_id, str(rec.get("batch_id", "")), str(src), str(dest), "link", False)
                record["action"] = "link"
                append_journal(record)
                outcome = "completed"
            else:
                append_journal(_done_record(op_id, str(rec.get("batch_id", "")), str(src), str(dest), op, True))
                outcome = "completed"
        elif dest_there and src_there and op != "link":
            append_journal({
                "id": uuid.uuid4().hex, "action": "needs_review", "planned_id": op_id,
                "timestamp": _now(), "detail": "source and destination both exist",
            })
            outcome = "conflict"
        elif dest_there and src_there and op == "link":
            record = _done_record(op_id, str(rec.get("batch_id", "")), str(src), str(dest), "link", False)
            record["action"] = "link"
            append_journal(record)
            outcome = "completed"
        else:
            _abort(op_id, "recovered: operation never happened")
            outcome = "aborted"
        logging.info("Journal recovery: %s %s -> %s (%s)", op, src, dest, outcome)
        outcomes.append({"planned_id": op_id, "op": op, "src": str(src), "dest": str(dest), "outcome": outcome})
    return outcomes
