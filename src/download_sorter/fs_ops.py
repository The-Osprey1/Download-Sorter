"""Safe atomic file moves, duplicate isolation, lock checks, and SQLite-backed undo."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import datetime
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import struct
import sys
import time
from typing import Any
import uuid

from download_sorter.db import StateDB
from download_sorter.dest import is_under_watch_root
from download_sorter.router import RouteDecision

ProgressCallback = Callable[[int, int, str], None]

_CHUNK_BYTES = 1024 * 1024

# Tiered duplicate check: files above the threshold are compared by a head + tail
# sample first, so two different multi-gigabyte files of equal size are told apart
# without reading either one in full.
_SAMPLE_BYTES = 64 * 1024
_SAMPLE_THRESHOLD_BYTES = 256 * 1024

# Cloud-sync placeholders (OneDrive Files On-Demand, iCloud): reading their content
# makes Windows download the whole file.
FILE_ATTRIBUTE_OFFLINE = 0x00001000
FILE_ATTRIBUTE_PINNED = 0x00080000
FILE_ATTRIBUTE_UNPINNED = 0x00100000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x00400000
_CLOUD_PLACEHOLDER_MASK = (
    FILE_ATTRIBUTE_OFFLINE
    | FILE_ATTRIBUTE_UNPINNED
    | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
)
STAGING_DIR_NAME = ".staging"
SPARSE_DEDUP_MIN_BYTES = 1024

# Slicers read a .3mf into memory and close the file, so a lock check cannot tell
# that the user is still working on it. A running slicer is the only signal.
SLICER_PROCESS_NAMES: frozenset[str] = frozenset(
    {
        "bambu-studio.exe",
        "bambustudio.exe",
        "orca-slicer.exe",
        "orcaslicer.exe",
        "prusaslicer.exe",
        "cura.exe",
    }
)
_TH32CS_SNAPPROCESS = 0x00000002

# ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION: the file is open in another program.
_LOCK_WINERRORS = (32, 33)


def is_file_locked(path: Path) -> bool:
    """Return True if another process holds an incompatible lock on ``path``."""
    if not path.exists() or not path.is_file():
        return False
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        # Explicit prototypes: without them ctypes returns the handle as a 32-bit
        # int, which never equals INVALID_HANDLE_VALUE on 64-bit Windows, so a
        # sharing violation went unnoticed and every file looked unlocked.
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        ctypes.set_last_error(0)
        handle = kernel32.CreateFileW(
            str(path),
            0x80000000,  # GENERIC_READ
            0,           # exclusive share check (non-destructive)
            None,
            3,           # OPEN_EXISTING
            0x80,        # FILE_ATTRIBUTE_NORMAL
            None,
        )
        if handle is None or handle == wintypes.HANDLE(-1).value:
            return ctypes.get_last_error() in _LOCK_WINERRORS
        kernel32.CloseHandle(handle)
        return False
    return False


def is_file_settled(path: Path, settle_seconds: float, now: float | None = None) -> bool:
    """Return True once ``path`` has been untouched for at least ``settle_seconds``."""
    if settle_seconds <= 0:
        return True
    try:
        st = path.stat()
    except OSError:
        return False
    cur = time.time() if now is None else float(now)
    newest_ts = max(st.st_mtime, st.st_ctime)
    return (cur - newest_ts) >= float(settle_seconds)


def is_cloud_placeholder(path: Path) -> bool:
    """Return True when ``path`` is a cloud-only file whose content is not on disk.

    Only the attributes are read (which never triggers a download).
    """
    try:
        attrs = int(getattr(os.stat(path), "st_file_attributes", 0) or 0)
    except OSError:
        return False
    if attrs & FILE_ATTRIBUTE_PINNED:
        return False
    return bool(attrs & _CLOUD_PLACEHOLDER_MASK)


def _iter_process_names() -> Iterator[str]:
    """Yield the lower-cased image name of every running process (Win32 snapshot).

    One ``CreateToolhelp32Snapshot`` call, well under a millisecond; no subprocess.
    """
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    # A private handle to kernel32, so the prototypes set here do not change how
    # other modules call the shared ``ctypes.windll.kernel32`` functions.
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if snapshot is None or snapshot == wintypes.HANDLE(-1).value:
        return
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            yield entry.szExeFile.lower()
            more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)


def is_slicer_running(process_names: set[str] | None = None) -> bool:
    """Return True while a 3D slicer (Bambu Studio, OrcaSlicer, PrusaSlicer, Cura) is open."""
    wanted = (
        SLICER_PROCESS_NAMES
        if process_names is None
        else {str(name).lower() for name in process_names}
    )
    if not wanted:
        return False
    try:
        return any(name in wanted for name in _iter_process_names())
    except Exception as exc:  # never let a process scan break sorting
        logging.debug("Slicer process scan failed: %s", exc)
        return False


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(_CHUNK_BYTES):
            h.update(chunk)
    return h.hexdigest()


def compute_sparse_hash(path: Path, size: int | None = None) -> str:
    """Size-gated 3-point SHA-256 (head / mid / tail 64 KB + size)."""
    sz = path.stat().st_size if size is None else int(size)
    h = hashlib.sha256()
    h.update(struct.pack("<Q", sz))
    with open(path, "rb") as fh:
        if sz <= 3 * _SAMPLE_BYTES:
            while chunk := fh.read(_SAMPLE_BYTES):
                h.update(chunk)
        else:
            # Head 64 KB
            h.update(fh.read(_SAMPLE_BYTES))
            # Mid 64 KB
            fh.seek((sz - _SAMPLE_BYTES) // 2)
            h.update(fh.read(_SAMPLE_BYTES))
            # Tail 64 KB
            fh.seek(sz - _SAMPLE_BYTES)
            h.update(fh.read(_SAMPLE_BYTES))
    return h.hexdigest()


def _sample_sha256(path: Path, size: int) -> str:
    """SHA-256 of the first and last 64 KB of ``path``."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        h.update(fh.read(_SAMPLE_BYTES))
        fh.seek(max(0, size - _SAMPLE_BYTES))
        h.update(fh.read(_SAMPLE_BYTES))
    return h.hexdigest()


