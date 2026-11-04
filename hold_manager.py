"""3MF Grace-Period Hold & MakerWorld Overhaul Manager for Download Sorter.

Holds active .3mf files in Downloads for a configurable grace period (default 7 days)
without moving them, preserving Bambu Studio / OrcaSlicer project shortcuts, recent
file lists, and direct saves.  Surfaces instant preview ribbons with embedded plate
thumbnails, and provides 1-click 'File now' and 'Keep here' controls.
"""

from __future__ import annotations

from contextlib import contextmanager
import copy
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import threading
import time
from typing import Iterable

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

import safe_ops
import sorter

SCHEMA = 1
STATE_NAME = "held_files.json"
DEFAULT_HOLD_DAYS = 7
DEFAULT_MAX_HELD = 15
DEFAULT_SLICERS = ["bambu-studio.exe", "bambu-studio-worker.exe", "orca-slicer.exe", "prusa-slicer.exe"]

_THREAD_LOCK = threading.RLock()


def state_file() -> Path:
    return Path(sorter.LOG_DIR) / STATE_NAME


@contextmanager
def state_lock(timeout: float = 2.0):
    """Cross-process file lock protecting held_files.json."""
    with _THREAD_LOCK:
        handle = None
        locked = False
        msvcrt = None
        if sys.platform == "win32":
            try:
                import msvcrt

                lock_path = state_file().with_suffix(".lock")
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    try:
                        handle = open(lock_path, "a+b")
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        locked = True
                        break
                    except (BlockingIOError, OSError):
                        if handle:
                            try:
                                handle.close()
                            except Exception:
                                pass
                            handle = None
                        time.sleep(0.02)
            except Exception as exc:
                logging.debug("Hold state lock fallback: %s", exc)
        try:
            yield
        finally:
            if locked and handle and msvcrt:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                except Exception:
                    pass
            if handle:
                try:
                    handle.close()
                except Exception:
                    pass


def read_state() -> dict:
    target = state_file()
    if not target.exists():
        return {"schema": SCHEMA, "held": {}}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "held" in data:
            return data
    except Exception as exc:
        logging.debug("Failed reading held_files.json: %s", exc)
    return {"schema": SCHEMA, "held": {}}


def write_state(data: dict) -> None:
    target = state_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".tmp")
    try:
        temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        temp.replace(target)
    except Exception as exc:
        logging.debug("Failed writing held_files.json: %s", exc)
        try:
            if temp.exists():
                temp.unlink()
        except Exception:
            pass


def is_slicer_running(slicer_names: Iterable[str] | None = None) -> bool:
    """Check if any 3D slicer executable is currently active in the process table."""
    if sys.platform != "win32":
        return False

    targets = {s.lower() for s in (slicer_names or DEFAULT_SLICERS)}
    try:
        TH32CS_SNAPPROCESS = 0x00000002

        class PROCESSENTRY32(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(wintypes.ULONG)),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", ctypes.c_wchar * 260),
            ]

        hSnapshot = ctypes.windll.kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        pe = PROCESSENTRY32()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
        found = False
        if ctypes.windll.kernel32.Process32FirstW(hSnapshot, ctypes.byref(pe)):
            while True:
                exe = pe.szExeFile.lower()
                if exe in targets:
                    found = True
                    break
                if not ctypes.windll.kernel32.Process32NextW(hSnapshot, ctypes.byref(pe)):
                    break
        ctypes.windll.kernel32.CloseHandle(hSnapshot)
        return found
    except Exception as exc:
        logging.debug("Slicer process check error: %s", exc)
        return False


def is_held(path: Path | str) -> bool:
    resolved = str(Path(path).resolve())
    with state_lock():
        state = read_state()
        return resolved in state.get("held", {})


def is_keep_here(path: Path | str) -> bool:
    resolved = str(Path(path).resolve())
    with state_lock():
        state = read_state()
        rec = state.get("held", {}).get(resolved)
        return bool(rec and rec.get("keep_here"))


