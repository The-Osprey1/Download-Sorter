"""Safe atomic file moves, duplicate isolation, lock checks, and SQLite-backed undo."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
import hashlib
import logging
import os
from pathlib import Path
import shutil
import sys
import time
from typing import Any

from download_sorter.db import StateDB
from download_sorter.router import RouteDecision

_CHUNK_BYTES = 1024 * 1024

# Tiered duplicate check: files above the threshold are compared by a head + tail
# sample first, so two different multi-gigabyte files of equal size are told apart
# without reading either one in full.
_SAMPLE_BYTES = 64 * 1024
_SAMPLE_THRESHOLD_BYTES = 256 * 1024

# Cloud-sync placeholders (OneDrive Files On-Demand, iCloud): reading their content
# makes Windows download the whole file.
FILE_ATTRIBUTE_OFFLINE = 0x00001000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x00400000
_CLOUD_PLACEHOLDER_MASK = FILE_ATTRIBUTE_OFFLINE | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS

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


def _sample_sha256(path: Path, size: int) -> str:
    """SHA-256 of the first and last 64 KB of ``path``."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        h.update(fh.read(_SAMPLE_BYTES))
        fh.seek(max(0, size - _SAMPLE_BYTES))
        h.update(fh.read(_SAMPLE_BYTES))
    return h.hexdigest()


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


def resolve_destination(src: Path, planned_dest: Path, watch_root: Path) -> tuple[Path, bool]:
    """Never overwrite an existing destination.

    If an exact byte-identical file already sits at ``planned_dest``, quarantine the
    incoming duplicate to ``watch_root/_Duplicates/<YYYY-MM>/<name>``. Otherwise add
    `` (2)`` / `` (3)`` numbering.
    """
    if not planned_dest.exists():
        return planned_dest, False
    if files_are_identical(src, planned_dest):
        month_dir = datetime.now().strftime("%Y-%m")
        dup_target = watch_root / "_Duplicates" / month_dir / src.name
        return _next_available_name(dup_target), True
    return _next_available_name(planned_dest), False


def _move_with_retries(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    last_err: OSError | None = None
    for attempt in range(4):
        try:
            try:
                os.replace(src, dest)
            except OSError as replace_err:
                # Cross-volume move falls back to shutil.move.
                if getattr(replace_err, "winerror", None) == 17 or getattr(replace_err, "errno", None) == 18:
                    shutil.move(str(src), str(dest))
                else:
                    raise
            return
        except OSError as exc:
            last_err = exc
            if attempt < 3:
                time.sleep(0.05 * (attempt + 1))
    assert last_err is not None
    raise last_err


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
    final_dest, is_dup = resolve_destination(src, planned_dest, watch_root)
    if final_dest == src_resolved:
        return None
    final_dest = final_dest.parent.resolve() / final_dest.name

    reason = f"Exact duplicate -> _Duplicates ({decision.reason})" if is_dup else decision.reason
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
    return {
        "id": move_id,
        "batch_id": batch_id,
        "filename": src.name,
        "src_path": str(src_resolved),
        "dest_path": str(final_dest),
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


def _undo_records(db: StateDB, records: list[dict[str, Any]]) -> dict[str, Any]:
    restored_ids: list[int] = []
    orphaned_ids: list[int] = []
    stale_paths: list[str] = []
    locked_files: list[str] = []
    renamed: list[dict[str, str]] = []
    failed = 0
    for rec in records:
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


def undo_move(db: StateDB, move_id: int) -> dict[str, Any]:
    """Undo a single recorded move by ID."""
    rec = db.get_move(move_id)
    if (
        not rec
        or rec.get("undone_at") is not None
        or str(rec.get("status") or "COMPLETED") != "COMPLETED"
    ):
        return _empty_undo_result()
    res = _undo_records(db, [rec])
    res["batch_id"] = rec["batch_id"]
    return res


def undo_batch(db: StateDB, batch_id: str | None = None) -> dict[str, Any]:
    """Undo every active move in ``batch_id`` (or the latest active batch).

    Files that are open in another program are skipped and stay undoable; files
    the user already deleted are retired from history; nothing is overwritten.
    """
    moves = db.get_batch_moves(batch_id)
    if not moves:
        return _empty_undo_result()
    res = _undo_records(db, moves)
    res["batch_id"] = str(moves[0]["batch_id"])
    return res