def _hide_dir_win32(path: Path) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        FILE_ATTRIBUTE_HIDDEN = 0x2
        attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
        if attrs != 0xFFFFFFFF:
            ctypes.windll.kernel32.SetFileAttributesW(str(path), attrs | FILE_ATTRIBUTE_HIDDEN)
    except Exception:
        pass


def staging_dir_for(base_dir: Path, batch_id: str) -> Path:
    """Create and return ``<base_dir>\\.staging\\<batch_id>``."""
    staging_root = Path(base_dir) / STAGING_DIR_NAME
    staging_root.mkdir(parents=True, exist_ok=True)
    _hide_dir_win32(staging_root)
    batch_dir = staging_root / (batch_id or uuid.uuid4().hex[:8])
    batch_dir.mkdir(parents=True, exist_ok=True)
    return batch_dir


def cleanup_orphaned_staging(watch_root: Path, max_age_seconds: float = 300.0) -> list[Path]:
    """Remove abandoned ``.staging\\<batch_id>`` directories older than ``max_age_seconds``."""
    root = Path(watch_root) / STAGING_DIR_NAME
    if not root.is_dir():
        return []
    now = time.time()
    removed: list[Path] = []
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        try:
            age = now - entry.stat().st_mtime
        except OSError:
            continue
        if age >= max_age_seconds:
            try:
                shutil.rmtree(entry)
                removed.append(entry)
            except OSError:
                pass
    return removed


def find_renamed_duplicate(
    src: Path,
    *,
    db: StateDB,
    watch_root: Path,
    min_bytes: int = SPARSE_DEDUP_MIN_BYTES,
) -> Path | None:
    """Find an identical file under ``watch_root`` even if renamed, via size -> 3-point sparse -> full SHA-256."""
    if is_cloud_placeholder(src):
        return None
    try:
        st = src.stat()
    except OSError:
        return None
    if st.st_size < min_bytes:
        return None

    candidates = db.find_hash_candidates(st.st_size, exclude_path=src)
    if not candidates:
        return None

    src_sparse = compute_sparse_hash(src, size=st.st_size)
    sparse_matches = [c for c in candidates if c["sparse_hash"] == src_sparse]
    if not sparse_matches:
        return None

    src_full: str | None = None
    root_resolved = watch_root.resolve()
    for row in sparse_matches:
        other = Path(str(row["path"]))
        if not other.is_file() or not is_under_watch_root(other, root_resolved):
            db.remove_file_hash(other)
            continue
        if is_cloud_placeholder(other):
            continue
        try:
            other_st = other.stat()
        except OSError:
            continue
        if other_st.st_size != st.st_size:
            db.remove_file_hash(other)
            continue
        if src_full is None:
            src_full = _sha256(src)
        other_full = row.get("full_hash")
        if not other_full or other_st.st_mtime_ns != row["mtime_ns"]:
            other_full = _sha256(other)
            db.record_file_hash(
                other,
                size_bytes=other_st.st_size,
                mtime_ns=other_st.st_mtime_ns,
                sparse_hash=str(row["sparse_hash"]),
                full_hash=other_full,
            )
        if src_full == other_full:
            return other
    return None


def _files_identical(a: Path, b: Path) -> bool:
    """Size check, then a head/tail sample for large files, then full SHA-256.

    A cloud-only placeholder is never read: it is reported as "not identical", which
    only means the incoming file gets a numbered name instead of being quarantined.
    """
    try:
        size = a.stat().st_size
        if size != b.stat().st_size:
            return False
        if is_cloud_placeholder(a) or is_cloud_placeholder(b):
            return False
        if size > _SAMPLE_THRESHOLD_BYTES and _sample_sha256(a, size) != _sample_sha256(b, size):
            return False
        return _sha256(a) == _sha256(b)
    except OSError:
        return False


def files_are_identical(a: Path, b: Path) -> bool:
    """Exact content comparison (tiered: size -> head/tail sample -> full SHA-256)."""
    return _files_identical(a, b)


def _next_available_name(target: Path) -> Path:
    if not target.exists():
        return target
    parent = target.parent
    stem = target.stem
    suffix = target.suffix
    counter = 2
    while True:
        cand = parent / f"{stem} ({counter}){suffix}"
        if not cand.exists():
            return cand
        counter += 1


def _restored_name(target: Path) -> Path:
    """A free ``name (Restored).ext`` / ``name (Restored 2).ext`` next to ``target``."""
    parent = target.parent
    stem = target.stem
    suffix = target.suffix
    cand = parent / f"{stem} (Restored){suffix}"
    counter = 2
    while os.path.lexists(cand):
        cand = parent / f"{stem} (Restored {counter}){suffix}"
        counter += 1
    return cand


_BROWSER_COPY_RE = re.compile(r"^(?P<base>.+?)\s*\((?P<num>\d+)\)$")


def strip_browser_copy_suffix(filename: str) -> str:
    """Normalize browser-numbered duplicate names like ``report (1).pdf`` to ``report.pdf``."""
    p = Path(filename)
    stem = p.stem
    while True:
        match = _BROWSER_COPY_RE.match(stem)
        if not match:
            break
        candidate = match.group("base").rstrip()
        if not candidate:
            break
        stem = candidate
    return f"{stem}{p.suffix}" if stem else p.name


