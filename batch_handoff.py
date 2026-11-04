"""Batch handoff engine behind the Quick Switcher (no UI code in here).

Everything the pill needs to hand a *batch* of freshly sorted files to another
application lives here so it can be unit-tested without a window:

* **State** - ``download_sorter_recent_file.json`` (schema 2) under
  ``%LOCALAPPDATA%\\download-sorter``: written atomically (temp file + ``os.replace``
  with retries) under a short cross-process lock.  The legacy top-level keys
  (``path``/``name``/``category``/``color``/``timestamp``) are preserved so old readers
  keep working.
* **Grouping** - a file joins the current batch when it arrives within ``gap_seconds`` of
  the previous one, within ``max_span_seconds`` of the first one, and the batch holds
  fewer than ``max_files`` files - or when it carries the same ``batch_id`` (one watcher
  pass / one archive unpack).
* **Three handoff paths** - native ``CF_HDROP`` drag (works in any file-accepting app),
  ``CF_HDROP`` clipboard copy (``Ctrl+V`` pastes real files), and dialog injection with
  UIPI/elevation detection, edit-box polling, try-and-recover on rejection and a hidden
  same-volume hardlink staging folder for batches that span several folders.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import shutil
import struct
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import sorter

if sys.platform == "win32":
    from ctypes import wintypes

STATE_NAME = "download_sorter_recent_file.json"
LEGACY_STATE_FILE = Path(tempfile.gettempdir()) / STATE_NAME
RECENT_STATE_FILE = Path(sorter.LOG_DIR) / STATE_NAME
SCHEMA = 2
STAGE_DIR_NAME = ".batch_stage"

CF_HDROP = 15
DROPEFFECT_COPY = 1
TYMED_HGLOBAL = 1
WM_SETTEXT = 0x000C
WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_COMMAND = 0x0111
WM_CLOSE = 0x0010
VK_RETURN = 0x0D
IDOK = 1

ADMIN_WARNING = "Can't reach admin windows \u2014 drag instead"
TOO_MANY_WARNING = "Too many files \u2014 drag instead"


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------

def batch_settings() -> dict:
    """Merged ``batch_handoff`` settings (defaults + user config, read-only)."""
    merged = dict(sorter.V2_DEFAULTS["batch_handoff"])
    try:
        raw = json.loads(Path(sorter.CONFIG_PATH).read_text(encoding="utf-8"))
        block = raw.get("batch_handoff") if isinstance(raw, dict) else None
        if isinstance(block, dict):
            merged.update(block)
    except (OSError, ValueError, AttributeError):
        pass
    return merged


# ---------------------------------------------------------------------------
# state file (schema 2)
# ---------------------------------------------------------------------------

_THREAD_LOCK = threading.RLock()


def state_file() -> Path:
    return Path(sorter.LOG_DIR) / STATE_NAME


@contextmanager
def state_lock(timeout: float = 2.0):
    """Short cross-process lock around read-modify-write of the state file."""
    with _THREAD_LOCK:
        handle = None
        locked = False
        msvcrt = None
        if sys.platform == "win32":
            try:
                import msvcrt  # type: ignore[no-redef]

                lock_path = state_file().with_suffix(".lock")
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                handle = open(lock_path, "a+")
                deadline = time.time() + timeout
                while True:
                    try:
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        locked = True
                        break
                    except OSError:
                        if time.time() >= deadline:
                            break
                        time.sleep(0.02)
            except OSError:
                handle = None
        try:
            yield
        finally:
            if handle is not None:
                try:
                    if locked and msvcrt is not None:
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                finally:
                    handle.close()


def _write_state(data: dict) -> bool:
    target = state_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f"{target.stem}.{os.getpid()}.{uuid.uuid4().hex[:6]}.tmp")
    try:
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        logging.debug("Could not stage recent-file state: %s", exc)
        return False
    for _attempt in range(3):
        try:
            os.replace(temp, target)
            return True
        except OSError:
            time.sleep(0.05)
    try:
        temp.unlink()
    except OSError:
        pass
    logging.debug("Could not publish recent-file state after 3 attempts")
    return False


def read_state() -> dict | None:
    """Read the state file tolerantly (retry briefly; never raise on torn JSON)."""
    for path in (state_file(), LEGACY_STATE_FILE):
        if not path.exists():
            continue
        for _attempt in range(3):
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                return data if isinstance(data, dict) else None
            except (OSError, ValueError):
                time.sleep(0.05)
        return None
    return None


def _items_of(state: dict | None) -> list[dict]:
    """Normalize schema 1 (single file) and schema 2 (items list) into a list."""
    if not state:
        return []
    items = state.get("items")
    if isinstance(items, list):
        return [dict(i) for i in items if isinstance(i, dict) and "path" in i]
    if "path" in state and "timestamp" in state:
        legacy = {k: state[k] for k in ("path", "name", "folder", "category", "color", "timestamp") if k in state}
        return [legacy]
    return []


def _exists(item: dict) -> bool:
    try:
        target = Path(item["path"])
        return target.exists() and target.is_file()
    except (OSError, KeyError):
        return False


def _compose(items: list[dict], batch_id: str) -> dict:
    latest = items[-1]
    return {
        "schema": SCHEMA,
        "path": latest["path"],
        "name": latest.get("name", Path(latest["path"]).name),
        "folder": latest.get("folder", ""),
        "category": latest.get("category", "default"),
        "color": latest.get("color", ""),
        "timestamp": latest["timestamp"],
        "batch_id": batch_id,
        "batch_started": items[0]["timestamp"],
        "items": items,
    }


def record_recent_file(
    dest_path: str | Path,
    downloads_root: str | Path | None = None,
    batch_id: str | None = None,
    *,
    settings: dict | None = None,
    now: float | None = None,
) -> None:
    """Record a freshly sorted file into the current (or a new) batch."""
    dest = Path(dest_path).resolve()
    if not dest.exists() or not dest.is_file():
        return

    from ribbon_popup import get_category_info

    dl_root = Path(downloads_root).resolve() if downloads_root else Path.home() / "Downloads"
    try:
        rel = dest.relative_to(dl_root)
        folder_disp = str(rel.parent) if rel.parent != Path(".") else "Downloads"
    except ValueError:
        folder_disp = dest.parent.name
    category, color = get_category_info(dest, folder_disp)

    cfg = settings if settings is not None else batch_settings()
    timestamp = time.time() if now is None else float(now)
    item: dict = {
        "path": str(dest),
        "name": dest.name,
        "folder": folder_disp,
        "category": category,
        "color": color,
        "timestamp": timestamp,
    }
    if batch_id:
        item["source_batch"] = str(batch_id)

    gap = float(cfg.get("gap_seconds", 25))
    span = float(cfg.get("max_span_seconds", 120))
    cap = int(cfg.get("max_files", 50))

    with state_lock():
        state = read_state()
        items = [i for i in _items_of(state) if _exists(i)]
        state_batch = str((state or {}).get("batch_id") or uuid.uuid4().hex[:8])
        joined = False
        if cfg.get("enabled", True) and items:
            for existing in items:
                if existing["path"] == item["path"] and timestamp - float(existing["timestamp"]) < gap:
                    return  # same file re-announced (e.g. by the ribbon): nothing to do
            last, first = items[-1], items[0]
            same_pass = bool(batch_id) and last.get("source_batch") == str(batch_id)
            by_time = (
                timestamp - float(last["timestamp"]) <= gap
                and timestamp - float(first["timestamp"]) <= span
            )
            joined = same_pass or by_time
        if joined:
            items = [i for i in items if i["path"] != item["path"]]
            if len(items) >= cap:
                items = items[len(items) - cap + 1 :]
        else:
            items = []
            state_batch = uuid.uuid4().hex[:8]
        items.append(item)
        _write_state(_compose(items, state_batch))

    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SHAddToRecentDocs(0x00000003, str(dest))
        except Exception:
            pass


def get_recent_file(max_age_sec: float = 300.0) -> dict | None:
    """Latest recorded file (legacy shape) plus ``items``/``batch_id``/``batch_count``."""
    state = read_state()
    items = [i for i in _items_of(state) if _exists(i)]
    if not items:
        return None
    latest = items[-1]
    try:
        age = time.time() - float(latest["timestamp"])
    except (TypeError, ValueError, KeyError):
        return None
    if age > max_age_sec:
        return None
    data = dict(latest)
    data["age_sec"] = age
    data["items"] = items
    data["batch_id"] = str((state or {}).get("batch_id") or "")
    data["batch_count"] = len(items)
    return data


get_recent_batch = get_recent_file


def clear_recent_file() -> None:
    for path in (state_file(), LEGACY_STATE_FILE):
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass


def consume_items(paths: Iterable[str | Path]) -> None:
    """Remove handed-off files from the batch; clears the state when none remain."""
    gone = {str(Path(p).resolve()) for p in paths}
    with state_lock():
        state = read_state()
        batch = str((state or {}).get("batch_id") or uuid.uuid4().hex[:8])
        items = [i for i in _items_of(state) if str(Path(i["path"]).resolve()) not in gone and _exists(i)]
        if not items:
            clear_recent_file()
            return
        _write_state(_compose(items, batch))


# ---------------------------------------------------------------------------
# hotkeys
# ---------------------------------------------------------------------------

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN = 0x1, 0x2, 0x4, 0x8


def parse_hotkey(spec: str) -> tuple[int, int] | None:
    """``"Ctrl+Shift+G"`` -> ``(modifiers, virtual_key)``; ``None`` if unparseable."""
    mods = 0
    key: int | None = None
    for part in str(spec).replace(" ", "").split("+"):
        low = part.lower()
        if low in ("ctrl", "control"):
            mods |= MOD_CONTROL
        elif low == "shift":
            mods |= MOD_SHIFT
        elif low == "alt":
            mods |= MOD_ALT
        elif low in ("win", "super"):
            mods |= MOD_WIN
        elif len(part) == 1 and part.isalnum():
            key = ord(part.upper())
        elif low.startswith("f") and low[1:].isdigit() and 1 <= int(low[1:]) <= 12:
            key = 0x70 + int(low[1:]) - 1
        else:
            return None
    if key is None or mods == 0:
        return None
    return mods, key


def hotkey_is_down(mods: int, vk: int) -> bool:
    """Polled check that *exactly* these modifiers plus the key are held."""
    if sys.platform != "win32":
        return False
    user32 = ctypes.windll.user32

    def down(code: int) -> bool:
        return bool(user32.GetAsyncKeyState(code) & 0x8000)

    wanted = {
        0x11: bool(mods & MOD_CONTROL),
        0x10: bool(mods & MOD_SHIFT),
        0x12: bool(mods & MOD_ALT),
    }
    return all(down(code) == need for code, need in wanted.items()) and down(vk)


# ---------------------------------------------------------------------------
# clipboard (CF_HDROP)
# ---------------------------------------------------------------------------

def build_dropfiles(paths: Iterable[str | Path]) -> bytes:
    """``DROPFILES`` header + double-null-terminated UTF-16 path list."""
    wide = "".join(f"{p}\0" for p in paths) + "\0"
    header = struct.pack("<IiiII", 20, 0, 0, 0, 1)  # pFiles, pt.x, pt.y, fNC, fWide
    return header + wide.encode("utf-16-le")


def _win_api():
    k32, u32, s32 = ctypes.windll.kernel32, ctypes.windll.user32, ctypes.windll.shell32
    k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    k32.GlobalAlloc.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [ctypes.c_void_p]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    k32.GlobalFree.argtypes = [ctypes.c_void_p]
    u32.OpenClipboard.argtypes = [wintypes.HWND]
    u32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
    u32.SetClipboardData.restype = ctypes.c_void_p
    u32.GetClipboardData.argtypes = [wintypes.UINT]
    u32.GetClipboardData.restype = ctypes.c_void_p
    u32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
    s32.DragQueryFileW.argtypes = [ctypes.c_void_p, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
    s32.DragQueryFileW.restype = wintypes.UINT
    return k32, u32, s32


def _global_copy(k32, payload: bytes) -> int | None:
    handle = k32.GlobalAlloc(0x0002 | 0x0040, len(payload))  # GMEM_MOVEABLE | GMEM_ZEROINIT
    if not handle:
        return None
    ptr = k32.GlobalLock(handle)
    if not ptr:
        k32.GlobalFree(handle)
        return None
    ctypes.memmove(ptr, payload, len(payload))
    k32.GlobalUnlock(handle)
    return handle


def copy_files_to_clipboard(paths: Iterable[str | Path]) -> bool:
    """Put real files on the clipboard (paste as *copy* in Explorer, chat apps, uploads)."""
    if sys.platform != "win32":
        return False
    resolved = []
    for p in paths:
        try:
            if Path(p).exists():
                resolved.append(str(Path(p).resolve()))
        except OSError:
            continue
    if not resolved:
        return False
    k32, u32, _s32 = _win_api()
    drop = _global_copy(k32, build_dropfiles(resolved))
    effect = _global_copy(k32, struct.pack("<I", DROPEFFECT_COPY))
    if not drop or not effect:
        return False
    opened = False
    for _attempt in range(10):
        if u32.OpenClipboard(None):
            opened = True
            break
        time.sleep(0.03)
    if not opened:
        k32.GlobalFree(drop)
        k32.GlobalFree(effect)
        return False
    try:
        u32.EmptyClipboard()
        if not u32.SetClipboardData(CF_HDROP, drop):
            k32.GlobalFree(drop)
            k32.GlobalFree(effect)
            return False
        fmt = u32.RegisterClipboardFormatW("Preferred DropEffect")
        if not u32.SetClipboardData(fmt, effect):
            k32.GlobalFree(effect)
        return True
    finally:
        u32.CloseClipboard()


def _hdrop_to_paths(s32, hdrop) -> list[str]:
    count = s32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
    out: list[str] = []
    for index in range(count):
        length = s32.DragQueryFileW(hdrop, index, None, 0)
        buf = ctypes.create_unicode_buffer(length + 1)
        s32.DragQueryFileW(hdrop, index, buf, length + 1)
        out.append(buf.value)
    return out


def read_clipboard_files() -> list[str]:
    """Return the file paths currently on the clipboard as ``CF_HDROP`` (for tests/diagnostics)."""
    if sys.platform != "win32":
        return []
    _k32, u32, s32 = _win_api()
    if not u32.OpenClipboard(None):
        return []
    try:
        hdrop = u32.GetClipboardData(CF_HDROP)
        return _hdrop_to_paths(s32, hdrop) if hdrop else []
    finally:
        u32.CloseClipboard()


# ---------------------------------------------------------------------------
# native OLE drag source (CF_HDROP)
# ---------------------------------------------------------------------------

def _com_call(obj, index: int, restype, argtypes, *args):
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    prototype = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return prototype(vtbl[index])(obj, *args)


def create_file_data_object(paths: Iterable[str | Path]):
    """Shell ``IDataObject`` carrying the files (``CF_HDROP`` + shell ID list), or ``None``."""
    if sys.platform != "win32":
        return None
    resolved = [str(Path(p).resolve()) for p in paths if Path(p).exists()]
    if not resolved:
        return None
    shell32, ole32 = ctypes.windll.shell32, ctypes.windll.ole32
    shell32.SHParseDisplayName.argtypes = [
        wintypes.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), wintypes.ULONG, ctypes.c_void_p,
    ]
    shell32.CIDLData_CreateFromIDArray.argtypes = [
        ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
    ]
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    pidls = (ctypes.c_void_p * len(resolved))()
    parsed = 0
    try:
        for index, path in enumerate(resolved):
            pidl = ctypes.c_void_p()
            if shell32.SHParseDisplayName(path, None, ctypes.byref(pidl), 0, None) != 0 or not pidl.value:
                return None
            pidls[index] = pidl.value
            parsed += 1
        data_object = ctypes.c_void_p()
        hr = shell32.CIDLData_CreateFromIDArray(None, len(resolved), pidls, ctypes.byref(data_object))
        return data_object if hr == 0 and data_object.value else None
    finally:
        for index in range(parsed):
            ole32.CoTaskMemFree(pidls[index])


def release_com(obj) -> None:
    if obj is not None and getattr(obj, "value", None):
        try:
            _com_call(obj, 2, ctypes.c_ulong, [])  # IUnknown::Release
        except Exception:
            pass


def data_object_paths(data_object) -> list[str]:
    """Read ``CF_HDROP`` back out of an ``IDataObject`` (verification helper)."""
    if sys.platform != "win32" or data_object is None:
        return []

    class FORMATETC(ctypes.Structure):
        _fields_ = [("cfFormat", wintypes.WORD), ("ptd", ctypes.c_void_p), ("dwAspect", wintypes.DWORD),
                    ("lindex", wintypes.LONG), ("tymed", wintypes.DWORD)]

    class STGMEDIUM(ctypes.Structure):
        _fields_ = [("tymed", wintypes.DWORD), ("hGlobal", ctypes.c_void_p), ("pUnkForRelease", ctypes.c_void_p)]

    fmt = FORMATETC(CF_HDROP, None, 1, -1, TYMED_HGLOBAL)
    medium = STGMEDIUM()
    hr = _com_call(
        data_object, 3, ctypes.c_long,
        [ctypes.POINTER(FORMATETC), ctypes.POINTER(STGMEDIUM)], ctypes.byref(fmt), ctypes.byref(medium),
    )
    if hr != 0:
        return []
    _k32, _u32, s32 = _win_api()
    try:
        return _hdrop_to_paths(s32, medium.hGlobal)
    finally:
        ctypes.windll.ole32.ReleaseStgMedium(ctypes.byref(medium))


def start_file_drag(paths: Iterable[str | Path], hwnd: int | None = None) -> int:
    """Run a native Windows file drag (copy only).  Returns the drop effect (0 = cancelled)."""
    if sys.platform != "win32":
        return 0
    ole32, shell32 = ctypes.windll.ole32, ctypes.windll.shell32
    try:
        ole32.OleInitialize(None)
    except Exception:
        pass
    data_object = create_file_data_object(paths)
    if data_object is None:
        return 0
    try:
        shell32.SHDoDragDrop.argtypes = [
            wintypes.HWND, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        ]
        effect = wintypes.DWORD(0)
        hr = shell32.SHDoDragDrop(hwnd, data_object, None, DROPEFFECT_COPY, ctypes.byref(effect))
        return int(effect.value) if hr in (0, 0x00040100) else 0
    finally:
        release_com(data_object)


# ---------------------------------------------------------------------------
# staging for batches that span several folders
# ---------------------------------------------------------------------------

@dataclass
class InjectionPlan:
    kind: str  # "single" | "same_folder" | "staged" | "drag_only"
    paths: list[Path]
    folder: Path | None = None
    names: list[str] = field(default_factory=list)
    stage_dir: Path | None = None
    message: str = ""


def quote_names(names: Iterable[str]) -> str:
    """Common-dialog multi-file syntax: ``"a.stl" "b.stl"``."""
    return " ".join(f'"{n}"' for n in names)


def total_size_mb(paths: Iterable[Path]) -> float:
    total = 0
    for p in paths:
        try:
            total += Path(p).stat().st_size
        except OSError:
            pass
    return total / (1024 * 1024)


def plan_injection(
    paths: Iterable[str | Path],
    *,
    downloads_root: str | Path | None,
    settings: dict | None = None,
) -> InjectionPlan:
    cfg = settings if settings is not None else batch_settings()
    resolved = [Path(p).resolve() for p in paths if Path(p).exists()]
    if not resolved:
        return InjectionPlan("drag_only", [], message="Nothing to inject")
    if len(resolved) == 1:
        return InjectionPlan("single", resolved, folder=resolved[0].parent, names=[resolved[0].name])
    folders = {p.parent for p in resolved}
    if len(folders) == 1:
        folder = next(iter(folders))
        return InjectionPlan("same_folder", resolved, folder=folder, names=[p.name for p in resolved])
    import safe_ops  # local import keeps the module light for the pill process

    root = Path(downloads_root) if downloads_root else Path.home() / "Downloads"
    same_vol = all(safe_ops.same_volume(p, root) for p in resolved)
    small = total_size_mb(resolved) < float(cfg.get("stage_copy_limit_mb", 200))
    if same_vol or small:
        return InjectionPlan("staged", resolved, folder=root / STAGE_DIR_NAME, names=[p.name for p in resolved])
    return InjectionPlan("drag_only", resolved, message=TOO_MANY_WARNING)


def stage_batch(
    paths: Iterable[str | Path],
    batch_id: str,
    downloads_root: str | Path | None,
    settings: dict | None = None,
) -> tuple[Path, list[Path]]:
    """Hardlink (or, under the size limit, copy) the files into one hidden folder."""
    import safe_ops

    cfg = settings if settings is not None else batch_settings()
    root = Path(downloads_root) if downloads_root else Path.home() / "Downloads"
    stage = root / STAGE_DIR_NAME / (batch_id or uuid.uuid4().hex[:8])
    stage.mkdir(parents=True, exist_ok=True)
    safe_ops.make_hidden(stage.parent)  # the folder, never the (shared-metadata) hardlinks
    staged: list[Path] = []
    for src in (Path(p).resolve() for p in paths):
        try:
            staged.append(safe_ops.link(src, stage / src.name, batch_id=batch_id))
        except (safe_ops.CrossVolumeError, OSError):
            if total_size_mb([src]) < float(cfg.get("stage_copy_limit_mb", 200)):
                target = sorter.unique_destination(stage, src.name)
                shutil.copy2(src, target)
                staged.append(target)
            else:
                raise
    return stage, staged


def prune_stages(downloads_root: str | Path | None, max_age_hours: float = 24.0) -> int:
    """Delete stage folders older than ``max_age_hours`` (only our own hidden staging)."""
    root = (Path(downloads_root) if downloads_root else Path.home() / "Downloads") / STAGE_DIR_NAME
    removed = 0
    if not root.is_dir():
        return 0
    cutoff = time.time() - max_age_hours * 3600
    for child in root.iterdir():
        try:
            if child.is_dir() and child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    return removed


# ---------------------------------------------------------------------------
# dialog injection
# ---------------------------------------------------------------------------

@dataclass
class InjectResult:
    ok: bool
    mode: str  # "single" | "multi" | "fallback-latest" | "blocked" | "failed"
    message: str = ""
    injected: list[str] = field(default_factory=list)


def _token_elevated(process_handle) -> bool | None:
    advapi32 = ctypes.windll.advapi32
    token = wintypes.HANDLE()
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    if not advapi32.OpenProcessToken(process_handle, 0x0008, ctypes.byref(token)):  # TOKEN_QUERY
        return None
    try:
        elevation = wintypes.DWORD(0)
        size = wintypes.DWORD(0)
        advapi32.GetTokenInformation.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        ]
        ok = advapi32.GetTokenInformation(token, 20, ctypes.byref(elevation), 4, ctypes.byref(size))  # TokenElevation
        return bool(elevation.value) if ok else None
    finally:
        ctypes.windll.kernel32.CloseHandle(token)


def window_is_elevated(hwnd: int) -> bool | None:
    """True/False for the window's process elevation; ``None`` when it cannot be queried."""
    if sys.platform != "win32" or not hwnd:
        return None
    pid = wintypes.DWORD(0)
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return None
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    handle = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return True  # cannot even open it: treat as higher integrity
    try:
        return _token_elevated(handle)
    finally:
        kernel32.CloseHandle(handle)