def get_held(path: Path | str) -> dict | None:
    resolved = str(Path(path).resolve())
    with state_lock():
        state = read_state()
        rec = state.get("held", {}).get(resolved)
        return dict(rec) if rec else None


def get_all_held() -> list[dict]:
    with state_lock():
        state = read_state()
        return list(state.get("held", {}).values())


def hold_file(
    path: Path | str,
    config: dict,
    planned_dest: Path | str,
    meta: dict | None = None,
    now: float | None = None,
) -> bool:
    """Register a .3mf file for grace-period hold in Downloads."""
    p = Path(path).resolve()
    if not p.exists() or not p.is_file():
        return False

    hold_cfg = config.get("hold", {})
    if not hold_cfg.get("enabled", True):
        return False

    hold_days = float(hold_cfg.get("hold_days", DEFAULT_HOLD_DAYS))
    max_held = int(hold_cfg.get("max_held", DEFAULT_MAX_HELD))
    cur_time = time.time() if now is None else float(now)

    try:
        st = p.stat()
        mtime = st.st_mtime
        size = st.st_size
    except OSError:
        return False

    key = str(p)
    with state_lock():
        state = read_state()
        held = state.setdefault("held", {})

        # If already marked keep_here, respect it
        if key in held and held[key].get("keep_here"):
            return True

        # Enforce max held cap
        if len(held) >= max_held and key not in held:
            # Drop or release the oldest eligible file
            oldest_key = min(held.keys(), key=lambda k: held[k].get("first_seen", 0))
            if not held[oldest_key].get("keep_here"):
                del held[oldest_key]

        release_at = cur_time + (hold_days * 86400.0)
        mw_meta = (meta or {}).get("makerworld_meta", {}) if meta else {}

        held[key] = {
            "path": key,
            "filename": p.name,
            "size": size,
            "mtime": mtime,
            "first_seen": cur_time,
            "release_at": release_at,
            "hold_days": hold_days,
            "planned_dest": str(Path(planned_dest).resolve()),
            "makerworld_meta": mw_meta,
            "keep_here": False,
        }
        write_state(state)

    logging.info("3MF Hold registered: %s (holding until %s)", p.name, time.ctime(release_at))
    return True


def keep_here(path: Path | str) -> bool:
    """User chosen override: keep this .3mf file permanently in Downloads."""
    p = Path(path).resolve()
    key = str(p)
    with state_lock():
        state = read_state()
        held = state.setdefault("held", {})
        if key in held:
            held[key]["keep_here"] = True
        else:
            try:
                st = p.stat()
                mtime, size = st.st_mtime, st.st_size
            except OSError:
                mtime, size = time.time(), 0
            held[key] = {
                "path": key,
                "filename": p.name,
                "size": size,
                "mtime": mtime,
                "first_seen": time.time(),
                "release_at": float("inf"),
                "hold_days": DEFAULT_HOLD_DAYS,
                "planned_dest": "",
                "makerworld_meta": {},
                "keep_here": True,
            }
        write_state(state)
    logging.info("3MF permanently retained in Downloads: %s", p.name)
    return True


def remove_held(path: Path | str) -> None:
    key = str(Path(path).resolve())
    with state_lock():
        state = read_state()
        held = state.get("held", {})
        if key in held:
            del held[key]
            write_state(state)