def find_existing_counterpart(src: Path, planned_dest: Path) -> Path | None:
    """Return an existing file in ``planned_dest.parent`` matching ``src``, if any.

    Matches both exact byte-identical files at ``planned_dest`` and browser-numbered
    copies (e.g. ``report (1).pdf``) whose canonical name (``report.pdf``) already
    exists in ``planned_dest.parent``.
    """
    if planned_dest.exists() and planned_dest.is_file() and files_are_identical(src, planned_dest):
        return planned_dest

    canonical_name = strip_browser_copy_suffix(planned_dest.name)
    if canonical_name.lower() != planned_dest.name.lower():
        canonical_dest = planned_dest.parent / canonical_name
        if (
            canonical_dest.exists()
            and canonical_dest.is_file()
            and not is_cloud_placeholder(canonical_dest)
            and not is_cloud_placeholder(src)
        ):
            return canonical_dest
    return None


def resolve_destination(src: Path, planned_dest: Path, watch_root: Path) -> tuple[Path, bool]:
    """Never overwrite an existing destination.

    If an exact byte-identical file or a browser-numbered copy (e.g. ``report (1).pdf``)
    matching an existing file in the destination already sits at ``planned_dest``,
    quarantine the incoming duplicate to ``watch_root/_Duplicates/<YYYY-MM>/<name>``.
    Otherwise add `` (2)`` / `` (3)`` numbering.
    """
    counterpart = find_existing_counterpart(src, planned_dest)
    if counterpart is not None:
        month_dir = datetime.now().strftime("%Y-%m")
        dup_target = watch_root / "_Duplicates" / month_dir / src.name
        return _next_available_name(dup_target), True
    if not planned_dest.exists():
        return planned_dest, False
    return _next_available_name(planned_dest), False