def self_is_elevated() -> bool:
    if sys.platform != "win32":
        return False
    kernel32 = ctypes.windll.kernel32
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    return bool(_token_elevated(kernel32.GetCurrentProcess()))


def blocked_by_uipi(hwnd: int) -> bool:
    """True when ``hwnd`` runs elevated and we do not (window messages would be dropped)."""
    return bool(window_is_elevated(hwnd)) and not self_is_elevated()


def _get_text(hwnd: int) -> str:
    user32 = ctypes.windll.user32
    length = user32.SendMessageW(hwnd, WM_GETTEXTLENGTH, 0, 0)
    buf = ctypes.create_unicode_buffer(length + 2)
    user32.SendMessageW(hwnd, WM_GETTEXT, length + 1, buf)
    return buf.value


def _set_text(hwnd: int, text: str) -> None:
    ctypes.windll.user32.SendMessageW(hwnd, WM_SETTEXT, 0, text)


def _press_enter(dialog_hwnd: int, edit_hwnd: int) -> None:
    user32 = ctypes.windll.user32
    user32.PostMessageW(edit_hwnd, WM_KEYDOWN, VK_RETURN, 0)
    user32.PostMessageW(edit_hwnd, WM_KEYUP, VK_RETURN, 0)
    user32.PostMessageW(dialog_hwnd, WM_COMMAND, IDOK, 0)