def file_now(path: Path | str, config: dict | None = None) -> Path | None:
    """User chosen override: immediately file this .3mf file to its planned destination."""
    p = Path(path).resolve()
    key = str(p)
    dest_target: Path | None = None

    with state_lock():
        state = read_state()
        held = state.get("held", {})
        rec = held.get(key)
        if rec and rec.get("planned_dest"):
            dest_target = Path(rec["planned_dest"])
        if key in held:
            del held[key]
            write_state(state)

    if not p.exists():
        return None

    if dest_target is None:
        cfg = config if config is not None else sorter.load_config()
        dest_target = sorter.sort_destination(p, cfg)

    dest_target.parent.mkdir(parents=True, exist_ok=True)
    try:
        final_dest = safe_ops.move(p, dest_target, batch_id="file_now", settle_seconds=0.0)
        logging.info("3MF 'File Now' completed: %s -> %s", p.name, final_dest)
        return final_dest
    except Exception as exc:
        logging.error("Failed executing 'File Now' for %s: %s", p, exc)
        return None


def check_releases(
    config: dict,
    now: float | None = None,
) -> list[tuple[Path, Path]]:
    """Scan held files and return list of (src, planned_dest) that are ready for sorting."""
    hold_cfg = config.get("hold", {})
    if not hold_cfg.get("enabled", True):
        return []

    cur_time = time.time() if now is None else float(now)
    slicers = hold_cfg.get("slicers", DEFAULT_SLICERS)
    slicer_active = is_slicer_running(slicers)

    ready: list[tuple[Path, Path]] = []
    to_delete: list[str] = []
    to_update: dict[str, dict] = {}

    with state_lock():
        state = read_state()
        held = state.get("held", {})

        for key, rec in list(held.items()):
            p = Path(key)
            if not p.exists():
                to_delete.append(key)
                continue

            if rec.get("keep_here"):
                continue

            try:
                st = p.stat()
                cur_mtime = st.st_mtime
            except OSError:
                continue

            # If the user or Bambu Studio edited the file, push out the grace period
            recorded_mtime = float(rec.get("mtime", 0))
            if abs(cur_mtime - recorded_mtime) > 2.0:
                hold_days = float(rec.get("hold_days", DEFAULT_HOLD_DAYS))
                rec["mtime"] = cur_mtime
                rec["release_at"] = cur_mtime + (hold_days * 86400.0)
                to_update[key] = rec
                logging.debug("File modification detected on %s; resetting hold period", p.name)
                continue

            release_at = float(rec.get("release_at", 0))
            if cur_time < release_at:
                continue

            # File has reached release age; check lock & slicer
            if not safe_ops.can_open_exclusive(p):
                # File is currently opened by Bambu Studio or another app; defer
                logging.debug("Held file %s is currently locked by a process; deferring", p.name)
                continue

            # If slicer is running, allow up to 7 extra days deferral before overriding
            max_defer = release_at + (7 * 86400.0)
            if slicer_active and cur_time < max_defer:
                logging.debug("Slicer process active; deferring move for %s", p.name)
                continue

            planned = Path(rec.get("planned_dest", ""))
            if not planned or str(planned) == ".":
                planned = sorter.sort_destination(p, config)

            ready.append((p, planned))
            to_delete.append(key)

        if to_delete or to_update:
            for k in to_delete:
                held.pop(k, None)
            for k, val in to_update.items():
                held[k] = val
            write_state(state)

    return ready


def get_held_summary() -> dict:
    with state_lock():
        state = read_state()
        held = state.get("held", {})
        active = [r for r in held.values() if not r.get("keep_here")]
        kept = [r for r in held.values() if r.get("keep_here")]
        next_rel = min([r["release_at"] for r in active], default=None)
        return {
            "total_held": len(active),
            "kept_here": len(kept),
            "next_release_at": next_rel,
            "next_release_str": time.strftime("%Y-%m-%d %H:%M", time.localtime(next_rel)) if next_rel else "None",
        }


def prune_missing() -> int:
    pruned = 0
    with state_lock():
        state = read_state()
        held = state.get("held", {})
        for key in list(held.keys()):
            if not Path(key).exists():
                del held[key]
                pruned += 1
        if pruned:
            write_state(state)
    return pruned


def main(argv: list[str] | None = None) -> int:
    import about

    if about.handle_version_flag(argv):
        return 0
    print(about.version_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