def _move_with_retries(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    last_err: OSError | None = None
    for attempt in range(4):
        try:
            try:
                os.replace(src, dest)
            except OSError as replace_err:
                # Cross-volume move stages into .staging\<batch_id>\ on destination volume first.
                if getattr(replace_err, "winerror", None) == 17 or getattr(replace_err, "errno", None) == 18:
                    stage_dir = staging_dir_for(dest.parent, "xvol")
                    staged_file = stage_dir / dest.name
                    try:
                        shutil.copy2(str(src), str(staged_file))
                        os.replace(staged_file, dest)
                        os.unlink(src)
                    except BaseException:
                        try:
                            if staged_file.exists():
                                staged_file.unlink()
                        except OSError:
                            pass
                        raise
                    finally:
                        try:
                            if stage_dir.exists() and not any(stage_dir.iterdir()):
                                stage_dir.rmdir()
                        except OSError:
                            pass
                else:
                    raise
            return
        except OSError as exc:
            last_err = exc
            if attempt < 3:
                time.sleep(0.05 * (attempt + 1))
    assert last_err is not None
    raise last_err


def sync_slicer_recent_projects(old_path: Path, new_path: Path) -> int:
    """Update Bambu Studio / OrcaSlicer ``recent_projects`` when a ``.3mf`` is moved."""
    if sys.platform != "win32":
        return 0
    appdata = os.environ.get("APPDATA", "")
    if not appdata:
        return 0
    old_norm = str(old_path.resolve()).lower()
    new_str = str(new_path.resolve())
    updated = 0
    for app_name in ("BambuStudio", "OrcaSlicer"):
        conf_path = Path(appdata) / app_name / f"{app_name}.conf"
        if not conf_path.is_file():
            continue
        try:
            data = json.loads(conf_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        recent = data.get("recent_projects")
        if not isinstance(recent, dict):
            continue
        changed = False
        for key, val in list(recent.items()):
            if isinstance(val, str) and val.strip() and str(Path(val)).lower() == old_norm:
                recent[key] = new_str
                changed = True
                updated += 1
        if changed:
            try:
                tmp = conf_path.with_suffix(".conf.tmp")
                tmp.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")
                os.replace(tmp, conf_path)
            except OSError:
                pass
    return updated


def execute_move(
    src: Path,
    decision: RouteDecision,
    *,
    batch_id: str,
    db: StateDB,
    watch_root: Path,
    settle_seconds: float = 0.0,
    source_url: str = "",
) -> dict[str, Any] | None:
    """Safely move one settled, unlocked file according to ``decision``.

    The move is journaled in two phases: a PENDING row is written before the file
    is touched and becomes COMPLETED once the file is in place, so a crash in
    between can always be reconciled on the next start.
    """
    if decision.action != "move" or decision.relative_dest is None:
        return None
    if not src.exists() or not src.is_file():
        return None
    if is_file_locked(src) or not is_file_settled(src, settle_seconds):
        return None

    size_bytes = src.stat().st_size
    src_resolved = src.resolve()
    planned_dest = (watch_root / decision.relative_dest).resolve()
    if not is_under_watch_root(planned_dest, watch_root):
        # Last line of defence: settings are cleaned when they load (dest.py), so
        # this only trips on a hand-made decision. Never move a file out of the folder.
        logging.warning("Not moving %s: %s is outside the watched folder", src.name, planned_dest)
        return None
    existing_counterpart = find_existing_counterpart(src, planned_dest)
    final_dest, is_dup = resolve_destination(src, planned_dest, watch_root)
    if not is_dup:
        renamed_dup = find_renamed_duplicate(src, db=db, watch_root=watch_root)
        if renamed_dup is not None:
            month_dir = datetime.now().strftime("%Y-%m")
            dup_target = watch_root / "_Duplicates" / month_dir / src.name
            final_dest = _next_available_name(dup_target)
            is_dup = True
            existing_counterpart = renamed_dup
    if final_dest == src_resolved:
        return None
    final_dest = final_dest.parent.resolve() / final_dest.name

    reason = f"Duplicate -> _Duplicates ({decision.reason})" if is_dup else decision.reason
    move_id = db.record_move_intent(
        batch_id=batch_id,
        filename=src.name,
        src_path=src_resolved,
        dest_path=final_dest,
        reason=reason,
        matched_rule=decision.matched_rule,
        match_type="duplicate" if is_dup else decision.match_type,
        source_url=source_url,
        size_bytes=size_bytes,
        is_duplicate=is_dup,
    )
    try:
        _move_with_retries(src, final_dest)
    except BaseException:
        # Roll back only when the file is provably still at its source; otherwise
        # leave the row PENDING so the next start reconciles it from the disk state.
        if os.path.lexists(src_resolved) and not os.path.lexists(final_dest):
            db.rollback_move_intent(move_id)
        raise
    db.complete_move_intent(move_id, final_dest)
    if not is_dup and size_bytes >= SPARSE_DEDUP_MIN_BYTES and not is_cloud_placeholder(final_dest):
        try:
            st_final = final_dest.stat()
            db.record_file_hash(
                final_dest,
                size_bytes=st_final.st_size,
                mtime_ns=st_final.st_mtime_ns,
                sparse_hash=compute_sparse_hash(final_dest, size=st_final.st_size),
            )
        except OSError:
            pass
    if final_dest.suffix.lower() == ".3mf":
        sync_slicer_recent_projects(src_resolved, final_dest)
    return {
        "id": move_id,
        "batch_id": batch_id,
        "filename": src.name,
        "src_path": str(src_resolved),
        "dest_path": str(final_dest),
        "existing_path": str(existing_counterpart.resolve()) if (is_dup and existing_counterpart is not None) else "",
        "reason": reason,
        "matched_rule": decision.matched_rule,
        "is_duplicate": is_dup,
    }


_RESTORED = "restored"
_LOCKED = "locked"
_MISSING = "missing"


def _restore_one(record: dict[str, Any]) -> tuple[str, Path | None]:
    """Put one sorted file back. Returns ``(outcome, where_it_went)``.

    Never overwrites: when the original path is occupied by another file, the
    restored one lands beside it as ``name (Restored).ext``.
    """
    src = Path(record["src_path"])
    dest = Path(record["dest_path"])
    if not dest.exists() or not dest.is_file():
        return _MISSING, None
    if is_file_locked(dest):
        return _LOCKED, None
    target = _restored_name(src) if os.path.lexists(src) else src
    _move_with_retries(dest, target)
    return _RESTORED, target


def _invalidate_recent(paths: list[str]) -> None:
    if not paths:
        return
    try:
        from download_sorter.quick_switcher import invalidate_recent_file

        for p in paths:
            invalidate_recent_file(p)
    except Exception:
        pass


def _undo_records(
    db: StateDB,
    records: list[dict[str, Any]],
    *,
    on_progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    restored_ids: list[int] = []
    orphaned_ids: list[int] = []
    stale_paths: list[str] = []
    locked_files: list[str] = []
    renamed: list[dict[str, str]] = []
    failed = 0
    total = len(records)
    for index, rec in enumerate(records, start=1):
        name = str(rec.get("filename") or Path(str(rec.get("dest_path") or "")).name)
        if on_progress is not None:
            on_progress(index - 1, total, name)
        try:
            outcome, target = _restore_one(rec)
        except OSError as exc:
            if getattr(exc, "winerror", None) in _LOCK_WINERRORS:
                # Opened by another program between the lock check and the move.
                outcome, target = _LOCKED, None
            else:
                logging.warning("Undo could not restore %s: %s", rec.get("dest_path"), exc)
                failed += 1
                continue
        if outcome == _RESTORED:
            restored_ids.append(int(rec["id"]))
            stale_paths.append(str(rec["dest_path"]))
            if target is not None and str(target) != str(rec["src_path"]):
                renamed.append({"original": str(rec["src_path"]), "restored_as": str(target)})
        elif outcome == _LOCKED:
            locked_files.append(str(rec["dest_path"]))
        else:
            # The sorted file was deleted or moved by the user: nothing to put back.
            orphaned_ids.append(int(rec["id"]))
            stale_paths.append(str(rec["dest_path"]))
    db.mark_undone(restored_ids)
    db.mark_orphaned(orphaned_ids)
    _invalidate_recent(stale_paths)
    if on_progress is not None and total > 0:
        on_progress(total, total, "Complete")
    return {
        "restored": len(restored_ids),
        "skipped": len(locked_files) + len(orphaned_ids) + failed,
        "skipped_locked": len(locked_files),
        "locked_files": locked_files,
        "orphaned": len(orphaned_ids),
        "restored_renamed": renamed,
    }


def _empty_undo_result() -> dict[str, Any]:
    return {
        "restored": 0,
        "skipped": 0,
        "skipped_locked": 0,
        "locked_files": [],
        "orphaned": 0,
        "restored_renamed": [],
        "batch_id": "",
    }


def undo_move(
    db: StateDB,
    move_id: int,
    *,
    on_progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Undo a single recorded move by ID."""
    rec = db.get_move(move_id)
    if (
        not rec
        or rec.get("undone_at") is not None
        or str(rec.get("status") or "COMPLETED") != "COMPLETED"
    ):
        return _empty_undo_result()
    res = _undo_records(db, [rec], on_progress=on_progress)
    res["batch_id"] = rec["batch_id"]
    return res


def undo_batch(
    db: StateDB,
    batch_id: str | None = None,
    *,
    on_progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Undo every active move in ``batch_id`` (or the latest active batch).

    Files that are open in another program are skipped and stay undoable; files
    the user already deleted are retired from history; nothing is overwritten.
    """
    moves = db.get_batch_moves(batch_id)
    if not moves:
        return _empty_undo_result()
    res = _undo_records(db, moves, on_progress=on_progress)
    res["batch_id"] = str(moves[0]["batch_id"])
    return res


def _send_to_recycle_bin_win32(paths: list[str]) -> bool:
    """Move files to the Windows Recycle Bin using native Shell ``SHFileOperationW``."""
    if sys.platform != "win32" or not paths:
        return False
    import ctypes
    from ctypes import wintypes

    class _SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", wintypes.WORD),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", wintypes.LPVOID),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    fo_delete = 0x0003
    fof_silent = 0x0004
    fof_noconfirmation = 0x0010
    fof_allowundo = 0x0040
    fof_noerrorui = 0x0400

    op = _SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = fo_delete
    op.pFrom = "\0".join(paths) + "\0\0"
    op.pTo = None
    op.fFlags = fof_allowundo | fof_noconfirmation | fof_noerrorui | fof_silent

    shell32 = getattr(ctypes, "windll", None)
    if shell32 is None:
        return False
    result = int(shell32.shell32.SHFileOperationW(ctypes.byref(op)))
    return result == 0 and not bool(op.fAnyOperationsAborted)


def _cleanup_empty_dup_dirs(dup_root: Path) -> None:
    """Remove empty monthly subfolders and ``_Duplicates`` if no quarantined files remain."""
    if not dup_root.is_dir():
        return
    try:
        for child in sorted(dup_root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if child.is_dir():
                try:
                    next(child.iterdir())
                except StopIteration:
                    child.rmdir()
        try:
            next(dup_root.iterdir())
        except StopIteration:
            dup_root.rmdir()
    except OSError:
        pass


def _index_watch_files(root: Path) -> dict[str, list[Path]]:
    """Map lower-cased canonical filename -> sorted candidate paths under ``root``."""
    by_canonical: dict[str, list[Path]] = {}
    for cur_dir, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if not d.startswith(".") and d.lower() not in ("_duplicates", "_extracted")
        ]
        base = Path(cur_dir)
        for fname in filenames:
            if fname.startswith("."):
                continue
            cand = base / fname
            key = strip_browser_copy_suffix(fname).lower()
            by_canonical.setdefault(key, []).append(cand)
    return by_canonical


def _best_existing_counterpart(
    dup_file: Path,
    root: Path,
    by_canonical: dict[str, list[Path]],
) -> Path | None:
    """Select the best matching existing file inside ``root`` for quarantined ``dup_file``."""
    canonical_lower = strip_browser_copy_suffix(dup_file.name).lower()
    exact_lower = dup_file.name.lower()
    candidates = by_canonical.get(canonical_lower, [])
    if not candidates:
        return None

    best_path: Path | None = None
    best_score: tuple[int, int, int, float] = (-1, -1, -1, 0.0)
    for cand in candidates:
        if cand == dup_file or not cand.is_file():
            continue
        try:
            rel_parts = cand.relative_to(root).parts
            in_subfolder = 1 if len(rel_parts) > 1 else 0
            st = cand.stat()
            mtime = float(st.st_mtime)
        except (OSError, ValueError):
            continue
        identical = 1 if (not is_cloud_placeholder(cand) and files_are_identical(dup_file, cand)) else 0
        # Exact canonical name matches (e.g. report.pdf for report (1).pdf) outrank numbered copies
        canonical_exact = 1 if cand.name.lower() == canonical_lower else (1 if cand.name.lower() == exact_lower else 0)
        score = (identical, in_subfolder, canonical_exact, mtime)
        if score > best_score:
            best_score = score
            best_path = cand
    return best_path


def scan_duplicate_files(watch_root: Path, *, db: StateDB | None = None) -> list[dict[str, Any]]:
    """Return quarantined files inside ``watch_root / '_Duplicates'``, newest first."""
    _ = db
    root = watch_root.resolve()
    dup_root = root / "_Duplicates"
    if not dup_root.is_dir():
        return []
    by_canonical = _index_watch_files(root)
    entries: list[dict[str, Any]] = []
    for fpath in dup_root.rglob("*"):
        if not fpath.is_file():
            continue
        try:
            st = fpath.stat()
            rel = str(fpath.relative_to(root))
            rel_parent = str(fpath.parent.relative_to(root))
        except (OSError, ValueError):
            continue
        existing = _best_existing_counterpart(fpath, root, by_canonical)
        existing_rel = ""
        if existing is not None:
            try:
                existing_rel = str(existing.relative_to(root))
            except ValueError:
                existing_rel = str(existing)
        entries.append(
            {
                "path": str(fpath),
                "name": fpath.name,
                "rel_path": rel,
                "subfolder": rel_parent,
                "existing_path": str(existing) if existing is not None else "",
                "existing_rel_path": existing_rel,
                "size_bytes": int(st.st_size),
                "mtime": float(st.st_mtime),
            }
        )
    entries.sort(key=lambda item: (-float(item["mtime"]), str(item["name"]).lower()))
    return entries


def resolve_duplicate_replace_old(
    dup_path: Path,
    existing_path: Path,
    watch_root: Path,
    *,
    db: StateDB | None = None,
    use_recycle_bin: bool = True,
) -> dict[str, Any]:
    """Replace an existing file under ``watch_root`` with a quarantined duplicate copy."""
    root = watch_root.resolve()
    dup_root = (root / "_Duplicates").resolve()
    dup = Path(dup_path).resolve()
    existing = Path(existing_path).resolve()

    if not dup.is_file() or not is_under_watch_root(dup, dup_root):
        return {"ok": False, "action": "replace_old", "error": "Duplicate file is no longer in _Duplicates."}
    if (
        not existing.is_file()
        or not is_under_watch_root(existing, root)
        or is_under_watch_root(existing, dup_root)
        or dup == existing
    ):
        return {"ok": False, "action": "replace_old", "error": "Original file could not be safely located inside Downloads."}
    if is_file_locked(dup) or is_file_locked(existing):
        return {"ok": False, "action": "replace_old", "error": "One of the files is currently open in another program."}

    del_res = delete_files_safely(
        [existing],
        watch_root=root,
        confine_dir=root,
        use_recycle_bin=use_recycle_bin,
    )
    if existing.exists() or not del_res.get("deleted"):
        return {
            "ok": False,
            "action": "replace_old",
            "error": "Could not recycle the older copy before replacing it.",
            "delete_result": del_res,
        }

    existing.parent.mkdir(parents=True, exist_ok=True)
    _move_with_retries(dup, existing)
    if db is not None:
        db.mark_orphaned_by_dest([str(dup)])
    _invalidate_recent([str(dup)])
    _cleanup_empty_dup_dirs(dup_root)
    return {
        "ok": True,
        "action": "replace_old",
        "dup_path": str(dup),
        "existing_path": str(existing),
        "final_path": str(existing),
    }


def resolve_duplicate_delete_new_locate_old(
    dup_path: Path,
    existing_path: Path,
    watch_root: Path,
    *,
    db: StateDB | None = None,
    use_recycle_bin: bool = True,
) -> dict[str, Any]:
    """Send the quarantined duplicate to Recycle Bin and return the verified old file path."""
    root = watch_root.resolve()
    dup_root = (root / "_Duplicates").resolve()
    dup = Path(dup_path).resolve()
    existing = Path(existing_path).resolve() if str(existing_path or "").strip() else None

    if not dup.is_file() or not is_under_watch_root(dup, dup_root):
        return {"ok": False, "action": "delete_locate_old", "error": "Duplicate file is no longer in _Duplicates."}
    if is_file_locked(dup):
        return {"ok": False, "action": "delete_locate_old", "error": "The duplicate file is open in another program."}

    del_res = delete_files_safely(
        [dup],
        watch_root=root,
        confine_dir=dup_root,
        use_recycle_bin=use_recycle_bin,
    )
    if not del_res.get("deleted"):
        return {
            "ok": False,
            "action": "delete_locate_old",
            "error": "Could not delete the duplicate file.",
            "delete_result": del_res,
        }
    if db is not None:
        db.mark_orphaned_by_dest([str(dup)])
    _invalidate_recent([str(dup)])
    _cleanup_empty_dup_dirs(dup_root)

    valid_existing = (
        existing is not None
        and existing.exists()
        and is_under_watch_root(existing, root)
        and not is_under_watch_root(existing, dup_root)
    )
    return {
        "ok": True,
        "action": "delete_locate_old",
        "dup_path": str(dup),
        "existing_path": str(existing) if valid_existing and existing is not None else "",
        "existing_exists": bool(valid_existing),
    }


def resolve_duplicate_keep_both(
    dup_path: Path,
    existing_path: Path,
    watch_root: Path,
    *,
    db: StateDB | None = None,
) -> dict[str, Any]:
    """Move a quarantined duplicate beside the original using numbered naming (e.g. `` (2)``)."""
    root = watch_root.resolve()
    dup_root = (root / "_Duplicates").resolve()
    dup = Path(dup_path).resolve()
    existing = Path(existing_path).resolve() if str(existing_path or "").strip() else None

    if not dup.is_file() or not is_under_watch_root(dup, dup_root):
        return {"ok": False, "action": "keep_both", "error": "Duplicate file is no longer in _Duplicates."}
    if is_file_locked(dup):
        return {"ok": False, "action": "keep_both", "error": "The duplicate file is open in another program."}

    if existing is not None and is_under_watch_root(existing, root) and not is_under_watch_root(existing, dup_root):
        dest_dir = existing.parent
        canonical_base = strip_browser_copy_suffix(existing.name)
    else:
        dest_dir = root
        canonical_base = strip_browser_copy_suffix(dup.name)

    dest_dir.mkdir(parents=True, exist_ok=True)
    target_dest = _next_available_name(dest_dir / canonical_base)
    _move_with_retries(dup, target_dest)
    if db is not None:
        db.mark_orphaned_by_dest([str(dup)])
    _invalidate_recent([str(dup)])
    _cleanup_empty_dup_dirs(dup_root)
    return {
        "ok": True,
        "action": "keep_both",
        "dup_path": str(dup),
        "existing_path": str(existing) if existing is not None else "",
        "final_path": str(target_dest),
    }


def delete_files_safely(
    paths: list[Path | str],
    *,
    watch_root: Path,
    confine_dir: Path | None = None,
    use_recycle_bin: bool = True,
    on_progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Safely send unlocked files under ``confine_dir`` (default ``watch_root``) to Recycle Bin or delete them."""
    root = watch_root.resolve()
    scope_root = (confine_dir if confine_dir is not None else root).resolve()
    unique_paths: list[Path] = []
    seen: set[Path] = set()
    for raw in paths:
        cand = Path(raw)
        try:
            resolved = cand.resolve()
        except OSError:
            resolved = cand
        if resolved in seen:
            continue
        seen.add(resolved)
        unique_paths.append(cand)

    deleted: list[str] = []
    skipped_locked: list[str] = []
    failed: list[dict[str, str]] = []
    missing: list[str] = []
    freed_bytes = 0
    eligible: list[tuple[Path, int]] = []

    for fpath in unique_paths:
        if not os.path.lexists(fpath):
            missing.append(str(fpath))
            continue
        if not is_under_watch_root(fpath, scope_root) or fpath.resolve() == scope_root:
            failed.append({"path": str(fpath), "error": "Refused path outside allowed folder"})
            continue
        if fpath.is_dir():
            failed.append({"path": str(fpath), "error": "Directories are not deleted individually"})
            continue
        if is_file_locked(fpath):
            skipped_locked.append(str(fpath))
            continue
        try:
            size = int(fpath.stat().st_size)
        except OSError:
            size = 0
        eligible.append((fpath, size))

    total = len(eligible)
    used_recycle = False
    recycle_attempted = False
    if eligible and use_recycle_bin and sys.platform == "win32":
        recycle_attempted = True
        if on_progress is not None:
            label = eligible[0][0].name if total == 1 else f"{total} file(s)"
            on_progress(0, total, label)
        try:
            recycle_ok = _send_to_recycle_bin_win32([str(p) for p, _sz in eligible])
        except Exception as exc:
            logging.warning("Windows Recycle Bin call failed: %s", exc)
            recycle_ok = False
        if recycle_ok:
            used_recycle = True
            for idx, (fpath, size) in enumerate(eligible, start=1):
                if on_progress is not None:
                    on_progress(idx, total, fpath.name)
                if not os.path.lexists(fpath):
                    deleted.append(str(fpath))
                    freed_bytes += size
                else:
                    failed.append({"path": str(fpath), "error": "File remained after Recycle Bin request"})
            eligible = []

    for idx, (fpath, size) in enumerate(eligible, start=1):
        if on_progress is not None:
            on_progress(idx - 1, total, fpath.name)
        try:
            # Permanent fallback only when caller opted out of Recycle Bin or Windows Shell unavailable/aborted
            if recycle_attempted and use_recycle_bin:
                # Re-try single-file Shell recycle before falling back to unlink only when Recycle Bin failed cleanly
                if _send_to_recycle_bin_win32([str(fpath)]) and not os.path.lexists(fpath):
                    used_recycle = True
                    deleted.append(str(fpath))
                    freed_bytes += size
                    continue
            fpath.unlink()
            deleted.append(str(fpath))
            freed_bytes += size
        except OSError as exc:
            if getattr(exc, "winerror", None) in _LOCK_WINERRORS:
                skipped_locked.append(str(fpath))
            else:
                failed.append({"path": str(fpath), "error": str(exc)})

    if deleted:
        _invalidate_recent(deleted)
    if on_progress is not None and total > 0:
        on_progress(total, total, "Complete")

    return {
        "deleted": deleted,
        "skipped_locked": skipped_locked,
        "failed": failed,
        "missing": missing,
        "freed_bytes": freed_bytes,
        "recycled": used_recycle,
    }


class ZipBombError(Exception):
    """Raised when a ZIP archive fails zip-bomb or path-safety verification."""


def extract_zip_safely(
    zip_path: Path,
    target_dir: Path,
    watch_root: Path,
    *,
    db: StateDB | None = None,
    batch_id: str | None = None,
    keep_original: bool = False,
    max_files: int = 500,
    max_file_bytes: int = 250 * 1024 * 1024,
    max_total_bytes: int = 500 * 1024 * 1024,
    max_compression_ratio: float = 100.0,
) -> dict[str, Any]:
    """Safely extract a ``.zip`` archive inside ``watch_root`` with stream-based zip-bomb verification.

    Verification process:
    1. Rejects > ``max_files`` entries, symlinks, Zip-Slip (``..`` / absolute paths),
       and declared total size > ``max_total_bytes``.
    2. Streams each entry in 64 KB blocks, counting **actual decompressed bytes**
       (ignoring spoofed headers) and aborting immediately if per-file size,
       total size, or compression ratio (> ``max_compression_ratio`` above 1 MB) is exceeded.
    3. Rolls back any partial extraction on failure and leaves the original ``.zip`` untouched.
    """
    import stat as _stat
    import zipfile

    root = watch_root.resolve()
    src = zip_path.resolve()
    if not src.is_file() or src.suffix.lower() != ".zip" or not is_under_watch_root(src, root):
        return {"ok": False, "error": "Invalid or out-of-bounds zip archive", "extracted": []}
    if is_cloud_placeholder(src):
        return {"ok": False, "error": "Cloud placeholder (not hydrated)", "extracted": []}

    extract_root = (target_dir / src.stem).resolve()
    if not is_under_watch_root(extract_root, root):
        return {"ok": False, "error": "Target directory outside watch root", "extracted": []}
    if extract_root.exists():
        extract_root = _next_available_name(extract_root)

    effective_batch = batch_id or f"unzip-{uuid.uuid4().hex[:8]}"
    stage_dir = staging_dir_for(target_dir, effective_batch)
    staged_files: list[str] = []
    extracted: list[str] = []
    total_extracted_bytes = 0
    try:
        with zipfile.ZipFile(src, "r") as zf:
            infos = [info for info in zf.infolist() if not info.is_dir()]
            if len(infos) > max_files:
                raise ZipBombError(f"Archive exceeds {max_files} files ({len(infos)})")

            declared_total = 0
            for info in infos:
                # Reject symlinks stored in Unix external_attr
                if _stat.S_ISLNK((info.external_attr >> 16) & 0o170000):
                    raise ZipBombError(f"Symlink entry blocked: {info.filename}")
                declared_total += max(0, int(info.file_size))
                if declared_total > max_total_bytes:
                    raise ZipBombError("Declared archive size exceeds safety limit")
                if "\\" in info.filename and ".." in info.filename.replace("\\", "/").split("/"):
                    raise ZipBombError(f"Unsafe archive path: {info.filename}")
                rel_member = Path(info.filename.replace("\\", "/"))
                if rel_member.is_absolute() or ".." in rel_member.parts:
                    raise ZipBombError(f"Unsafe archive path: {info.filename}")
                dest_member = (stage_dir / rel_member).resolve()
                if not is_under_watch_root(dest_member, stage_dir.resolve()):
                    raise ZipBombError(f"Unsafe archive target: {info.filename}")

            for info in infos:
                rel_member = Path(info.filename.replace("\\", "/"))
                dest_member = (stage_dir / rel_member).resolve()
                dest_member.parent.mkdir(parents=True, exist_ok=True)
                compress_size = max(1, int(info.compress_size))
                file_bytes = 0

                with zf.open(info, "r") as src_stream, dest_member.open("wb") as out_stream:
                    while True:
                        chunk = src_stream.read(65536)
                        if not chunk:
                            break
                        chunk_len = len(chunk)
                        file_bytes += chunk_len
                        total_extracted_bytes += chunk_len

                        if file_bytes > max_file_bytes:
                            raise ZipBombError(
                                f"File '{info.filename}' exceeded {max_file_bytes // (1024 * 1024)} MB limit"
                            )
                        if total_extracted_bytes > max_total_bytes:
                            raise ZipBombError(
                                f"Archive exceeded {max_total_bytes // (1024 * 1024)} MB total limit"
                            )
                        # Enforce compression ratio once at least 1 MB has been decompressed
                        # so tiny valid files (e.g. small text/JSON) never false-positive.
                        if file_bytes >= 1024 * 1024:
                            ratio = file_bytes / compress_size
                            if ratio > max_compression_ratio:
                                raise ZipBombError(
                                    f"Suspected zip bomb in '{info.filename}' (ratio {ratio:.1f}:1 > {max_compression_ratio:.0f}:1)"
                                )
                        out_stream.write(chunk)
                staged_files.append(str(dest_member.relative_to(stage_dir.resolve())))

        extract_root.parent.mkdir(parents=True, exist_ok=True)
        os.replace(stage_dir, extract_root)
        extracted = [str((extract_root / rel).resolve()) for rel in staged_files]
    except (ZipBombError, zipfile.BadZipFile, OSError, RuntimeError) as exc:
        shutil.rmtree(stage_dir, ignore_errors=True)
        shutil.rmtree(extract_root, ignore_errors=True)
        logging.warning("Zip extraction aborted for %s: %s", src.name, exc)
        return {
            "ok": False,
            "zip_bomb": isinstance(exc, ZipBombError),
            "error": str(exc),
            "extracted": [],
        }

    archived_path = ""
    if not keep_original and db is not None:
        archive_rel = Path("Archives") / "_Extracted" / src.name
        rec = execute_move(
            src,
            RouteDecision(
                action="move",
                relative_dest=archive_rel,
                reason=f"Archived after extracting {len(extracted)} file(s)",
                matched_rule="Auto-Unzip",
                match_type="auto_unzip",
            ),
            batch_id=batch_id or f"unzip-{int(time.time())}",
            db=db,
            watch_root=root,
            settle_seconds=0.0,
        )
        if rec:
            archived_path = str(rec.get("dest_path") or "")

    return {
        "ok": True,
        "extract_dir": str(extract_root),
        "extracted": extracted,
        "total_bytes": total_extracted_bytes,
        "archived_zip": archived_path,
    }


_INSTALLER_EXTS = {".exe", ".msi", ".msix", ".msp"}


def scan_detox_candidates(
    watch_root: Path,
    *,
    old_installer_days: int = 30,
    now: float | None = None,
) -> dict[str, Any]:
    """Find old installers (>.N days) and empty directories inside ``watch_root``."""
    root = watch_root.resolve()
    if not root.is_dir():
        return {"old_installers": [], "empty_folders": [], "total_bytes": 0}

    ref_now = now if now is not None else time.time()
    cutoff = ref_now - max(1, int(old_installer_days)) * 86400.0
    old_installers: list[dict[str, Any]] = []
    empty_folders: list[str] = []
    total_bytes = 0

    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        cur = Path(dirpath)
        # Skip hidden and internal system/archive folders
        dirnames[:] = [
            d for d in dirnames if not d.startswith(".") and d not in ("_Duplicates", "_Extracted")
        ]
        for fname in filenames:
            fpath = cur / fname
            if fpath.suffix.lower() not in _INSTALLER_EXTS:
                continue
            try:
                st = fpath.stat()
            except OSError:
                continue
            if st.st_mtime <= cutoff:
                age_days = int((ref_now - st.st_mtime) / 86400.0)
                total_bytes += st.st_size
                old_installers.append(
                    {
                        "path": str(fpath),
                        "name": fpath.name,
                        "size_bytes": st.st_size,
                        "age_days": age_days,
                    }
                )

    # Bottom-up walk for empty directories
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        cur = Path(dirpath)
        if cur == root:
            continue
        if cur.name.startswith(".") or cur.name in ("_Duplicates", "_Extracted"):
            continue
        try:
            if not any(cur.iterdir()):
                empty_folders.append(str(cur))
        except OSError:
            pass

    return {
        "old_installers": sorted(old_installers, key=lambda item: item["age_days"], reverse=True),
        "empty_folders": sorted(empty_folders),
        "total_bytes": total_bytes,
    }


def prune_empty_folders(folder_paths: list[str], watch_root: Path) -> dict[str, Any]:
    """Remove truly empty directories strictly inside ``watch_root``."""
    root = watch_root.resolve()
    removed: list[str] = []
    failed: list[str] = []
    for raw in folder_paths:
        p = Path(raw).resolve()
        if p == root or not is_under_watch_root(p, root) or not p.is_dir():
            continue
        try:
            if not any(p.iterdir()):
                p.rmdir()
                removed.append(str(p))
        except OSError:
            failed.append(str(p))
    return {"removed": removed, "failed": failed}