def wait_until(predicate, timeout: float = 2.0, interval: float = 0.04) -> bool:
    """Poll ``predicate`` instead of sleeping blindly; True as soon as it holds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if predicate():
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def find_owned_error_dialog(dialog_hwnd: int, wait: float = 0.5) -> int:
    """HWND of a message box owned by ``dialog_hwnd`` that appears within ``wait`` seconds."""
    if sys.platform != "win32" or not dialog_hwnd:
        return 0
    user32 = ctypes.windll.user32
    found = {"hwnd": 0}
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def enum_proc(hwnd, _lparam):
        if hwnd == dialog_hwnd or not user32.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        if cls.value == "#32770" and user32.GetWindow(hwnd, 4) == dialog_hwnd:  # GW_OWNER
            found["hwnd"] = hwnd
            return False
        return True

    def scan() -> bool:
        found["hwnd"] = 0
        user32.EnumWindows(WNDENUMPROC(enum_proc), 0)
        return bool(found["hwnd"])

    wait_until(scan, timeout=wait, interval=0.03)
    return found["hwnd"]


def dismiss_dialog(hwnd: int) -> None:
    user32 = ctypes.windll.user32
    user32.PostMessageW(hwnd, WM_COMMAND, IDOK, 0)
    time.sleep(0.05)
    if user32.IsWindow(hwnd):
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


def inject_files_into_dialog(
    dialog_hwnd: int,
    paths: Iterable[str | Path],
    *,
    batch_id: str | None = None,
    downloads_root: str | Path | None = None,
    settings: dict | None = None,
) -> InjectResult:
    """Select several files in an Open dialog; degrades gracefully, never leaves a popup behind."""
    from quick_switcher import find_dialog_edit_control, inject_file_into_dialog

    if sys.platform != "win32" or not dialog_hwnd:
        return InjectResult(False, "failed", "Dialog unavailable")
    cfg = settings if settings is not None else batch_settings()
    plan = plan_injection(paths, downloads_root=downloads_root, settings=cfg)
    if plan.kind == "drag_only":
        return InjectResult(False, "failed", plan.message or TOO_MANY_WARNING)
    if blocked_by_uipi(dialog_hwnd):
        return InjectResult(False, "blocked", ADMIN_WARNING)

    latest = plan.paths[-1]
    if plan.kind == "single":
        ok = inject_file_into_dialog(dialog_hwnd, latest)
        return InjectResult(ok, "single", "" if ok else "Dialog unavailable", [str(latest)] if ok else [])

    folder, names, injected = plan.folder, plan.names, [str(p) for p in plan.paths]
    if plan.kind == "staged":
        try:
            prune_stages(downloads_root, float(cfg.get("stage_prune_hours", 24)))
            stage, staged = stage_batch(plan.paths, batch_id or uuid.uuid4().hex[:8], downloads_root, cfg)
        except (OSError, Exception) as exc:  # staging failed: still give the user the latest file
            logging.debug("Batch staging failed: %s", exc)
            ok = inject_file_into_dialog(dialog_hwnd, latest)
            return InjectResult(ok, "fallback-latest", TOO_MANY_WARNING, [str(latest)] if ok else [])
        folder, names = stage, [p.name for p in staged]

    edit = find_dialog_edit_control(dialog_hwnd)
    if not edit or folder is None:
        return InjectResult(False, "failed", "Dialog unavailable")
    user32 = ctypes.windll.user32
    try:
        user32.SetForegroundWindow(dialog_hwnd)
    except Exception:
        pass

    # 1) navigate to the folder, then wait (poll) for the dialog to consume the text
    _set_text(edit, str(folder))
    _press_enter(dialog_hwnd, edit)
    navigated = wait_until(lambda: _get_text(edit).strip() != str(folder), timeout=2.0)
    if not navigated:
        _set_text(edit, "")
        ok = inject_file_into_dialog(dialog_hwnd, latest)
        return InjectResult(ok, "fallback-latest", "Could not open the batch folder \u2014 injected latest", [str(latest)] if ok else [])

    # 2) hand over every name at once and watch for a rejection popup
    _set_text(edit, quote_names(names))
    _press_enter(dialog_hwnd, edit)
    popup = find_owned_error_dialog(dialog_hwnd, wait=0.5)
    if popup:
        dismiss_dialog(popup)
        _set_text(edit, "")
        ok = inject_file_into_dialog(dialog_hwnd, latest)
        return InjectResult(
            ok, "fallback-latest",
            "This dialog takes one file \u2014 injected latest. Drag the pill for all.",
            [str(latest)] if ok else [],
        )
    return InjectResult(True, "multi", "", injected)


def main(argv: list[str] | None = None) -> int:
    import about

    if about.handle_version_flag(argv):
        return 0
    print(about.version_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

